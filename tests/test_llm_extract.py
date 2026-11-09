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
    assert b["members"]["organization"] == {**b["members"]["organization"], "type": "cog", "roles": ["policy-reader"]}, "it signs nothing: it only reads the policy"
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


# ---------------------------------------------------------------- diff-based reading (change control v2, section 4)
from hubproto.llm_extract import CommitteeExtractor, baseline_for, diff_plan  # noqa: E402

MODEL = "ollama/qwen3:14b"


def route(amount: float, line: int, quote: str) -> dict:
    return {"kind": "routing", "fields": {"process": "purchase-to-pay", "step": "approve",
                                         "condition": {"field": "amount", "op": ">", "value": amount, "currency": "USD"},
                                         "assignee": "role:controller", "mode": "assign"}, "line": line, "quote": quote}


def line_of(hub, text: str, name: str = "finance-policy") -> int:
    f = hub.frames.get(name)
    return next(i for i, ln in enumerate(f.lines, start=f.body_start) if ln.strip() == text)


def rewrite(hub, old: str, new: str, name: str = "finance-policy") -> None:
    path = hub.frames.get(name).path
    text = path.read_text()
    assert old in text, old
    path.write_text(text.replace(old, new))
    hub._frames = None


def reader(answer):
    """A fake model: ``answer(user)`` for a diff reading; nothing for a whole reading."""
    calls = []

    def call(model, system, user):
        calls.append({"system": system, "user": user})
        return json.dumps(answer(user, system) if "is being revised" in user else {"rules": []})
    return call, calls


def model_reader(hub, tmp_path, call, **kw):
    return ModelExtractor(cache_dir=tmp_path / "cache", context=context_for(hub), call=call, model=MODEL,
                          baseline=lambda: baseline_for(hub), **kw)


@pytest.fixture
def prose_hub(hub, tmp_path):
    """The release in force read PROSE (in a ## Notes section of finance-policy) with the model."""
    path = hub.frames.get("finance-policy").path
    path.write_text(path.read_text().rstrip("\n") + "\n\n## Notes\n" + PROSE + "\n")
    hub._frames = None
    line = line_of(hub, PROSE)
    call, _ = reader(lambda user, system: {"changes": [dict(route(5000, line, PROSE), op="add")]})
    p = hub.compile(extractor=model_reader(hub, tmp_path, call))
    assert not [f for f in p["findings"] if f["severity"] == "error"], p["findings"]
    for who in ("bob", "carol"):
        hub.review(p["id"], who, approve_all=True)
    hub.release(p["id"], "bob")
    [r] = [r for r in hub.releases.rules(hub.releases.current()["digest"]) if (r.get("source") or {}).get("read_by")]
    assert r["source"]["line"] == line and r["condition"]["value"] == 5000
    return hub


def test_an_unchanged_sop_keeps_the_release_s_reading_with_no_model_call(prose_hub, tmp_path):
    def never(model, system, user):
        raise AssertionError("an unchanged SOP is not read again")

    ex = model_reader(prose_hub, tmp_path / "x", never)
    p = prose_hub.compile(save=False, extractor=ex)
    assert ex.calls == 0 and p["changes"] == [] and p["questions"] == []
    assert any((r.get("source") or {}).get("read_by") and r["kind"] == "routing" for r in p["rules"]), "the reading in force stands"
    assert not [f for f in p["findings"] if f["severity"] == "error"]


def test_a_changed_sop_is_read_as_its_diff_with_the_rules_in_force(prose_hub, tmp_path):
    hub = prose_hub
    old_line = line_of(hub, PROSE)
    new_prose = PROSE.replace("5,000", "7,500")
    rewrite(hub, "Everything else is approved by a person.\n", "Everything else is approved by a person.\nBills are paid on net 30 terms.\n")
    rewrite(hub, PROSE, new_prose)
    new_line = line_of(hub, new_prose)
    assert new_line == old_line + 1, "an inserted line above moves the rest down"
    call, calls = reader(lambda user, system: {"changes": [dict(route(7500, new_line, new_prose), op="change", ref="r1")]})
    ex = model_reader(hub, tmp_path / "d", call)
    p = hub.compile(save=False, extractor=ex)
    assert ex.calls == 1, "only the SOP that changed is read, once"
    sent = calls[0]["user"]
    assert f"-{old_line:5d}: {PROSE}" in sent and f"+{new_line:5d}: {new_prose}" in sent, "the diff, with line numbers"
    assert "Bills are paid on net 30 terms." in sent and f"- r1 (line {old_line}): Approvals in purchase-to-pay over 5,000 USD go to the controller." in sent
    assert "The match tolerance is 2 percent." not in sent and "## Process: journal-reversal" not in sent, "not the whole SOP"
    [add] = [c for c in p["changes"] if c["op"] == "add" and c["rule"]["kind"] == "routing"]
    [gone] = [c for c in p["changes"] if c["op"] == "remove" and c["rule"]["kind"] == "routing"]
    assert gone["rule"]["condition"]["value"] == 5000 and add["rule"]["condition"]["value"] == 7500
    assert gone["origin"]["line"] == old_line and gone["origin"]["read_by"] == MODEL, "a removal cites the line in force"
    assert add["origin"] == {"kind": "prose", "by": None, "frame": "finance-policy", "line": new_line, "quote": new_prose,
                             "read_by": MODEL, "at": p["created_at"]}
    assert add["warnings"] == [] and add["stale"] is False and add["read_by"] == MODEL
    assert not add["widening"], "widenings are detected as before"
    assert not [f for f in p["findings"] if f["severity"] == "error"], "the quote is grounded at its new line"


