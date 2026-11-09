"""``hubproto dev``: development hubs on localhost (hubproto.cli_dev).

The registry is read and written under its lock, so concurrent callers never get the same name or port; reserved ports
(8080, 9980, 11434) are never handed out; ``list`` prunes instances whose process is gone (a reused pid included);
``stop`` and ``clean`` act only on what they should; ``serve`` builds a fresh hub from this worktree, answers ``/healthz``
on its port, and stops. Every test points ``HUBPROTO_DEV_HOME`` at a temporary directory and the served instances at
ports the kernel hands out, so nothing touches a developer's real instances.
"""

import io
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from urllib.request import urlopen

import pytest

from conftest import ROOT
from hubproto import cli, cli_dev


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "dev-home"
    monkeypatch.setenv("HUBPROTO_DEV_HOME", str(h))
    return h


def dev(*argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cli.main(["dev", *argv])
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


def registry(home: Path) -> dict:
    return json.loads((home / "registry.json").read_text())


def kernel_port() -> int:
    """A free port the kernel picks (outside 8100-8999, where a developer's real instances live)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def healthz(url: str) -> str:
    with urlopen(url + "healthz", timeout=5) as r:
        return r.read().decode()


def sleeper(home: Path, name: str, port: int = 8100) -> subprocess.Popen:
    """A stand-in for a served hub: a live process registered as instance ``name`` with a marked state directory."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    state = home / name
    state.mkdir(parents=True)
    (state / cli_dev.MARKER).write_text("{}")
    with cli_dev.locked_registry(home) as reg:
        reg["instances"][name] = {"name": name, "pid": proc.pid, "pid_start": cli_dev.start_time(proc.pid), "port": port,
                                  "state_dir": str(state), "url": f"http://127.0.0.1:{port}/", "status": "running"}
    return proc


# ------------------------------------------------------------------ registry, names and ports
RESERVE = """
import json, sys
from pathlib import Path
from hubproto import cli_dev
owner = int(sys.argv[1])
with cli_dev.locked_registry() as reg:
    cli_dev.prune(reg)
    name = cli_dev.choose_name(reg, None, Path("/somewhere/wt"))
    port = cli_dev.choose_port(reg)
    reg["instances"][name] = {"name": name, "port": port, "pid": owner, "pid_start": cli_dev.start_time(owner)}
print(json.dumps([name, port]))
"""


def test_concurrent_callers_never_get_the_same_name_or_port(home):
    env = {**os.environ, "PYTHONPATH": str(ROOT), "HUBPROTO_DEV_HOME": str(home)}
    procs = [subprocess.Popen([sys.executable, "-c", RESERVE, str(os.getpid())], env=env, stdout=subprocess.PIPE, text=True)
             for _ in range(12)]
    got = [json.loads(p.communicate(timeout=60)[0]) for p in procs]
    names, ports = [g[0] for g in got], [g[1] for g in got]
    assert sorted(names) == sorted(["wt"] + [f"wt-{n}" for n in range(2, 13)])
    assert len(set(ports)) == 12 and not set(ports) & cli_dev.FORBIDDEN_PORTS and min(ports) >= cli_dev.FIRST_PORT
    assert set(registry(home)["instances"]) == set(names), "every reservation was written; none was lost to a race"


def test_reserved_and_registered_ports_are_skipped(home, monkeypatch):
    monkeypatch.setattr(cli_dev, "FIRST_PORT", 8079)
    monkeypatch.setattr(cli_dev, "LAST_PORT", 8081)
    monkeypatch.setattr(cli_dev, "bindable", lambda port, host="127.0.0.1": True)
    reg = {"instances": {"a": {"port": 8079}}, "stopped": {}}
    assert cli_dev.choose_port(reg) == 8081, "8079 is registered and 8080 (the Docker hub) is never handed out"
    reg["instances"]["b"] = {"port": 8081}
    with pytest.raises(SystemExit, match="no free port"):
        cli_dev.choose_port(reg)
    for port in (8080, 9980, 11434):
        with pytest.raises(SystemExit, match="reserved") as e:
            cli_dev.choose_port(reg, str(port))
        assert e.value.code == 2
    with pytest.raises(SystemExit, match="instance a has it"):
        cli_dev.choose_port(reg, "8079")


def test_a_port_something_listens_on_is_skipped(home, monkeypatch):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        monkeypatch.setattr(cli_dev, "FIRST_PORT", port)
        monkeypatch.setattr(cli_dev, "LAST_PORT", port + 20)
        assert cli_dev.choose_port({"instances": {}, "stopped": {}}) != port
        with pytest.raises(SystemExit, match="already listens"):
            cli_dev.choose_port({"instances": {}, "stopped": {}}, str(port))


def test_names_are_checked_and_a_running_name_is_refused(home):
    reg = {"instances": {"ws3": {"url": "http://127.0.0.1:8100/", "pid": 1}}, "stopped": {}}
    assert cli_dev.choose_name(reg, None, Path("/x/WS3")) == "ws3-2"
    with pytest.raises(SystemExit, match="already running") as e:
        cli_dev.choose_name(reg, "ws3", Path("/x/y"))
    assert e.value.code == 2
    with pytest.raises(SystemExit, match="lower-case"):
        cli_dev.choose_name(reg, "My Hub", Path("/x/y"))


def test_list_prunes_dead_and_reused_pids(home):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    with cli_dev.locked_registry(home) as reg:
        reg["instances"]["gone"] = {"name": "gone", "pid": dead.pid, "port": 8100, "state_dir": str(home / "gone")}
        reg["instances"]["reused"] = {"name": "reused", "pid": os.getpid(), "pid_start": "1", "port": 8101,
                                      "state_dir": str(home / "reused")}
    rc, out, _ = dev("list")
    assert rc == 0 and "no dev hubs running" in out and "2 stopped instance(s)" in out
    reg = registry(home)
    assert reg["instances"] == {} and set(reg["stopped"]) == {"gone", "reused"} and reg["stopped"]["gone"]["status"] == "stopped"


def test_stop_and_clean(home):
    proc = sleeper(home, "s1")
    rc, out, _ = dev("list")
    assert "s1" in out and str(proc.pid) in out
    rc, out, _ = dev("list", "--json")
    assert [e["name"] for e in json.loads(out)] == ["s1"]
    rc, _, err = dev("clean", "s1")
    assert rc == 1 and "is running" in err and (home / "s1").exists()
    rc, out, _ = dev("stop", "s1")
    assert rc == 0 and "stopped s1" in out
    assert proc.wait(5) is not None
    assert "s1" in registry(home)["stopped"] and (home / "s1").exists(), "stopping keeps the state"
    rc, out, _ = dev("stop", "s1")
    assert rc == 0 and "not running" in out
    rc, _, err = dev("stop", "nope")
    assert rc == 2 and "no instance named nope" in err
    rc, out, _ = dev("clean", "s1")
    assert rc == 0 and "removed s1" in out and not (home / "s1").exists() and registry(home)["stopped"] == {}

    # --all: stopped instances and leftover marked directories go; a running one and an unmarked directory stay.
    alive = sleeper(home, "s2")
    (home / "leftover").mkdir()
    (home / "leftover" / cli_dev.MARKER).write_text("{}")
    (home / "mine").mkdir()
    (home / "mine" / "notes.txt").write_text("not a hub")
    with cli_dev.locked_registry(home) as reg:
        reg["stopped"]["plain"] = {"name": "plain", "state_dir": str(home / "mine")}
    rc, out, _ = dev("clean", "--all")
    assert rc == 0 and "removed leftover" in out and "kept" in out
    assert not (home / "leftover").exists() and (home / "mine" / "notes.txt").exists() and (home / "s2").exists()
    rc, out, _ = dev("stop", "--all")
    assert rc == 0 and "stopped s2" in out and alive.wait(5) is not None
    assert dev("stop")[0] == 2


# ------------------------------------------------------------------ serve, end to end
def test_serve_detached_builds_a_released_hub_with_demo_data_and_stops(home):
    port = kernel_port()
    rc, out, err = dev("serve", "--worktree", str(ROOT), "--detach", "--name", "t1", "--port", str(port))
    assert rc == 0, out + err
    entry = registry(home)["instances"]["t1"]
    url = f"http://127.0.0.1:{port}/"
    assert entry["url"] == url and entry["port"] == port and entry["status"] == "running" and entry["worktree"] == str(ROOT)
    assert entry["branch"] and entry["commit"] and entry["started"] and Path(entry["state_dir"]) == home / "t1"
    assert (home / "t1" / "hub.pid").read_text().strip() == str(entry["pid"]) and (home / "t1" / "hub.log").exists()
    try:
        assert healthz(url).startswith("ok")
        assert "release v1" in out and "NCR-" in out and "BILL-" in out and "CO-" in out
        ncr = next(w.strip(",") for w in out.split() if w.startswith("NCR-"))
        with urlopen(url + "?as=carol", timeout=10) as r:
            assert ncr in r.read().decode(), "the demo nonconformance waits in carol's inbox"
        rc, out, _ = dev("list")
        assert "t1" in out and url in out
        rc, _, err = dev("serve", "--worktree", str(ROOT), "--detach", "--name", "t1", "--port", str(kernel_port()))
        assert rc == 2 and "already running" in err
        rc, _, err = dev("serve", "--worktree", str(ROOT), "--detach", "--name", "t2", "--port", str(port))
        assert rc == 2 and "instance t1 has it" in err
    finally:
        rc, out, _ = dev("stop", "t1")
    assert rc == 0
    with pytest.raises(OSError):  # refused (URLError) or reset
        healthz(url)
    assert dev("clean", "t1")[0] == 0 and not (home / "t1").exists()


def test_serve_in_the_foreground_runs_until_stopped(home):
    port = kernel_port()
    env = {**os.environ, "PYTHONPATH": str(ROOT), "HUBPROTO_DEV_HOME": str(home)}
    proc = subprocess.Popen([sys.executable, "-c", cli_dev.CLI_MAIN, "dev", "serve", "--worktree", str(ROOT), "--seed", "empty",
                             "--name", "fg", "--port", str(port)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and proc.poll() is None:
            if (registry(home)["instances"].get("fg") or {}).get("status") == "running" if (home / "registry.json").exists() else False:
                try:
                    if healthz(f"http://127.0.0.1:{port}/").startswith("ok"):
                        break
                except OSError:
                    pass
            time.sleep(0.2)
        else:
            pytest.fail("the foreground hub never answered: " + (proc.stdout.read() if proc.poll() is not None else ""))
        rc, out, _ = dev("stop", "fg")
        assert rc == 0
        assert proc.wait(20) == 0, "stopped from outside, the foreground serve exits cleanly"
        assert "fg" not in registry(home)["instances"]
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_a_failed_build_releases_its_reservation(home, tmp_path):
    wt = tmp_path / "broken"
    (wt / "frames").mkdir(parents=True)  # no Frames: nobody to release the first release
    (wt / "hubproto").symlink_to(ROOT / "hubproto")
    rc, _, err = dev("serve", "--worktree", str(wt), "--detach", "--name", "broken", "--port", str(kernel_port()))
    assert rc == 1 and "could not be built" in err and "bootstrap-release" in err
    assert registry(home)["instances"] == {}


def test_serve_refuses_a_directory_that_is_not_a_dev_instance(home, tmp_path):
    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / "precious.txt").write_text("keep me")
    rc, _, err = dev("serve", "--worktree", str(ROOT), "--state-dir", str(mine), "--name", "x", "--port", str(kernel_port()))
    assert rc == 2 and "not a dev instance" in err and (mine / "precious.txt").exists()
    assert registry(home)["instances"] == {}
    rc, _, err = dev("serve", "--worktree", str(tmp_path), "--name", "y")
    assert rc == 2 and "no hub code" in err
