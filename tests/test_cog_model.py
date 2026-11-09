"""The cog model (change control v2, section 7): a persona is a shared behaviour document; a cog profile is identity
(what it does, its model, its persona); what a cog follows and a person must read is derived from the SOPs that name
them or their roles; autonomy is derived per action (act, propose where a person confirms, refuse); a cog's context is
its process, not whole SOPs; the IT and management cogs and the IT SOP."""

import json
from pathlib import Path

import pytest

from conftest import edit_frame, make_part, release_all
from hubproto import authz, inventory
from hubproto import scopes as sc
from hubproto.agent import Agent, AgentStore, AgentTools, due_duties
from hubproto.cogs import RunContext, cog_context
from hubproto.extract import ConventionExtractor
from hubproto.frames import load_frame_text


def bundle(hub):
    return hub.releases.bundle(hub.releases.current()["digest"])


def extract(body: str, name: str = "x", scope: str = "persona") -> tuple[list[dict], list[dict]]:
    text = f'---\ntype: frame [0.2]\nname: {name}\ndescription: x\nvisibility: internal\nversion: "1.0.0"\nscope: {scope}\nmaintainer: carol\n---\n# X\n\n{body}'
    ex = ConventionExtractor().extract(load_frame_text(text, Path(f"{name}.frame.md")))
    return ex.rules, ex.notes


def tools(hub, cog, requester="carol", mode="derived"):
    return AgentTools(hub, cog, f"t-{cog}-{requester}", mode, 8, AgentStore(hub.root / "state" / "agents"), requester=requester)


def scripted(steps, final="done"):
    def fake(**kw):
        calls = []
        for name, args in steps:
            res = kw["call_tool"](name, args)
            calls.append({"tool": name, "arguments": args, "ok": "error" not in res, "result": res})
        return {"text": final, "stand_in": False, "served_model": "scripted", "model": "scripted", "tool_calls": calls}
    return fake


# ---------------------------------------------------------------- personas
def test_a_persona_is_a_behaviour_document_shared_by_cogs(released):
    b = bundle(released)
    assert set(b["personas"]) == {"reviewer", "author", "executor", "advisor"}
    rev = b["personas"]["reviewer"]
    assert rev["stance"] == "reviewer" and rev["tone"] == "formal" and rev["lenses"] == ["compliance", "consistency", "impact"]
    assert rev["passes"] == 3 and rev["escalates"].startswith("when an item is outside its approval criteria")
    assert rev["behaviour"].startswith("You review items against the procedures you follow") and "quoting it" in rev["behaviour"]
    assert rev["cogs"] == ["ap-reviewer", "doc-reviewer", "quality-assistant"], "one persona, many cogs"
    assert b["personas"]["executor"]["cogs"] == ["it-admin", "purchasing-assistant", "stores"]
    # The cog's merged profile carries its persona's behaviour; lenses drive its adversarial reviews.
    qa = b["cogs"]["qa-manager"]
    assert qa["persona"] == "author" and qa["lenses"] == ["compliance", "consistency", "impact"] and qa["passes"] == 3
    assert "You draft and revise in plain, numbered, testable steps" in qa["prompt"] and "Your tone is formal." in qa["prompt"]


def test_persona_grammar_and_the_role_personas_that_are_gone():
    rules, notes = extract("## Persona: careful\nTone: informal\nStance: advisor\nLenses: impact, clarity\nReviews: two adversarial passes\n"
                           "Escalates: when unsure.\nBehaviour: You think first.\nThen you speak.\n")
    [p] = rules
    assert p["kind"] == "persona" and p["tone"] == "informal" and p["stance"] == "advisor" and p["passes"] == 2
    assert p["behaviour"] == "You think first. Then you speak." and p["escalates"] == "when unsure." and not notes
    rules, notes = extract("## Persona: staff (role)\nLayers: company\n")
    assert not rules and any("role personas" not in n["note"] and "what a cog or a role reads is derived" in n["note"] for n in notes)
    assert notes[0]["severity"] == "error"
    _, notes = extract("## Persona: odd\nStance: boss\nBehaviour: x\n")
    assert any("stance is one of" in n["note"] for n in notes)


