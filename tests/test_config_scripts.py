"""Configuration scripts (issue #34): a person's Python script, run in the script worker behind an API, and its output
carried on a change order as data.

The tests never need Docker: the worker module is driven through its local subprocess (``scriptworker.run_local``, the
fallback a dev hub uses) and through its /invoke server on a Unix socket in a temporary folder. No model is called.

What is shown here:
* the hub's allow-list check refuses with a sentence naming the line, before anything runs;
* the tripwire (audit hook) stops ``os.system``, sockets and file reads and writes even when the check is got past with
  ``__subclasses__``; the kernel filter refuses execve, fork and socket in the run's process;
* the limits: a loop gets the timeout, a memory bomb the address-space limit, output past the cap is cut off;
* the output is parsed, held to the format's schema and #33's checks; bad output is refused, and recorded;
* the change order shows the script, where it ran and its output; the release applies what was carried and signed,
  and never runs the script again;
* no cog submits a script, anywhere; a deployed hub refuses the local subprocess.
"""

import html
import json
import os
import re
import subprocess
import sys

import pytest

from conftest import ROOT
from hubproto import changecontrol as cc
from hubproto import configjson, schemacheck, scriptcheck, scripts, scriptworker
from test_change_control import released as release_co
from test_config_json import config_dir, exported, hub, run_cli, shell  # noqa: F401  (fixtures)

SUBCLASSES = "[c for c in ().__class__.__base__.__subclasses__() if c.__name__ == '_wrap_close'][0].__init__.__globals__"
THRESHOLD = '''th = next(e for e in config["policy"]["thresholds"] if e["name"] == "purchase-approval-threshold")
th["value"] = {value}
print("lowering", th["name"])
result = {{k: config[k] for k in ("schema", "hub", "release", "versions")}}
result["policy"] = {{"thresholds": config["policy"]["thresholds"]}}
'''
LINK = '''result = {k: config[k] for k in ("schema", "hub", "release", "versions")}
result["links"] = dict(config["links"], inspires="inspired-by")
'''
SNAP = json.dumps({"schema": "hub-erp/config/1", "n": 1})


def threshold(hub) -> float:
    return next(r["value"] for r in hub.settings_rules() if r["kind"] == "threshold" and r["name"] == "purchase-approval-threshold")


def new_co(hub, who="alice", title="Tune by script"):
    return hub.kernel.create("change-order", "Quality/Changes", {"title": title, "description": "a script's change", "requested_by": who}, by=who)


# ------------------------------------------------------------------ the allow-list check (hub side, before anything runs)
@pytest.mark.parametrize("source,words", [
    ("import os", "Line 1: import os is not allowed: a script may import only json, math"),
    ("x = 1\nimport subprocess, json", "Line 2: import subprocess is not allowed"),
    ("from socket import socket", "Line 1: from socket import ... is not allowed"),
    ("from . import x", "Line 1: a relative import is not allowed"),
    ("eval('1')", "Line 1: eval is not allowed: it runs text as code"),
    ("exec('x = 1')", "Line 1: exec is not allowed"),
    ("\n\ncompile('1', 'x', 'eval')", "Line 3: compile is not allowed"),
    ("__import__('os')", "Line 1: __import__ is not allowed"),
    ("open('/etc/passwd')", "Line 1: open is not allowed"),
    ("n = 'x'\ngetattr(config, n)", "Line 2: getattr is given a computed name"),
    ("setattr(config, 'a' + 'b', 1)", "Line 1: setattr is given a computed name"),
    ("delattr(config, name)", "Line 1: delattr is given a computed name"),
    ("getattr(config, '__class__')", "Line 1: getattr names __class__, a special attribute"),
    ("g = getattr", "Line 1: getattr is used other than called with a literal name"),
    ("globals()", "Line 1: globals is not allowed"),
    ("locals()", "Line 1: locals is not allowed"),
    ("vars(config)", "Line 1: vars is not allowed"),
    ("().__class__.__base__.__subclasses__()", "Line 1: .__base__ is a special attribute"),
    ("x = __builtins__", "Line 1: __builtins__ is a special name"),
    ("import string\nstring.Formatter().get_field('0.__class__', [()], {})", "Line 2: .Formatter is not allowed"),
    ("def f():\n    yield 1\nf().gi_frame.f_globals", "Line 3: .gi_frame is not allowed"),
    ("x = (", "Line 1: the script is not Python the hub can read"),
])
def test_the_allow_list_refuses_with_a_sentence_naming_the_line(source, words):
    out = scriptcheck.check(source)
    assert any(words in r for r in out), out
    assert all(r.startswith(("Line ", "the script", "The script")) for r in out), out


