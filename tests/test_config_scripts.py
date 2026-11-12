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

import threading
import time
from pathlib import Path

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
ATTACKS = Path("/nonexistent")  # the reviewer's scripts are inlined below; nothing here reads outside the checkout
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
    ("n = 'x'\ngetattr(config, n)", "Line 2: getattr is not allowed: it reaches an attribute by a name"),
    ("setattr(config, 'a' + 'b', 1)", "Line 1: setattr is not allowed"),
    ("delattr(config, name)", "Line 1: delattr is not allowed"),
    ("g = getattr", "Line 1: getattr is not allowed"),
    # The review of PR #55 (finding 4): each of these passed the check before.
    ("x = config\ngetattr(x, 'tb_frame')", "Line 2: getattr is not allowed"),
    ("import typing\nsys = typing.sys", "Line 1: import typing is not allowed"),
    ("import statistics\nx = statistics.sys", "Line 2: statistics.sys is not allowed: a script uses only the names of statistics on its list"),
    ("import statistics\nstatistics.sys.modules", "Line 2: statistics.sys is not allowed"),
    ("import collections\ncollections._sys.modules", "Line 2: collections._sys is not allowed"),
    ("import collections.abc\ncollections.abc.sys", "Line 2: collections.abc.sys is not allowed"),
    ("from typing import TypeVar", "Line 1: from typing import ... is not allowed"),
    ("from statistics import sys", "Line 1: statistics.sys is not allowed"),
    ("import json as j\nj.decoder.scanstring", "Line 2: json.decoder is not allowed"),
    ("import json\nm = json\nm.loads", "Line 2: json is a module: use it only as json.name"),
    ("def f(x: 'int'): pass", "Line 1: an annotation written as text is not allowed"),
    ("x = config\nx.get_type_hints", "Line 2: .get_type_hints is not allowed: it evaluates text as code"),
    ("x = config\nx.evaluate_bound(1).evaluate()", "Line 2: .evaluate is not allowed: it evaluates text as code"),
    ("import collections\ncollections.namedtuple('P', 'a')", "Line 2: collections.namedtuple is not allowed"),
    # The follow-up review (N1): dunder text built by joining, and the names that evaluate text.
    ("u = '_' + '_'", "Line 1: the text '_' holds two underscores together, or begins or ends with one"),
    ("u = 'x_'\nv = u + '_class'", "Line 1: the text 'x_' holds two underscores together, or begins or ends with one"),
    ("u = '_' * 2", "Line 1: the text '_'"),
    ("u = ''.join(['_', '_'])", "Line 1: the text '_'"),
    ("u = f'{1}_'", "Line 1: the text '_'"),
    ("u = b'_' + b'_'", "Line 1: the text b'_'"),
    ("u = chr(95) * 2", "Line 1: chr is not allowed"),
    ("x: 'list' = []", "Line 1: an annotation written as text is not allowed"),
    ("match ():\n    case object(__class__=t):\n        pass", "Line 2: a class pattern in match is not allowed"),
    ("match config:\n    case dict(schema=s):\n        pass", "Line 2: a class pattern in match is not allowed"),
    ("f = lambda: 0\nx = '{0.__globals__}'.format(f)", "Line 2: the text '{0.__globals__}' holds two underscores together"),
    ("x = '{0.gi_frame}'.format(config)", "Line 1: .format is not allowed: it reads attributes by names written in the text"),
    ("x = '{a}'.format_map(config)", "Line 1: .format_map is not allowed"),
    ("from json import *", "Line 1: from json import * is not allowed"),
    ("globals()", "Line 1: globals is not allowed"),
    ("locals()", "Line 1: locals is not allowed"),
    ("vars(config)", "Line 1: vars is not allowed"),
    ("().__class__.__base__.__subclasses__()", "Line 1: .__base__ is a special attribute"),
    ("x = __builtins__", "Line 1: __builtins__ is a special name"),
    ("import string\nstring.Formatter().get_field('0', [()], {})", "Line 2: string.Formatter is not allowed"),
    ("def f():\n    yield 1\nf().gi_frame.f_globals", "Line 3: .gi_frame is not allowed"),
    ("x = (", "Line 1: the script is not Python the hub can read"),
])
def test_the_allow_list_refuses_with_a_sentence_naming_the_line(source, words):
    out = scriptcheck.check(source)
    assert any(words in r for r in out), out
    assert all(r.startswith(("Line ", "the script", "The script")) for r in out), out


def test_a_plain_script_passes_the_check():
    ok = ("import json, math, re, datetime, decimal, collections, itertools, functools, statistics, string\n"
          "from collections import defaultdict, Counter\nimport collections.abc\n"
          "class Row:\n    def __init__(self, v):\n        self.v = v\n"
          "x = config.get('policy')\ny = collections.abc.Mapping\nmatch x:\n    case {'roles': r}:\n        pass\n"
          "def f(n: int) -> int:\n    return n\nresult = {'purchase_approval': config.get('x_y')}\n")
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


def test_the_run_writes_no_file_even_in_its_own_folder():
    # The tripwire allows its own folder; the kernel's allow-list opens files only to read (a computation needs no file).
    src = SUBCLASSES + "['__builtins__']['open']('scratch.txt', 'w').write('kept')\nresult = {}"
    rec = scriptworker.run_local(src, SNAP, check=False)
    assert rec["exit"] == "error" and "PermissionError" in rec["detail"], rec["detail"]


def test_a_script_cannot_import_what_is_off_the_list_even_past_the_check():
    rec = scriptworker.run_local("import os\nresult = {'x': os.name}", SNAP, check=False)
    assert rec["exit"] == "error" and "os is not a module a script may import" in rec["detail"], rec
    rec = scriptworker.run_local("import statistics\nresult = {'x': str(statistics.sys)}", SNAP, check=False, tripwire=False)
    assert rec["exit"] == "error" and "has no attribute 'sys'" in rec["detail"], "an allowed module is its names only, at run time too"


