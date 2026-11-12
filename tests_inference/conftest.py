"""The local-inference suite (issue #47): the cogs on the models the Frames name, served by a real Ollama.

The main suite (``tests/``) never calls a model; this one only calls models, so it says what a real model does to the
hub: output that does not parse, format drift, refusals, a model that ignores its tools, slowness. Run it by hand:

    python -m pytest tests_inference -v            # HUBPROTO_OLLAMA_URL, default http://localhost:11434
    python -m pytest tests_inference -v -m "not slow"
    HUBPROTO_INFERENCE_SHOW=1 python -m pytest tests_inference    # print every answer, passing tests too

It is never collected from the repository root (``pyproject.toml``: ``testpaths = ["tests"]``), and GitHub's runners
have no GPU, so CI does not run it. Run it after any change to the prompts, the model gateway, the cog profiles or the
models the Frames name.

- No stand-in, ever: ``HUBPROTO_ALLOW_STAND_IN`` is cleared, so a model that does not answer fails the call instead of
  being answered for. No hosted model, ever: ``HUBPROTO_HOSTED_MODELS`` and ``ANTHROPIC_API_KEY`` are cleared, so the
  suite spends nothing.
- If Ollama does not answer, or a model the test needs is not pulled, the test is skipped with a sentence that says
  what to start or pull. A missing model is never a failure.
- A failing test prints every model call it made, with the model's raw output, so the failure reads as a finding about
  the model. The run ends with each test's model calls and their timings.
"""

import json
import os
import re
import shutil
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URL = "http://localhost:11434"

# Cleared before anything of the hub is imported: no stand-in answers, no hosted model is reachable.
for _name in ("HUBPROTO_ALLOW_STAND_IN", "HUBPROTO_HOSTED_MODELS", "ANTHROPIC_API_KEY", "HUBPROTO_MODEL_FAKE", "HUBPROTO_EXTRACTOR",
              "HUBPROTO_EXTRACTOR_MODEL", "HUBPROTO_MODEL_MAX_CHARS"):
    os.environ.pop(_name, None)
os.environ["HUBPROTO_OLLAMA_URL"] = (os.environ.get("HUBPROTO_OLLAMA_URL") or DEFAULT_URL).rstrip("/")
os.environ["HUBPROTO_COG_QUEUE"] = "inline"  # a cog's work is done when the call returns
os.environ.setdefault("HUBPROTO_MODEL_TIMEOUT", "300")  # one model call: generous (a cold load on a shared GPU), but bounded

from hubproto import model as model_mod  # noqa: E402
from hubproto.app import Hub  # noqa: E402

# Each test's bound on its model calls together (seconds); a test that asks more says so with its own bound.
CALL_BOUND = float(os.environ.get("HUBPROTO_INFERENCE_BOUND") or 240)


def ollama_url() -> str:
    return os.environ["HUBPROTO_OLLAMA_URL"]


def frame_models() -> dict[str, str]:
    """Each cog's model as cog-profiles (frames/company/cogs.frame.md) names it: {cog: model}."""
    text = (ROOT / "frames" / "company" / "cogs.frame.md").read_text()
    out, cog = {}, None
    for line in text.splitlines():
        m = re.match(r"## Cog:\s*(\S+)", line)
        if m:
            cog = m.group(1)
        m = re.match(r"Model:\s*(\S+)", line)
        if m and cog:
            out[cog] = m.group(1)
    return out


JUNIOR = frame_models()["ap-reviewer"]  # the junior cogs' model (Gemma 4 12B after issue #46)


def installed() -> tuple[list[str] | None, str]:
    """The models this Ollama has pulled, or None and why it could not be asked."""
    try:
        with urllib.request.urlopen(ollama_url() + "/api/tags", timeout=5) as r:
            return [m["name"] for m in json.load(r).get("models") or []], ""
    except Exception as e:
        return None, f"Ollama does not answer at {ollama_url()} ({type(e).__name__}: {e})"