def test_a_plain_script_passes_the_check():
    ok = ("import json, math, re, datetime, decimal, collections, itertools, functools, statistics, string, typing\n"
          "from collections import defaultdict, namedtuple\nimport collections.abc\n"
          "class Row:\n    def __init__(self, v):\n        self.v = v\n"
          "x = getattr(config, 'get')('policy')\nresult = config\n")
    assert scriptcheck.check(ok) == []


def test_the_check_runs_before_anything_is_sent(hub, monkeypatch):
    def never(*a, **k):
        raise AssertionError("a refused script was run")

    monkeypatch.setattr(scriptworker, "run_local", never)
    monkeypatch.setattr(scripts, "_invoke", never)
    with pytest.raises(configjson.Refused, match="Line 2: import os is not allowed"):
        scripts.propose(hub, "x = 1\nimport os\nos.system('id')", by="alice")
    assert not hub.kernel.query(type_="change-order"), "nothing ran, nothing started"


# ------------------------------------------------------------------ the tripwire and the kernel filter (past the check)
@pytest.mark.parametrize("source,event", [
    (SUBCLASSES + "['system']('echo pwned')", "os.system"),
    (SUBCLASSES + "['__builtins__']['__import__']('socket').socket()", "import"),
    (SUBCLASSES + "['sys'].modules['posix'].system('id')", "os.system"),
    (SUBCLASSES + "['execv']('/bin/sh', ['sh'])", "os.exec"),
    (SUBCLASSES + "['__builtins__']['open']('{written}', 'w').write('x')", "open"),
    (SUBCLASSES + "['__builtins__']['open']('{secret}').read()", "open"),
    (SUBCLASSES + "['listdir']('/')", "os.listdir"),
])
def test_the_tripwire_stops_what_gets_past_the_check(tmp_path, source, event):
    secret = tmp_path / ".secret"
    secret.write_text("the hub's signing secret")
    written = tmp_path / "written"  # outside the run's own folder
    source = source.replace("{secret}", str(secret)).replace("{written}", str(written))
    assert scriptcheck.check(source), "the hub's check refuses it: this test goes past it on purpose"
    rec = scriptworker.run_local(source, SNAP, check=False)
    assert rec["exit"] == "tripwire" and rec["tripped"]["event"] == event, rec
    assert "the run was stopped" in rec["detail"] and rec["result"] is None
    assert not written.exists(), "the write did not happen"
    assert "signing secret" not in json.dumps(rec), "the secret was not read"


def test_the_tripwire_allows_the_runs_own_temporary_folder():
    src = (SUBCLASSES + "['__builtins__']['open']('scratch.txt', 'w').write('kept')\n"
           "result = {'read': " + SUBCLASSES + "['__builtins__']['open']('scratch.txt').read()}")
    rec = scriptworker.run_local(src, SNAP, check=False)
    assert rec["exit"] == "ok" and json.loads(rec["result"]) == {"read": "kept"}, rec


def test_a_script_cannot_import_what_is_off_the_list_even_past_the_check():
    rec = scriptworker.run_local("import os\nresult = {}", SNAP, check=False)
    assert rec["exit"] == "error" and "os is not a module a script may import" in rec["detail"], rec


