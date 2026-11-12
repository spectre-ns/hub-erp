"""Compiling prose the sentence grammar cannot read, on a hub whose cogs run on local models (issues #46 and #47).

The grammar is a fast path, never a gate: a changed sentence it cannot read goes to the compile-frame committee, and
one nobody read is a blocking question for a person. The committee is the senior cogs, bundled with a hosted model;
with hosted models off (always, in this suite) they cannot read, and the junior cogs never read in their place. The
slow test puts the committee on the local model, to see whether its reading parses and stays grounded."""

import json

import pytest

from conftest import JUNIOR, need

RULE_LIKE = "Bills from a supplier added in the last 90 days always go to the controller."
NARRATIVE = "The finance team meets on Mondays."
ANCHOR = "Everything else is approved by a person.\n"


def add_prose(hub, *sentences: str) -> None:
    path = hub.frames.get("finance-policy").path
    text = path.read_text()
    assert ANCHOR in text
    path.write_text(text.replace(ANCHOR, ANCHOR + "".join(s + "\n" for s in sentences), 1))
    hub._frames = None


def line_of(hub, sentence: str) -> int:
    return next(i for i, ln in enumerate(hub.frames.get("finance-policy").file_lines, 1) if ln == sentence)


def prose_rules(p: dict, line: int) -> list[dict]:
    return [c for c in p["changes"] if (c.get("origin") or {}).get("frame") == "finance-policy" and (c.get("origin") or {}).get("line") == line]


def test_prose_the_grammar_cannot_read_is_a_blocking_question_never_a_rule(released):
    need(JUNIOR)
    hub = released
    committee = hub.compile_committee()
    assert committee["unavailable"] and set(committee["unavailable"]) == {m for m, _ in committee["members"]}, \
        "the seniors are on a hosted model, switched off here"
    add_prose(hub, RULE_LIKE)
    n = len(hub.model.calls)
    p = hub.compile(save=False, extractor=hub.extractor(calls=True))  # as the compile-frame job reads it
    assert len(hub.model.calls) == n, "no junior cog reads in the committee's place"
    line = line_of(hub, RULE_LIKE)
    assert not prose_rules(p, line), "nobody read it, so it is no rule"
    unread = [q for q in p["questions"] if q["code"] == "unread-rule" and q.get("quote") == RULE_LIKE]
    assert unread and unread[0]["blocking"] and unread[0].get("chair") == "carol", p["questions"]
    assert any(q["code"] == "member-unavailable" and q["blocking"] for q in p["questions"]), "the committee's chair is told why nobody read it"


@pytest.mark.slow
def test_a_committee_on_the_local_model_reads_the_prose_into_grounded_rules_or_questions(released, monkeypatch, timed, raw):
    """Each member's answer must parse (an answer that does not is a member-unavailable question), every rule it reads
    must quote the line it rests on, and a sentence that states no rule gives none. What it reads is a proposal for
    people to approve, not a rule in force."""
    need(JUNIOR)
    hub = released
    real = hub.releases.bundle

    def bundle(digest):
        b = json.loads(json.dumps(real(digest)))
        for name in ("senior-organization", "senior-qa-manager"):
            b["cogs"][name]["model"] = JUNIOR
        return b
    monkeypatch.setattr(hub.releases, "bundle", bundle)
    committee = hub.compile_committee()
    assert committee["unavailable"] == {} and {m for _, m in committee["members"]} == {JUNIOR}, committee
    in_force = json.dumps(hub.releases.rules(hub.releases.current()["digest"]), sort_keys=True)
    add_prose(hub, RULE_LIKE, NARRATIVE)
    p = hub.compile(save=False, extractor=hub.extractor(calls=True))
    readers = [c for c in hub.model.calls if not c["stand_in"]]
    assert len(readers) >= 2, f"each member read the diff:\n{raw()}"
    failed = [q for q in p["questions"] if q["code"] == "member-unavailable"]
    assert not failed, "a member's answer did not parse or did not come: " + "; ".join(q["text"] for q in failed) + "\n" + raw()
    rule_line, narrative_line = line_of(hub, RULE_LIKE), line_of(hub, NARRATIVE)
    assert not prose_rules(p, narrative_line), f"a sentence that states no rule was read as one:\n{raw()}"
    grounding = [f for f in p["findings"] if f["severity"] == "error" and f.get("guard") == "grounding"]
    assert not grounding, f"a reading quotes words that are not at its line: {grounding}\n{raw()}"
    read = prose_rules(p, rule_line)
    asked = [q for q in p["questions"] if q.get("line") == rule_line or q.get("quote") == RULE_LIKE]
    assert read or asked, f"the rule-like sentence ended as neither a rule to approve nor a question:\n{raw()}"
    for c in read:
        assert c["origin"]["quote"] == RULE_LIKE, c["origin"]
    assert json.dumps(hub.releases.rules(hub.releases.current()["digest"]), sort_keys=True) == in_force, "nothing is in force until people approve"
    timed(bound=480)
