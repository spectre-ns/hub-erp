"""The kernel: numbering, typed fields, links, labels, holds, queries, the ledger command, the event store."""

from datetime import date

import pytest

from hubproto.kernel import KernelGuardError, parse_locator

FOLDER = "Widgets/Requests"


@pytest.fixture
def k(hub):
    return hub.kernel


def task(k, title="a task", folder=FOLDER, **fields):
    return k.create("task", folder, {"title": title, **fields}, by="erin")


def guard(excinfo) -> str:
    return excinfo.value.guard


# ------------------------------------------------------------------ numbering

def test_numbering_uses_the_type_prefix_and_digits(k):
    t = task(k)
    assert t["number"] == "TASK-0001" and t["locator"] == "rec:task:TASK-0001"
    j = k.create("journal", "Finance", {"memo": "manual"}, by="bob")
    assert j["number"] == "JE-00001", "journal numbers have five digits"


def test_yearly_numbering_includes_the_year(k):
    year = date.today().year
    n1 = k.create("ncr", "Quality", {"title": "n1", "description": "d"}, by="carol")
    n2 = k.create("ncr", "Quality", {"title": "n2", "description": "d"}, by="carol")
    assert n1["number"] == f"NCR-{year}-0001"
    assert n2["number"] == f"NCR-{year}-0002"


def test_sequence_is_gapless_per_type(k):
    numbers = [task(k, f"t{i}")["number"] for i in range(5)]
    assert numbers == [f"TASK-{i:04d}" for i in range(1, 6)]
    k.create("ncr", "Quality", {"title": "x", "description": "d"}, by="carol")
    assert task(k, "after an ncr")["number"] == "TASK-0006", "another type does not advance the sequence"
    assert k.next_number("task") == "TASK-0007"


def test_lookup_by_locator_and_by_bare_number(k):
    t = task(k, "find me")
    assert parse_locator("rec:task:TASK-0001") == ("task", "TASK-0001")
    assert parse_locator("TASK-0001") == (None, "TASK-0001")
    assert k.get("rec:task:TASK-0001")["fields"]["title"] == "find me"
    assert k.get("TASK-0001")["locator"] == t["locator"]
    assert k.get("task:TASK-0001")["locator"] == t["locator"]
    assert k.get("TASK-0099") is None
    with pytest.raises(KernelGuardError, match="no record") as e:
        k.require("TASK-0099")
    assert guard(e) == "exists"


# --------------------------------------------------------------------- fields

def test_enum_fields_are_validated_on_create_and_set(k):
    with pytest.raises(KernelGuardError, match="must be one of low,medium,high") as e:
        task(k, priority="urgent")
    assert guard(e) == "field-enum"
    assert k.query(type_="task") == [], "a refused create leaves no record"
    t = task(k, priority="high")
    assert t["fields"]["priority"] == "high"
    with pytest.raises(KernelGuardError) as e:
        k.set_fields(t["locator"], {"priority": "whenever"}, by="erin")
    assert guard(e) == "field-enum"
    assert k.get(t["locator"])["fields"]["priority"] == "high"


def test_unknown_record_type_is_refused(k):
    with pytest.raises(KernelGuardError, match="unknown record type") as e:
        k.create("gizmo", FOLDER, {}, by="erin")
    assert guard(e) == "record-type"


def test_required_by_state_blocks_the_transition_and_names_the_fields(k):
    t = k.create("task", FOLDER, {"description": "no title yet"}, by="erin")
    assert k.required_missing(t, "done") == ["title"]
    with pytest.raises(KernelGuardError, match="cannot enter 'done' without title") as e:
        k.transition(t["locator"], "done", by="erin")
    assert guard(e) == "required-fields"
    assert k.get(t["locator"])["state"] == "open"
    k.set_fields(t["locator"], {"title": "now titled"}, by="erin")
    done = k.transition(t["locator"], "done", by="erin", reason="finished")
    assert done["state"] == "done"
    assert done["transitions"] == [{"from": "open", "to": "done", "at": done["transitions"][0]["at"], "by": "erin",
                                    "run_id": "", "reason": "finished"}]


# ---------------------------------------------------------------------- links

def test_link_vocabulary_is_enforced(k):
    a, b = task(k, "a"), task(k, "b")
    with pytest.raises(KernelGuardError, match="unknown link relation 'likes'") as e:
        k.link(a["locator"], "likes", b["locator"], by="erin")
    assert guard(e) == "link-vocabulary"
    with pytest.raises(KernelGuardError) as e:
        k.link(a["locator"], "concerns", "rec:task:TASK-0099", by="erin")
    assert guard(e) == "exists"
    assert k.get(a["locator"])["links"] == []