# ---------------------------------------------------------------- profiles: identity only
def test_a_cog_profile_is_its_identity(released):
    p = released.compile(save=False)
    ap = next(r for r in p["rules"] if r["kind"] == "cog" and r["name"] == "ap-reviewer")
    assert {k for k in ap if k != "source"} == {"kind", "name", "about", "model", "persona", "prompt_from", "references"}
    assert ap["persona"] == "reviewer" and ap["model"] == "ollama/qwen3:14b" and ap["about"].startswith("Matches supplier bills")
    prof = bundle(released)["cogs"]["ap-reviewer"]
    assert prof["prompt"].startswith("You are the ap-reviewer cog, a member of the organization. What you do: Matches supplier bills")
    rules, notes = extract("## Cog: helper\nAbout: Helps.\nModel: ollama/qwen3:14b\nPersona: persona-executor\nDuty: Do things.\nPrompt: Be good.\nand kind.\n",
                           scope="company")
    [c] = rules
    assert c["persona"] == "executor" and c["about"] == "Helps."
    assert len(notes) == 2 and all(n["severity"] == "error" for n in notes), "the continuation of a moved Prompt: is not read either"
    assert "a duty is a sentence in the SOP that owns the work" in notes[0]["note"] and "behaviour prompt is in its persona" in notes[1]["note"]


def test_a_new_cog_is_proposed_with_its_identity_and_a_persona_that_exists(released):
    with pytest.raises(ValueError, match="no such persona"):
        released.propose_new_cog(name="helper", roles=["staff"], model="ollama/qwen3:14b", about="Helps.", persona="boss", by="carol")
    p = released.propose_new_cog(name="helper", roles=["staff"], model="ollama/qwen3:14b", about="Helps with tasks.", persona="executor", by="carol")
    text = p["overrides"]["cog-profiles"]["text"]
    section = text.split("## Cog: helper\n", 1)[1]
    assert section.startswith("About: Helps with tasks.\nModel: ollama/qwen3:14b\nPersona: executor\n")
    assert text.index("## Cog: helper") < text.index("## Committee: compile-frame"), "with the other profiles"
    assert not [f for f in p["findings"] if f["severity"] == "error"]


# ---------------------------------------------------------------- derived follows and required reading
def test_what_a_cog_follows_is_derived_from_the_roles_and_responsibilities_tables(released):
    b = bundle(released)
    company = ["org-model", "quality-manual", "tasks-sop"]
    assert b["cogs"]["ap-reviewer"]["follows"] == company + ["finance-policy"]
    assert b["cogs"]["doc-reviewer"]["follows"] == company + ["document-control-sop", "wi-review-a-document"]
    assert b["cogs"]["it-admin"]["follows"] == company + ["change-control-sop", "it-sop"]
    assert b["cogs"]["management"]["follows"] == company + ["change-control-sop", "it-sop", "management-review-sop"]
    why = b["cogs"]["purchasing-assistant"]["follows_why"]
    assert "cog:purchasing-assistant" in why["design-control-sop"], "the swarm step names it"
    assert "role:stock-reviewer" in why["inventory-policy"] and why["org-model"][0] == "company" and why["quality-manual"] == ["company", "role:staff"]
    assert {f["frame"]: f["because"] for f in sc.following(b, "doc-reviewer")}["document-control-sop"] == ["cog:doc-reviewer", "role:author", "role:reviewer"]
    # The table decides, not any mention: a sentence naming its role elsewhere does not make it follow that SOP...
    edit_frame(released, "audit-sop", "## Permissions\n", "## Permissions\nPayables reviewers may read Quality/Audits.\n", bump="0.9.0")
    release_all(released)
    assert "audit-sop" not in bundle(released)["cogs"]["ap-reviewer"]["follows"]
    # ...a row in the table does.
    edit_frame(released, "audit-sop", "| Internal auditor |", "| Payables reviewer | Reads the audit findings on payables. |\n| Internal auditor |", bump="0.9.1")
    release_all(released)
    assert "audit-sop" in bundle(released)["cogs"]["ap-reviewer"]["follows"]


