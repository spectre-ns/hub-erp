"""The configuration in force as JSON, and a JSON document proposed on a change order (issue #33).

``hubproto config export`` and ``GET /api/config`` write everything in force as one deterministic document;
``hubproto config propose`` and a change order's upload put its difference from what is in force on a change order
through the paths that already exist, so an edited export gives the same change order as the same edit made in Settings
(and in the forms editor, on the record types page). Nothing comes into force before the change order is released. The
link vocabulary and the retention classes are under change control the way record types are. No model is called."""

import copy
import io
import json
import shutil
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from conftest import ROOT, release_all
from hubproto import changecontrol as cc
from hubproto import cli, closure, configdocs, configjson, formlayout, recordtypes, sopdocs
from hubproto.app import Hub
from hubproto.kernel import KernelGuardError
from hubproto.rules import KINDS, rule_key
from test_change_control import released as release_co

SCHEMA_FILE = ROOT / "docs" / "design" / "config-json.schema.json"


@pytest.fixture
def config_dir(tmp_path, monkeypatch) -> Path:
    cfg = tmp_path / "config"
    shutil.copytree(ROOT / "config", cfg)
    monkeypatch.setattr("hubproto.kernel.CONFIG_DIR", cfg)
    return cfg


def converted(root: Path) -> Hub:
    shutil.copytree(ROOT / "frames", root / "frames")
    h = Hub(root)
    h.kernel.seed()
    release_all(h)
    sopdocs.convert(h, by="carol")
    return h


@pytest.fixture
def hub(tmp_path, config_dir) -> Hub:
    return converted(tmp_path / "a")


@pytest.fixture
def twin(tmp_path, config_dir) -> Hub:
    """A second hub made the same way: the same edit is made on screen in one and as JSON in the other."""
    return converted(tmp_path / "b")


def exported(hub) -> dict:
    return configjson.loads(configjson.dumps(configjson.export(hub)))


def entry(doc, section, **match) -> dict:
    return next(e for e in doc["policy"][section] if all(e.get(k) == v for k, v in match.items()))


def cos(hub) -> list[dict]:
    return hub.kernel.query(type_="change-order")


def without_times(obj):
    """A change order's carried changes without the moments they were made (two hubs never make them at once)."""
    if isinstance(obj, dict):
        return {k: without_times(v) for k, v in obj.items() if k not in ("at", "updated_at")}
    if isinstance(obj, list):
        return [without_times(v) for v in obj]
    return obj


def run_cli(root, *argv) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cli.main(["--root", str(root), *argv])
        except SystemExit as e:
            return (e.code if isinstance(e.code, int) else 1), out.getvalue() + err.getvalue()
    return rc, out.getvalue() + err.getvalue()


# ------------------------------------------------------------------ export
def test_the_export_holds_everything_in_force_deterministically(hub):
    doc = configjson.export(hub)
    meta = hub.releases.current()
    assert doc["schema"] == "hub-erp/config/1" and doc["release"]["digest"] == meta["digest"] and doc["release"]["version"] == 1
    assert set(doc) == set(configjson.TOP)
    assert set(configjson.SECTIONS.values()) == set(KINDS), "every kind of rule has a section"
    assert set(doc["policy"]) == set(configjson.SECTIONS)
    for section in ("roles", "members", "groups", "folders", "permissions", "thresholds", "routing", "due_dates", "approval_criteria",
                    "segregation_of_duties", "duties", "cogs", "personas", "processes"):
        assert doc["policy"][section], f"{section} is exported"
    rules = hub.settings_rules()
    assert {rule_key({**e, "kind": configjson.SECTIONS[s]}) for s, es in doc["policy"].items() for e in es} == {rule_key(r) for r in rules}
    assert all("source" not in e and "kind" not in e for es in doc["policy"].values() for e in es)
    assert doc["record_types"] == recordtypes.in_force(hub)["types"] and set(doc["forms"]) == set(doc["record_types"])
    assert doc["forms"]["ncr"]["sections"][0]["fields"], "the layout in force (the base layout until one is released)"
    assert doc["links"] == hub.kernel.link_vocab and doc["retention"] == hub.kernel.retention
    assert doc["versions"] == {"record_types": 1, "links": 1, "retention": 1, "forms": {}}
    text = configjson.dumps(doc)
    assert text == configjson.dumps(configjson.export(hub)) == configjson.dumps(configjson.export(Hub(hub.root))), "the same bytes every time"
    assert text.index('"forms"') < text.index('"links"') < text.index('"policy"'), "sorted keys"
    for section, es in doc["policy"].items():
        assert es == sorted(es, key=lambda e: json.dumps(e, sort_keys=True, separators=(",", ":"), ensure_ascii=False)), f"{section} in a stable order"


def test_the_described_format_is_the_one_exported(hub):
    schema = json.loads(SCHEMA_FILE.read_text())
    doc = configjson.export(hub)
    assert set(schema["properties"]) == set(configjson.TOP) and schema["properties"]["schema"]["const"] == configjson.SCHEMA
    assert set(schema["properties"]["policy"]["properties"]) == set(configjson.SECTIONS)
    assert set(schema["required"]) <= set(doc) and set(schema["properties"]["release"]["properties"]) == set(doc["release"])
    assert set(schema["properties"]["versions"]["properties"]) == set(doc["versions"])
    evidence = schema["properties"]["retention"]["additionalProperties"]["properties"]["evidence"]["items"]["enum"]
    assert evidence == list(configdocs.EVIDENCE)
    assert "config-json.schema.json" in (ROOT / "docs" / "design" / "config-json.md").read_text()