def test_links_are_stored_once_and_seen_from_both_sides(k, hub):
    a, b = task(k, "a"), task(k, "b")
    k.link(a["locator"], "concerns", b["locator"], by="erin")
    k.link(a["locator"], "concerns", "TASK-0002", by="erin")  # same link by bare number: no duplicate
    assert k.get(a["locator"])["links"] == [{"rel": "concerns", "target": b["locator"],
                                             "at": k.get(a["locator"])["links"][0]["at"]}]
    assert k.links_of(a["locator"]) == [{"rel": "concerns", "target": b["locator"], "direction": "out",
                                         "label": "concerns"}]
    assert k.links_of(b["locator"]) == [{"rel": "concerns", "target": a["locator"], "direction": "in",
                                         "label": "concerned-by"}]
    assert hub.kernel.link_vocab["concerns"] == "concerned-by"
    assert [r["locator"] for r in k.query(linked_to=b["locator"])] == [a["locator"]]
    assert [r["locator"] for r in k.query(linked_to=a["locator"])] == [b["locator"]]
    k.unlink(a["locator"], "concerns", b["locator"], by="erin")
    assert k.links_of(a["locator"]) == [] and k.links_of(b["locator"]) == []


# --------------------------------------------------------------------- labels

def test_sensitivity_may_rise_but_lowering_needs_a_decision(k):
    t = task(k)
    assert t["labels"] == {"sensitivity": "internal", "retention": "standard"}
    t = k.set_labels(t["locator"], {"sensitivity": "confidential"}, by="erin")
    assert t["labels"]["sensitivity"] == "confidential"
    with pytest.raises(KernelGuardError, match="needs a Gate decision") as e:
        k.set_labels(t["locator"], {"sensitivity": "internal"}, by="erin")
    assert guard(e) == "label-lowering"
    assert k.get(t["locator"])["labels"]["sensitivity"] == "confidential"
    t = k.set_labels(t["locator"], {"sensitivity": "public"}, by="bob", decision={"by": "bob", "verdict": "approve"})
    assert t["labels"]["sensitivity"] == "public"
    assert t["labels"]["retention"] == "standard", "other labels are kept"


def test_hold_blocks_fields_transitions_and_archiving(k):
    t = task(k)
    t = k.hold(t["locator"], True, "litigation", by="bob")
    assert t["hold"] is True and t["hold_reason"] == "litigation"
    for cmd in (lambda: k.set_fields(t["locator"], {"title": "x"}, by="erin"),
                lambda: k.transition(t["locator"], "done", by="erin"),
                lambda: k.archive(t["locator"], by="erin")):
        with pytest.raises(KernelGuardError, match="on hold") as e:
            cmd()
        assert guard(e) == "hold"
    rec = k.get(t["locator"])
    assert rec["fields"]["title"] == "a task" and rec["state"] == "open" and not rec["archived"]
    k.hold(t["locator"], False, "released", by="bob")
    assert k.set_fields(t["locator"], {"title": "x"}, by="erin")["fields"]["title"] == "x"


def test_archive_hides_the_record_unless_asked_for(k):
    keep, gone = task(k, "keep"), task(k, "gone")
    k.archive(gone["locator"], by="erin")
    assert [r["number"] for r in k.query(type_="task")] == [keep["number"]]
    assert [r["number"] for r in k.query(type_="task", include_archived=True)] == [keep["number"], gone["number"]]
    assert k.get(gone["locator"])["archived"] is True, "an archived record is still addressable"


# ---------------------------------------------------------------------- query

def test_query_by_type_folder_and_state(k):
    q1 = task(k, "root", folder="Quality")
    q2 = task(k, "sub", folder="Quality/Training")
    task(k, "lookalike", folder="QualityAssurance")
    f = task(k, "finance", folder="Finance")
    n = k.create("ncr", "Quality", {"title": "n", "description": "d"}, by="carol")
    k.transition(f["locator"], "done", by="erin")
    assert [r["number"] for r in k.query(type_="ncr")] == [n["number"]]
    assert [r["number"] for r in k.query(folder="Quality")] == [q1["number"], q2["number"], n["number"]]
    assert [r["number"] for r in k.query(folder="Quality/Training")] == [q2["number"]]
    assert [r["number"] for r in k.query(type_="task", state="done")] == [f["number"]]
    assert [r["number"] for r in k.query(type_="task", folder="Quality", state="open")] == [q1["number"], q2["number"]]
    assert k.query(folder="Nowhere") == []