def capabilities(name: str) -> list[str]:
    """What this Ollama says the model can do (``/api/show``), straight from Ollama (no cache)."""
    req = urllib.request.Request(ollama_url() + "/api/show", data=json.dumps({"model": name}).encode(), method="POST",
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return [str(c) for c in json.load(r).get("capabilities") or []]


def need(*models: str) -> None:
    """Skip the test, with a sentence saying what to do, unless Ollama answers and has every model pulled."""
    names, why = installed()
    if names is None:
        pytest.skip(f"{why}: start Ollama (ollama serve), or set HUBPROTO_OLLAMA_URL to one that runs")
    missing = [m for m in (model_mod.served_name(x) for x in models) if not model_mod._installed(m, [{"name": n} for n in names])]
    if missing:
        pytest.skip(f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not pulled into the Ollama at {ollama_url()}: run "
                    + " and ".join(f"`ollama pull {m}`" for m in missing))


def text_only_model() -> str | None:
    """A pulled model that Ollama says reads text only (for the refusal test), or None."""
    names, _ = installed()
    for n in sorted(names or []):
        try:
            caps = capabilities(n)
        except Exception:
            continue
        if "completion" in caps and "vision" not in caps and "embedding" not in caps:
            return n
    return None


# ---------------------------------------------------------------- every model call a test makes, and its raw output
CALLS: dict[str, list[dict]] = {}
_current: list[str] = [""]


def _recorded(fn, kind):
    def wrapper(self, *a, **kw):
        t0 = time.monotonic()
        entry = {"kind": kind, "by": kw.get("by") or "-", "model": kw.get("model") or "", "images": len(kw.get("images") or []),
                 "prompt_chars": len(kw.get("system") or "") + len(kw.get("user") or "")}
        CALLS.setdefault(_current[0], []).append(entry)
        try:
            out = fn(self, *a, **kw)
        except Exception as e:
            entry.update(s=round(time.monotonic() - t0, 1), error=f"{type(e).__name__}: {e}")
            raise
        entry.update(s=round(time.monotonic() - t0, 1), text=out.get("text") or "", backend=out.get("backend"),
                     stand_in=out.get("stand_in"), usage=out.get("usage"), tools=[c["tool"] for c in out.get("tool_calls") or []])
        return out
    return wrapper


model_mod.ModelGateway.complete = _recorded(model_mod.ModelGateway.complete, "complete")
model_mod.ModelGateway.complete_with_tools = _recorded(model_mod.ModelGateway.complete_with_tools, "tools")


def calls_of(nodeid: str) -> list[dict]:
    return CALLS.get(nodeid) or []


def raw_output(nodeid: str) -> str:
    lines = []
    for i, c in enumerate(calls_of(nodeid), 1):
        u = c.get("usage") or {}
        lines.append(f"--- call {i}: {c['kind']} by {c['by']} on {c['model']} ({c.get('backend', '?')}), {c['prompt_chars']} prompt chars"
                     + (f", {c['images']} picture(s)" if c["images"] else "") + f", {c.get('s', '?')} s"
                     + (f", {u.get('prompt_tokens')} prompt tokens, {u.get('output_tokens')} output tokens, done: {u.get('done_reason')}" if u else "")
                     + (f", tools {c['tools']}" if c.get("tools") else ""))
        lines.append(c.get("error") or c.get("text") or "(empty answer)")
    return "\n".join(lines) or "(no model call was made)"


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    _current[0] = item.nodeid


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call" and rep.failed:
        rep.sections.append(("the model's raw output", raw_output(item.nodeid)))


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: asks a model more than once or reads a whole SOP diff; deselect with -m 'not slow'")


def pytest_terminal_summary(terminalreporter):
    if not CALLS:
        return
    terminalreporter.section("model calls")
    for nodeid, calls in CALLS.items():
        total = sum(c.get("s") or 0 for c in calls)
        each = ", ".join(f"{c['by']} {c.get('s', '?')} s" + (" (failed)" if c.get("error") else "") for c in calls)
        terminalreporter.write_line(f"{nodeid.split('::')[-1]}: {len(calls)} call(s), {total:.1f} s: {each}")
    if os.environ.get("HUBPROTO_INFERENCE_SHOW"):  # every answer, passing tests too: for reading what the model said
        for nodeid in CALLS:
            terminalreporter.section(nodeid.split("::")[-1], sep="-")
            terminalreporter.write_line(raw_output(nodeid))


# ---------------------------------------------------------------- the hub
@pytest.fixture(autouse=True)
def no_stand_in_no_hosted(monkeypatch):
    """Whatever ran before (another suite in the same session), this test has no stand-in and no hosted model."""
    for name in ("HUBPROTO_ALLOW_STAND_IN", "HUBPROTO_HOSTED_MODELS", "ANTHROPIC_API_KEY", "HUBPROTO_MODEL_FAKE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(model_mod, "_CAPS", {})  # Ollama is asked again what each model can do
    assert not model_mod.stand_in_allowed() and not model_mod.hosted_allowed()


def release_all(hub: Hub, reviewers=("bob", "carol")) -> dict:
    """Compile, approve every change with two policy owners, and release, the Frame files kept as the source (as
    tests/conftest.py's helper; not imported from there, which would switch the stand-in on)."""
    from hubproto import sopdocs

    if not hub.releases.list() and not hub.sop_source():
        sopdocs.keep_frame_files(hub, by="tests", reason="the local-inference suite (tests_inference/conftest.py release_all)")
    proposal = hub.compile()
    for r in reviewers:
        hub.review(proposal["id"], r, approve_all=True)
    return hub.release(proposal["id"], reviewers[0])


@pytest.fixture
def released(tmp_path) -> Hub:
    """A fresh hub on a copy of the repository Frames, released, its cogs on the models the Frames name."""
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    hub = Hub(tmp_path)
    hub.kernel.seed()
    release_all(hub)
    return hub


def dated(bill: dict) -> dict:
    from datetime import date

    return {**bill, "date": date.today().isoformat()}


SMALL_BILL = {"supplier": "acme-metals", "amount": 1500, "currency": "USD", "po_ref": "PO-1001", "description": "Aluminium stock"}
BIG_BILL = {"supplier": "acme-metals", "amount": 12000, "currency": "USD", "po_ref": "PO-1002", "description": "CNC fixtures"}


@pytest.fixture
def timed(request):
    """Call ``timed()`` at the end of a test to check its model calls together stayed inside the bound (CALL_BOUND
    seconds, or the ``bound`` given); returns the seconds they took."""
    def check(bound: float = CALL_BOUND) -> float:
        total = sum(c.get("s") or 0 for c in calls_of(request.node.nodeid))
        assert total <= bound, f"the model calls took {total:.0f} s together, more than the bound of {bound:.0f} s\n{raw_output(request.node.nodeid)}"
        return total
    return check


@pytest.fixture
def raw(request):
    """The model's raw output so far in this test, for an assertion's message."""
    return lambda: raw_output(request.node.nodeid)
