"""Configuration scripts on a change order (issue #34, part of #27).

A person may write a short Python script that reads the configuration in force (the export of #33,
``hubproto.configjson``) and proposes a change to it: the whole document, or a part of it, in the same format. The
script is a **generator of a proposed change**; what goes on the change order is its output, as data:

1. ``scriptcheck.check``: the allow-list check, in the hub, before anything is sent (no model, nothing run);
2. the run: in the script worker (``HUBPROTO_SCRIPT_WORKER_URL``, the ``script-worker`` container of
   ``docker-compose.yml``: no network, read-only, every capability dropped, seccomp), or, on a hub that is not deployed
   and has no worker, in a local subprocess with the same limits and hooks, labelled **weaker isolation**. A deployed
   hub (``HUBPROTO_DEPLOYED=1``, set by the hub's image) refuses the local subprocess;
3. the output: parsed (``configjson.loads``: the limits and checks of any uploaded document), held to the format's JSON
   Schema (``schemacheck``) and carried by ``configjson.propose``: the same checks, guards, widenings, reviews and
   signatures as the same document uploaded, or the same edit made on screen.

The change order keeps each run in its ``config_scripts`` field (``record`` below): the script and its sha256, the
digest of the snapshot it read and the release it was taken from, the output it gave and its sha256, the run record
(exit reason, stdout and stderr excerpts, limits, what the tripwire saw, where it ran) and what it carried. The field
is part of the change order's content digest (``changecontrol.content_digest``), so a run recorded after a review round
or after sending is caught like any other change. A run that fails is recorded too, carrying nothing.

The release applies what was carried and signed; it never reads ``config_scripts`` and never runs a script again.
Only a person submits a script: a cog is refused here, at the CLI and on the page, and no cog tool reaches this module.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
from urllib.parse import urlparse

from . import configjson, schemacheck, scriptcheck, scriptworker
from .release import utcnow

FIELD = "config_scripts"
ISOLATION = {
    "container": "Run in the script worker: its own container, with no network, a read-only file system, every capability "
                 "dropped, a seccomp profile and no hub state or secrets.",
    "local": "Run in a local subprocess beside the hub, not in the script worker: weaker isolation. The same limits, kernel "
             "filter and tripwire held, but no container was around it. A deployed hub refuses this.",
}
STATUS = {"carried": "carried on the change order", "nothing": "its output says what is in force: nothing carried",
          "refused": "its output was refused: nothing carried", "failed": "it did not finish: nothing carried"}
TEXT_CAP = 16000
TIMEOUT_MARGIN = 30.0


def worker_url() -> str:
    return os.environ.get("HUBPROTO_SCRIPT_WORKER_URL", "").strip()


def deployed() -> bool:
    """A deployed hub: set by the hub's image (Dockerfile ``HUBPROTO_DEPLOYED=1``); a dev hub and the tests leave it unset."""
    return os.environ.get("HUBPROTO_DEPLOYED", "").strip().lower() in ("1", "true", "yes", "on")


def where_it_runs() -> tuple[str, str]:
    """("container" | "local" | "refused", the sentence)."""
    if worker_url():
        return "container", ISOLATION["container"]
    if deployed():
        return "refused", ("This hub is deployed and has no script worker (HUBPROTO_SCRIPT_WORKER_URL is not set): here a "
                           "script runs only in the script-worker container, never in a subprocess beside the hub. Start the "
                           "script-worker service (docker compose up -d script-worker) and set the hub's "
                           "HUBPROTO_SCRIPT_WORKER_URL, as docker-compose.yml does.")
    return "local", ISOLATION["local"]


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__("script-worker", timeout=timeout)
        self._path = path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._path)
        self.sock = s