def test_a_persons_required_reading_is_derived_the_same_way(released):
    b = bundle(released)
    reading = sc.required_reading(b, "dave")
    dave = {r["frame"]: r["roles"] for r in reading}
    assert list(dave)[:3] == ["org-model", "quality-manual", "tasks-sop"], "the company basics: their tables list staff"
    assert dave["quality-manual"] == ["staff"] and "payables-reviewer" in dave["finance-policy"] and "storekeeper" in dave["inventory-policy"]
    assert dave["wi-respond-to-a-cog-escalation"] == ["staff"], "a work instruction's table"
    assert "it-sop" not in dave and "audit-sop" not in dave and "document-control-sop" not in dave
    assert next(r for r in reading if r["frame"] == "finance-policy")["responsibilities"] == ["Reviews and approves bills within their limits."]
    bob = {r["frame"]: r["roles"] for r in sc.required_reading(b, "bob")}
    assert bob["it-sop"] == ["internal-auditor", "system-administrator"] and "wi-run-an-audit" in bob
    assert sorted({r["who"] for r in b["responsibilities"] if r["frame"] == "it-sop"}) == [
        "cog:it-admin", "cog:management", "role:internal-auditor", "role:management-reviewer", "role:quality-manager", "role:system-administrator"]


# ---------------------------------------------------------------- derived autonomy
def test_autonomy_is_derived_per_action(released):
    b = bundle(released)
    act = authz.autonomy(b, "quality-assistant", "comment", "nonconformance", "Quality/Nonconformances")
    assert act["mode"] == "act" and "Staff may comment on anything in Quality." in act["why"]
    prop = authz.autonomy(b, "doc-reviewer", "comment", "nonconformance", "Quality/Nonconformances")
    assert prop["mode"] == "propose" and prop["confirmer"] == "person"
    # The confirm sentence narrows a broader permission: the doc-reviewer is an author, yet a document controller confirms.
    assert authz.check(b, "doc-reviewer", "create", "document", "Quality/Controlled Documents")["allowed"]
    narrowed = authz.autonomy(b, "doc-reviewer", "create", "document", "Quality/Controlled Documents")
    assert narrowed["mode"] == "propose" and narrowed["confirmer"] == "document-controller"
    assert not authz.check(b, "doc-reviewer", "comment", "nonconformance", "Quality/Nonconformances")["allowed"], "a confirm sentence is no allow"
    refused = authz.autonomy(b, "organization", "comment", "task", "Tasks")
    assert refused["mode"] == "refuse" and "no SOP lets organization" in refused["why"]
    denied = authz.autonomy(b, "it-admin", "approve", "document", "Policy")
    assert denied["mode"] == "refuse" and "The it-admin cog may not approve documents in Policy." in denied["why"]


def test_the_harness_acts_proposes_or_refuses_each_write(released):
    hub = released
    ncr = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "Chipped lens", "description": "lens chipped"}, by="carol")
    qa = tools(hub, "quality-assistant")
    assert qa.execute("comment", {"number": ncr["number"], "text": "Waiting three days now."})["ok"], "act"
    doc = tools(hub, "doc-reviewer")
    out = doc.execute("comment", {"number": ncr["number"], "text": "Shall I?"})
    assert out["proposed"] and "a person confirms it before it happens" in out["note"], "propose"
    org = tools(hub, "organization")
    out = org.execute("message", {"to": "carol", "body": "hello"})
    assert "describe what should be done instead" in out["error"] and "organization may not do this on its own" in out["error"], "refuse"
    it = tools(hub, "it-admin", requester="bob")
    assert it.decide("propose_policy_change", {"frame": "finance-policy"})["mode"] == "act", "system administrators raise change orders"
    out = it.execute("create_document", {"title": "Runbook", "folder": "Policy"})
    assert "describe what should be done instead" in out["error"]
    evs = [e for e in AgentStore(hub.root / "state" / "agents").load(doc.run_id) if e.get("decided")]
    assert evs and evs[-1]["decided"]["mode"] == "propose" and evs[-1]["decided"]["intent"]["action"] == "comment"