@pytest.mark.skipif(sys.platform != "linux" or os.uname().machine not in ("x86_64", "aarch64"), reason="seccomp: Linux x86_64 or aarch64")
def test_the_kernel_filter_refuses_execve_fork_and_sockets_in_the_runs_process():
    sandbox = scriptworker.SANDBOX
    probe = ("import importlib.util, os, socket, sys\n"
             f"spec = importlib.util.spec_from_file_location('sb', {str(sandbox)!r})\n"
             "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
             "print(m.install_kernel_filter())\n"
             "for name, fn in [('execve', lambda: os.execv(sys.executable, [sys.executable, '-c', 'print(1)'])), ('fork', os.fork),\n"
             "                 ('socket', socket.socket), ('kill', lambda: os.kill(os.getppid(), 0))]:\n"
             "    try:\n        fn(); print(name, 'allowed')\n"
             "    except OSError as e:\n        print(name, 'refused', e.errno)\n")
    out = subprocess.run([sys.executable, "-I", "-S", "-c", probe], capture_output=True, text=True, timeout=30).stdout.split("\n")
    assert out[0] == "installed", out
    assert out[1:5] == ["execve refused 1", "fork refused 1", "socket refused 1", "kill refused 1"], out
    rec = scriptworker.run_local("result = {}", SNAP)
    assert rec["kernel_filter"] == "installed"


# ------------------------------------------------------------------ the limits
def test_an_endless_loop_gets_the_timeout():
    rec = scriptworker.run_local("while True:\n    pass", SNAP, {"seconds": 1.5})
    assert rec["exit"] == "timeout" and "longer than 1.5 seconds" in rec["detail"] and rec["seconds"] < 10, rec


def test_processor_time_has_its_own_limit():
    rec = scriptworker.run_local("x = 0\nwhile True:\n    x += 1", SNAP, {"seconds": 20, "cpu_seconds": 1})
    assert rec["exit"] == "cpu-limit" and rec["seconds"] < 15, rec


@pytest.mark.parametrize("bomb", ["x = bytearray(2 ** 40)", "x = []\nwhile True:\n    x.append(bytearray(10 ** 7))", "x = 'a' * (2 ** 36)"])
def test_a_memory_bomb_gets_the_address_space_limit(bomb):
    rec = scriptworker.run_local(bomb, SNAP, {"memory_bytes": 256 * 1024 * 1024, "seconds": 20})
    assert rec["exit"] == "memory-limit" and "256 MB" in rec["detail"], rec


def test_printing_past_the_cap_is_cut_off():
    rec = scriptworker.run_local("for i in range(20000):\n    print('line', i)\nresult = {}", SNAP, {"excerpt_chars": 1000})
    assert rec["exit"] == "ok" and len(rec["stdout"]) == 1000 and rec["stdout"].startswith("line 0\n") and rec["stdout_dropped"] > 100000, rec


def test_output_past_the_cap_stops_the_run():
    rec = scriptworker.run_local("result = {'x': 'y' * (8 * 1024 * 1024)}", SNAP, {"output_bytes": 1024 * 1024})
    assert rec["exit"] == "output-limit" and rec["result"] is None, rec["detail"]


def test_limits_are_lowered_never_raised():
    lim = scriptworker.limits_for({"seconds": 10 ** 6, "memory_bytes": 1, "cpu_seconds": 2, "open_files": True})
    assert lim["seconds"] == scriptworker.LIMITS["seconds"] and lim["cpu_seconds"] == 2
    assert lim["memory_bytes"] == scriptworker.MIN_MEMORY and lim["open_files"] == scriptworker.LIMITS["open_files"]