def test_query_by_text_searches_fields_and_number(k):
    a = task(k, "Calibrate the Press", description="annual")
    b = task(k, "Order parts")
    assert [r["number"] for r in k.query(text="the press")] == [a["number"]], "substring, case-insensitive"
    assert [r["number"] for r in k.query(text="ANNUAL")] == [a["number"]], "any field"
    assert [r["number"] for r in k.query(text="task-0002")] == [b["number"]], "the number too"
    assert k.query(text="nothing like this") == []


def test_query_by_assignee_after_assign(k):
    a, b = task(k, "a"), task(k, "b")
    assert k.query(assignee="frank") == []
    a = k.assign(a["locator"], ["frank", "erin"], by="hub", due="2026-12-01")
    assert a["assignees"] == ["frank", "erin"] and a["due"] == "2026-12-01"
    assert [r["number"] for r in k.query(assignee="frank")] == [a["number"]]
    assert [r["number"] for r in k.query(assignee="erin")] == [a["number"]]
    k.assign(b["locator"], ["erin"], by="hub")
    assert [r["number"] for r in k.query(assignee="erin")] == [a["number"], b["number"]]
    assert k.get(b["locator"])["due"] is None


def test_query_overdue_at_needs_a_past_due_date_and_a_waiting_state(k):
    waiting = task(k, "waiting")
    k.transition(waiting["locator"], "awaiting-check", by="hub", assignees=["erin"], due="2026-01-10")
    closed = task(k, "closed")
    k.transition(closed["locator"], "done", by="hub", due="2026-01-10")
    no_due = task(k, "no due")
    k.transition(no_due["locator"], "awaiting-check", by="hub", assignees=["erin"])
    assert [r["number"] for r in k.query(overdue_at="2026-02-01")] == [waiting["number"]]
    assert k.query(overdue_at="2026-01-10") == [], "due on the day is not overdue"
    assert k.query(overdue_at="2025-12-31") == []
    assert [r["number"] for r in k.query(overdue_at="2026-02-01", assignee="erin")] == [waiting["number"]]


# ----------------------------------------------------------------- projections

def test_flat_exposes_the_list_view_keys(k):
    t = task(k, "flat", priority="low")
    k.transition(t["locator"], "awaiting-check", by="hub", assignees=["erin"], due="2026-12-01")
    flat = k.flat(k.get(t["locator"]))
    assert flat["id"] == "TASK-0001" and flat["kind"] == "task" and flat["type"] == "task"
    assert flat["state"] == "awaiting-check" and flat["folder"] == FOLDER and flat["locator"] == t["locator"]
    assert flat["title"] == "flat" and flat["priority"] == "low", "fields are merged to the top level"
    assert flat["assignees"] == ["erin"] and flat["due"] == "2026-12-01"
    assert flat["last_event"] == "record.transitioned" and len(flat["events"]) == 2


def test_records_in_folder_is_exact_while_query_is_a_prefix(k):
    root = task(k, "root", folder="Quality")
    sub = task(k, "sub", folder="Quality/Training")
    assert [r["id"] for r in k.records_in_folder("Quality")] == [root["number"]]
    assert [r["id"] for r in k.records_in_folder("Quality/Training")] == [sub["number"]]
    assert [r["number"] for r in k.query(folder="Quality")] == [root["number"], sub["number"]]
    assert k.records_in_folder("Qual") == []


# --------------------------------------------------------------------- ledger

def journal(**over):
    lines = over.pop("lines", [{"account": "5000", "debit": 10.0, "credit": 0.0},
                               {"account": "2000", "debit": 0.0, "credit": 10.0}])
    return {"date": date.today().isoformat(), "currency": "USD", "memo": "test", "lines": lines, **over}


def post(k, j, grant, prepared_by="alice", approved_by="bob", run_id="r1"):
    return k.post_journal(j, run_id=run_id, release="rel", grant=grant, prepared_by=prepared_by,
                          approved_by=approved_by, by="ledger-poster")


GRANT = {"id": "g1", "run_id": "r1", "decided_by": "bob"}


def test_post_journal_needs_a_grant_for_this_run(k):
    with pytest.raises(KernelGuardError, match="no grant") as e:
        post(k, journal(), None)
    assert guard(e) == "grant"
    with pytest.raises(KernelGuardError) as e:
        post(k, journal(), {**GRANT, "run_id": "someone-elses-run"})
    assert guard(e) == "grant"
    assert k.journals() == {}