def test_the_command_line_exports_to_a_file_or_standard_output(hub, tmp_path):
    rc, out = run_cli(hub.root, "config", "export")
    assert rc == 0 and json.loads(out) == configjson.export(hub)
    rc, out = run_cli(hub.root, "config", "export", "--out", str(tmp_path / "c.json"))
    assert rc == 0 and "wrote the configuration in force to" in out
    assert (tmp_path / "c.json").read_text() == configjson.dumps(configjson.export(hub))


# ------------------------------------------------------------------ propose: no change, and the same change order as on screen
def test_an_export_proposed_unchanged_changes_nothing(hub, tmp_path):
    out = configjson.propose(hub, exported(hub), by="bob")
    assert out["changed"] is False and out["change_order"] is None and cos(hub) == [], "no change order is started for nothing"
    path = tmp_path / "c.json"
    path.write_text(configjson.dumps(configjson.export(hub)))
    rc, text = run_cli(hub.root, "config", "propose", str(path), "--as", "bob")
    assert rc == 0 and "c.json says what is in force: nothing to propose" in text and cos(hub) == []
    partial = {k: exported(hub)[k] for k in ("schema", "hub", "release", "versions")}
    assert configjson.propose(hub, partial, by="bob")["changed"] is False, "a part left out is left as it is"


def test_an_edited_export_gives_the_same_change_order_as_settings(hub, twin):
    # On screen, in one hub: bob raises the AP cog's limit and lowers the purchase approval threshold in Settings.
    rules = hub.settings_draft("bob")["rules"]
    ap = next(r for r in rules if r["kind"] == "approval_criteria" and r["approver"] == "cog:ap-reviewer" and r["process"] == "purchase-to-pay")
    hub.settings_edit("bob", {"action": "replace", "key": rule_key(ap), "rule": {**{k: v for k, v in ap.items() if k != "source"}, "max_amount": 3000.0}})
    th = next(r for r in hub.settings_draft("bob")["rules"] if r["kind"] == "threshold" and r["name"] == "purchase-approval-threshold")
    hub.settings_edit("bob", {"action": "replace", "key": rule_key(th), "rule": {**{k: v for k, v in th.items() if k != "source"}, "value": 8000.0}})
    on_screen = hub.kernel.get(hub.settings_propose("bob")["locator"])
    # As JSON, in its twin: the same two edits in an export.
    doc = exported(twin)
    entry(doc, "approval_criteria", approver="cog:ap-reviewer", process="purchase-to-pay")["max_amount"] = 3000.0
    entry(doc, "thresholds", name="purchase-approval-threshold")["value"] = 8000.0
    out = configjson.propose(twin, doc, by="bob")
    as_json = twin.kernel.get(out["locator"])
    assert out["changed"] and out["change_order"] == on_screen["number"] and len(out["summary"]) == 2
    assert without_times(as_json["fields"]["direct_changes"]) == without_times(on_screen["fields"]["direct_changes"])
    assert as_json["fields"]["from_settings"] and as_json["fields"]["requested_by"] == "bob" and as_json["fields"]["title"] == on_screen["fields"]["title"]
    assert cc.contributors(twin, as_json["number"]) == cc.contributors(hub, on_screen["number"]) == ["bob"]
    assert [d["widening"] for d in as_json["fields"]["direct_changes"]] == [d["widening"] for d in on_screen["fields"]["direct_changes"]]
    # The review round writes the prose back the same way in both: the SOPs carry the same new sentences.
    for h, co in ((hub, on_screen), (twin, as_json)):
        cc.request_review(h, co["number"], "bob")
    texts = [{c["name"]: sopdocs.frame_text(h, c["draft"]) for c in __import__("hubproto.eco", fromlist=["x"]).carried(h, h.kernel.get(co["locator"]))}
             for h, co in ((hub, on_screen), (twin, as_json))]
    assert set(texts[0]) == set(texts[1]) == {"finance-policy", "purchasing-policy"}
    assert all(texts[0][n].split("\n---\n", 1)[1] == texts[1][n].split("\n---\n", 1)[1] for n in texts[0]), "the same prose"
    assert "under 3,000 USD" in texts[1]["finance-policy"]
    same = [{k: i.get(k) for k in ("id", "op", "reason", "widening")} for i in cc.delta(hub, on_screen["number"]) if i["op"] != "text"]
    assert same and same == [{k: i.get(k) for k in ("id", "op", "reason", "widening")} for i in cc.delta(twin, as_json["number"]) if i["op"] != "text"]