def _invoke(url: str, request: dict, timeout: float) -> dict:
    """POST one request to the worker's /invoke; the envelope it answers (data), or ValueError with a sentence."""
    u = urlparse(url)
    if u.scheme == "unix":
        conn = _UnixConnection(u.netloc + u.path, timeout)  # unix:///abs/path (or unix://relative/path)
    elif u.scheme == "http" and u.hostname:
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
    else:
        raise ValueError(f"HUBPROTO_SCRIPT_WORKER_URL {url!r} is not unix:///path/to/socket or http://host:port")
    body = json.dumps(request).encode("utf-8")
    try:
        conn.request("POST", "/invoke", body, {"Content-Type": "application/json", "Content-Length": str(len(body))})
        resp = conn.getresponse()
        data = resp.read(configjson.MAX_BYTES * 2 + 1_000_000)
    except (OSError, http.client.HTTPException) as e:
        raise ValueError(f"the script worker at {url} cannot be reached ({type(e).__name__}: {e}); the script was not run, "
                         "and a hub with a worker never runs one beside itself instead") from None
    finally:
        conn.close()
    try:
        env = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ValueError(f"the script worker answered with something that is not JSON (HTTP {resp.status})") from None
    if not isinstance(env, dict):
        raise ValueError("the script worker's answer is not an envelope")
    if env.get("error"):
        err = env["error"] if isinstance(env["error"], dict) else {}
        raise ValueError(f"the script worker could not run it: {str(err.get('detail') or err)[:300]}")
    if not isinstance(env.get("payload"), dict):
        raise ValueError("the script worker's answer has no run record")
    return env


def _text(v, cap: int = TEXT_CAP) -> str:
    return v[:cap] if isinstance(v, str) else ""