@pytest.mark.skipif(sys.platform != "linux" or os.uname().machine not in ("x86_64", "aarch64"), reason="seccomp: Linux x86_64 or aarch64")
def test_the_kernel_filter_refuses_execve_fork_and_sockets_in_the_runs_process():
    sandbox = scriptworker.SANDBOX
    probe = ("import importlib.util, os, socket, sys\n"
             f"spec = importlib.util.spec_from_file_location('sb', {str(sandbox)!r})\n"
             "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
             "m.not_dumpable(); print(m.install_kernel_filter())\n"
             "for name, fn in [('execve', lambda: os.execv(sys.executable, [sys.executable, '-c', 'print(1)'])), ('fork', os.fork),\n"
             "                 ('socket', socket.socket), ('kill', lambda: os.kill(os.getppid(), 0))]:\n"
             "    try:\n        fn(); print(name, 'allowed')\n"
             "    except OSError as e:\n        print(name, 'refused', e.errno)\n")
    out = subprocess.run([sys.executable, "-I", "-S", "-c", probe], capture_output=True, text=True, timeout=30).stdout.split("\n")
    assert out[0] == "installed", out
    assert out[1:5] == ["execve refused 1", "fork refused 1", "socket refused 1", "kill refused 1"], out
    rec = scriptworker.run_local("result = {}", SNAP)
    assert scripts.protected({**rec["protection"], "checked_by": "the hub"}), rec["protection"]


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
    for name in ("socket", "socketpair", "ptrace", "mount", "umount2", "unshare", "setns", "bpf", "execveat", "kexec_load",
                 "init_module", "pivot_root", "chroot", "setpriority", "syslog", "sched_setaffinity", "ioprio_set"):
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


# ------------------------------------------------------------------ the security review of PR #55, finding by finding
# Each test below is built from one of the reviewer's attack scripts. ESCAPE reaches the os module without the allow-list
# (a match class pattern, finding 4) and the tests run it with the check and the tripwire both switched off, so what
# holds is the kernel: Landlock, seccomp, not dumpable, the limits.
ESCAPE = """match ():
    case object(__class__=t):
        pass
match t:
    case object(__base__=o):
        pass
match o:
    case object(__subclasses__=subs):
        pass
match [c for c in subs() if "wrap_close" in str(c)][0]:
    case object(__init__=init):
        pass
match init:
    case object(__globals__=g):
        pass
class OS:
    pass
os = OS()
for k, v in g.items():
    os.__dict__[k] = v
sys = g["sys"]
out = {}
def attempt(name, fn):
    try:
        out[name] = "WORKED " + str(fn())[:300]
    except Exception as e:
        out[name] = "refused " + type(e).__name__
"""


def escaped(body: str, **kw) -> dict:
    rec = scriptworker.run_local(ESCAPE + body + "\nresult = out\n", SNAP, {"seconds": 10, **kw.pop("limits", {})}, check=False,
                                 tripwire=False, **kw)
    assert rec["exit"] == "ok", (rec["exit"], rec["detail"], rec["raw_output"][-400:])
    return json.loads(rec["result"])


def test_finding_1_the_run_cannot_read_the_hubs_secrets_or_write_outside_its_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-REVIEW-FAKE-0000")  # the parent's environment, as the hub's
    os.environ["FAKE_ANTHROPIC_API_KEY"] = "sk-ant-REVIEW-FAKE-0000"
    state = tmp_path / "state"
    state.mkdir()
    (state / ".secret").write_text("the-hubs-signing-secret")
    outside = tmp_path / "written-by-script.txt"
    in_tmp = Path("/tmp") / f"left-behind-by-a-run-{os.getpid()}-{time.time_ns()}.txt"  # outside the run's own folder
    try:
        got = escaped(f"""
attempt("parent environ", lambda: os.read(os.open("/proc/%d/environ" % os.getppid(), os.O_RDONLY), 1 << 20))
attempt("hub cwd", lambda: os.readlink("/proc/%d/cwd" % os.getppid()))
attempt("secret", lambda: os.read(os.open({str(state / '.secret')!r}, os.O_RDONLY), 100))
attempt("write outside", lambda: os.write(os.open({str(outside)!r}, os.O_WRONLY | os.O_CREAT, 0o644), b"x"))
attempt("builtins open", lambda: g["__builtins__"]["open"]({str(outside)!r}, "w").write("x"))
attempt("write /tmp", lambda: os.write(os.open({str(in_tmp)!r}, os.O_WRONLY | os.O_CREAT, 0o644), b"x"))
def dirfd():  # the reviewer's 1(b): a deep cwd in the run's folder, dir_fd on an allowed root, then climb out
    os.makedirs("a/b/c/d/e/f/g/h/i/j/k/l")
    os.chdir("a/b/c/d/e/f/g/h/i/j/k/l")
    dfd = os.open([p for p in sys.path if p.endswith("lib-dynload")][0], os.O_RDONLY)
    return os.read(os.open("../" * 12 + "proc/%d/environ" % os.getppid(), os.O_RDONLY, dir_fd=dfd), 1 << 20)
attempt("dir_fd", dirfd)
attempt("list /", lambda: os.listdir("/"))
attempt("other runs' /proc", lambda: [p for p in os.listdir("/proc") if p.isdigit()])
attempt("own folder", lambda: os.write(os.open("mine.txt", os.O_WRONLY | os.O_CREAT, 0o600), b"ok"))
attempt("own folder listed", lambda: os.listdir("."))
attempt("stdlib", lambda: len(os.read(os.open(os.path.join(os.path.dirname(os.__file__ if False else g["__file__"]), "json", "__init__.py"), os.O_RDONLY), 10)))
""")
    finally:
        os.environ.pop("FAKE_ANTHROPIC_API_KEY", None)
    for name in ("parent environ", "hub cwd", "secret", "write outside", "builtins open", "write /tmp", "dir_fd", "list /", "other runs' /proc"):
        assert got[name] == "refused PermissionError", (name, got[name])
    assert got["own folder"] == "refused PermissionError", "open only to read, even in its own folder"
    assert got["own folder listed"] == "WORKED []" and got["stdlib"] == "WORKED 10", got
    assert not outside.exists() and not in_tmp.exists()
    assert "REVIEW-FAKE" not in json.dumps(got) and "signing-secret" not in json.dumps(got)


def test_finding_1_the_reviewers_leak_through_a_change_order_records_nothing(hub, tmp_path):
    leak = open(ATTACKS / "a2_disable_hook.py").read() if (ATTACKS / "a2_disable_hook.py").exists() else (
        "import typing\nsys = typing.sys\ntry:\n    1 / 0\nexcept ZeroDivisionError:\n    tb = sys.exc_info()[2]\n"
        "getattr(getattr(tb, 'tb_frame'), 'f_back')\nresult = {}\n")
    co = new_co(hub)
    with pytest.raises(configjson.Refused, match="import typing is not allowed"):
        scripts.propose(hub, leak, by="alice", co_ref=co["number"])
    assert not hub.kernel.get(co["locator"])["fields"].get("config_scripts"), "refused before it ran: nothing to record"