def test_members_record_types_forms_and_links_match_their_own_pages(hub, twin):
    # On screen: a new person (the People page), a record-type field (the record types page), a form layout (the forms
    # editor) and a relation (carried on the change order as the JSON upload carries it).
    co = hub.propose_new_person(name="erin", roles=["staff"], by="carol")
    co = hub.kernel.get(co["locator"])
    recordtypes.carry(hub, co["number"], [{"op": "add_field", "type": "ncr", "field": "batch", "spec": "text"}], by="carol")
    lay = formlayout.active_layout(hub, "ncr")
    lay["sections"][0]["title"] = "The nonconformance"
    formlayout.save_draft(hub, "carol", "ncr", lay)
    formlayout.carry(hub, co["number"], "carol", "ncr")
    configdocs.carry(hub, co["number"], [{"doc": "links", "op": "set", "key": "inspires", "value": "inspired-by"}], by="carol")
    on_screen = hub.kernel.get(co["locator"])
    # As JSON: the same edits in one document, on carol's settings change order.
    doc = exported(twin)
    doc["policy"]["members"].append({"principal": "erin", "principal_type": "person", "roles": ["staff"]})
    doc["record_types"]["ncr"]["fields"]["batch"] = "text"
    doc["forms"]["ncr"]["sections"][0]["title"] = "The nonconformance"
    doc["links"]["inspires"] = "inspired-by"
    as_json = twin.kernel.get(configjson.propose(twin, doc, by="carol")["locator"])
    f, g = on_screen["fields"], as_json["fields"]
    assert without_times(g["direct_changes"]) == without_times(f["direct_changes"])
    drop = ("reason", "summary")
    assert [{k: v for k, v in c.items() if k not in drop} for c in without_times(g["record_type_changes"])] == \
           [{k: v for k, v in c.items() if k not in drop} for c in without_times(f["record_type_changes"])]
    assert without_times(g["form_changes"]) == without_times(f["form_changes"])
    assert [{k: v for k, v in c.items() if k not in drop} for c in without_times(g["config_changes"])] == \
           [{k: v for k, v in c.items() if k not in drop} for c in without_times(f["config_changes"])]


# ------------------------------------------------------------------ each kind of section, and nothing before release
def test_each_kind_of_section_is_carried_and_nothing_changes_before_release(hub):
    before = configjson.dumps(configjson.export(hub))
    doc = exported(hub)
    p = doc["policy"]
    p["roles"].append({"name": "calibration-technician"})
    p["members"].append({"principal": "erin", "principal_type": "person", "roles": ["staff", "calibration-technician"]})
    p["folders"].append({"path": "Quality/Calibration/Lab"})
    p["permissions"].append({"subject": "role:calibration-technician", "action": "read", "object": "*", "resource": "folder:Quality/Calibration/Lab",
                             "effect": "allow"})
    entry(doc, "thresholds", name="purchase-approval-threshold")["value"] = 9000.0
    ctl = entry(doc, "permissions", subject="role:controller", action="read", resource="folder:Finance")
    p["permissions"].remove(ctl)
    sla = p["due_dates"][0]
    sla["days"] = int(sla["days"]) + 1
    cog = entry(doc, "cogs", name="ap-reviewer")
    cog["model"] = "ollama/qwen3:8b"
    persona = p["personas"][0]
    persona["tone"] = "informal" if persona.get("tone") != "informal" else "formal"
    doc["record_types"]["ncr"]["fields"]["batch"] = "text"
    doc["forms"]["ncr"]["sections"][0]["title"] = "The nonconformance"
    doc["links"]["inspires"] = "inspired-by"
    doc["retention"]["clinical"] = {"years": 15, "evidence": ["release_digest", "decisions", "documents"]}
    doc["record_types"]["complaint"]["retention"] = "clinical"  # a record type kept under a class the same document adds
    out = configjson.propose(hub, doc, by="carol", reason="calibration lab")
    co = hub.kernel.get(out["locator"])
    kinds = {(it.get("rule") or it.get("was"))["kind"] for it in co["fields"]["direct_changes"]}
    assert kinds == {"role", "member", "folder", "permission", "threshold", "sla", "cog", "persona"}
    assert any(it["op"] == "remove" and it["rule"]["subject"] == "role:controller" for it in co["fields"]["direct_changes"])
    assert [(c["op"], c["type"]) for c in co["fields"]["record_type_changes"]] == [("set_retention", "complaint"), ("add_field", "ncr")]
    assert set(co["fields"]["form_changes"]) == {"ncr"}
    assert [(c["doc"], c["op"], c["key"]) for c in co["fields"]["config_changes"]] == [("links", "set", "inspires"), ("retention", "set", "clinical")]
    assert recordtypes.carried_reasons(hub, co) == [] and configdocs.carried_reasons(hub, co) == [], "it checks as one change order"
    assert out["carried"]["forms"] == ["ncr"] and len(out["summary"]) == len(co["fields"]["direct_changes"]) + 2 + 1 + 2
    # Nothing is in force until the change order is released.
    assert configjson.dumps(configjson.export(hub)) == before
    again = Hub(hub.root)
    assert configjson.dumps(configjson.export(again)) == before, "nor in another process"
    assert "inspires" not in again.kernel.link_vocab and "clinical" not in again.kernel.retention and "batch" not in again.kernel.types["ncr"]["fields"]


def test_a_cog_profile_change_is_written_into_the_prose_by_the_round(hub):
    doc = exported(hub)
    entry(doc, "cogs", name="ap-reviewer")["model"] = "ollama/qwen3:8b"
    co = hub.kernel.get(configjson.propose(hub, doc, by="bob")["locator"])
    cc.request_review(hub, co["number"], "bob")
    co = hub.kernel.get(co["locator"])
    [it] = co["fields"]["direct_changes"]
    assert it["rule"]["kind"] == "cog" and it["round_trip"]["ok"], "the cog's prose reads back as the change"
    from hubproto import eco

    texts = [sopdocs.frame_text(hub, c["draft"]) for c in eco.carried(hub, co)]
    assert any("Model: ollama/qwen3:8b" in t for t in texts), "SOP prose stays the source of truth"


# ------------------------------------------------------------------ refusals: a sentence, and nothing carried
def _stale(doc, hub):
    doc["release"]["digest"] = "0" * 64