def test_questions_are_raised_kept_by_id_and_closed_when_they_no_longer_arise(prose_hub, tmp_path):
    hub = prose_hub
    vague = "Larger bills go to the controller, whoever entered them."
    clear = "Bills over 5,000 USD go to the controller, whoever entered them."

    def answer(user, system):
        if f"+{line_of(hub, vague) if vague in user else 0:5d}: {vague}" in user:
            return {"changes": [], "questions": [{"line": line_of(hub, vague), "quote": vague, "text": "Over what amount is a bill larger?"}]}
        return {"changes": [dict(route(5000, line_of(hub, clear), clear), op="change", ref="r1")]}

    call, calls = reader(answer)
    rewrite(hub, PROSE, vague)
    first = hub.compile(extractor=model_reader(hub, tmp_path / "q", call))
    qs = {q["code"]: q for q in first["questions"]}
    assert set(qs) == {"ambiguous", "unread"} and all(q["status"] == "open" and q["answer"] is None for q in qs.values())
    assert qs["ambiguous"]["raised_by"] == MODEL and qs["ambiguous"]["line"] == line_of(hub, vague)
    assert qs["unread"]["raised_by"] == "hub" and "neither restated nor removed" in qs["unread"]["text"]
    assert any(c["op"] == "remove" and c["rule"]["kind"] == "routing" for c in first["changes"]), "an unread reading is left out"
    again = hub.compile(extractor=model_reader(hub, tmp_path / "q", call))
    assert len(calls) == 1, "the same diff is read once"
    assert sorted(q["id"] for q in again["questions"]) == sorted(q["id"] for q in first["questions"]), "ids are stable"
    assert all(q["status"] == "open" for q in again["questions"])
    assert hub.proposals.load(first["id"])["status"] == "superseded"
    rewrite(hub, vague, clear)  # the author answers in the prose
    answered = hub.compile(extractor=model_reader(hub, tmp_path / "q", call))
    by_id = {q["id"]: q for q in answered["questions"]}
    assert set(by_id) == {q["id"] for q in first["questions"]}
    for q in first["questions"]:
        assert by_id[q["id"]]["status"] == "answered" and by_id[q["id"]]["by"] == "hub"
        assert by_id[q["id"]]["answer"] == f"no longer arises at line {q['line']}"
    assert not [c for c in answered["changes"] if c["rule"].get("kind") == "routing"], "the restated rule is the one in force"


def test_carrying_questions_keeps_a_person_s_answer_and_reopens_what_arises_again():
    from hubproto.compile import carry_questions, question

    q = question("finance-policy", 40, "Over what amount?", raised_by="qa-manager")
    assert q == question("finance-policy", 40, "Over what amount?", raised_by="organization") | {"raised_by": "qa-manager"}
    closed = carry_questions([q], [])
    assert closed[0]["status"] == "answered" and closed[0]["by"] == "hub"
    assert carry_questions(closed, [q])[0]["status"] == "open", "a question the hub closed opens again when it arises again"
    mine = {**q, "status": "answered", "answer": "see line 41", "by": "bob"}
    assert carry_questions([mine], [q])[0]["answer"] == "see line 41", "a person's answer stands"
    trip = question("finance-policy", 42, "does not read back", raised_by="hub", code="round-trip")
    assert carry_questions([trip], [])[0]["status"] == "open", "a compile closes only the questions compiling raises"
    assert carry_questions(carry_questions([q, trip], []), []) == carry_questions([q, trip], []), "idempotent"


def test_the_committee_reads_the_diff_each_member_on_its_own(prose_hub, tmp_path):
    hub = prose_hub
    new_prose = PROSE.replace("5,000", "7,500")
    extra = "Credit notes go to the controller first."
    rewrite(hub, PROSE, new_prose + "\n" + extra)
    line, xline = line_of(hub, new_prose), line_of(hub, extra)

    def answer(user, system):
        who = system.split("You are the ")[1].split(" cog")[0]
        out = {"changes": [dict(route(7500, line, new_prose), op="change", ref="r1")], "questions": []}
        if who == "qa-manager":
            out["changes"].append({"op": "add", "kind": "sla", "fields": {"process": "purchase-to-pay", "step": "approve", "days": 3},
                                   "line": xline, "quote": extra})
        else:
            out["questions"].append({"line": xline, "quote": extra, "text": "Which credit notes, and first before whom?"})
        return out

    call, calls = reader(answer)
    ex = CommitteeExtractor([("qa-manager", MODEL), ("organization", MODEL)], 2, cache_dir=tmp_path / "cc", context=context_for(hub),
                            call=call, baseline=lambda: baseline_for(hub))
    out = ex.extract(hub.frames.get("finance-policy"))
    assert len(calls) == 2 and all("is being revised" in c["user"] for c in calls)
    read = [r for r in out.rules if (r.get("source") or {}).get("read_by")]
    assert [r["condition"]["value"] for r in read if r["kind"] == "routing"] == [7500]
    assert "2 of 2 agree" in read[0]["source"]["read_by"] and not any(r["kind"] == "sla" and r["source"].get("read_by") for r in read)
    assert any("disputed, left out" in n["note"] and "qa-manager" in n["note"] for n in out.notes)
    [q] = out.questions
    assert q["raised_by"] == "organization" and q["line"] == xline and q["status"] == "open"