def test_the_run_has_no_environment_and_an_empty_folder(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-for-scripts")
    src = ("result = {'env': sorted(" + SUBCLASSES + "['environ']), 'cwd': " + SUBCLASSES + "['listdir']('.')}")
    rec = scriptworker.run_local(src, SNAP, check=False)
    got = json.loads(rec["result"]) if rec["exit"] == "ok" else rec
    # LC_CTYPE is the child's own (Python coerces the C locale, PEP 538); nothing of the hub's environment is passed.
    assert got["cwd"] == [] and set(got["env"]) <= {"PATH", "LC_CTYPE"} and "PATH" in got["env"], got


# ------------------------------------------------------------------ the /invoke seam
def test_the_worker_answers_invoke_on_a_unix_socket(tmp_path, monkeypatch):
    sock = tmp_path / "w.sock"
    server = scriptworker.serve_in_thread(socket_path=str(sock))
    try:
        url = f"unix://{sock}"
        env = scripts._invoke(url, {"tool": "run-script", "script": "result = dict(config, n=2)", "snapshot": SNAP}, 60)
        assert env["ok"] and env["cog"]["id"] == "script-worker" and env["payload"]["exit"] == "ok"
        assert json.loads(env["payload"]["result"]) == {"schema": "hub-erp/config/1", "n": 2}
        assert env["payload"]["script_sha256"] == scriptworker.sha256("result = dict(config, n=2)")
        env = scripts._invoke(url, {"tool": "run-script", "script": "import os", "snapshot": SNAP}, 60)
        assert env["payload"]["exit"] == "refused" and env["problems"], "the worker checks again: never runs what the list refuses"
        with pytest.raises(ValueError, match="unknown tool"):
            scripts._invoke(url, {"tool": "shell", "script": "x", "snapshot": "{}"}, 60)
        monkeypatch.setenv("HUBPROTO_SCRIPT_WORKER_URL", url)
        rec = scripts.run("result = {}", SNAP)
        assert rec["isolation"] == "container" and rec["exit"] == "ok"
    finally:
        server.shutdown()
        server.server_close()


def test_a_worker_that_cannot_be_reached_is_never_replaced_by_a_local_run(tmp_path, monkeypatch):
    monkeypatch.setenv("HUBPROTO_SCRIPT_WORKER_URL", f"unix://{tmp_path / 'none.sock'}")
    monkeypatch.setattr(scriptworker, "run_local", lambda *a, **k: pytest.fail("ran beside the hub"))
    with pytest.raises(ValueError, match="cannot be reached"):
        scripts.run("result = {}", SNAP)


# ------------------------------------------------------------------ the output on a change order
def test_a_script_puts_its_output_on_a_change_order_with_the_script(hub):
    co = new_co(hub)
    out = scripts.propose(hub, THRESHOLD.format(value=8000.0), by="alice", co_ref=co["number"], name="tune.py", reason="fewer approvals")
    assert out["changed"] and out["change_order"] == co["number"]
    assert out["summary"] == ["change “The purchase approval threshold is 10,000 USD.” to “The purchase approval threshold is 8,000 USD.”"]
    co = hub.kernel.get(co["locator"])
    [run] = co["fields"]["config_scripts"]
    snapshot = configjson.dumps(configjson.export(hub))
    assert run["script"] == THRESHOLD.format(value=8000.0) and run["script_sha256"] == scriptworker.sha256(run["script"])
    assert run["snapshot_sha256"] == scriptworker.sha256(snapshot) and run["snapshot_release"] == hub.releases.current()["digest"]
    assert run["output_sha256"] == scriptworker.sha256(run["output"]) and json.loads(run["output"])["policy"]["thresholds"]
    assert run["isolation"] == "local" and "weaker isolation" in run["isolation_note"]
    assert run["status"] == "carried" and run["exit"] == "ok" and run["stdout"] == "lowering purchase-approval-threshold\n"
    assert [d["rule"]["value"] for d in co["fields"]["direct_changes"]] == [8000.0], "carried as Settings carries it"
    assert cc.contributors(hub, co["number"]) == ["alice"]
    assert threshold(hub) == 10000.0, "nothing changes before the release"


def test_a_script_recorded_after_a_round_needs_another(hub):
    co = new_co(hub)
    scripts.propose(hub, THRESHOLD.format(value=8000.0), by="alice", co_ref=co["number"])
    cc.request_review(hub, co["number"], "alice")
    assert not [r for r in cc.send_reasons(hub, co["number"]) if "script" in r]
    with pytest.raises(configjson.Refused):
        scripts.propose(hub, "result = {'x': 1}", by="alice", co_ref=co["number"])  # refused, but recorded
    assert "configuration scripts and their output changed since round 1: request review again" in cc.send_reasons(hub, co["number"])


@pytest.mark.parametrize("result,words", [
    ("{'x': 1}", "the script's output does not match the configuration format: the document has no schema"),
    ("dict(config, schema='other/1')", 'schema is "other/1"; the format says "hub-erp/config/1"'),
    ("dict(config, hub={'id': '\\u00e9' * 32})", "hub.id"),
    ("dict(config, hub={'id': 'f' * 32})", "the document was exported from another hub"),
    ("{k: v for k, v in config.items() if k != 'versions'}", "the document has no versions"),
    ("dict(config, links={k: v for k, v in config['links'].items() if k != 'concerns'})",
     "the script's output: links.concerns: concerns is never removed"),
    ("dict(config, policy={'nothing': []})", "policy has no part nothing"),
])
def test_output_that_is_not_a_configuration_it_may_carry_is_refused_and_recorded(hub, result, words):
    co = new_co(hub)
    with pytest.raises(configjson.Refused) as e:
        scripts.propose(hub, f"result = {result}", by="alice", co_ref=co["number"])
    assert any(words in r for r in e.value.reasons), e.value.reasons
    co = hub.kernel.get(co["locator"])
    [run] = co["fields"]["config_scripts"]
    assert run["status"] == "refused" and run["refusals"] == e.value.reasons and run["output"] is not None
    assert not co["fields"].get("direct_changes") and not co["fields"].get("config_changes"), "nothing carried"


@pytest.mark.parametrize("source,words", [
    ("result = float('nan')", "the script's result is not JSON data"),
    ("x = 1", "the script left no result"),
    ("raise ValueError('no such threshold')", "ValueError: no such threshold"),
])
def test_a_script_that_does_not_finish_is_recorded_as_failed(hub, source, words):
    co = new_co(hub)
    with pytest.raises(configjson.Refused, match=re.escape(words)):
        scripts.propose(hub, source, by="alice", co_ref=co["number"])
    [run] = hub.kernel.get(co["locator"])["fields"]["config_scripts"]
    assert run["status"] == "failed" and run["output"] is None


def test_the_schema_check_holds_the_export_and_names_what_differs(hub):
    doc = exported(hub)
    assert schemacheck.problems(doc) == []
    doc["versions"]["links"] = "1"
    doc["release"].pop("digest")
    assert schemacheck.problems(doc) == ["release has no digest; the format requires it", "versions.links is text; the format says integer"]


def test_without_a_change_order_a_settings_one_is_started_only_when_something_is_carried(hub):
    with pytest.raises(configjson.Refused):
        scripts.propose(hub, "result = {'x': 1}", by="alice")
    assert not hub.kernel.query(type_="change-order"), "a failed run starts no change order"
    out = scripts.propose(hub, "result = config", by="alice")
    assert not out["changed"] and not hub.kernel.query(type_="change-order")
    out = scripts.propose(hub, THRESHOLD.format(value=7000.0), by="alice")
    assert out["changed"] and out["created"] and hub.kernel.get(out["locator"])["fields"]["config_scripts"][0]["status"] == "carried"


# ------------------------------------------------------------------ the release applies the stored output, never the script
def test_the_release_applies_the_signed_output_and_never_runs_the_script_again(hub, monkeypatch):
    co = new_co(hub)
    scripts.propose(hub, LINK, by="alice", co_ref=co["number"])
    co = hub.kernel.get(co["locator"])
    stored = co["fields"]["config_scripts"][0]

    def never(*a, **k):
        raise AssertionError("a script was run after it was submitted")

    for target, name in ((scriptworker, "run_local"), (scripts, "_invoke"), (scripts, "run"), (scripts, "propose"),
                         (subprocess, "Popen")):
        monkeypatch.setattr(target, name, never)
    co = release_co(hub, co)
    assert co["state"] == "released" or co["fields"].get("released_release"), co["state"]
    assert hub.kernel.link_vocab.get("inspires") == "inspired-by", "what was carried and signed is in force"
    assert "inspires" not in json.loads(stored["output"]).get("policy", {}), "and only what the output said"
    assert hub.kernel.get(co["locator"])["fields"]["config_scripts"] == [stored], "the record is kept as it was signed"
    from hubproto import automation

    assert "script" not in json.dumps(automation.MODEL_STEPS).lower()
    assert not any("script" in s.lower() for s in automation.AUTOMATIONS["release-change-order"]["steps"])


def test_a_change_to_the_recorded_run_after_signing_is_caught(hub):
    co = new_co(hub)
    scripts.propose(hub, THRESHOLD.format(value=8000.0), by="alice", co_ref=co["number"])
    cc.request_review(hub, co["number"], "alice")
    cc.send_for_approval(hub, co["number"], "alice")
    co = hub.kernel.get(co["locator"])
    runs = co["fields"]["config_scripts"]
    hub.kernel.set_fields(co["locator"], {"config_scripts": [{**runs[0], "script": "result = config  # not what ran"}]}, by="mallory")
    assert "its configuration scripts and their output" in cc.changed_since_sent(hub, co["number"])


# ------------------------------------------------------------------ who: a person, never a cog
def test_no_cog_may_submit_a_script(hub, tmp_path):
    with pytest.raises(configjson.Refused, match="qa-manager is a cog: a person writes and submits a configuration script"):
        scripts.propose(hub, "result = config", by="qa-manager")
    with pytest.raises(configjson.Refused, match="is not a person of the organization"):
        scripts.propose(hub, "result = config", by="mallory")
    f = tmp_path / "tune.py"
    f.write_text(THRESHOLD.format(value=8000.0))
    rc, text = run_cli(hub.root, "config", "propose", str(f), "--as", "qa-manager")
    assert rc == 2 and "qa-manager is a cog; only a person may do this" in text
    assert not hub.kernel.query(type_="change-order")


def test_no_cog_tool_writes_or_runs_a_script():
    import hubproto.agent_audit  # noqa: F401  (registers its tools)
    import hubproto.agent_config  # noqa: F401
    from hubproto import agent, tools

    specs = tools.TOOLS + agent.ACTION_TOOLS + agent.look_specs() + [s for s, _ in agent.EXTRA_ACTS.values()]
    assert specs
    for s in specs:
        text = (s["name"] + " " + s.get("description", "")).lower()
        assert not re.search(r"\bscript|python|\bcode\b|execute|\beval", text), s["name"]
    fns = [fn for _, fn in list(agent.EXTRA_ACTS.values()) + list(agent.EXTRA_LOOKS.values())]
    assert not any((getattr(fn, "__module__", "") or "").startswith("hubproto.script") for fn in fns)


def test_the_hub_never_runs_code_itself():
    """No eval or exec in the hub: the one exec is the run's own, in script_sandbox.py, which the hub never imports."""
    found = []
    for p in sorted((ROOT / "hubproto").rglob("*.py")):
        for n, line in enumerate(p.read_text().splitlines(), start=1):
            code = line.split("#", 1)[0]
            if re.search(r"(?<![\w.])(eval|exec)\s*\(", code) and not code.strip().startswith(("\"", "'")):
                found.append(f"{p.relative_to(ROOT)}:{n}")
    assert len(found) == 1 and found[0].startswith("hubproto/script_sandbox.py:"), found
    probe = "import sys, hubproto.app, hubproto.scripts, hubproto.scriptworker, hubproto.shell; print('hubproto.script_sandbox' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert out.stdout.strip() == "False", out.stderr[-400:]


# ------------------------------------------------------------------ where it runs: a deployed hub refuses the fallback
def test_a_deployed_hub_refuses_the_local_subprocess(hub, monkeypatch):
    monkeypatch.setenv("HUBPROTO_DEPLOYED", "1")
    monkeypatch.delenv("HUBPROTO_SCRIPT_WORKER_URL", raising=False)
    monkeypatch.setattr(scriptworker, "run_local", lambda *a, **k: pytest.fail("ran beside a deployed hub"))
    co = new_co(hub)
    with pytest.raises(configjson.Refused, match="This hub is deployed and has no script worker"):
        scripts.propose(hub, THRESHOLD.format(value=8000.0), by="alice", co_ref=co["number"])
    assert not hub.kernel.get(co["locator"])["fields"].get("config_scripts")
    assert scripts.where_it_runs()[0] == "refused"
    monkeypatch.setenv("HUBPROTO_DEPLOYED", "0")
    assert scripts.where_it_runs()[0] == "local"


def test_the_hub_image_is_deployed_and_compose_hardens_the_worker():
    assert "HUBPROTO_DEPLOYED=1" in (ROOT / "Dockerfile").read_text()
    compose = (ROOT / "docker-compose.yml").read_text()
    svc = compose.split("\n  script-worker:\n", 1)[1].split("\n\n", 1)[0]
    for line in ("network_mode: none", "read_only: true", "- ALL", "no-new-privileges:true", "seccomp=./docker/script-worker/seccomp.json",
                 'user: "10001:10001"', "pids_limit:", "mem_limit:", "cpus:"):
        assert line in svc, line
    assert "docker.sock" not in compose and "hub-state" not in svc and "secret" not in svc.lower() and "networks:" not in svc
    assert "HUBPROTO_SCRIPT_WORKER_URL: unix:///run/script-worker/worker.sock" in compose
    prof = json.loads((ROOT / "docker" / "script-worker" / "seccomp.json").read_text())
    assert prof["defaultAction"] == "SCMP_ACT_ERRNO"
    allowed = {n for s in prof["syscalls"] if s["action"] == "SCMP_ACT_ALLOW" and not s.get("args") for n in s["names"]}
    for name in ("connect", "socket", "socketpair", "ptrace", "mount", "umount2", "unshare", "setns", "bpf", "execveat", "kexec_load",
                 "init_module", "pivot_root", "chroot"):
        assert name not in allowed, name
    [sock] = [s for s in prof["syscalls"] if s["names"] == ["socket"]]
    assert sock["args"] == [{"index": 0, "value": 1, "op": "SCMP_CMP_EQ"}], "socket only for AF_UNIX"
    image = (ROOT / "docker" / "script-worker" / "Dockerfile").read_text()
    assert "pip install" not in image and "COPY hubproto/envelope.py hubproto/scriptcheck.py hubproto/scriptworker.py hubproto/script_sandbox.py" in image


# ------------------------------------------------------------------ the command line and the page
def test_the_command_line_runs_a_script_and_says_where(hub, tmp_path):
    f = tmp_path / "tune.py"
    f.write_text(THRESHOLD.format(value=8000.0))
    rc, text = run_cli(hub.root, "config", "propose", str(f), "--as", "alice")
    assert rc == 0, text
    assert "tune.py ran in a local subprocess (weaker isolation: no script worker)" in text and "with the script and its output (script-1)" in text
    bad = tmp_path / "bad.txt"
    bad.write_text("import os\n")
    rc, text = run_cli(hub.root, "config", "propose", str(bad), "--script", "--as", "alice")
    assert rc == 1 and "Line 1: import os is not allowed" in text


def test_the_change_order_page_runs_a_script_and_shows_it_with_its_output(shell):
    from test_change_order_pages import _post_file, co_page, new_co as page_co
    from test_shell_views import main_of, plain, post

    hub = shell["hub"]
    co = page_co(shell, who="alice", title="Tune by script")
    page = co_page(shell, co, "alice")
    assert "Run a configuration script (Python)" in page and "weaker isolation" in page
    assert "Run a configuration script" not in co_page(shell, co, "dave"), "only who may change what it carries"
    fields = {"as": "alice", "co": co["locator"], "form": "script-run", "reason": "fewer approvals", "script": THRESHOLD.format(value=8000.0)}
    status, url, body = _post_file(shell, "/co-script-run", fields, [])
    assert status == 200, plain(main_of(body))[:400]
    assert f"carried on {co['number']}: 1 change" in plain(main_of(body)) and "weaker isolation: a local subprocess" in plain(main_of(body))
    page = co_page(shell, co, "carol")  # a reviewer sees the script, where it ran, the output and what it carried
    text = html.unescape(plain(page))
    assert "Configuration scripts" in text and "script-1: script.py" in text and "weaker isolation: local subprocess" in text
    assert 'th["value"] = 8000.0' in text, "the script, line by line"
    assert '"purchase-approval-threshold"' in text and "Its output" in text, "the output"
    assert "The purchase approval threshold is 8,000 USD." in text and "lowering purchase-approval-threshold" in text
    run = hub.kernel.get(co["locator"])["fields"]["config_scripts"][0]
    assert run["script_sha256"] in page and run["output_sha256"] in page
    # A file, refused past the run: the sentence on the page, and the run recorded with why.
    status, url, body = _post_file(shell, "/co-script-run", {**fields, "script": "", "once": "s2"}, [("file", "bad.py", b"result = {'x': 1}")])
    assert status == 400 and "bad.py was not carried; nothing was" in plain(main_of(body)) and "has no schema" in plain(main_of(body))
    assert [r["status"] for r in hub.kernel.get(co["locator"])["fields"]["config_scripts"]] == ["carried", "refused"]
    # A cog: refused at the page too.
    status, url, body = post(shell, "/co-script-run", {**fields, "as": "qa-manager", "once": "s3"})
    assert status in (400, 403) and "cog" in plain(body), plain(body)[:300]
    assert len(hub.kernel.get(co["locator"])["fields"]["config_scripts"]) == 2