def _rt_version(doc, hub):
    doc["versions"]["record_types"] = 7


def _unknown_part(doc, hub):
    doc["scripts"] = []


def _unknown_section(doc, hub):
    doc["policy"]["budgets"] = []


def _unknown_field(doc, hub):
    entry(doc, "thresholds", name="purchase-approval-threshold")["owner"] = "bob"


def _wrong_kind(doc, hub):
    doc["policy"]["roles"].append({"kind": "member", "name": "x"})


def _bad_effect(doc, hub):
    doc["policy"]["permissions"].append({"subject": "role:staff", "action": "read", "object": "*", "resource": "folder:Finance", "effect": "maybe"})


def _responsibility(doc, hub):
    doc["policy"]["responsibilities"].pop()


def _remove_field(doc, hub):
    doc["record_types"]["ncr"]["fields"].pop("title")


def _retype(doc, hub):
    doc["record_types"]["ncr"]["fields"]["title"] = "int"


def _drop_type(doc, hub):
    doc["record_types"].pop("ncr")


def _shipped_link(doc, hub):
    doc["links"].pop("concerns")


def _bad_inverse(doc, hub):
    doc["links"]["inspires"] = "Inspired By!"


def _standard(doc, hub):
    doc["retention"].pop("standard")


def _used_class(doc, hub):
    doc["retention"].pop("quality")


def _evidence(doc, hub):
    doc["retention"]["clinical"] = {"years": 15, "evidence": ["vibes"]}


def _unknown_form(doc, hub):
    doc["forms"]["gizmo"] = {"sections": []}


def _bad_form(doc, hub):
    doc["forms"]["ncr"]["sections"][0]["fields"].append({"field": "no_such_field"})


def _control(doc, hub):
    doc["policy"]["roles"].append({"name": "evil\x1b[31m"})


def _no_class(doc, hub):
    doc["record_types"]["complaint"]["retention"] = "clinical"


@pytest.mark.parametrize("edit,words", [
    (_stale, "the document was exported from release 000000000000, and policy version 1 is in force now: export this hub's"),
    (_rt_version, "the record types changed since the document was exported (version 7 then, 1 now): export this hub's"),
    (_unknown_part, "the document has no part scripts"),
    (_unknown_section, "policy has no section budgets"),
    (_unknown_field, "a threshold has no field owner"),
    (_wrong_kind, "says it is a member, in the roles section"),
    (_bad_effect, "permission: effect must be allow, deny or propose"),
    (_responsibility, "policy.responsibilities differs from what is in force: the Roles and responsibilities rows are each SOP's table"),
    (_remove_field, "the document has no field ncr.title: a field is not removed this way"),
    (_retype, "ncr.title is int in the document (in force: text): a field's type never changes"),
    (_drop_type, "the document has no record type ncr: a record type is archived, never dropped"),
    (_shipped_link, "concerns is never removed"),
    (_bad_inverse, "the inverse is a lowercase name"),
    (_standard, "standard is never removed"),
    (_used_class, "quality is never removed while a record type is kept under it"),
    (_evidence, "the hub does not know the evidence vibes"),
    (_unknown_form, "forms.gizmo: there is no record type gizmo in force"),
    (_bad_form, "forms.ncr:"),
    (_control, "holds control characters"),
    (_no_class, "no retention class 'clinical'"),
])
def test_what_cannot_be_carried_is_refused_with_a_sentence_and_nothing_is_carried(hub, edit, words):
    doc = exported(hub)
    entry(doc, "thresholds", name="purchase-approval-threshold")["value"] = 9000.0  # a change that could be carried on its own
    edit(doc, hub)
    with pytest.raises(configjson.Refused) as e:
        configjson.propose(hub, doc, by="bob")
    assert any(words in r for r in e.value.reasons), e.value.reasons
    assert cos(hub) == [], "nothing carried, no change order started"


def test_not_a_configuration_document_is_refused(hub, tmp_path):
    for text, words in (("{not json", "the file is not JSON"), ("[1, 2]", "its top level is an object"), ('{"a": NaN}', "NaN is not a number the hub reads")):
        with pytest.raises(configjson.Refused, match=words):
            configjson.loads(text)
    with pytest.raises(configjson.Refused, match="not a hub configuration document"):
        configjson.propose(hub, {"schema": "other/1"}, by="bob")
    with pytest.raises(configjson.Refused, match="say which release the document was exported from"):
        configjson.propose(hub, {"schema": configjson.SCHEMA}, by="bob")
    with pytest.raises(configjson.Refused, match="a person proposes a configuration change"):
        configjson.propose(hub, exported(hub), by="qa-manager")
    (tmp_path / "bad.json").write_text("{not json")
    rc, out = run_cli(hub.root, "config", "propose", str(tmp_path / "bad.json"), "--as", "bob")
    assert rc == 1 and "bad.json was not proposed; nothing was carried" in out and "the file is not JSON" in out
    doc = exported(hub)
    doc["links"].pop("concerns")
    (tmp_path / "refused.json").write_text(json.dumps(doc))
    rc, out = run_cli(hub.root, "config", "propose", str(tmp_path / "refused.json"), "--as", "bob")
    assert rc == 1 and "refused.json was not proposed; nothing was carried" in out and "concerns is never removed" in out