def test_a_storekeeper_confirms_what_the_stores_cog_proposes(hub, monkeypatch):
    edit_frame(hub, "inventory-policy", "Staff may comment on anything in Inventory.\n",
               "Staff may comment on anything in Inventory.\nThe stores cog may move stock items in Inventory/Stock when a storekeeper confirms.\n",
               bump="1.3.0")
    release_all(hub)
    b = bundle(hub)
    rule = next(r for r in b["permissions"] if r["subject"] == "cog:stores")
    assert rule["effect"] == "propose" and rule["confirmer"] == "storekeeper" and rule["action"] == "move" and rule["object"] == "stock-item"
    part = make_part(hub.kernel, "SCREW-M3", title="M3 screw", unit_cost=0.01, stock=500)
    [lot] = inventory.lots(hub.kernel, part=part)
    prod = inventory.location_of_kind(hub.kernel, "MAIN", "production")["locator"]
    monkeypatch.setattr(hub.model, "complete_with_tools", scripted([
        ("transfer_stock", {"lot": lot["number"], "to_location": prod, "quantity": 20}),
        ("count_stock", {"lot": lot["number"], "counted": 500}),          # not a move: the storekeeper's right, it acts
        ("finish", {"summary": "moved and counted"})]))
    res = Agent(hub, "stores").run("move 20 to production and count it", by="dave")
    assert res["status"] == "proposed" and [p["tool"] for p in res["proposed"]] == ["transfer_stock"]
    assert res["proposed"][0]["confirmer"] == "storekeeper" and inventory.location_path(hub.kernel, hub.kernel.get(lot["locator"])["fields"]["location"]) == "Main site / Stores"
    with pytest.raises(ValueError, match="only a storekeeper confirms transfer_stock"):
        Agent(hub, "stores").confirm(res["run"], "carol")
    out = Agent(hub, "stores").confirm(res["run"], "dave")
    assert out["results"][0]["result"].get("ok"), out
    moved = [r for r in inventory.lots(hub.kernel, part=part) if r["fields"].get("quantity") == 20]
    assert moved, "the move happened once dave confirmed it"


# ---------------------------------------------------------------- context by process
def ctx_for(hub, cog: str) -> RunContext:
    b = bundle(hub)
    return RunContext(run_id="t", release="", bundle=b, owner=cog, kernel=hub.kernel, runner=hub.runner, inbox=hub.inbox, dms=hub.dms,
                      frames=lambda: hub.released_frames(), model=hub.model, extras={"cog_profile": b["cogs"][cog]})


def test_a_cogs_context_is_its_process_with_the_sentences_and_any_divergence(released):
    hub = released
    ncr = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "Bent pin", "description": "J4"}, by="carol")
    route = next(r for r in bundle(hub)["routing"] if r["assignee"] == "cog:quality-assistant")
    # A divergence recorded in the release (workstream 1 writes them): the rule in force is shown next to the line.
    index = json.loads(hub.releases.index_path.read_text())
    index[-1]["divergences"] = [{"id": "d1", "frame": route["source"]["frame"], "line": route["source"]["line"], "prose": route["source"]["text"],
                                 "in_force": {**route, "kind": "routing", "condition": {"field": "risk", "op": "==", "value": "medium"}},
                                 "co": "CO-2026-0007", "accepted_by": "bob", "meaning": "medium too", "at": "2026-11-09"}]
    hub.releases.index_path.write_text(json.dumps(index))
    prompt = cog_context(ctx_for(hub, "quality-assistant"), hub.kernel.get(ncr["locator"]))
    assert prompt.startswith("== WHO YOU ARE ==\nYou are the quality-assistant cog") and "You review items against the procedures you follow" in prompt
    assert "THE PROCESS THAT GOVERNS THIS WORK: nonconformance (in ncr-sop" in prompt and "5. [quality-manager] Disposition the nonconformance" in prompt
    part = prompt.split("YOUR PART IN IT")[1].split("THE PROCEDURES YOU ARE TRAINED ON")[0]
    assert route["source"]["text"] in part and "The quality-assistant cog may approve nonconformance nonconformances when the risk is low." in part
    assert "Every day the quality-assistant cog finds nonconformances" in part, "its duty in this SOP"
    assert ("The rule in force differs from this line: At Disposition the nonconformance in nonconformance with medium risk go to the "
            "quality-assistant cog.: follow the rule in force") in part
    assert "accepted by bob on CO-2026-0007" in part
    # Not whole SOPs: the purchasing policy's prose is not in front of it, only its sections.
    assert "A purchase request names a part" not in prompt and "- purchasing-policy: " in prompt
    # A process whose SOP it is not trained on: it is told so, and to escalate.
    ap = cog_context(ctx_for(hub, "ap-reviewer"), hub.kernel.get(ncr["locator"]))
    assert "You are NOT trained on ncr-sop" in ap and "escalate" in ap