def test_a_large_diff_is_read_in_parts_that_fit_the_window(prose_hub, tmp_path):
    hub = prose_hub
    rewrite(hub, "The match tolerance is 2 percent.", "The match tolerance is 3 percent.\nSmall differences are written off.")
    new_prose = PROSE.replace("5,000", "7,500")
    rewrite(hub, PROSE, new_prose)
    frame = hub.frames.get("finance-policy")
    call, calls = reader(lambda user, system: {"changes": [dict(route(7500, line_of(hub, new_prose), new_prose), op="change", ref="r1")]}
                         if "r1" in user else {"changes": []})
    ex = model_reader(hub, tmp_path / "b", call)
    plan = diff_plan(baseline_for(hub)["texts"]["finance-policy"], frame, baseline_for(hub)["rules"])
    from hubproto.extract import ConventionExtractor

    conv = ConventionExtractor().extract(frame)
    assert len(plan.hunks) == 2 and len(ex.chunks(frame, plan, conv)) == 1, "a diff that fits is one call"
    from hubproto.llm_extract import DIFF_SYSTEM

    parts = [ex._prompt_parts(frame, plan, conv, [h], False)[0] for h in plan.hunks]
    whole = ex._prompt_parts(frame, plan, conv, plan.hunks, True)[0]
    ex.budget = len(DIFF_SYSTEM) + max(len(p) for p in parts) + 10
    assert len(DIFF_SYSTEM) + len(whole) > ex.budget
    chunks = ex.chunks(frame, plan, conv)
    assert len(chunks) == 2 and all(len(DIFF_SYSTEM) + len(u) <= ex.budget for u, _ in chunks)
    assert "## Thresholds" in chunks[0][0] and "## Notes" not in chunks[0][0] and "## Notes" in chunks[1][0]
    assert "r1" not in chunks[0][0] and "r1 (line" in chunks[1][0], "each part gets the readings in force of its sections"
    out = ex.extract(frame)
    assert ex.calls == 2 and [r["condition"]["value"] for r in out.rules if (r.get("source") or {}).get("read_by")] == [7500]


def test_every_change_says_where_it_came_from(released):
    from conftest import edit_frame

    edit_frame(released, "finance-policy", "The match tolerance is 2 percent.", "The match tolerance is 3 percent.")
    p = released.compile(save=False)
    assert p["changes"] and p["questions"] == []
    for c in p["changes"]:
        assert c["warnings"] == [] and c["stale"] is False and c["origin"]["kind"] == "prose"
        assert c["origin"]["frame"] == "finance-policy" and c["origin"]["read_by"] is None
    [add] = [c for c in p["changes"] if c["op"] == "add"]
    assert add["origin"]["quote"] == "The match tolerance is 3 percent." and add["origin"]["line"] == 30
    [text] = [c for c in p["changes"] if c["op"] == "text"]
    assert text["origin"]["line"] is None


def test_hosted_compilation_is_an_administrator_s_switch_for_compiling_only(hub, monkeypatch):
    from hubproto import model as model_mod
    from hubproto.llm_extract import COMPILE_HOSTED_MODEL, compile_hosted

    for var in ("HUBPROTO_COMPILE_HOSTED", "HUBPROTO_HOSTED_MODELS", "HUBPROTO_OLLAMA_URL", "HUBPROTO_EXTRACTOR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert not compile_hosted() and not isinstance(hub.extractor(calls=True), CommitteeExtractor), "off by default"
    monkeypatch.setenv("HUBPROTO_COMPILE_HOSTED", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert not compile_hosted(), "it needs an API key"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert compile_hosted() and COMPILE_HOSTED_MODEL == "claude-opus-5-5"
    ex = hub.extractor(calls=True)
    assert isinstance(ex, CommitteeExtractor) and ex.members == [("qa-manager", COMPILE_HOSTED_MODEL), ("organization", COMPILE_HOSTED_MODEL)]
    assert ex.readers[0].budget > 200000 and ex.readers[0].baseline is not None
    assert model_mod.backend_for("claude-opus-5-5") == "none", "cogs keep their models: hosted models stay off for them"