def test_carrying_that_stops_part_way_puts_the_change_order_back(hub, monkeypatch):
    co = hub.kernel.create("change-order", "Quality/Changes", {"title": "Tune", "description": "x", "requested_by": "bob"}, by="bob")
    doc = exported(hub)
    entry(doc, "thresholds", name="purchase-approval-threshold")["value"] = 9000.0
    doc["record_types"]["ncr"]["fields"]["batch"] = "text"

    def stop(*a, **k):
        raise ValueError("the record types store is not writable")
    monkeypatch.setattr(recordtypes, "carry", stop)
    with pytest.raises(configjson.Refused, match="nothing was carried: the record types store is not writable"):
        configjson.propose(hub, doc, by="bob", co_ref=co["number"])
    after = hub.kernel.get(co["locator"])["fields"]
    assert not after.get("direct_changes") and not after.get("record_type_changes"), "the rule change carried first was taken off again"


def test_a_change_order_that_is_not_theirs_or_is_fixed_is_refused(hub):
    co = hub.kernel.create("change-order", "Quality/Changes", {"title": "Mine", "description": "x", "requested_by": "carol"}, by="carol")
    doc = exported(hub)
    entry(doc, "thresholds", name="purchase-approval-threshold")["value"] = 9000.0
    with pytest.raises(configjson.Refused, match="carol's change order"):
        configjson.propose(hub, doc, by="dave", co_ref=co["number"])
    out = configjson.propose(hub, doc, by="carol", co_ref=co["number"])
    assert out["change_order"] == co["number"] and not hub.kernel.get(co["locator"])["fields"].get("from_settings")


# ------------------------------------------------------------------ links and retention under change control
def test_links_and_retention_come_into_force_only_through_a_released_change_order(hub):
    co = hub.kernel.create("change-order", "Quality/Changes", {"title": "Calibration", "description": "a new class and relation",
                                                               "requested_by": "alice"}, by="alice")
    doc = exported(hub)
    doc["links"]["inspires"] = "inspired-by"
    doc["retention"]["clinical"] = {"years": 15, "evidence": ["release_digest", "decisions", "documents"]}
    doc["record_types"]["complaint"]["retention"] = "clinical"
    configjson.propose(hub, doc, by="alice", co_ref=co["number"])
    co = hub.kernel.get(co["locator"])
    delta = configdocs.delta_items(hub, co)
    assert [d["id"] for d in delta] == ["config:cfg-1", "config:cfg-2"] and all(not d["errors"] for d in delta)
    assert {"links", "retention"} <= cc.touches(hub, co["number"])["kind"]
    from hubproto import validation

    assert {i["kind"] for i in validation.items_of(hub, co)} >= {"links", "retention", "record_type"}
    ncr = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "a", "description": "b"}, by="carol")
    cap = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "c", "description": "d"}, by="carol")
    with pytest.raises(KernelGuardError, match="unknown link relation 'inspires'"):
        hub.kernel.link(ncr["locator"], "inspires", cap["locator"], by="carol")
    with pytest.raises(closure.ChangeOrderRequired, match="only once it is approved"):
        configdocs.activate(hub, co, hub.releases.current()["digest"])
    other = Hub(hub.root)  # another process holding the hub open (the shell)
    co = release_co(hub, co)
    [job] = [j for j in hub.automation.of(co["locator"]) if j["automation"] == "release-change-order"]
    step = next(s for s in job["steps"] if s["name"] == "Apply the link and retention changes")
    assert job["status"] == "success" and step["status"] == "done" and "link vocabulary v2" in step["detail"], job
    assert hub.kernel.link_vocab["inspires"] == "inspired-by" and hub.kernel.retention["clinical"]["years"] == 15
    assert hub.kernel.types["complaint"]["retention"] == "clinical", "the record type is kept under the class released with it"
    assert other.kernel.link_vocab.get("inspires") == "inspired-by" and Hub(hub.root).kernel.retention.get("clinical"), "every process sees it"
    hub.kernel.link(ncr["locator"], "inspires", cap["locator"], by="carol")
    [entry_] = [e for e in configdocs.store(hub, "links").history() if e.get("released_with") == co["number"]]
    assert entry_["version"] == 2 and entry_["by"] == f"change-order:{co['number']}"
    assert configdocs.activate(hub, co, hub.releases.current()["digest"])[0].endswith(f"(already in force with {co['number']})")
    doc = configjson.export(hub)
    assert doc["versions"]["links"] == 2 and doc["links"]["inspires"] == "inspired-by"
    assert hub.kernel.config_digests["links.json"] == configdocs.in_force(hub, "links")["digest"]
    # Now a link uses it: it is never removed.
    doc = exported(hub)
    doc["links"].pop("inspires")
    with pytest.raises(configjson.Refused, match="inspires is never removed: 1 link"):
        configjson.propose(hub, doc, by="carol")


def test_a_file_on_disk_is_not_used_once_the_hub_keeps_them(hub, config_dir):
    data = json.loads((config_dir / "retention.json").read_text())
    data["standard"]["years"] = 0
    (config_dir / "retention.json").write_text(json.dumps(data))
    restarted = Hub(hub.root)
    assert restarted.kernel.retention["standard"]["years"] == 1, "the retention classes in force, not the file"
    d = configdocs.drift(restarted, "retention")
    assert d and d["in_file"] == ["standard: 0 year(s), evidence release_digest, manifest_digest, decisions (not 1 year(s), evidence "
                                  "release_digest, manifest_digest, decisions)"]
    rc, out = run_cli(hub.root, "status", "--check")
    assert rc == 1 and "config/retention.json differs from the retention classes in force (v1); it is not used" in out


