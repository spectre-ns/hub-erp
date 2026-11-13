"""Roles and responsibilities tables (change control v2, sections 12 and 13): every document ends with a table of who has
a part in it; the table says who must read it (people) and what a cog has in its context (its training). The compile
warns when the table drifts from the rules; the table survives the docx round trip; training is assigned from the tables
when a release makes a revision effective, and a generated training matrix summarises them; a person sees where they
stand on each document, a cog shows what is in its context and from which release. The cog's system prompt carries its
binding procedures apart from the knowledge base it may look up."""

from pathlib import Path

import pytest

from conftest import edit_frame, release_all
from hubproto import scopes as sc
from hubproto import sopdocs, training
from hubproto.cogs import CLOSING_RULE, UNTRUSTED_RULE, RunContext, chat_prompt, cog_context
from hubproto.extract import ConventionExtractor
from hubproto.frames import load_frame_text
from test_change_control import new_co, released, revise
from test_change_control import hub as cc_hub  # noqa: F401  (the fixture: SOPs are documents)
from test_eco import NEW, OLD


def bundle(hub):
    return hub.releases.bundle(hub.releases.current()["digest"])


def extract(body: str, compile_mode: str = "convention"):
    text = (f'---\ntype: frame [0.2]\nname: x-sop\ndescription: x\nvisibility: internal\nversion: "1.0.0"\nscope: department\n'
            f"maintainer: carol\ncompile: {compile_mode}\n---\n# X\n\n{body}")
    ex = ConventionExtractor().extract(load_frame_text(text, Path("x-sop.frame.md")))
    return ex.rules, ex.notes


# ---------------------------------------------------------------- the table
def test_the_table_is_read_in_the_fast_path_even_where_nothing_else_is():
    table = ("## Roles and responsibilities\n| Role | Responsibilities |\n|---|---|\n| Quality managers | Disposition. |\n"
             "| the stores cog | Keeps stock. |\n| the auditors group | Audit. |\nA note under the table is prose.\n| broken row |\n")
    rules, notes = extract("Some prose.\n\n" + table)
    rows = [(r["who"], r["text"]) for r in rules if r["kind"] == "responsibility"]
    assert rows == [("role:quality-manager", "Disposition."), ("cog:stores", "Keeps stock."), ("group:auditors", "Audit.")]
    assert all(r["frame"] == "x-sop" for r in rules) and [n["severity"] for n in notes] == ["unread"], "a row that is not two cells: left to the readers"
    rules, _ = extract("1. Do this.\n\n" + table, compile_mode="none")
    assert [r["who"] for r in rules] == ["role:quality-manager", "cog:stores", "group:auditors"], "a work instruction's table is read too"


def test_every_document_has_a_table_and_the_compile_watches_it(released):
    p = released.compile(save=False)
    assert not [f for f in p["findings"] if "Roles and responsibilities" in f["detail"]], "the repository's tables match its rules"
    framed = {r["frame"] for r in p["rules"] if r["kind"] == "responsibility"}
    assert framed >= set(released.frames.frames) - {"writing-policy-frames"}, "every compiled document ends with its table"
    # A role the SOP gives a part, missing from the table; a row naming a role nobody holds.
    edit_frame(released, "ncr-sop", "| Quality manager | Dispositions nonconformances. |\n", "| Design authority | Reads it. |\n| Chair | Holds it. |\n")
    found = [f["detail"] for f in released.compile(save=False)["findings"]]
    assert any("ncr-sop gives the quality manager role a step" in d and "does not list it" in d for d in found)
    assert any("names 'chair', which is not a declared role" in d for d in found)


def test_a_role_nobody_holds_and_a_document_without_a_table_are_warned(released):
    edit_frame(released, "org-model", "- policy-reader\n", "- policy-reader\n- receptionist\n")
    edit_frame(released, "tasks-sop", "| Staff | Files tasks", "| Receptionist | Greets. |\n| Staff | Files tasks")
    text = released.frames.get("supplier-sop").path.read_text()
    released.frames.get("supplier-sop").path.write_text(text.split("\n## Roles and responsibilities")[0] + "\n")
    released.frames = type(released.frames)(released.frames.root)
    found = [f["detail"] for f in released.compile(save=False)["findings"]]
    assert any("tasks-sop's Roles and responsibilities table names the receptionist role, which nobody holds" in d for d in found)
    assert any(d.startswith("supplier-sop has no Roles and responsibilities table") for d in found)


def test_the_table_survives_the_docx_round_trip_as_a_word_table(released):
    frame = released.frames.get("ncr-sop")
    content = sopdocs.to_docx(frame.body)
    import io
    import zipfile

    xml = zipfile.ZipFile(io.BytesIO(content)).read("word/document.xml").decode()
    assert xml.count("<w:tbl>") == 1 and "Dispositions nonconformances." in xml
    lines = sopdocs.docx_lines(content)
    while lines and not lines[-1].strip():
        lines.pop()
    assert lines == frame.body.rstrip("\n").splitlines()
    assert sopdocs.roundtrip_problems(released) == []