def test_post_journal_must_balance(k):
    with pytest.raises(KernelGuardError, match="debits 10.0 do not equal credits 9.0") as e:
        post(k, journal(lines=[{"account": "a", "debit": 10, "credit": 0}, {"account": "b", "debit": 0, "credit": 9}]), GRANT)
    assert guard(e) == "balanced-journal"
    with pytest.raises(KernelGuardError) as e:
        post(k, journal(lines=[{"account": "a", "debit": 0, "credit": 0}]), GRANT)
    assert guard(e) == "balanced-journal", "an empty journal is not balanced"
    assert k.journals() == {}


def test_post_journal_needs_an_open_period(k):
    closed = f"{date.today().year - 1}-12-15"
    with pytest.raises(KernelGuardError, match="is not open") as e:
        post(k, journal(date=closed), GRANT)
    assert guard(e) == "period-open"
    with pytest.raises(KernelGuardError) as e:
        post(k, journal(date="1999-01-01"), GRANT)
    assert guard(e) == "period-open", "an unknown period is not open either"
    assert k.journals() == {}


def test_post_journal_enforces_segregation_of_duties(k):
    with pytest.raises(KernelGuardError, match="prepared and approved") as e:
        post(k, journal(), GRANT, prepared_by="bob", approved_by="bob")
    assert guard(e) == "segregation-of-duties"
    assert k.journals() == {}


def test_posted_journal_settles_its_bill_and_numbers_stay_gapless(k):
    bill = k.create("bill", "Finance/Payables", {"supplier": "acme-metals", "amount": 10.0, "currency": "USD"},
                    by="alice", state="filed")
    n1 = post(k, journal(bill=bill["number"]), GRANT)
    n2 = post(k, journal(bill="BILL-0404"), GRANT)  # no such bill: posted, but nothing to settle
    assert (n1, n2) == ("JE-00001", "JE-00002")
    j1 = k.get(f"journal:{n1}")
    assert j1["state"] == "posted" and j1["run_id"] == "r1" and j1["release"] == "rel"
    assert j1["fields"]["prepared_by"] == "alice" and j1["fields"]["approved_by"] == "bob"
    assert j1["fields"]["grant"] == "g1"
    assert k.links_of(j1["locator"]) == [{"rel": "settles", "target": bill["locator"], "direction": "out",
                                          "label": "settles"}]
    assert k.links_of(bill["locator"]) == [{"rel": "settles", "target": j1["locator"], "direction": "in",
                                            "label": "settled-by"}]
    assert k.get(f"journal:{n2}")["links"] == []
    with pytest.raises(KernelGuardError):
        post(k, journal(), None)
    assert post(k, journal(), GRANT) == "JE-00003", "a refused post consumes no number"


# --------------------------------------------------------------------- events

def test_events_carry_the_run_and_release_and_come_back_in_order(k):
    t = k.create("task", FOLDER, {"title": "t"}, by="erin", run_id="run-a", release="digest-a")
    k.set_fields(t["locator"], {"description": "d"}, by="hub", run_id="run-b", release="digest-b")
    k.link(t["locator"], "concerns", t["locator"], by="erin")  # no run: the record keeps the last run seen
    evs = k.events(aggregate=t["locator"])
    assert [e["type"] for e in evs] == ["record.created", "record.fields_set", "record.linked"]
    assert [e["seq"] for e in evs] == sorted(e["seq"] for e in evs)
    assert (evs[0]["run_id"], evs[0]["release"], evs[0]["by"]) == ("run-a", "digest-a", "erin")
    assert (evs[1]["run_id"], evs[1]["release"], evs[1]["by"]) == ("run-b", "digest-b", "hub")
    assert (evs[2]["run_id"], evs[2]["release"]) == ("", "")
    rec = k.get(t["locator"])
    assert rec["created_by"] == "erin" and rec["run_id"] == "run-b" and rec["release"] == "digest-b"
    assert rec["events"] == [e["seq"] for e in evs]
    assert k.events(aggregate="rec:task:TASK-0099") == []
    assert [e["aggregate"] for e in k.events(prefix="rec:") if e["by"] != "seed"] == [t["locator"]] * 3  # the seed's sample locations aside


def test_record_cache_is_invalidated_by_every_append(k):
    t = task(k)
    first = k._records()
    assert k._records() is first, "the projection is cached between reads"
    k.append("record.fields_set", t["locator"], {"fields": {"title": "raw append"}}, by="erin")
    assert k._records() is not first
    assert k.get(t["locator"])["fields"]["title"] == "raw append"
    k.append("record.transitioned", t["locator"], {"from": "open", "to": "done", "assignees": [], "due": None}, by="erin")
    assert k.get(t["locator"])["state"] == "done"