def test_before_the_conversion_the_files_are_used(tmp_path, config_dir):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    h = Hub(tmp_path)
    h.kernel.seed()
    assert configdocs.in_force(h, "links")["version"] == 0 and not configdocs.governed(h, "links")
    data = json.loads((config_dir / "links.json").read_text())
    data["inspires"] = "inspired-by"
    (config_dir / "links.json").write_text(json.dumps(data))
    assert Hub(tmp_path).kernel.link_vocab["inspires"] == "inspired-by"


def test_a_hub_converted_before_this_keeps_the_files_as_they_are_at_its_next_start(hub):
    shutil.rmtree(hub.root / "state" / "config")  # an existing hub, upgraded
    restarted = Hub(hub.root)
    for doc in configdocs.DOCS:
        entry_ = configdocs.store(restarted, doc).active()
        assert entry_ and entry_["version"] == 1 and entry_["source"].startswith("the file as of release v1")
    assert restarted.kernel.link_vocab == configdocs.file_values(restarted, "links")


def test_the_backup_keeps_the_link_vocabulary_and_retention_classes_in_force(hub, tmp_path):
    rc, _ = run_cli(hub.root, "backup", str(tmp_path / "bk"))
    assert rc == 0 and (tmp_path / "bk" / "config" / "links" / "active.json").exists()
    assert (tmp_path / "bk" / "config" / "retention" / "active.json").exists()


# ------------------------------------------------------------------ the shell: GET /api/config and the upload
@pytest.fixture
def shell(hub):
    from hubproto.shell import serve_in_thread

    server = serve_in_thread(hub, "127.0.0.1", 0)
    yield {"hub": hub, "base": f"http://127.0.0.1:{server.server_address[1]}"}
    server.shutdown()
    server.server_close()


def test_the_api_gives_the_configuration_to_those_allowed_to_read_the_policy(shell):
    from test_shell_views import get

    hub = shell["hub"]
    status, text = get(shell, "/api/config", **{"as": "carol"})
    assert status == 200 and text == configjson.dumps(configjson.export(hub))
    status, text = get(shell, "/api/config")
    assert status == 401 and "Say who you are" in json.loads(text)["error"]
    status, text = get(shell, "/api/config", **{"as": "mallory"})
    assert status == 403 and "not a member" in json.loads(text)["error"]
    from hubproto import authz

    assert not authz.check(hub.current_bundle(), "ap-reviewer", "read", "*", "Policy")["allowed"]
    status, text = get(shell, "/api/config", **{"as": "ap-reviewer"})
    assert status == 403 and "You cannot read the policy (Policy)" in json.loads(text)["error"] and "policy" not in json.loads(text)


def test_a_configuration_file_is_carried_from_a_change_order_page(shell):
    from test_change_order_pages import _post_file, co_page, new_co
    from test_shell_views import main_of, plain

    hub = shell["hub"]
    co = new_co(shell, who="alice", title="Tune the threshold")
    page = co_page(shell, co, "alice")
    assert "Propose a configuration file (JSON)" in page and "/api/config?as=alice" in page
    assert "Propose a configuration file" not in co_page(shell, co, "dave"), "only who may change what it carries"
    doc = exported(hub)
    entry(doc, "thresholds", name="purchase-approval-threshold")["value"] = 9000.0
    doc["links"]["inspires"] = "inspired-by"
    fields = {"as": "alice", "co": co["locator"], "form": "config-upload", "reason": "cheaper approvals"}
    status, url, body = _post_file(shell, "/co-config-upload", fields, [("file", "tune.json", json.dumps(doc).encode())])
    assert status == 200 and "Carried on " + co["number"] + " from tune.json: 2 changes" in plain(main_of(body)), plain(main_of(body))[:400]
    got = hub.kernel.get(co["locator"])["fields"]
    assert len(got["direct_changes"]) == 1 and [c["key"] for c in got["config_changes"]] == ["inspires"]
    page = co_page(shell, co, "alice")
    assert "link vocabulary: inspires (inverse inspired-by)" in page
    # A file that cannot be carried: the sentence on the page, nothing more carried.
    doc["links"].pop("concerns")
    status, url, body = _post_file(shell, "/co-config-upload", {**fields, "once": "x1"}, [("file", "bad.json", json.dumps(doc).encode())])
    assert status == 400 and "bad.json was not carried; nothing was" in plain(main_of(body)) and "concerns is never removed" in plain(main_of(body))
    assert hub.kernel.get(co["locator"])["fields"]["config_changes"] == got["config_changes"]
    status, url, body = _post_file(shell, "/co-cfg-uncarry", {"as": "alice", "co": co["locator"], "form": "cfg-cfg-1", "change": "cfg-1"}, [])
    assert status == 200 and not hub.kernel.get(co["locator"])["fields"]["config_changes"]


def test_the_auditor_cog_and_its_duty_are_in_the_export_and_carried_like_any_rule(hub):
    doc = exported(hub)
    assert {"principal": "auditor", "principal_type": "cog", "roles": ["internal-auditor"]} in doc["policy"]["members"]
    assert {"name": "internal-auditor"} in doc["policy"]["roles"] and entry(doc, "cogs", name="auditor")
    assert entry(doc, "permissions", subject="role:internal-auditor", action="read", resource="folder:Policy")["effect"] == "allow"
    duty = entry(doc, "duties", cog="auditor")
    assert duty["every"] == "month" and "finding_decisions" in doc["record_types"]["audit"]["fields"]
    duty["every"] = "week"
    co = hub.kernel.get(configjson.propose(hub, doc, by="bob")["locator"])
    [it] = co["fields"]["direct_changes"]
    assert it["op"] == "change" and it["rule"]["kind"] == "duty" and it["rule"]["every"] == "week" and it["was"]["every"] == "month"