def test_a_cog_reads_its_procedures_and_looks_up_what_its_roles_may_read(released):
    t = tools(released, "quality-assistant")
    routing = t.execute("read_sop_section", {"name": "ncr-sop", "heading": "routing"})
    assert routing["section"] == "Routing" and "At Disposition the nonconformance" in routing["text"] and "## Process" not in routing["text"]
    assert routing["standing"].startswith("trained") and "Duties" in t.execute("read_sop_section", {"name": "ncr-sop"})["sections"]
    looked = t.execute("read_sop_section", {"name": "audit-sop", "heading": "Permissions"})
    assert looked["standing"].startswith("reference") and "Internal auditors may create audits" in looked["text"], "the knowledge base"
    ap = tools(released, "ap-reviewer", requester="dave")
    assert "you may not read 'ncr-sop'" in ap.execute("read_sop_section", {"name": "ncr-sop"})["error"], "only what its roles may read"
    assert "has no section" in t.execute("read_sop_section", {"name": "ncr-sop", "heading": "Nowhere"})["error"]
    assert t.actions == 0, "a look tool"


# ---------------------------------------------------------------- new members and the IT SOP
def test_the_it_and_management_cogs_and_the_it_sop(released):
    b = bundle(released)
    assert b["members"]["it-admin"]["roles"] == ["system-administrator"] and b["members"]["management"]["roles"] == ["management-reviewer"]
    assert "system-administrator" in b["members"]["bob"]["roles"] and b["members"]["organization"]["roles"] == ["policy-reader"]
    assert b["cogs"]["it-admin"]["model"] == b["cogs"]["management"]["model"] == "ollama/qwen3:14b"
    assert b["cogs"]["it-admin"]["persona"] == "executor" and b["cogs"]["management"]["persona"] == "advisor"
    assert b["scopes"]["it"]["compiles"] == ["it-sop"]
    for cog in ("it-admin", "management"):
        for obj, folder in (("document", "Policy"), ("change-order", "Quality/Changes"), ("document", "Quality/Controlled Documents")):
            assert not authz.check(b, cog, "approve", obj, folder)["allowed"], f"{cog} approves no policy"
        assert not [c for c in b["approval_criteria"] if c["approver"] == f"cog:{cog}"]
    assert authz.check(b, "it-admin", "create", "change-order", "Quality/Changes")["allowed"]
    assert authz.check(b, "management", "comment", "change-order", "Quality/Changes")["allowed"]
    assert authz.check(b, "bob", "approve", "document", "Policy")["allowed"], "the cogs' denies name them, not bob's role"
    sod = next(s for s in b["sod"] if s.get("only"))
    assert sod["object"] == "change-order" and sod["roles"] == ["made-a-direct-configuration-change", "approver"]
    assert b["thresholds"]["restore-test-interval"]["value"] == 90
    due = dict((c, d) for c, d in due_duties(released, today="2026-11-07"))
    assert due["it-admin"].startswith("Every week the it-admin cog reviews the open change orders")
    assert due["management"].startswith("Every month the management cog summarises")


def test_duty_and_permission_shapes():
    rules, notes = extract("## Duties\nEvery week the it-admin cog checks the backups.\nEvery fortnight the it-admin cog naps.\n"
                           "## Permissions\nThe stores cog may move stock items in Inventory/Stock when a storekeeper confirms.\n"
                           "Staff may comment on anything in Tasks.\n", scope="department")
    duty = next(r for r in rules if r["kind"] == "duty")
    assert duty["cog"] == "it-admin" and duty["every"] == "week" and duty["task"] == "checks the backups."
    assert any(n.get("severity") == "error" and "a duty reads" in n["note"] for n in notes)
    perm = [r for r in rules if r["kind"] == "permission"]
    assert perm[0]["subject"] == "cog:stores" and perm[0]["effect"] == "propose" and perm[0]["confirmer"] == "storekeeper"
    assert perm[1] == {**perm[1], "subject": "role:staff", "action": "comment", "object": "*", "resource": "folder:Tasks", "effect": "allow"}
    from hubproto.settings import sentence

    assert sentence(perm[0]) == "The stores cog may move stock items in Inventory/Stock when a storekeeper confirms."
    assert sentence(perm[1]) == "Staff may comment on anything in Tasks."
    assert sentence(duty) == "Every week the it-admin cog checks the backups."