@pytest.mark.parametrize("layer,words", [("landlock", "no Landlock ruleset"), ("seccomp", "no seccomp filter of its own"),
                                         ("dumpable", "dumpable left out")])
def test_finding_7_a_run_that_cannot_protect_itself_is_refused(layer, words):
    rec = scriptworker.run_local("result = {'ran': True}", SNAP, _skip=(layer,))
    assert rec["exit"] == "unprotected" and words in rec["detail"] and rec["result"] is None, rec["detail"]
    assert "the script was not run" in rec["detail"]


@pytest.mark.parametrize("layer,words", [("seccomp", "no seccomp filter of its own"), ("dumpable", "it is still dumpable")])
def test_finding_3_the_parent_checks_the_protection_itself_not_what_the_run_says(layer, words):
    rec = scriptworker.run_local("result = {'ran': True}", SNAP, _skip=(layer,), _claim=True)  # the run says "installed"
    assert rec["exit"] == "unprotected" and words in rec["detail"], rec["detail"]


def test_finding_3_a_record_the_script_writes_is_not_the_record(hub):
    # The reviewer's a6: a fake answer on standard output, then exit. And a fake frame on the control descriptor.
    rec = scriptworker.run_local(ESCAPE + """
print("evidence the reviewer should see")
fake = {"exit": "ok", "detail": "the script finished", "result": '{"forged": true}', "protection": {"seccomp_mode": 2}}
import json
os.write(1, (json.dumps(fake) + "\\n").encode())
os._exit(0)
""", SNAP, check=False, tripwire=False)
    assert rec["exit"] == "crashed" and rec["result"] is None and "forged" in rec["raw_output"], rec
    assert "evidence the reviewer should see" not in rec["stdout"], "nothing it claims is taken as its record"
    assert rec["protection"]["missing"] == [] and rec["protection"]["seccomp_filters"] >= 1, "what the parent saw"
    rec = scriptworker.run_local(ESCAPE + """
import json
struct = sys.modules["struct"]
frame = json.dumps({"exit": "timeout", "detail": "nothing to see"}).encode()
os.write(int(sys.argv[1]), struct.pack(">Q", len(frame)) + frame)
os._exit(0)
""", SNAP, check=False, tripwire=False)
    assert rec["exit"] == "crashed" and "claimed 'timeout' but ended normally: not believed" in rec["detail"], rec["detail"]


def test_finding_3_a_worker_answer_without_confirmed_protection_counts_for_nothing(monkeypatch):
    monkeypatch.setenv("HUBPROTO_SCRIPT_WORKER_URL", "unix:///nowhere.sock")
    fake = {"exit": "ok", "detail": "the script finished", "result": "{}", "protection": {"seccomp_mode": 2}}
    monkeypatch.setattr(scripts, "_invoke", lambda *a, **k: {"payload": fake})
    rec = scripts.run("result = {}", SNAP)
    assert rec["exit"] == "unprotected" and rec["result"] is None
    assert "Not protected" in scripts.protection_words(rec["protection"])