def test_status_still_reports_the_audit_clauses_file_which_is_not_under_change_control(hub, config_dir):
    path = config_dir / "standard-clauses.json"
    data = json.loads(path.read_text())
    data["default"] = "iso13485"
    path.write_text(json.dumps(data))
    rc, out = run_cli(hub.root, "status", "--check")
    assert rc == 1 and "config/standard-clauses.json differs from what the release in force, v1 (" in out and "it is used as it is" in out


# ------------------------------------------------------------------ review of #43: findings 1 to 6
def _released_link_co(hub, monkeypatch=None, stop_second_write=False):
    """A change order adding a relation and a retention class, approved and released (optionally with the second
    document's write stopped, as a full disk or a killed process would)."""
    co = hub.kernel.create("change-order", "Quality/Changes", {"title": "Calibration", "description": "a relation and a class",
                                                               "requested_by": "alice"}, by="alice")
    doc = exported(hub)
    doc["links"]["inspires"] = "inspired-by"
    doc["retention"]["clinical"] = {"years": 15, "evidence": ["release_digest", "decisions", "documents"]}
    configjson.propose(hub, doc, by="alice", co_ref=co["number"])
    if stop_second_write:
        real = configdocs.DocStore.set_active
        calls = []

        def flaky(self, entry):
            calls.append(self.root.name)
            if len(calls) == 2:
                raise OSError(28, "No space left on device")
            return real(self, entry)
        monkeypatch.setattr(configdocs.DocStore, "set_active", flaky)
    return release_co(hub, hub.kernel.get(co["locator"]))


def test_finding_1_a_release_stopped_between_the_two_writes_completes_when_retried(hub, monkeypatch):
    from hubproto import automation

    _released_link_co(hub, monkeypatch, stop_second_write=True)  # signed; the release job stops at the step
    co = next(r for r in cos(hub) if r["fields"]["title"] == "Calibration")
    [job] = [j for j in hub.automation.of(co["locator"]) if j["automation"] == "release-change-order"]
    step = next(s for s in job["steps"] if s["name"] == "Apply the link and retention changes")
    assert step["status"] == "failed" and configdocs.in_force(hub, "links")["version"] == 2 and configdocs.in_force(hub, "retention")["version"] == 1
    monkeypatch.undo()
    automation.retry(hub, job["id"], by="carol")
    job = hub.automation.load(job["id"])
    assert job["status"] == "success", [(s["name"], s["status"], s["detail"]) for s in job["steps"]]
    step = next(s for s in job["steps"] if s["name"] == "Apply the link and retention changes")
    assert "link vocabulary v2 (already in force with" in step["detail"] and "retention classes v2:" in step["detail"]
    assert hub.kernel.link_vocab["inspires"] == "inspired-by" and hub.kernel.retention["clinical"]["years"] == 15
    assert configdocs.carried_reasons(hub, hub.kernel.get(co["locator"])) == [], "nothing it already did reads as stale"


@pytest.mark.parametrize("drop", [("versions",), ("links", "forms")])
def test_finding_2_an_old_export_without_its_versions_cannot_undo_what_was_released(hub, drop):
    old = exported(hub)
    lay = formlayout.active_layout(hub, "ncr")
    lay["sections"][0]["title"] = "The nonconformance"
    formlayout.save_draft(hub, "alice", "ncr", lay)
    co = hub.kernel.create("change-order", "Quality/Changes", {"title": "Rel", "description": "x", "requested_by": "alice"}, by="alice")
    formlayout.carry(hub, co["number"], "alice", "ncr")
    configdocs.carry(hub, co["number"], [{"doc": "links", "op": "set", "key": "inspires", "value": "inspired-by"}], by="alice")
    release_co(hub, hub.kernel.get(co["locator"]))
    assert hub.releases.current()["digest"] == old["release"]["digest"], "no new policy release: only the versions say it is old"
    old["retention"]["standard"]["years"] = 2
    if drop == ("versions",):
        del old["versions"]
    else:
        for k in drop:  # just the parts that changed since: the others still match
            del old["versions"][k]
    with pytest.raises(configjson.Refused) as e:
        configjson.propose(hub, old, by="bob")
    assert any(("versions" in r and "missing" in r) or "say which versions" in r or "changed since the document was exported" in r
               for r in e.value.reasons), e.value.reasons
    assert "inspires" in hub.kernel.link_vocab and formlayout.active_layout(hub, "ncr")["sections"][0]["title"] == "The nonconformance"
    assert not [r for r in cos(hub) if r["fields"]["title"] != "Rel"], "nothing carried"


@pytest.mark.parametrize("name,words", [
    ("cаrol", "U+0430 CYRILLIC SMALL LETTER A, which only looks like a letter"),
    ("carol​", "an invisible character (U+200B ZERO WIDTH SPACE)"),
    ("carol‮", "a direction control (U+202E RIGHT-TO-LEFT OVERRIDE)"),
    ("ｃarol", "U+FF43 FULLWIDTH LATIN SMALL LETTER C"),
    ("Carol Smith", "is not a name: lowercase letters, digits and hyphens"),
])
def test_finding_3_look_alike_names_are_refused_as_settings_never_writes_them(hub, name, words):
    doc = exported(hub)
    doc["policy"]["members"].append({"principal": name, "principal_type": "person", "roles": ["policy-owner", "staff"]})
    with pytest.raises(configjson.Refused) as e:
        configjson.propose(hub, doc, by="bob")
    assert any("policy.members.principal" in r and words in r for r in e.value.reasons), e.value.reasons
    assert cos(hub) == []


