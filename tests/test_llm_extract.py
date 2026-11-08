"""SOP prose read by a model, behind the guards: the compiler's sentences first; the model's rules quote their line,
use the organization's names, are checked like any rule and shown to the reviewers; answers are cached by the exact
text; a sentence nobody could read still stops the release."""

import json
import shutil

import pytest

from conftest import ROOT, release_all
from hubproto.app import Hub
from hubproto.frames import load_frame_text
from hubproto.llm_extract import ModelExtractor, context_for
from pathlib import Path

PROSE = "Any bill of more than 5,000 USD must be approved by the controller, whoever entered it."


@pytest.fixture
def hub(tmp_path):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    h = Hub(tmp_path)
    h.kernel.seed()
    release_all(h)
    return h


def fake(answer: dict):
    calls = []

    def call(model, system, user):
        calls.append(user)
        return "```json\n" + json.dumps(answer) + "\n```"
    return call, calls


def frame_with(hub, extra: str):
    f = hub.frames.get("finance-policy")
    return load_frame_text(f.text.rstrip("\n") + "\n\n## Notes\n" + extra + "\n", Path(f.path))


def test_the_model_reads_prose_rules_that_quote_their_line(hub, tmp_path):
    frame = frame_with(hub, PROSE)
    line = next(i for i, ln in enumerate(frame.lines, start=frame.body_start) if ln.strip() == PROSE)
    call, calls = fake({"rules": [{"kind": "routing", "fields": {"process": "purchase-to-pay", "step": "approve",
                                                                   "condition": {"field": "amount", "op": ">", "value": 5000, "currency": "USD"},
                                                                   "assignee": "role:controller", "mode": "assign"}, "line": line, "quote": PROSE}]})
    ex = ModelExtractor(cache_dir=tmp_path / "cache", context=context_for(hub), call=call, model="ollama/qwen3:14b")
    out = ex.extract(frame)
    read = [r for r in out.rules if r["source"].get("read_by")]
    assert len(read) == 1 and read[0]["assignee"] == "role:controller" and read[0]["source"]["text"] == PROSE
    assert "Rules the hub has ALREADY read" in calls[0] and "controller" in calls[0], "the model is told what was read and which names exist"
    again = ModelExtractor(cache_dir=tmp_path / "cache", context=context_for(hub), call=call, model="ollama/qwen3:14b").extract(frame)
    assert len(calls) == 1 and [r for r in again.rules if r["source"].get("read_by")] == read, "the same text is read once"


def test_what_the_model_reads_goes_through_the_guards_and_to_the_reviewers(hub, monkeypatch):
    path = hub.frames.get("finance-policy").path
    path.write_text(path.read_text().rstrip("\n") + "\n\n## Notes\n" + PROSE + "\n")
    hub._frames = None
    frame = hub.frames.get("finance-policy")
    line = next(i for i, ln in enumerate(frame.lines, start=frame.body_start) if ln.strip() == PROSE)
    good = {"kind": "routing", "fields": {"process": "purchase-to-pay", "step": "approve", "condition": {"field": "amount", "op": ">", "value": 5000,
                                                                                                         "currency": "USD"},
                                          "assignee": "role:controller", "mode": "assign"}, "line": line, "quote": PROSE}
    invented = {"kind": "permission", "fields": {"subject": "role:wizard", "action": "approve", "object": "bill", "resource": "folder:Finance/Payables",
                                                 "effect": "allow"}, "line": line, "quote": "the wizard approves bills"}
    call, _ = fake({"rules": [good, invented]})
    monkeypatch.setattr(hub, "extractor", lambda calls=False: ModelExtractor(cache_dir=hub.root / "state" / "x", context=context_for(hub), call=call,
                                                                              model="ollama/qwen3:14b"))
    p = hub.compile(save=False)
    reasons = [c["reason"] for c in p["changes"] if c.get("read_by")]
    assert any("read by" in r and PROSE[:30] in r for r in reasons), reasons
    errors = [f["detail"] for f in p["findings"] if f["severity"] == "error"]
    assert any("wizard" in d for d in errors), "an invented role is caught by the consistency guard"
    assert any("the wizard approves bills" in d or "not in" in d.lower() for d in errors + [f["detail"] for f in p["findings"]]), \
        "a quote that is not in the SOP is caught by the grounding guard"
    assert any(f["severity"] == "warning" and "read by" in f["detail"] for f in p["findings"]), "the reviewers see what the model read"