def test_finding_2_runs_are_one_at_a_time_and_a_full_queue_is_refused(monkeypatch):
    spans = []
    real = scriptworker._run_unqueued

    def timed(*a, **k):
        start = time.monotonic()
        try:
            return real(*a, **k)
        finally:
            spans.append((start, time.monotonic()))

    monkeypatch.setattr(scriptworker, "_run_unqueued", timed)
    busy = "x = 0\nfor i in range(4 * 10 ** 6):\n    x += 1\nresult = {'x': x}"
    threads = [threading.Thread(target=scriptworker.run_local, args=(busy, SNAP)) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    spans.sort()
    assert len(spans) == 3 and all(a[1] <= b[0] for a, b in zip(spans, spans[1:])), f"runs overlapped: {spans}"
    monkeypatch.setattr(scriptworker, "MAX_QUEUED", 0)
    t = threading.Thread(target=scriptworker.run_local, args=(busy, SNAP))
    t.start()
    deadline = time.monotonic() + 10
    while not spans[3:] and scriptworker._waiting == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    with pytest.raises(scriptworker.Busy, match="try again in a minute"):
        scriptworker.run_local("result = {}", SNAP)
    t.join()


def test_finding_2_one_run_cannot_reach_another_even_at_the_same_time():
    # The reviewer's x_attacker and x_victim, run side by side with the queue bypassed: the attacker looks for any
    # other process's stdout to write a forged answer into. Not dumpable and Landlock: it finds none.
    attacker = ESCAPE + """
time = sys.modules["time"]
me, found, tried = os.getpid(), None, 0
end = time.monotonic() + 3
while found is None and time.monotonic() < end:
    for p in range(me - 400, me + 400):
        if p in (me, os.getppid()) or p <= 1:
            continue
        try:
            found = (p, os.open("/proc/%d/fd/1" % p, os.O_WRONLY | os.O_NONBLOCK))
            break
        except OSError:
            tried += 1
out["found"] = found
out["tried"] = tried
"""
    res = {}
    ta = threading.Thread(target=lambda: res.__setitem__("a", scriptworker._run_unqueued(attacker + "\nresult = out", SNAP, {"seconds": 10},
                                                                                           check=False, tripwire=False)))
    ta.start()
    time.sleep(0.3)
    res["b"] = scriptworker._run_unqueued("x = 0\nfor i in range(3 * 10 ** 6):\n    x += 1\nprint('honest')\n"
                                         "result = {'links': {'honest': 'output'}}", SNAP)
    ta.join()
    a, b = json.loads(res["a"]["result"]), res["b"]
    assert a["found"] is None and a["tried"] > 0, a
    assert b["exit"] == "ok" and json.loads(b["result"]) == {"links": {"honest": "output"}} and b["stdout"] == "honest\n", b


def test_finding_2_each_run_has_a_fresh_private_folder_removed_after_it():
    first = escaped('attempt("cwd", lambda: os.getcwd())\nattempt("mode", lambda: oct(os.stat(".").st_mode & 0o777))\n'
                    'attempt("leave", lambda: os.write(os.open("left.txt", os.O_WRONLY | os.O_CREAT, 0o600), b"x"))')
    cwd = first["cwd"].split(" ", 1)[1]
    assert first["mode"] == "WORKED 0o700" and not Path(cwd).exists(), first
    second = escaped('attempt("cwd", lambda: os.getcwd())\nattempt("here", lambda: os.listdir("."))\n'
                     f'attempt("earlier", lambda: os.listdir({cwd!r}))')
    assert second["cwd"] != first["cwd"] and second["here"] == "WORKED []" and second["earlier"].startswith("refused"), second


def test_finding_2_the_worker_socket_cannot_be_removed_by_a_run_and_a_lost_one_is_noticed(tmp_path):
    sock = tmp_path / "w.sock"
    lost = []
    server = scriptworker.serve_in_thread(socket_path=str(sock), on_lost=lost.append)
    try:
        got = escaped(f'attempt("unlink", lambda: os.unlink({str(sock)!r}))\nattempt("stat", lambda: os.listdir({str(tmp_path)!r}))')
        assert got["unlink"] == "refused PermissionError" and got["stat"] == "refused PermissionError" and sock.exists(), got
        sock.unlink()  # anything else that removes it: the worker notices (serve() then exits for the restart policy)
        deadline = time.monotonic() + 5
        while not lost and time.monotonic() < deadline:
            time.sleep(0.05)
        assert lost and "is gone" in lost[0]
    finally:
        server.shutdown()
        server.server_close()


def test_finding_8_connections_past_the_bound_are_refused_without_a_thread(tmp_path, monkeypatch):
    import socket as socket_

    monkeypatch.setattr(scriptworker, "MAX_CONNECTIONS", 1)
    sock = tmp_path / "w.sock"
    server = scriptworker.serve_in_thread(socket_path=str(sock))
    held = socket_.socket(socket_.AF_UNIX)
    try:
        held.connect(str(sock))  # a connection that sends nothing holds the one thread
        time.sleep(0.2)
        before = threading.active_count()
        with pytest.raises(ValueError, match="as many requests as it serves at once"):
            scripts._invoke(f"unix://{sock}", {"tool": "run-script", "script": "result = {}", "snapshot": SNAP}, 10)
        assert threading.active_count() <= before + 1
    finally:
        held.close()
        server.shutdown()
        server.server_close()


def test_finding_8_two_large_runs_one_after_the_other_both_finish():
    big = "b = bytearray(200 * 1024 * 1024)\nfor i in range(0, len(b), 4096):\n    b[i] = 1\nresult = {'held': len(b)}"
    res = []
    threads = [threading.Thread(target=lambda: res.append(scriptworker.run_local(big, SNAP))) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert [r["exit"] for r in res] == ["ok", "ok"], [r["detail"] for r in res]


def test_finding_5_the_local_subprocess_is_opt_in(hub, monkeypatch):
    monkeypatch.delenv("HUBPROTO_SCRIPT_FALLBACK", raising=False)
    monkeypatch.delenv("HUBPROTO_DEPLOYED", raising=False)
    monkeypatch.delenv("HUBPROTO_SCRIPT_WORKER_URL", raising=False)
    monkeypatch.setattr(scriptworker, "run_local", lambda *a, **k: pytest.fail("ran beside a hub that did not opt in"))
    where, why = scripts.where_it_runs()
    assert where == "refused" and "is not a development hub told it may run scripts beside itself (HUBPROTO_SCRIPT_FALLBACK=1)" in why
    with pytest.raises(configjson.Refused, match="no script worker"):
        scripts.propose(hub, THRESHOLD.format(value=8000.0), by="alice")
    monkeypatch.setenv("HUBPROTO_SCRIPT_FALLBACK", "1")
    monkeypatch.setenv("HUBPROTO_DEPLOYED", "1")
    assert scripts.where_it_runs()[0] == "refused", "a deployed hub never, even when told"
    from hubproto import cli_dev

    monkeypatch.delenv("HUBPROTO_SCRIPT_FALLBACK", raising=False)
    assert cli_dev.child_env(ROOT, "stand-in")["HUBPROTO_SCRIPT_FALLBACK"] == "1", "hubproto dev serve opts in"


def test_finding_3_the_page_shows_what_the_parent_checked(hub):
    co = new_co(hub)
    scripts.propose(hub, THRESHOLD.format(value=8000.0), by="alice", co_ref=co["number"])
    run = hub.kernel.get(co["locator"])["fields"]["config_scripts"][0]
    words = scripts.protection_words(run["protection"])
    assert words.startswith("Checked by the hub before the script was sent: seccomp filter on") and "Landlock ABI" in words, words


# ------------------------------------------------------------------ the follow-up review of PR #55 (N1 to N7)
N1 = """import typing
u = "_" + "_"
expr = "[c for c in ()." + u + "class" + u + "." + u + "base" + u + "." + u + "subclasses" + u + "() if c." + u + "name" + u + " == '_wrap_close'][0]." + u + "init" + u + "." + u + "globals" + u
T = typing.TypeVar("T", bound=expr)
osg = T.evaluate_bound(1).evaluate()
os = osg["sys"].modules["os"]
result = {"reached": sorted(k for k in osg if k in ("system", "execv", "fork", "open"))}
"""
N3_SOCKET = N1.replace('result = {"reached"', '''std = [p for p in osg["sys"].path if p.endswith("lib-dynload")][0]
fd = os.open(std, os.O_RDONLY)
os.makedirs("/".join(["d"] * 20))
os.chdir("/".join(["d"] * 20))
os.chmod("../" * (std.count("/") + 1) + "{target}", 0, dir_fd=fd)
result = {"reached"''')


def test_n1_the_reviewers_typevar_chain_is_refused_over_invoke_with_every_layer_on(tmp_path):
    sock = tmp_path / "w.sock"
    server = scriptworker.serve_in_thread(socket_path=str(sock))
    try:
        url = f"unix://{sock}"
        for src in (N1, N3_SOCKET.replace("{target}", str(sock).lstrip("/"))):
            env = scripts._invoke(url, {"tool": "run-script", "script": src, "snapshot": SNAP}, 60)
            p = env["payload"]
            assert p["exit"] == "refused" and "import typing is not allowed" in p["detail"] and "the text '_'" in p["detail"], p["detail"]
        assert oct(sock.stat().st_mode & 0o777) == "0o666", "the socket is untouched"
        env = scripts._invoke(url, {"tool": "run-script", "script": "result = {'next': 1}", "snapshot": SNAP}, 60)
        assert env["payload"]["exit"] == "ok", "and the next run is served"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("how", [
    'g["sys"].modules["typing"].TypeVar("T", bound="1 + 1").evaluate_bound(1).evaluate()',  # the reviewer's N1, past the list
    'g["__builtins__"]["eval"]("1 + 1")',  # a generic eval reached some other way
    'g["sys"].modules["collections"].namedtuple("P", "a b")',  # an allowed module's helper that evaluates text
    'g["sys"].modules["typing"].ForwardRef("1 + 1").evaluate()',
])
def test_n1_any_compile_or_exec_once_the_script_runs_ends_it(how):
    rec = scriptworker.run_local(ESCAPE + f"x = {how}\nresult = {{}}\n", SNAP, check=False)  # the check off, the tripwire on
    assert rec["exit"] == "tripwire" and rec["tripped"]["event"] in ("compile", "exec"), (rec["exit"], rec["detail"])
    assert "evaluated text as code" in rec["detail"] and rec["result"] is None


def test_n1_an_error_in_a_script_is_still_an_error_not_a_tripwire():
    rec = scriptworker.run_local("def f():\n    return 1 / 0\nf()", SNAP)
    assert rec["exit"] == "error" and "ZeroDivisionError" in rec["detail"] and "line 2: return 1 / 0" in rec["stderr"], rec


def test_n2_mode_owner_time_xattr_truncate_and_inotify_are_refused_by_the_kernel(tmp_path):
    secret = tmp_path / ".secret"
    secret.write_text("the hub's signing secret")
    secret.chmod(0o600)
    got = escaped(f"""
std = [p for p in sys.path if p.endswith("lib-dynload")][0]
fd = os.open(std, os.O_RDONLY)
up = "../" * std.count("/")
attempt("chmod secret 0644 via dir_fd", lambda: os.chmod(up + {str(secret).lstrip('/')!r}, 0o644, dir_fd=fd))
attempt("chmod secret", lambda: os.chmod({str(secret)!r}, 0o644))
attempt("chown", lambda: os.chown({str(secret)!r}, os.getuid(), os.getgid()))
attempt("utime", lambda: os.utime({str(secret)!r}, (0, 0)))
attempt("setxattr", lambda: os.setxattr({str(secret)!r}, "user.planted", b"1"))
attempt("removexattr", lambda: os.removexattr({str(secret)!r}, "user.planted"))
attempt("truncate", lambda: os.truncate({str(secret)!r}, 0))
attempt("own file", lambda: os.write(os.open("mine", os.O_WRONLY | os.O_CREAT, 0o600), b"ok"))
attempt("fchmod stdlib fd", lambda: os.fchmod(os.open(std, os.O_RDONLY), 0o755))
attempt("ftruncate", lambda: os.ftruncate(1, 0))
def notify(name):
    ctypes = sys.modules["importlib"].import_module("ctypes")
    libc = ctypes.CDLL(None, use_errno=True)
    r = libc[name](0, 0) if name == "fanotify_init" else libc[name](0)
    return (r, ctypes.get_errno())
attempt("inotify_init1", lambda: notify("inotify_init1"))
attempt("fanotify_init", lambda: notify("fanotify_init"))
""")
    for name in ("chmod secret 0644 via dir_fd", "chmod secret", "chown", "utime", "setxattr", "removexattr", "truncate",
                 "fchmod stdlib fd", "own file", "ftruncate"):
        assert got[name] == "refused PermissionError", (name, got[name])
    assert got["inotify_init1"] == "WORKED (-1, 1)" and got["fanotify_init"] == "WORKED (-1, 1)", got
    assert oct(secret.stat().st_mode & 0o777) == "0o600" and secret.read_text() == "the hub's signing secret"


def test_n2_the_tripwire_honours_dir_fd():
    rec = scriptworker.run_local(ESCAPE + """
std = [p for p in sys.path if p.endswith("lib-dynload")][0]
fd = os.open(std, os.O_RDONLY)
os.chmod("x", 0o644, dir_fd=fd)
""", SNAP, check=False)
    assert rec["exit"] == "tripwire" and rec["tripped"]["event"] == "os.chmod" and "relative to a descriptor" in rec["detail"], rec["detail"]


@pytest.mark.parametrize("what", ["socket", "directory"])
def test_n2_a_run_cannot_make_the_socket_unusable_and_the_worker_notices_if_anything_does(tmp_path, what):
    home = tmp_path / "run"
    home.mkdir()
    sock = home / "w.sock"
    lost = []
    server = scriptworker.serve_in_thread(socket_path=str(sock), on_lost=lost.append)
    try:
        target = sock if what == "socket" else home
        got = escaped(f'attempt("chmod 000", lambda: os.chmod({str(target)!r}, 0))')
        assert got["chmod 000"] == "refused PermissionError" and scriptworker.socket_problem(str(sock)) == "" and not lost, got
        target.chmod(0)  # anything else that does it: the watchdog sees the hub could not connect
        deadline = time.monotonic() + 5
        while not lost and time.monotonic() < deadline:
            time.sleep(0.05)
        assert lost and ("mode 0o0" in lost[0] or "directory is mode 0o0" in lost[0]), lost
        assert scriptworker.socket_problem(str(sock)) or what == "directory"
    finally:
        home.chmod(0o755)
        server.shutdown()
        server.server_close()
    if what == "directory":  # a restart repairs a directory the worker owns, then serves
        home.chmod(0)
        again = scriptworker.unix_server(str(sock))
        try:
            assert oct(home.stat().st_mode & 0o777) == "0o755" and scriptworker.socket_problem(str(sock)) == ""
        finally:
            again.server_close()


def test_n2_the_healthcheck_asks_whether_the_hub_could_use_the_socket():
    image = (ROOT / "docker" / "script-worker" / "Dockerfile").read_text()
    assert "why = socket_problem(p) or answers(p, 3.0)" in image, "the healthcheck asks the worker itself, over its socket"


@pytest.mark.parametrize("cap,ok", [(1, False), (2, False), (3, True)])
def test_n3_a_landlock_abi_that_does_not_control_truncate_is_refused(cap, ok):
    rec = scriptworker.run_local("result = {'ran': True}", SNAP, _abi_cap=cap)
    if ok:
        assert rec["exit"] == "ok" and rec["protection"]["landlock_abi"] == 3, rec["detail"]
    else:
        assert rec["exit"] == "unprotected" and f"Landlock ABI {cap} does not control truncate" in rec["detail"], rec["detail"]


def test_n5_a_script_never_runs_as_root(monkeypatch):
    monkeypatch.setattr(scriptworker.os, "geteuid", lambda: 0)
    rec = scriptworker.run_local("result = {}", SNAP)
    assert rec["exit"] == "unprotected" and "a script never runs as root" in rec["detail"]
    assert "it would run as root" in scriptworker.verify(os.getpid(), {"ready": True, "protection": {}})[1][0]


def test_n6_a_forged_tripwire_on_a_finished_run_is_not_shown():
    rec = scriptworker.run_local(ESCAPE + """
import json
struct = sys.modules["struct"]
frame = json.dumps({"exit": "ok", "detail": "finished", "result": "{}", "tripped": {"event": "x", "args": "y", "why": "z"}}).encode()
os.write(int(sys.argv[1]), struct.pack(">Q", len(frame)) + frame)
os._exit(0)
""", SNAP, check=False, tripwire=False)
    assert rec["exit"] == "ok" and rec["tripped"] is None, rec
    clean = scripts._clean_record({**rec, "tripped": {"event": "x", "args": "y", "why": "z"}})
    assert clean["tripped"] is None, "shown only for a run the tripwire stopped"


def test_n7_a_runs_folder_is_removed_even_if_it_was_made_unremovable(tmp_path):
    top = tmp_path / "script-run-x"
    (top / "keep" / "deep").mkdir(parents=True)
    (top / "keep" / "deep" / "f").write_bytes(b"x" * 1000)
    (top / "keep" / "deep").chmod(0)
    (top / "keep").chmod(0)
    top.chmod(0o500)
    scriptworker.remove_folder(str(top))
    assert not top.exists()
    got = escaped('attempt("chmod own folder", lambda: os.chmod(".", 0o500))')
    assert got["chmod own folder"] == "refused PermissionError", "and a run cannot do it to its own folder now"


def test_n7_a_busy_worker_is_read_as_busy_even_while_the_hub_is_still_sending(tmp_path, monkeypatch):
    import socket as socket_

    monkeypatch.setattr(scriptworker, "MAX_CONNECTIONS", 1)
    sock = tmp_path / "w.sock"
    server = scriptworker.serve_in_thread(socket_path=str(sock))
    held = socket_.socket(socket_.AF_UNIX)
    try:
        held.connect(str(sock))
        time.sleep(0.2)
        big = "x = 1\n" + "# " + "y" * (4 * 1024 * 1024) + "\nresult = {}"  # more than the socket buffers hold
        for _ in range(10):
            with pytest.raises(ValueError, match="as many requests as it serves at once"):
                scripts._invoke(f"unix://{sock}", {"tool": "run-script", "script": big, "snapshot": SNAP}, 10)
    finally:
        held.close()
        server.shutdown()
        server.server_close()


def test_a_run_waits_for_the_one_before_it_only_so_long(monkeypatch):
    monkeypatch.setattr(scriptworker, "MAX_WAIT", 0.3)
    slow = "x = 0\nfor i in range(3 * 10 ** 7):\n    x += 1\nresult = {}"
    t = threading.Thread(target=scriptworker.run_local, args=(slow, SNAP))
    t.start()
    deadline = time.monotonic() + 10
    while not scriptworker._RUN.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    with pytest.raises(scriptworker.Busy, match="busy for 0.3 seconds"):
        scriptworker.run_local("result = {}", SNAP)
    t.join()



# ------------------------------------------------------------------ the third review of PR #55 (R1 to R3)
# REPRESENTATIVE uses every allowed module the way a configuration script would, with memory growth, recursion,
# exceptions, codecs and much printing. It is what the run's seccomp allow-list was measured against: traced with
# strace -f in the worker image, the run made only brk, mmap, munmap, read, write and exit_group after the filter.
REPRESENTATIVE = r'''
import json, math, re, datetime, decimal, collections, itertools, functools, statistics, string
import collections.abc
data = json.loads(json.dumps(config))
roles = collections.Counter(r for m in data.get("members", []) for r in m.get("roles", []))
od = collections.OrderedDict(sorted(roles.items())); dd = collections.defaultdict(list); dq = collections.deque(range(10), maxlen=5)
cm = collections.ChainMap({"a": 1}, {"b": 2}); ud = collections.UserDict(a=1)
isinstance(od, collections.abc.Mapping)
pat = re.compile(r"(\w+)-(\d+)"); found = pat.findall("ab-1 cd-22 ef-333"); subbed = re.sub(r"\d", "#", "a1b2c3")
now = datetime.datetime.now(); utc = datetime.datetime.now(datetime.UTC); parsed = datetime.datetime.strptime("2026-11-12 10:00", "%Y-%m-%d %H:%M")
iso = datetime.date.fromisoformat("2026-11-12").isoformat(); delta = (parsed - datetime.datetime(2026, 1, 1)).days
with decimal.localcontext() as ctx:
    ctx.prec = 30
    d = (decimal.Decimal("10000") * decimal.Decimal("1.0375")).quantize(decimal.Decimal("0.01"), rounding=decimal.ROUND_HALF_UP)
grouped = {k: len(list(v)) for k, v in itertools.groupby(sorted("mississippi"))}
combos = len(list(itertools.combinations(range(12), 4))); prod = list(itertools.product("ab", repeat=3))
batches = list(itertools.batched(range(10), 3)); acc = list(itertools.accumulate(range(10)))
@functools.lru_cache(maxsize=None)
def fib(n):
    return n if n < 2 else fib(n - 1) + fib(n - 2)
@functools.total_ordering
class Ver:
    def __init__(self, v):
        self.v = v
    def __eq__(self, o):
        return self.v == o.v
    def __lt__(self, o):
        return self.v < o.v
    def __repr__(self):
        return "Ver(%r)" % self.v
vers = sorted([Ver(3), Ver(1), Ver(2)])
red = functools.reduce(lambda a, b: a * b, range(1, 20)); part = functools.partial(int, base=2)("1011")
xs = [math.sin(i) * 100 for i in range(1000)]
st = (statistics.mean(xs), statistics.median(xs), statistics.stdev(xs), statistics.quantiles(xs, n=4),
      statistics.correlation(xs, list(range(1000))), statistics.linear_regression(list(range(1000)), xs).slope,
      statistics.NormalDist(0, 1).samples(5, seed=1), statistics.NormalDist.from_samples(xs[:50]).mean)
tmpl = string.Template("$who approves under $limit").substitute(who="bob", limit=8000); cap = string.capwords("hello world")
big = list(range(2 * 10 ** 6)); bigd = {i: str(i) for i in range(2 * 10 ** 5)}; del big, bigd
def deep(n):
    return 0 if n == 0 else 1 + deep(n - 1)
depth = deep(800)
def gen():
    yield from range(5)
g = list(gen())
try:
    {}["missing"]
except KeyError as e:
    caught = str(e)
enc = ["é".encode(c) for c in ("utf-8", "latin-1", "utf-16", "cp1252", "ascii" if False else "utf-32")]
dec = b"caf\xe9".decode("latin-1") + b"\xff\xfe".decode("utf-16")
for i in range(20000):
    print("line", i)
result = {"roles": dict(od), "found": found, "subbed": subbed, "now": now.isoformat()[:4], "parsed": str(parsed), "iso": iso,
          "delta": delta, "d": str(d), "grouped": grouped, "combos": combos, "batches": [list(b) for b in batches], "fib": fib(80),
          "vers": repr(vers), "red": red, "part": part, "st": [str(x)[:12] for x in st], "tmpl": tmpl, "cap": cap, "depth": depth,
          "caught": caught, "enc": [len(x) for x in enc], "dec": dec, "x": config}
'''


def test_r1_a_representative_script_runs_under_the_allow_list():
    rec = scriptworker.run_local(REPRESENTATIVE, json.dumps({"schema": "hub-erp/config/1", "members": [{"principal": "a", "roles": ["x"]}]}),
                                 {"seconds": 60})
    assert rec["exit"] == "ok", (rec["detail"], rec["stderr"][-400:], rec["raw_output"][-400:])
    got = json.loads(rec["result"])
    assert got["fib"] == 23416728348467685 and got["d"] == "10375.00" and got["depth"] == 800 and got["dec"] == "café"
    assert rec["stdout_dropped"] > 0, "it printed past the cap"


def _bpf(prog, nr, args=(0,) * 6, arch=0xC000003E):
    """A classic-BPF interpreter for the run's filter: what the kernel would decide."""
    import struct

    data = struct.pack("<iIQ6Q", nr, arch, 0, *[x & 0xFFFFFFFFFFFFFFFF for x in args])
    a, pc = 0, 0
    while True:
        code, jt, jf, k = prog[pc]
        if code == 0x20:
            a, pc = struct.unpack_from("<I", data, k)[0], pc + 1
        elif code in (0x15, 0x35, 0x45):
            hit = a == k if code == 0x15 else (a >= k if code == 0x35 else bool(a & k))
            pc += 1 + (jt if hit else jf)
        elif code == 0x06:
            return {0x7FFF0000: "allow", 0x80000000: "kill"}.get(k, "deny" if k == 0x00050001 else hex(k))
        else:
            raise AssertionError(f"unexpected BPF instruction {code:#x}")


@pytest.mark.parametrize("machine,arch,numbers", [
    ("x86_64", 0xC000003E, {"prlimit64": 302, "setrlimit": 160, "setpriority": 141, "sched_setaffinity": 203, "sched_setscheduler": 144,
                            "sched_setparam": 142, "sched_setattr": 314, "ioprio_set": 251, "syslog": 103, "kill": 62, "ptrace": 101,
                            "execve": 59, "socket": 41, "connect": 42, "chmod": 90, "mkdir": 83, "unlink": 87, "prctl": 157,
                            "seccomp": 317, "process_vm_readv": 310, "move_pages": 279, "memfd_create": 319, "splice": 275}),
    ("aarch64", 0xC00000B7, {"prlimit64": 261, "setrlimit": 164, "setpriority": 140, "sched_setaffinity": 122, "ioprio_set": 30,
                             "syslog": 116, "kill": 129, "execve": 221, "socket": 198, "prctl": 167}),
])
def test_r1_the_filter_is_an_allow_list_that_denies_by_default(machine, arch, numbers):
    from hubproto import script_sandbox as sb

    prog = sb.filter_program(machine)
    for name, nr in numbers.items():
        assert _bpf(prog, nr, arch=arch) == "deny", name
    assert _bpf(prog, 999, arch=arch) == "deny", "a call it does not know: denied"
    col = 0 if machine == "x86_64" else 1
    for name, nums in sb.ALLOWED_CALLS.items():
        if nums[col] is not None:
            assert _bpf(prog, nums[col], arch=arch) == "allow", name
    fcntl, ioctl, openat = sb.FCNTL["fcntl"][col], sb.IOCTL["ioctl"][col], sb.OPEN_READ_ONLY["openat"][0][col]
    for cmd, want in ((1, "allow"), (2, "allow"), (3, "allow"), (1030, "allow"), (8, "deny"), (15, "deny"), (10, "deny"),
                      (1024, "deny"), (1026, "deny"), (0, "deny"), ((1 << 32) | 1, "deny")):
        assert _bpf(prog, fcntl, (3, cmd, 0, 0, 0, 0), arch) == want, ("fcntl", cmd)
    assert _bpf(prog, fcntl, (3, 4, 0o4000, 0, 0, 0), arch) == "allow", "F_SETFL O_NONBLOCK"
    assert _bpf(prog, fcntl, (3, 4, 0o20000, 0, 0, 0), arch) == "deny", "F_SETFL O_ASYNC"
    for req, want in ((0x5401, "allow"), (0x5451, "allow"), (0x5450, "allow"), (0x8901, "deny"), (0x8902, "deny"), (0x5452, "deny"),
                      (0x541B, "deny")):
        assert _bpf(prog, ioctl, (3, req, 0, 0, 0, 0), arch) == want, ("ioctl", hex(req))
    assert _bpf(prog, openat, (-100, 0, 0, 0, 0, 0), arch) == "allow", "openat to read"
    for flag in (0o1, 0o2, 0o100, 0o1000, 0o2000):
        assert _bpf(prog, openat, (-100, 0, flag, 0, 0, 0), arch) == "deny", ("openat", oct(flag))
    assert _bpf(prog, 0, arch=0x40000003) == "kill", "another architecture"
    if machine == "x86_64":
        assert _bpf(prog, 0x40000000 | 41, arch=arch) == "kill", "the x32 ABI"


def _same_user_pids() -> list[int]:
    out = []
    for p in os.listdir("/proc"):
        if p.isdigit():
            try:
                if os.stat(f"/proc/{p}").st_uid == os.getuid():
                    out.append(int(p))
            except OSError:
                pass
    return out[:200]


@pytest.mark.parametrize("cap", [0, 5, 3])
def test_r1_a_run_cannot_act_on_any_other_process_of_its_user(cap):
    # The reviewer's s1/s2/s3/c4/c5, with the check and the tripwire off, at the kernel's own Landlock ABI and capped at
    # 5 and 3 (no signal scoping). Each call asks for what is already so (the same limits, priority, CPUs; a signal
    # that is ignored), so that even a filter that let it through would harm nothing here: it must still be EPERM.
    import resource

    me = os.getpid()
    nofile, cpu = resource.getrlimit(resource.RLIMIT_NOFILE), resource.getrlimit(resource.RLIMIT_CPU)
    prio, cpus = os.getpriority(os.PRIO_PROCESS, me), sorted(os.sched_getaffinity(me))
    got = escaped(f"""
resource = sys.modules["resource"]
fcntl = sys.modules.get("fcntl") or sys.modules["importlib"].import_module("fcntl")
pp = os.getppid()
attempt("prlimit parent NOFILE", lambda: resource.prlimit(pp, resource.RLIMIT_NOFILE, {nofile!r}))
attempt("prlimit parent CPU", lambda: resource.prlimit(pp, resource.RLIMIT_CPU, {cpu!r}))
attempt("prlimit pid 1", lambda: resource.prlimit(1, resource.RLIMIT_NOFILE))
others = {_same_user_pids()!r}
res = []
for p in others:
    try:
        resource.prlimit(p, resource.RLIMIT_NOFILE)
        res.append(p)
    except OSError:
        pass
out["prlimit any same-user pid"] = "WORKED " + str(res) if res else "refused PermissionError"
attempt("setpriority parent", lambda: os.setpriority(os.PRIO_PROCESS, pp, {prio}))
attempt("sched_setaffinity parent", lambda: os.sched_setaffinity(pp, {set(cpus)!r}))
attempt("setrlimit own", lambda: resource.setrlimit(resource.RLIMIT_CORE, (0, 0)))
r, w = os.pipe() if False else (0, 1)
attempt("F_SETOWN parent", lambda: fcntl.fcntl(0, fcntl.F_SETOWN, pp))
attempt("F_SETSIG", lambda: fcntl.fcntl(0, 10, 28))
attempt("O_ASYNC", lambda: fcntl.fcntl(0, fcntl.F_SETFL, fcntl.fcntl(0, fcntl.F_GETFL) | os.O_ASYNC))
attempt("F_SETLEASE stdlib", lambda: fcntl.fcntl(os.open(os.path.join([p for p in sys.path if p.endswith("lib-dynload")][0], ".."), os.O_RDONLY), 1024, 0))
ctypes = sys.modules["importlib"].import_module("ctypes")
libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
def sc(*a):
    return (libc.syscall(*[ctypes.c_long(x) for x in a]), ctypes.get_errno())
attempt("ioprio_get parent", lambda: sc(252, 1, pp))
attempt("syslog read", lambda: (libc.syscall(ctypes.c_long(103), ctypes.c_long(3), ctypes.create_string_buffer(256), ctypes.c_long(256)), ctypes.get_errno()))
attempt("ioctl FIOSETOWN", lambda: (libc.ioctl(0, 0x8901, ctypes.byref(ctypes.c_int(pp))), ctypes.get_errno()))
attempt("ioctl FIOASYNC", lambda: (libc.ioctl(0, 0x5452, ctypes.byref(ctypes.c_int(1))), ctypes.get_errno()))
attempt("fcntl F_GETFD (allowed)", lambda: fcntl.fcntl(0, fcntl.F_GETFD))
""", _abi_cap=cap)
    for name in ("prlimit parent NOFILE", "prlimit parent CPU", "prlimit pid 1", "prlimit any same-user pid", "setpriority parent",
                 "sched_setaffinity parent", "F_SETOWN parent", "F_SETSIG", "O_ASYNC", "F_SETLEASE stdlib"):
        assert got[name] == "refused PermissionError", (name, got[name])
    assert got["setrlimit own"] == "refused ValueError", "EPERM, which the resource module reports as ValueError"
    for name in ("ioprio_get parent", "syslog read", "ioctl FIOSETOWN", "ioctl FIOASYNC"):
        assert got[name] == "WORKED (-1, 1)", (name, got[name])
    assert got["fcntl F_GETFD (allowed)"].startswith("WORKED"), got
    assert resource.getrlimit(resource.RLIMIT_NOFILE) == nofile and os.getpriority(os.PRIO_PROCESS, me) == prio


@pytest.mark.parametrize("name,body,event", [
    ("e1 eval", 'x = g["__builtins__"]["eval"]("1+1")', "compile"),
    ("e2 function new", 'def f():\n    return 7\nh = type(f)(f.__code__, {})', "function.__new__"),
    ("e3 code replace", 'def f():\n    return 7\nc = f.__code__.replace(co_consts=(None, 9))', "code.__new__"),
    ("e4 exec again", 'try:\n    1 / 0\nexcept ZeroDivisionError as e:\n    tb = e.__traceback__\n'
                      'g["__builtins__"]["exec"](tb.tb_frame.f_code, {"__builtins__": {}})', "exec"),
    ("e6 marshal", 'm = sys.modules["marshal"]\nc = m.loads(m.dumps((lambda: 5).__code__))', "marshal.loads"),
])
def test_r1_the_reviewers_catch_all_scripts_still_trip(name, body, event):
    rec = scriptworker.run_local(ESCAPE + body + "\nresult = {}\n", SNAP, check=False)
    assert rec["exit"] == "tripwire" and rec["tripped"]["event"] == event, (name, rec["exit"], rec["detail"][:200])


def test_r1_the_worker_is_restarted_when_it_cannot_serve(tmp_path):
    sock = tmp_path / "w.sock"
    lost = []
    server = scriptworker.serve_in_thread(socket_path=str(sock), on_lost=lost.append, answer_every=0.3, answer_within=0.5)
    try:
        assert scriptworker.answers(str(sock), 2.0) == ""
        time.sleep(1.2)
        assert not lost, "a worker that answers is left alone"
        server.shutdown()  # it stops serving: the socket is still there, nothing answers on it
        deadline = time.monotonic() + 8
        while not lost and time.monotonic() < deadline:
            time.sleep(0.05)
        assert lost and "did not answer" in lost[0], lost
    finally:
        server.server_close()


def test_r1_the_worker_notices_its_own_limits_changing(tmp_path):
    import resource

    sock = tmp_path / "w.sock"
    lost = []
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    server = scriptworker.serve_in_thread(socket_path=str(sock), on_lost=lost.append)
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft - 1, hard))  # what a prlimit from outside would do
        deadline = time.monotonic() + 5
        while not lost and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
        server.shutdown()
        server.server_close()
    assert lost and "the worker's own limits changed (RLIMIT_NOFILE" in lost[0], lost


def test_r2_the_reviewers_text_building_reads_no_attributes():
    t1 = """import json
u = "a_b"[1]
p = "%c" % 95
d = u * 2
def gen():
    yield ("{0.gi_frame.f_back.f_back.f_locals[setup][limits]}").format(g)
g = gen()
result = {"class_text": ("{0." + d + "class" + d + "}").format(())}
"""
    out = scriptcheck.check(t1)
    assert any(".format is not allowed: it reads attributes by names written in the text" in r for r in out), out
    assert any(".gi_frame" in r or "the text" in r for r in out), out