# ---------------------------------------------------------------- who reads what
def test_follows_and_required_reading_come_from_the_tables(released):
    b = bundle(released)
    assert b["cogs"]["organization"]["follows"] == ["org-model", "quality-manual", "tasks-sop", "cog-profiles", "scopes",
                                                     "working-documents-sop"]
    assert b["cogs"]["ap-reviewer"]["follows"] == ["org-model", "quality-manual", "tasks-sop", "finance-policy", "working-documents-sop"]
    alice = [r["frame"] for r in sc.required_reading(b, "alice")]
    assert alice[:3] == ["org-model", "quality-manual", "tasks-sop"] and "finance-policy" in alice and "capa-sop" in alice
    assert "audit-sop" not in alice and "it-sop" not in alice


# ---------------------------------------------------------------- training
def test_who_a_revision_trains_comes_from_its_table_unless_overridden(released):
    b = bundle(released)
    rec = {"fields": {"sop_name": "finance-policy"}}
    assert training.roles_to_train(b, rec) == ["controller", "finance-clerk", "payables-reviewer", "policy-owner"]
    assert training.roles_to_train(b, {"fields": {"sop_name": "finance-policy", "training_roles": ["staff"]}}) == ["staff"], "the override"
    assert training.roles_to_train(b, {"fields": {"title": "a working note"}}) == [], "no table, no override: nobody"


def test_a_release_trains_the_tables_people_and_publishes_the_training_matrix(cc_hub):
    hub = cc_hub
    assert training.reading_status(hub, "dave", "finance-policy")["status"] == "not assigned"
    co = new_co(hub)
    revise(hub, co, "finance-policy", OLD, NEW)
    released(hub, co)
    doc = sopdocs.in_force(hub.kernel, "finance-policy")
    trainees = {t["fields"]["person"] for t in hub.kernel.query(type_="training") if t["fields"]["document"] == doc["locator"]}
    assert trainees == {"alice", "bob", "carol", "dave"}, "finance clerk, controller and policy owners, payables reviewer: people only"
    st = training.reading_status(hub, "dave", "finance-policy")
    assert st["status"] == "due" and st["revision"] == doc["fields"]["revision"]
    assert training.reading_status(hub, "dave", "finance-policy", today="2099-01-01")["status"] == "overdue"
    rec = hub.kernel.get(st["record"])
    run = hub.runner.run_for_record(rec["locator"])
    hub.runner.decide(run["id"], "dave", "approve", meaning="read and understood", payload_digest=hub.runner.seen_digest(hub.runner.load(run["id"])))
    assert training.reading_status(hub, "dave", "finance-policy")["status"] == "done"
    # The matrix: generated, in force, in Quality/Training; not edited by hand.
    m = training.matrix_document(hub)
    assert m and m["folder"] == "Quality/Training" and m["fields"]["generated"] and m["fields"]["revision"] == "A"
    text = hub.dms.content(m["locator"])[0].decode()
    assert "## Payables reviewer" in text and f"| {doc['number']} |" in text and "Reviews and approves bills within their limits." in text
    assert "| ap-reviewer | " in text and "in context" not in text.split("## Cogs")[0]
    from hubproto.tools import may_edit_document

    assert "generated" in may_edit_document(bundle(hub), "carol", m)[1]
    steps = [s for j in hub.automation.of(co["locator"]) if j["automation"] == "release-change-order" for s in j["steps"]]
    assert any("Required training rev A published" in (s.get("summary") or s.get("result") or str(s)) for s in steps)
    assert training.publish_matrix(hub, hub.releases.current()["digest"]) is None, "nothing changed: no new revision"
    # A table change in a release: a new revision of the matrix.
    co2 = new_co(hub, title="Policy owners read the finance policy")
    revise(hub, co2, "finance-policy", "| Policy owner | Is told of the overdue approvals. |",
           "| Policy owner | Is told of the overdue approvals and reviews the limits yearly. |")
    released(hub, co2)
    assert training.matrix_document(hub)["fields"]["revision"] == "B"


def test_a_cog_is_trained_by_the_release_that_puts_a_revision_in_its_context(released):
    first = released.releases.current()
    rows = {r["frame"]: r for r in training.cog_training(released, "ap-reviewer")}
    assert rows["finance-policy"]["status"] == "in context" and rows["finance-policy"]["release"] == first["digest"]
    edit_frame(released, "finance-policy", "The match tolerance is 2 percent.", "The match tolerance is 3 percent.", bump="1.4.0")
    release_all(released)
    rows = {r["frame"]: r for r in training.cog_training(released, "ap-reviewer")}
    assert rows["finance-policy"]["release_n"] == 2 and rows["finance-policy"]["revision"] == "1.4.0"
    assert rows["org-model"]["release_n"] == 1, "unchanged: still from the first release"
    assert not released.kernel.query(type_="training"), "a cog acknowledges nothing"