def _count(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def _clean_record(rec: dict) -> dict:
    """The worker's run record as data the hub keeps: known keys, plain types, bounded text (the worker is not trusted
    to say more than it should)."""
    reason = rec.get("exit") if rec.get("exit") in scriptworker.REASONS else "crashed"
    result = rec.get("result") if reason == "ok" and isinstance(rec.get("result"), str) else None
    tripped = rec.get("tripped") if isinstance(rec.get("tripped"), dict) else None
    lim = rec.get("limits") if isinstance(rec.get("limits"), dict) else {}
    return {
        "exit": reason, "detail": _text(rec.get("detail"), 1000) or scriptworker.REASONS[reason], "result": result,
        "stdout": _text(rec.get("stdout")), "stderr": _text(rec.get("stderr")),
        "stdout_dropped": _count(rec.get("stdout_dropped")), "stderr_dropped": _count(rec.get("stderr_dropped")),
        "tripped": {k: _text(tripped.get(k), 400) for k in ("event", "args", "why")} if tripped else None,
        "kernel_filter": _text(rec.get("kernel_filter"), 200), "worker_version": _text(rec.get("worker_version"), 40),
        "python": _text(rec.get("python"), 40),
        "limits": {k: lim[k] for k in scriptworker.LIMITS if isinstance(lim.get(k), (int, float)) and not isinstance(lim.get(k), bool)},
        "seconds": float(rec["seconds"]) if isinstance(rec.get("seconds"), (int, float)) and not isinstance(rec.get("seconds"), bool) else 0.0,
        "refusals": [_text(x, 400) for x in rec.get("refusals") or [] if isinstance(x, str)][:50],
    }


def run(script: str, snapshot: str, limits: dict | None = None) -> dict:
    """Run a script that passed the allow-list check, where this hub runs scripts (``where_it_runs``). Returns the run
    record, with ``isolation`` ("container" or "local"). Raises ValueError with a sentence when it cannot be run here."""
    where, why = where_it_runs()
    if where == "refused":
        raise ValueError(why)
    if where == "container":
        lim = scriptworker.limits_for(limits)
        env = _invoke(worker_url(), {"tool": "run-script", "script": script, "snapshot": snapshot, "limits": lim},
                      timeout=float(lim["seconds"]) + TIMEOUT_MARGIN)
        rec = _clean_record(env["payload"])
    else:
        rec = _clean_record(scriptworker.run_local(script, snapshot, limits))
    return {**rec, "isolation": where}


def refuse_cog(hub, by: str) -> None:
    """Only a person of the organization submits a script; a cog never writes or runs one."""
    meta = hub.releases.current()
    b = hub.releases.bundle(meta["digest"]) if meta else {}
    kind = ((b.get("members") or {}).get(by) or {}).get("type")
    if kind == "cog":
        raise configjson.Refused([f"{by} is a cog: a person writes and submits a configuration script; no cog may write or run one"])
    if kind != "person":
        raise configjson.Refused([f"{by} is not a person of the organization: a person submits a configuration script"])


def _target(hub, by: str, co_ref):
    """The change order a run is recorded on: ``co_ref`` (it must be one ``by`` may change, being prepared), or the
    person's open settings change order (None when they have none: one is started only when something is carried)."""
    from . import changecontrol, eco

    if co_ref is None:
        return changecontrol.settings_change_order(hub, by, create=False)
    try:
        co = changecontrol.sync(hub, co_ref)
    except (KeyError, ValueError) as e:
        raise configjson.Refused([str(e)]) from None
    why = changecontrol.may_edit(hub, co, by)
    if why:
        raise configjson.Refused([why])
    st = changecontrol.stage_of(co)
    if eco.finished(co) or st not in changecontrol.EDITABLE or co["state"].startswith("awaiting"):
        raise configjson.Refused([f"{co['number']} is {st.replace('-', ' ')}: its changes are fixed now; submit the script on another change order"])
    return co


def runs_of(co: dict) -> list[dict]:
    v = co["fields"].get(FIELD)
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _record(hub, co_ref: str, entry: dict, by: str) -> dict:
    co = hub.kernel.get(co_ref)
    runs = runs_of(co)
    entry = {**entry, "id": f"script-{len(runs) + 1}"}
    hub.kernel.set_fields(co["locator"], {FIELD: runs + [entry]}, by=by)
    return entry


def propose(hub, script: str, *, by: str, co_ref=None, reason: str = "", name: str = "", limits: dict | None = None) -> dict:
    """Check, run and carry a configuration script (the module docstring). Returns configjson.propose's answer plus
    ``script`` (the run as recorded). Raises configjson.Refused (nothing carried) with every reason; a run that ran is
    recorded on the change order it was submitted to even then (on none when it had none to go to)."""
    refuse_cog(hub, by)
    if not isinstance(script, str):
        raise configjson.Refused(["a script is Python source text"])
    refusals = scriptcheck.check(script)
    if refusals:
        raise configjson.Refused(refusals)
    co = _target(hub, by, co_ref)
    snapshot_doc = configjson.export(hub)
    snapshot = configjson.dumps(snapshot_doc)
    try:
        rec = run(script, snapshot, limits)
    except ValueError as e:
        raise configjson.Refused([str(e)]) from None
    entry = {
        "name": (name or "script.py")[:200], "by": by, "at": utcnow(), "reason": reason[:2000], "script": script,
        "script_sha256": scriptworker.sha256(script), "snapshot_sha256": scriptworker.sha256(snapshot),
        "snapshot_release": (snapshot_doc.get("release") or {}).get("digest"), "snapshot_versions": snapshot_doc.get("versions"),
        "isolation": rec["isolation"], "isolation_note": ISOLATION[rec["isolation"]],
        **{k: rec[k] for k in ("exit", "detail", "tripped", "stdout", "stderr", "stdout_dropped", "stderr_dropped", "kernel_filter",
                               "worker_version", "python", "limits", "seconds")},
        "output": rec["result"], "output_sha256": scriptworker.sha256(rec["result"]) if rec["result"] is not None else None,
        "status": "", "summary": [], "refusals": [], "carried": {},
    }

    def refused(status: str, reasons: list[str]):
        e = {**entry, "status": status, "refusals": list(reasons)[:50]}
        if co is not None:
            e = _record(hub, co["locator"], e, by)
        raise configjson.Refused(list(reasons))

    if rec["exit"] != "ok":
        tail = (rec["stderr"] or "").strip().splitlines()[-3:]
        refused("failed", [f"the script did not finish: {rec['detail']}"] + ([f"what it wrote last: {' | '.join(tail)[:600]}"] if tail else []))
    try:
        doc = configjson.loads(rec["result"])
    except configjson.Refused as e:
        refused("refused", [f"the script's output: {r}" for r in e.reasons])
    shape = schemacheck.problems(doc)
    if shape:
        refused("refused", [f"the script's output does not match the configuration format: {p}" for p in shape])
    try:
        out = configjson.propose(hub, doc, by=by, co_ref=co["locator"] if co else None, reason=reason or f"Configuration changes from the script {entry['name']}.",
                                 name=f"the script {entry['name']}")
    except configjson.Refused as e:
        refused("refused", [f"the script's output: {r}" for r in e.reasons])
    entry.update(status="carried" if out["changed"] else "nothing", summary=list(out["summary"]), carried=out.get("carried") or {})
    if out.get("locator"):
        entry = _record(hub, out["locator"], entry, by)
    return {**out, "script": entry}