def test_without_a_model_the_compiler_reading_stands(hub, tmp_path):
    def broken(model, system, user):
        raise ConnectionError("offline")

    frame = frame_with(hub, PROSE)
    out = ModelExtractor(cache_dir=tmp_path / "c", context=context_for(hub), call=broken, model="ollama/qwen3:14b").extract(frame)
    assert not any(r["source"].get("read_by") for r in out.rules)
    assert any("could not read the prose" in n["note"] for n in out.notes)


def test_a_page_never_waits_on_the_model_and_hosted_models_are_off(hub, tmp_path, monkeypatch):
    from hubproto import model as model_mod

    frame = frame_with(hub, PROSE)
    call, calls = fake({"rules": []})
    out = ModelExtractor(cache_dir=tmp_path / "c2", context=context_for(hub), call=call, model="ollama/qwen3:14b", calls=False).extract(frame)
    assert calls == [] and any("not read by the model yet" in n["note"] for n in out.notes), "a page uses only what was read before"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("HUBPROTO_HOSTED_MODELS", raising=False)
    assert model_mod.backend_for("claude-opus-5-5") == "none", "the hub talks only to local models"
    assert not any(c["value"].startswith("claude") for c in model_mod.available_models()["choices"])
    monkeypatch.setenv("HUBPROTO_EXTRACTOR_MODEL", "claude-opus-5-5")
    from hubproto.llm_extract import local_model

    assert not local_model().startswith("claude"), "a hosted model is never the SOP reader unless switched on"



def test_the_compile_op_is_read_by_the_quality_manager_and_the_organization_cog(hub, monkeypatch):
    """Reading SOPs is the Compile the Frame Op, done by the compile-frame committee: the QA manager cog and the
    organization cog, each with the model its profile names (changed through the policy); both must agree."""
    from hubproto import model as model_mod
    from hubproto.llm_extract import CommitteeExtractor

    served = [{"name": "qwen3:14b", "params": "14.8B", "family": "qwen3", "quant": "Q4", "size_gb": 9.3}]
    monkeypatch.setattr(model_mod, "ollama_models", lambda refresh=False: (served, ""))
    monkeypatch.setattr(model_mod, "ollama_url", lambda: "http://ollama.test")
    b = hub.releases.bundle(hub.releases.current()["digest"])
    c = b["committees"]["compile-frame"]
    assert c["members"] == ["qa-manager", "organization"] and c["quorum"] == 2
    assert b["members"]["organization"] == {**b["members"]["organization"], "type": "cog", "roles": ["staff"]}, "it signs nothing"
    ex = hub.extractor(calls=True)
    assert isinstance(ex, CommitteeExtractor) and [n for n, _ in ex.members] == ["qa-manager", "organization"] and ex.quorum == 2
    from hubproto.automation import AUTOMATIONS

    assert AUTOMATIONS["compile-frame"]["committee"] == "compile-frame"

def test_a_committee_of_cogs_compiles_the_rules_two_of_three_agreeing(hub, tmp_path, monkeypatch):
    from hubproto.llm_extract import CommitteeExtractor

    frame = frame_with(hub, PROSE + "\nThe controller approves journal reversals.")
    line = next(i for i, ln in enumerate(frame.lines, start=frame.body_start) if ln.strip() == PROSE)
    agreed = {"kind": "routing", "fields": {"process": "purchase-to-pay", "step": "approve", "condition": {"field": "amount", "op": ">", "value": 5000,
                                                                                                          "currency": "USD"},
                                            "assignee": "role:controller", "mode": "assign"}, "line": line, "quote": PROSE}
    lone = {"kind": "sla", "fields": {"process": "purchase-to-pay", "step": "approve", "days": 2}, "line": line, "quote": PROSE}

    def call(model, system, user):
        who = system.split("You are the ")[1].split(" cog")[0]
        rules = [agreed] if who != "doc-reviewer" else []
        if who == "qa-manager":
            rules.append(lone)
        return json.dumps({"rules": rules})

    ex = CommitteeExtractor([("frame-compiler", "ollama/qwen3:14b"), ("qa-manager", "ollama/qwen3:14b"), ("doc-reviewer", "ollama/qwen3:14b")], 2,
                            cache_dir=tmp_path / "c", context=context_for(hub), call=call)
    out = ex.extract(frame)
    read = [r for r in out.rules if (r.get("source") or {}).get("read_by")]
    assert len(read) == 1 and read[0]["kind"] == "routing" and "2 of 3 agree" in read[0]["source"]["read_by"]
    assert any("disputed, left out" in n["note"] and "qa-manager" in n["note"] for n in out.notes), "a rule only one member read is shown, not used"
    b = hub.releases.bundle(hub.releases.current()["digest"])
    assert b["committees"]["compile-frame"]["quorum"] == 2 and len(b["committees"]["compile-frame"]["members"]) == 2