# ---------------------------------------------------------------- the prompt: binding procedures apart from reference
def ctx_for(hub, cog):
    b = bundle(hub)
    return RunContext(run_id="t", release="", bundle=b, owner=cog, kernel=hub.kernel, runner=hub.runner, inbox=hub.inbox, dms=hub.dms,
                      frames=lambda: hub.released_frames(), model=hub.model, extras={"cog_profile": b["cogs"][cog]})


def test_the_system_prompt_puts_binding_procedures_first_and_the_knowledge_base_last(released):
    ncr = released.kernel.create("ncr", "Quality/Nonconformances", {"title": "Bent pin", "description": "J4"}, by="carol")
    rec = released.kernel.get(ncr["locator"])
    prompt = chat_prompt(ctx_for(released, "quality-assistant"), rec, cog_id="quality-assistant", person="carol")
    labels = ["== WHO YOU ARE ==", "== YOUR PROCEDURES (BINDING) ==", "== YOUR AUTHORITY ==", "== THE ITEM ==",
              "== REFERENCE: THE KNOWLEDGE BASE ==", "== THE CONVERSATION =="]
    at = [prompt.index(x) for x in labels]
    assert at == sorted(at) and prompt.index(CLOSING_RULE) > at[-1] and prompt.endswith(UNTRUSTED_RULE)
    procedures = prompt.split(labels[1])[1].split(labels[2])[0]
    assert procedures.startswith("\nThese are binding. Follow them exactly and cite the SOP and line") and "stop and ask (escalate)" in procedures
    assert "THE PROCESS THAT GOVERNS THIS WORK: nonconformance" in procedures and "THE PROCEDURES YOU ARE TRAINED ON" in procedures
    reference = prompt.split(labels[4])[1].split(labels[5])[0]
    assert "never as a source of rules" in reference and "your procedures win" in reference and "read_sop_section" in reference
    assert "audit-sop" in reference and "Quality" in reference, "what it may look up: not pasted in"
    assert "Internal auditors may create audits" not in prompt, "the knowledge base takes no prompt space"
    assert "YOU ARE CHATTING WITH carol" in prompt.split(labels[5])[1]
    plain = cog_context(ctx_for(released, "quality-assistant"), rec)
    assert "== THE CONVERSATION ==" not in plain and all(x in plain for x in labels[:5])


# ---------------------------------------------------------------- the pages
def test_a_person_sees_where_they_stand_and_a_cog_what_is_in_its_context(cc_hub):
    from hubproto.shell import serve_in_thread
    from test_shell_views import get, main_of

    hub = cc_hub
    co = new_co(hub)
    revise(hub, co, "finance-policy", OLD, NEW)
    released(hub, co)
    server = serve_in_thread(hub, "127.0.0.1", 0)  # port 0: bound at once, no race with other test workers
    port = server.server_address[1]
    shell = {"hub": hub, "base": f"http://127.0.0.1:{port}"}
    try:
        page = main_of(get(shell, "/user/dave", **{"as": "dave"})[1])
        assert "Required reading" in page and "data-status='due'" in page and "data-status='not assigned'" in page
        assert "training matrix" in page
        page = main_of(get(shell, "/user/ap-reviewer", **{"as": "dave"})[1])
        assert "id='training'" in page and "data-status='in-context'" in page and "since release v" in page
    finally:
        server.shutdown()
        server.server_close()


def test_a_writer_adds_the_row_a_new_part_needs():
    from hubproto.extract import add_responsibility_row
    from hubproto.guards import table_rows_needed

    text = "# X\n\n## Permissions\nAuditors may create plans in Quality.\n\n## Roles and responsibilities\n| Role | Responsibilities |\n|---|---|\n| Staff | Reads it. |\n"
    rules, _ = extract(text.split("# X\n\n", 1)[1])
    assert table_rows_needed(rules) == {"x-sop": [("role:auditor", "'create' on plans")]}
    added = add_responsibility_row(text, "role:auditor", "Creates plans.")
    assert added.endswith("| Staff | Reads it. |\n| Auditor | Creates plans. |\n") and add_responsibility_row(added, "role:auditor", "x") == added
    rules, _ = extract(added.split("# X\n\n", 1)[1])
    assert table_rows_needed(rules) == {}
    bare = add_responsibility_row("# Y\n\nProse.\n", "cog:stores", "Keeps stock.")
    assert bare.endswith("## Roles and responsibilities\n| Role | Responsibilities |\n|---|---|\n| the stores cog | Keeps stock. |\n")