def test_finding_3_roles_groups_folders_cogs_and_text_are_held_to_it_too(hub):
    doc = exported(hub)
    doc["policy"]["roles"].append({"name": "аuditor"})
    doc["policy"]["groups"].append({"name": "board", "members": ["carol‍"], "roles": []})
    doc["policy"]["folders"].append({"path": "Quality/Cаlibration"})  # Latin and Cyrillic in one folder name
    doc["policy"]["folders"].append({"path": "Quality/ﬁles"})  # a ligature: not in its normal form
    entry(doc, "cogs", name="ap-reviewer")["about"] = "Reviews bills ‮slib sweiveR"
    entry(doc, "permissions", subject="role:controller", action="read", resource="folder:Finance")["subject"] = "role:cоntroller"
    with pytest.raises(configjson.Refused) as e:
        configjson.propose(hub, doc, by="bob")
    text = " | ".join(e.value.reasons)
    for words in ("policy.roles.name", "policy.groups.members[0]", "mixes letters of CYRILLIC and LATIN", "not in its normal form (NFKC",
                  "policy.cogs.about holds a direction control", "policy.permissions.subject"):
        assert words in text, (words, e.value.reasons)


@pytest.mark.parametrize("text,words", [
    ('{"schema": "hub-erp/config/1", "x": 1e400}', "1e400 is not a number the hub reads"),
    ('{"schema": "hub-erp/config/1", "x": -Infinity}', "-Infinity is not a number the hub reads"),
    ('{"schema": "hub-erp/config/1", "x": NaN}', "NaN is not a number the hub reads"),
    ('{"x": 123456789012345678901234567890}', "is not a number the hub reads"),
    ("[" * 100000 + "]" * 100000, "nests objects and lists 100000 deep"),
    ('{"a": ' * 50 + "1" + "}" * 50, ""),  # 50 deep parses; the checks then refuse it as a document
])
def test_finding_4_bad_numbers_and_deep_nesting_are_refused_with_a_sentence(hub, tmp_path, text, words):
    path = tmp_path / "bad.json"
    path.write_text(text)
    rc, out = run_cli(hub.root, "config", "propose", str(path), "--as", "bob")
    assert rc == 1 and "bad.json was not proposed; nothing was carried" in out and words in out and "Traceback" not in out, out[-600:]


def test_finding_4_a_number_out_of_range_in_an_entry_is_refused_not_a_traceback(hub):
    doc = exported(hub)
    entry(doc, "thresholds", name="purchase-approval-threshold")["value"] = float("inf")  # a script handing a dict, not text
    doc["policy"]["due_dates"][0]["days"] = 1e300
    with pytest.raises(configjson.Refused) as e:
        configjson.propose(hub, doc, by="bob")
    assert sum("is not a number the hub reads" in r for r in e.value.reasons) == 2, e.value.reasons


def test_finding_5_a_key_given_twice_is_refused_naming_it(hub, tmp_path):
    with pytest.raises(configjson.Refused, match="the key 'schema' is given twice in one object"):
        configjson.loads('{"schema": "x", "schema": "hub-erp/config/1"}')
    text = configjson.dumps(configjson.export(hub)).replace('"links": {', '"links": {"inspires": "inspired-by", "inspires": "x", ', 1)
    (tmp_path / "dup.json").write_text(text)
    rc, out = run_cli(hub.root, "config", "propose", str(tmp_path / "dup.json"), "--as", "bob")
    assert rc == 1 and "the key 'inspires' is given twice" in out and cos(hub) == []


def test_finding_6_an_export_from_another_hub_is_refused_and_a_restored_hub_keeps_its_identity(hub, twin, tmp_path):
    doc = exported(twin)
    assert doc["release"]["digest"] == exported(hub)["release"]["digest"] and doc["versions"] == exported(hub)["versions"], "twins agree on that"
    assert doc["hub"]["id"] != exported(hub)["hub"]["id"] and len(doc["hub"]["id"]) == 32
    assert (hub.root / "state" / ".secret").read_text().strip() not in configjson.dumps(configjson.export(hub)), "never the secret"
    doc["links"]["inspires"] = "inspired-by"
    with pytest.raises(configjson.Refused, match="the document was exported from another hub"):
        configjson.propose(hub, doc, by="bob")
    del doc["hub"]
    with pytest.raises(configjson.Refused, match=r"say which hub the document was exported from \(hub.id\)"):
        configjson.propose(hub, doc, by="bob")
    # A backup restored elsewhere is the same hub: its exports still propose.
    mine = exported(hub)
    mine["links"]["inspires"] = "inspired-by"
    assert run_cli(hub.root, "backup", str(tmp_path / "bk"))[0] == 0
    target = tmp_path / "restored"
    shutil.copytree(hub.root / "frames", target / "frames")
    assert run_cli(target, "restore", str(tmp_path / "bk"))[0] == 0
    moved = Hub(target)
    assert configjson.hub_id(moved) == mine["hub"]["id"]
    assert configjson.propose(moved, mine, by="bob")["changed"]
