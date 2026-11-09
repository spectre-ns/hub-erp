"""No configuration change without a change order (change control v2, principle 3).

Once the SOPs are documents, every path that would change the configuration in force outside a change order refuses
and names the change-order route: compiling a proposal for release, reviewing or releasing a proposal no change order
staged, editing a Frame, the command line's policy verbs, the shell's proposal actions. Exempt: everything before the
conversion (bootstrapping the first release) and the release automation acting for an approved change order.

Record types are under the same control: the record types in force are the file as of the release in force plus the
record-type changes released change orders carried. A change order carries them, they are checked (destructive ones
refused), they come into force on release, and a file on disk that differs is reported and not used.

Every hub here reads a copy of config/ (``hubproto.kernel.CONFIG_DIR`` is pointed at it), so a test that edits the
record types file never touches the repository's.
"""

import io
import json
import shutil
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from conftest import ROOT, release_all
from hubproto import cli, closure, eco, recordtypes, sopdocs
from hubproto.app import Hub
from hubproto.compile import CompileError
from test_eco import NEW, OLD, approve, write

CO_SENTENCE = "Configuration changes only through a change order: open one under Quality > Change orders"


@pytest.fixture
def config_dir(tmp_path, monkeypatch) -> Path:
    cfg = tmp_path / "config"
    shutil.copytree(ROOT / "config", cfg)
    monkeypatch.setattr("hubproto.kernel.CONFIG_DIR", cfg)
    return cfg


@pytest.fixture
def fresh(tmp_path, config_dir) -> Hub:
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    h = Hub(tmp_path)
    h.kernel.seed()
    return h


@pytest.fixture
def closed(fresh) -> Hub:
    """Released, then the SOPs converted into documents: configuration changes only through change orders."""
    release_all(fresh)
    sopdocs.convert(fresh, by="carol")
    return fresh


def run_cli(root, *argv) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cli.main(["--root", str(root), *argv])
        except SystemExit as e:
            return (e.code if isinstance(e.code, int) else 1), out.getvalue() + err.getvalue()
    return rc, out.getvalue() + err.getvalue()


def new_co(hub, who="carol", title="A change") -> dict:
    return hub.kernel.create("change-order", "Quality/Changes", {"title": title, "description": "x", "requested_by": who}, by=who)


def approved(hub, co) -> dict:
    """The change order as its approval leaves it (the approval Gate's run is tested in test_eco)."""
    return hub.kernel.transition(co["locator"], "approved", by="carol", reason="approved")


def staged_co(hub) -> tuple[dict, dict]:
    """A change order with an approved revision of the finance policy, its Frame staged by the compile-frame automation."""
    hub.kernel.set_fields(sopdocs.in_force(hub.kernel, "finance-policy")["locator"], {"owner": "alice"}, by="setup")
    co = new_co(hub, who="alice", title="Lower the AP cog limit")
    draft = eco.add_sop(hub, co["number"], "finance-policy", by="alice")
    write(hub, draft, OLD, NEW, "alice")
    run = hub.runner.submit("document-control", {"record": {"change_type": "minor"}}, owner="alice", record=draft["locator"])
    approve(hub, run, hub.runner.load(run)["pending"]["assignees"][0])
    co = hub.kernel.get(co["locator"])
    assert co["fields"]["staged_proposal"], co["fields"]
    return co, hub.proposals.load(co["fields"]["staged_proposal"])


def ap_limit(hub) -> float:
    b = hub.current_bundle()
    return next(c for c in b["approval_criteria"] if c["approver"] == "cog:ap-reviewer" and c["process"] == "purchase-to-pay")["max_amount"]


# ------------------------------------------------------------------ exempt: before the conversion
def test_before_the_conversion_the_policy_owners_compile_review_and_release(fresh):
    meta = release_all(fresh)  # bootstrapping the first release
    assert meta["digest"] and not fresh.sop_source()
    draft = fresh.frame_draft("finance-policy", by="alice")  # the Frame-file workflow still works
    p = fresh.compile(overrides={"finance-policy": fresh.draft_text(draft).replace("2,000 USD when", "1,500 USD when")},
                      drafts={"finance-policy": draft["locator"]}, by="alice")
    for who in ("bob", "carol"):
        fresh.review(p["id"], who, approve_all=True, meaning="ok")
    fresh.release(p["id"], "bob")
    assert ap_limit(fresh) == 1500.0
    rt = recordtypes.in_force(fresh)
    assert rt["version"] == 0 and not recordtypes.governed(fresh), "the file is the source of the record types until conversion"
    assert recordtypes.drift(fresh) is None


def test_before_the_conversion_an_edited_record_types_file_is_used(fresh, config_dir):
    release_all(fresh)
    path = config_dir / "record-types.json"
    data = json.loads(path.read_text())
    data["ncr"]["fields"]["batch"] = "text"
    path.write_text(json.dumps(data))
    assert "batch" in Hub(fresh.root).kernel.types["ncr"]["fields"]


# ------------------------------------------------------------------ refused once the SOPs are documents
def test_compiling_a_proposal_for_release_is_refused_and_a_preview_is_not(closed):
    with pytest.raises(closure.ChangeOrderRequired) as e:
        closed.compile()
    assert CO_SENTENCE in str(e.value) and isinstance(e.value, CompileError) and isinstance(e.value, ValueError)
    assert closed.compile(save=False)["changes"] == [], "a preview changes nothing and is allowed"
    assert not [p for p in closed.proposals.list() if p["status"] == "open"]


def test_editing_a_frame_is_refused_with_the_change_order_route(closed):
    with pytest.raises(ValueError) as e:
        closed.frame_draft("finance-policy", by="bob")
    assert CO_SENTENCE in str(e.value) and "compiled from DOC-" in str(e.value)


def test_a_proposal_from_before_the_conversion_goes_stale_and_cannot_be_reviewed_or_released(fresh):
    release_all(fresh)
    draft = fresh.frame_draft("finance-policy", by="alice")
    p = fresh.compile(overrides={"finance-policy": fresh.draft_text(draft).replace("2,000 USD when", "1,500 USD when")},
                      drafts={"finance-policy": draft["locator"]}, by="alice")
    fresh.review(p["id"], "bob", approve_all=True, meaning="ok")
    sopdocs.convert(fresh, by="carol")
    p = fresh.proposals.load(p["id"])
    assert p["status"] == "stale" and CO_SENTENCE in p["stale_reason"]
    with pytest.raises(CompileError) as e:
        fresh.review(p["id"], "carol", approve_all=True)
    assert CO_SENTENCE in str(e.value)
    with pytest.raises(CompileError) as e:
        fresh.release(p["id"], "bob")
    assert CO_SENTENCE in str(e.value)
    assert ap_limit(fresh) == 2000.0
    assert "2,000 USD when" in (fresh.root / "frames" / "finance" / "finance-policy.frame.md").read_text(), "no file was written"


def test_a_proposal_no_change_order_staged_is_refused_review_and_release(closed):
    # Compiling the drafts of SOP revisions is allowed (that is how a change order stages its Frame), but a proposal no
    # change order staged can never be decided or brought into force.
    co = new_co(closed)
    draft = eco.add_sop(closed, co["number"], "finance-policy", by="carol")
    write(closed, draft, OLD, NEW, "carol")
    p = closed.compile(sop_drafts={"finance-policy": draft["locator"]})
    assert not p.get("eco") and p["changes"]
    for call in (lambda: closed.review(p["id"], "bob", approve_all=True), lambda: closed.release(p["id"], "bob")):
        with pytest.raises(closure.ChangeOrderRequired) as e:
            call()
        assert CO_SENTENCE in str(e.value) and "not staged by a change order" in str(e.value)
    assert ap_limit(closed) == 2000.0


def test_a_settings_proposal_from_before_the_conversion_cannot_be_released_after(fresh):
    from hubproto.rules import rule_key

    release_all(fresh)
    rules = fresh.settings_draft("bob")["rules"]
    ap = next(r for r in rules if r["kind"] == "approval_criteria" and r["approver"] == "cog:ap-reviewer" and r["process"] == "purchase-to-pay")
    fresh.settings_edit("bob", {"action": "replace", "key": rule_key(ap), "rule": {**{k: v for k, v in ap.items() if k != "source"}, "max_amount": 1000.0}})
    p = fresh.settings_propose("bob", reason="tighten")
    fresh.review(p["id"], "bob", approve_all=True, meaning="ok")
    sopdocs.convert(fresh, by="carol")
    with pytest.raises(CompileError) as e:
        fresh.release(p["id"], "bob")
    assert CO_SENTENCE in str(e.value) and ap_limit(fresh) == 2000.0
    # After the conversion the same Settings change goes onto a change order (the change-order route).
    fresh.settings_edit("bob", {"action": "replace", "key": rule_key(ap), "rule": {**{k: v for k, v in ap.items() if k != "source"}, "max_amount": 1000.0}})
    out = fresh.settings_propose("bob", reason="tighten")
    assert out["change_order"] and ap_limit(fresh) == 2000.0


def test_the_people_page_and_cog_profiles_propose_on_a_change_order(closed):
    out = closed.propose_new_person(name="erin", roles=["staff"], by="bob")
    assert out["change_order"] and "erin" not in closed.current_bundle()["members"]
    again = closed.propose_cog_model("quality-assistant", "claude-opus-5-5", by="bob")
    assert again["change_order"] == out["change_order"]


def test_a_staged_proposal_comes_into_force_only_with_its_approved_change_order(closed):
    co, p = staged_co(closed)
    assert p["eco"] == co["number"]
    # The policy owners still sign a staged proposal's widenings (here: none needed), but never release it themselves.
    rc, out = run_cli(closed.root, "review", p["id"][:6], "--as", "bob", "--approve-all")
    assert rc == 0 and f"it comes into force with {co['number']}" in out and "ready: hubproto release" not in out
    with pytest.raises(closure.ChangeOrderRequired) as e:
        closed.release(p["id"], "bob")
    assert f"staged by {co['number']}" in str(e.value) and CO_SENTENCE in str(e.value)
    with pytest.raises(closure.ChangeOrderRequired) as e:
        closed.release(p["id"], f"change-order:{co['number']}")
    assert "only once it is approved" in str(e.value)
    assert ap_limit(closed) == 2000.0
    # Exempt: the release automation acting for the approved change order.
    corun = closed.runner.submit("change-order", {"record": {}}, owner="alice", record=co["locator"])
    done = approve(closed, corun, "carol")
    assert done["status"] == "completed"
    [job] = [j for j in closed.automation.of(co["locator"]) if j["automation"] == "release-change-order"]
    assert job["status"] == "success", job
    assert ap_limit(closed) == 1500.0 and closed.kernel.get(co["locator"])["state"] == "released"


def test_after_a_release_a_proposal_no_change_order_staged_goes_stale_and_others_are_left_to_their_change_order(closed):
    other = new_co(closed, title="Another change")
    draft2 = eco.add_sop(closed, other["number"], "document-control-sop", by="carol")
    eco.stage(closed, other["number"], drafts={"document-control-sop": draft2["locator"]})
    theirs = closed.kernel.get(other["locator"])["fields"]["staged_proposal"]
    third = new_co(closed, title="A third change")
    draft3 = eco.add_sop(closed, third["number"], "purchasing-policy", by="carol")
    write(closed, draft3, "under 500 USD go to", "under 400 USD go to", "carol")
    loose = closed.compile(sop_drafts={"purchasing-policy": draft3["locator"]})  # compiled, but no change order staged it
    co, _ = staged_co(closed)
    corun = closed.runner.submit("change-order", {"record": {}}, owner="alice", record=co["locator"])
    approve(closed, corun, "carol")
    assert ap_limit(closed) == 1500.0
    loose = closed.proposals.load(loose["id"])
    assert loose["status"] == "stale" and CO_SENTENCE in loose["stale_reason"]
    assert closed.proposals.load(theirs)["status"] == "open", "its change order compiles it again; no stray proposal is made"
    assert not [p for p in closed.proposals.list() if p["status"] == "open" and not p.get("eco")]


# ------------------------------------------------------------------ the command line
def test_the_policy_verbs_refuse_on_the_command_line(closed):
    root = closed.root
    for argv in (("compile",), ("bootstrap-release", "--as", "bob", "--second", "carol", "--force"), ("seed", "iso9001")):
        rc, out = run_cli(root, *argv)
        assert rc == 1 and CO_SENTENCE in out, (argv, out)
    co = new_co(closed)
    draft = eco.add_sop(closed, co["number"], "finance-policy", by="carol")
    write(closed, draft, OLD, NEW, "carol")
    p = closed.compile(sop_drafts={"finance-policy": draft["locator"]})
    for argv in (("review", p["id"][:6], "--as", "bob", "--approve-all"), ("release", p["id"][:6], "--as", "bob")):
        rc, out = run_cli(root, *argv)
        assert rc == 1 and CO_SENTENCE in out and "Traceback" not in out, (argv, out)
    rc, out = run_cli(root, "proposal", p["id"][:6])
    assert rc == 0 and "not released this way" in out and CO_SENTENCE in out and "ready: hubproto release" not in out
    rc, out = run_cli(root, "sops", "convert", "--as", "carol")
    assert rc == 0 and "already" in out
    assert ap_limit(closed) == 2000.0


def test_sops_convert_refuses_what_the_shell_refuses(fresh, monkeypatch):
    rc, out = run_cli(fresh.root, "bootstrap-release", "--as", "bob", "--second", "carol")
    assert rc == 0
    monkeypatch.setattr(sopdocs, "roundtrip_problems", lambda hub, frames=None: ["finance-policy"])
    rc, out = run_cli(fresh.root, "sops", "convert", "--as", "carol")
    assert rc == 1 and "not converted" in out and "would not read back exactly" in out
    assert not Hub(fresh.root).sop_source(), "a conversion that would change rules is not made"
    monkeypatch.undo()
    rc, out = run_cli(fresh.root, "sops", "convert", "--as", "carol")
    assert rc == 0 and "changes 0 rule(s)" in out
    assert Hub(fresh.root).sop_source()


def test_status_says_configuration_changes_only_through_change_orders(closed):
    rc, out = run_cli(closed.root, "status", "--check")
    assert rc == 0, out
    assert "configuration changes only through change orders" in out and "record types in force: v1" in out
    frame = closed.frames.get("finance-policy").path
    frame.write_text(frame.read_text().replace(OLD, NEW))
    rc, out = run_cli(closed.root, "status", "--check")
    assert rc == 1 and "changed: frames/finance/finance-policy.frame.md" in out
    assert "they are not used" in out and CO_SENTENCE in out and "hubproto compile &&" not in out


# ------------------------------------------------------------------ the shell
def test_the_shell_refuses_a_frame_draft_and_a_release_outside_a_change_order(fresh):
    from hubproto.shell import free_port, serve_in_thread
    from test_shell_views import main_of, post

    release_all(fresh)
    draft = fresh.frame_draft("finance-policy", by="alice")
    before = fresh.compile(overrides={"finance-policy": fresh.draft_text(draft).replace("2,000 USD when", "1,500 USD when")},
                           drafts={"finance-policy": draft["locator"]}, by="alice")
    for who in ("bob", "carol"):
        fresh.review(before["id"], who, approve_all=True, meaning="ok")  # ready to release, but not released before converting
    sopdocs.convert(fresh, by="carol")
    co = new_co(fresh)
    d = eco.add_sop(fresh, co["number"], "finance-policy", by="carol")
    write(fresh, d, OLD, NEW, "carol")
    loose = fresh.compile(sop_drafts={"finance-policy": d["locator"]})
    port = free_port()
    server = serve_in_thread(fresh, "127.0.0.1", port)
    shell = {"hub": fresh, "base": f"http://127.0.0.1:{port}"}
    said = "Configuration changes only through a change order: open one under Quality &gt; Change orders"
    try:
        status, _, body = post(shell, "/frame-draft", {"as": "bob", "frame": "finance-policy"})
        assert status == 409 and said in main_of(body), main_of(body)[:500]
        for proposal in (before, loose):
            status, _, body = post(shell, "/proposal-release", {"as": "bob", "proposal": proposal["id"]})
            assert status == 400 and said in main_of(body), main_of(body)[:800]
        status, _, body = post(shell, "/proposal-review", {"as": "bob", "proposal": loose["id"], "verdict": "approve", "meaning": "ok"})
        assert status == 400 and said in main_of(body), main_of(body)[:800]
    finally:
        server.shutdown()
        server.server_close()
    assert ap_limit(fresh) == 2000.0


# ------------------------------------------------------------------ record types under change control
def test_the_conversion_keeps_the_record_types_in_force(closed, config_dir):
    rt = recordtypes.in_force(closed)
    assert recordtypes.governed(closed) and rt["version"] == 1
    assert rt["types"] == recordtypes.file_types(closed) and rt["digest"] == closed.kernel.config_digests["record-types.json"]
    assert recordtypes.drift(closed) is None


def test_a_record_types_file_that_differs_is_reported_and_not_used(closed, config_dir):
    path = config_dir / "record-types.json"
    data = json.loads(path.read_text())
    data["ncr"]["fields"]["batch"] = "text"
    path.write_text(json.dumps(data))
    restarted = Hub(closed.root)
    assert "batch" not in restarted.kernel.types["ncr"]["fields"], "the file is not silently used"
    d = recordtypes.drift(restarted)
    assert d and any("ncr.batch (text)" in x for x in d["in_file"])
    rc, out = run_cli(closed.root, "status", "--check")
    assert rc == 1 and "config/record-types.json differs from the record types in force (v1); it is not used" in out
    assert "ncr.batch (text)" in out and "record-types carry" in out
    rc, out = run_cli(closed.root, "status")
    assert rc == 0, "without --check it is reported, not an error"
    # The way to bring it into force: carry what the file adds on a change order, which is approved and released.
    co = new_co(restarted)
    rc, out = run_cli(closed.root, "record-types", "carry", co["number"], "--as", "carol", "--from-file")
    assert rc == 0 and "ncr: new field batch (text)" in out, out
    co = restarted.kernel.get(co["locator"])
    assert recordtypes.carried_reasons(restarted, co) == []
    done = recordtypes.activate(restarted, approved(restarted, co), restarted.releases.current()["digest"])
    assert done and "v2" in done[0]
    assert "batch" in restarted.kernel.types["ncr"]["fields"] and recordtypes.drift(restarted) is None
    rc, out = run_cli(closed.root, "status", "--check")
    assert rc == 0 and "record types in force: v2" in out


def test_a_file_that_removes_a_field_cannot_be_carried(closed, config_dir):
    path = config_dir / "record-types.json"
    data = json.loads(path.read_text())
    del data["ncr"]["fields"]["lot"]
    path.write_text(json.dumps(data))
    co = new_co(closed)
    with pytest.raises(ValueError) as e:
        recordtypes.carry_file_changes(closed, co["number"], by="carol")
    assert "adds nothing" in str(e.value) and "ncr.lot" in str(e.value)
    assert "lot" in Hub(closed.root).kernel.types["ncr"]["fields"]
    assert recordtypes.drift(closed)["in_force"] == ["ncr.lot (text)"]


def test_record_type_changes_are_carried_checked_and_activated_on_release(closed):
    co = new_co(closed)
    items = recordtypes.carry(closed, co["number"], [
        {"op": "add_field", "type": "ncr", "field": "batch", "spec": "text"},
        {"op": "add_state", "type": "ncr", "state": "awaiting-batch-check", "fields": ["batch"]},
        {"op": "add_enum_values", "type": "ncr", "field": "source", "values": ["customer-return"]},
        {"op": "set_retention", "type": "complaint", "value": "calibration"},
        {"op": "add_type", "type": "deviation", "definition": {
            "prefix": "DEV", "digits": 4, "yearly": True, "object": "deviation", "retention": "quality", "sensitivity": "internal",
            "fields": {"title": "text", "description": "text", "until": "date"}, "required_by_state": {"closed": ["title"]}}},
    ], by="carol", reason="batch tracking")
    assert [i["id"] for i in items] == ["rt-1", "rt-2", "rt-3", "rt-4", "rt-5"]
    assert all(i["origin"]["kind"] == "direct" and i["by"] == "carol" and i["base_version"] == 1 for i in items)
    co = closed.kernel.get(co["locator"])
    assert recordtypes.carried_reasons(closed, co) == []
    delta = recordtypes.delta_items(closed, co)
    assert [d["id"] for d in delta][0] == "record-type:rt-1" and delta[0]["kind"] == "record-type" and delta[0]["reason"] == "ncr: new field batch (text)"
    assert all(set(d) >= {"id", "op", "reason", "origin", "warnings", "stale", "decisions"} for d in delta)
    assert "batch" not in closed.kernel.types["ncr"]["fields"], "nothing is in force until the change order is released"
    with pytest.raises(closure.ChangeOrderRequired) as e:
        recordtypes.activate(closed, co, closed.releases.current()["digest"])
    assert "only once it is approved" in str(e.value)
    other = Hub(closed.root)  # another process (the shell) holding the hub open
    digest = closed.releases.current()["digest"]
    done = recordtypes.activate(closed, approved(closed, co), digest)
    assert done == [f"record types v2: {'; '.join(i['summary'] for i in items)}"]
    assert recordtypes.activate(closed, closed.kernel.get(co["locator"]), digest)[0].endswith(f"(already in force with {co['number']})")
    k = closed.kernel
    assert k.types["ncr"]["fields"]["batch"] == "text" and "customer-return" in k.types["ncr"]["fields"]["source"]
    assert k.types["ncr"]["required_by_state"]["awaiting-batch-check"] == ["batch"] and k.types["complaint"]["retention"] == "calibration"
    dev = k.create("deviation", "Quality/Deviations", {"title": "Temporary supplier"}, by="carol")
    assert dev["number"].startswith("DEV-")
    assert "batch" in other.kernel.types["ncr"]["fields"], "another process picks up the record types now in force"
    assert "batch" in Hub(closed.root).kernel.types["ncr"]["fields"], "and so does a restart"
    [entry] = [e for e in recordtypes.store(closed).history() if e.get("released_with") == co["number"]]
    assert entry["version"] == 2 and entry["release"] == digest
    # A later compile records the record types in force, not the file.
    assert closed.compile(save=False)["record_types"]["ncr"]["fields"]["batch"] == "text"
    assert closed.kernel.config_digests["record-types.json"] == recordtypes.in_force(closed)["digest"]


@pytest.mark.parametrize("change,words", [
    ({"op": "rename_field", "type": "ncr", "field": "lot", "to": "batch"}, "a field is never renamed"),
    ({"op": "rename_type", "type": "ncr", "to": "nonconformance"}, "a record type is never renamed"),
    ({"op": "retype_field", "type": "ncr", "field": "quantity", "spec": "text"}, "a field's type never changes"),
    ({"op": "remove_enum_values", "type": "ncr", "field": "source", "values": ["audit"]}, "options are added, never removed"),
    ({"op": "add_field", "type": "ncr", "field": "lot", "spec": "text"}, "already has a field lot"),
    ({"op": "add_field", "type": "ncr", "field": "x_batch", "spec": "text"}, "custom fields"),
    ({"op": "add_field", "type": "ncr", "field": "batch", "spec": "colour"}, "unknown field type"),
    ({"op": "add_field", "type": "nothing", "field": "batch", "spec": "text"}, "there is no record type nothing"),
    ({"op": "add_type", "type": "deviation", "definition": {"prefix": "NCR", "fields": {"title": "text"}}}, "prefix NCR is taken by ncr"),
    ({"op": "add_type", "type": "deviation", "definition": {"prefix": "DEV", "fields": {"title": "text"}, "retention": "forever"}}, "no retention class"),
    ({"op": "set_retention", "type": "ncr", "value": "forever"}, "no retention class"),
    ({"op": "set_default", "type": "ncr", "field": "quantity", "value": "lots"}, "must be a number"),
    ({"op": "archive_type", "type": "ncr"}, "file(s) ncr records"),
    ({"op": "remove_type", "type": "ncr"}, "file(s) ncr records"),
    ({"op": "remove_field", "type": "ncr", "field": "title"}, "is used"),
    ({"op": "drop_everything", "type": "ncr"}, "unknown change"),
])
def test_destructive_or_invalid_record_type_changes_are_refused(closed, change, words):
    co = new_co(closed)
    with pytest.raises(ValueError) as e:
        recordtypes.carry(closed, co["number"], [change], by="carol")
    assert words in str(e.value) and "nothing carried" in str(e.value)
    assert not closed.kernel.get(co["locator"])["fields"].get("record_type_changes")


def test_a_field_with_data_is_never_removed_and_one_without_may_be(closed):
    co = new_co(closed)
    closed.kernel.create("ncr", "Quality/Nonconformances", {"title": "Scratched", "description": "x", "lot": "L-7"}, by="carol")
    with pytest.raises(ValueError) as e:
        recordtypes.carry(closed, co["number"], [{"op": "remove_field", "type": "ncr", "field": "lot"}], by="carol")
    assert "holds data in 1 record(s)" in str(e.value)
    [item] = recordtypes.carry(closed, co["number"], [{"op": "remove_field", "type": "ncr", "field": "stock_items"}], by="carol")
    assert item["summary"] == "ncr: field stock_items removed (it holds no data)"
    # Data written after it was carried stops the release (checked again then), and nothing changes.
    closed.kernel.create("ncr", "Quality/Nonconformances", {"title": "Bent", "description": "x", "stock_items": ["LOT-1"]}, by="carol")
    co = closed.kernel.get(co["locator"])
    assert any("holds data in 1 record(s)" in r for r in recordtypes.carried_reasons(closed, co))
    with pytest.raises(ValueError):
        recordtypes.activate(closed, approved(closed, co), closed.releases.current()["digest"])
    assert "stock_items" in closed.kernel.types["ncr"]["fields"] and recordtypes.in_force(closed)["version"] == 1


def test_only_the_requester_or_a_document_controller_carries_record_type_changes(closed):
    co = new_co(closed, who="carol")
    with pytest.raises(ValueError) as e:
        recordtypes.carry(closed, co["number"], [{"op": "add_field", "type": "ncr", "field": "batch", "spec": "text"}], by="alice")
    assert "carol's change order" in str(e.value)
    recordtypes.carry(closed, co["number"], [{"op": "add_field", "type": "ncr", "field": "batch", "spec": "text"}], by="carol")
    co = approved(closed, closed.kernel.get(co["locator"]))
    with pytest.raises(ValueError) as e:
        recordtypes.carry(closed, co["number"], [{"op": "add_field", "type": "ncr", "field": "lot2", "spec": "text"}], by="carol")
    assert "while it is being prepared" in str(e.value)


def test_record_types_carry_on_the_command_line(closed):
    co = new_co(closed)
    rc, out = run_cli(closed.root, "record-types", "carry", co["number"], "--as", "carol", "--changes", "[{not json")
    assert rc == 2 and "JSON does not parse" in out
    rc, out = run_cli(closed.root, "record-types", "carry", co["number"], "--as", "carol",
                      "--changes", json.dumps([{"op": "rename_field", "type": "ncr", "field": "lot", "to": "batch"}]))
    assert rc == 1 and "a field is never renamed" in out
    rc, out = run_cli(closed.root, "record-types", "carry", co["number"], "--as", "carol",
                      "--changes", json.dumps({"op": "add_field", "type": "ncr", "field": "batch", "spec": "text"}))
    assert rc == 0 and "rt-1" in out and "ncr: new field batch (text)" in out
    rc, out = run_cli(closed.root, "record-types", "carry", "CO-1999-0001", "--as", "carol", "--from-file")
    assert rc == 2
    rc, out = run_cli(closed.root, "record-types", "status")
    assert rc == 0 and "record types in force: v1" in out
    rc, out = run_cli(closed.root, "record-types", "show", "ncr")
    assert rc == 0 and json.loads(out)["prefix"] == "NCR"


def test_the_backup_keeps_the_record_types_and_form_layouts_in_force(closed, tmp_path):
    co = new_co(closed)
    recordtypes.carry(closed, co["number"], [{"op": "add_field", "type": "ncr", "field": "batch", "spec": "text"}], by="carol")
    recordtypes.activate(closed, approved(closed, closed.kernel.get(co["locator"])), closed.releases.current()["digest"])
    rc, out = run_cli(closed.root, "backup", str(tmp_path / "bk"))
    assert rc == 0 and "record-types (" in out and "forms (" in out
    target = tmp_path / "restored"
    shutil.copytree(closed.root / "frames", target / "frames")
    rc, out = run_cli(target, "restore", str(tmp_path / "bk"))
    assert rc == 0
    assert "batch" in Hub(target).kernel.types["ncr"]["fields"]


def test_the_automation_api_for_a_change_order_with_no_record_type_changes(closed):
    co = new_co(closed)
    assert recordtypes.carried_reasons(closed, co) == [] and recordtypes.delta_items(closed, co) == []
    assert recordtypes.activate(closed, co, closed.releases.current()["digest"]) == [], "nothing carried: nothing to do, whatever its state"


def test_a_hub_converted_before_record_types_were_kept_starts_from_the_release_in_force(closed, config_dir):
    shutil.rmtree(closed.root / "state" / "record-types")  # converted by a hub that did not keep them yet
    path = config_dir / "record-types.json"
    data = json.loads(path.read_text())
    data["ncr"]["fields"]["batch"] = "text"
    path.write_text(json.dumps(data))  # and the file changed since that release
    restarted = Hub(closed.root)
    rt = recordtypes.in_force(restarted)
    assert rt["version"] == 1 and "was compiled with" in rt["source"]
    assert "batch" not in restarted.kernel.types["ncr"]["fields"], "the file as of the release in force, not as it is now"
    assert any("ncr.batch" in x for x in recordtypes.drift(restarted)["in_file"])


def test_record_types_in_force_that_cannot_be_read_stop_the_hub_rather_than_fall_back_to_the_file(closed):
    (closed.root / "state" / "record-types" / "active.json").write_text("{not json")
    with pytest.raises(ValueError) as e:
        Hub(closed.root)
    assert "restore it from a backup" in str(e.value)


def test_status_reports_other_configuration_files_that_differ_from_the_release(closed, config_dir):
    path = config_dir / "links.json"
    data = json.loads(path.read_text())
    data["inspires"] = "is inspired by"
    path.write_text(json.dumps(data))
    rc, out = run_cli(closed.root, "status", "--check")
    assert rc == 1 and "config/links.json differs from what the release in force, v1 (" in out and "recorded: it is used as it is" in out


def test_form_layouts_come_into_force_only_for_an_approved_change_order(closed):
    from hubproto import formlayout

    co = new_co(closed)
    lay = formlayout.active_layout(closed, "ncr")
    lay["sections"][0]["title"] = "The nonconformance"
    formlayout.save_draft(closed, "carol", "ncr", lay)
    formlayout.carry(closed, co["number"], "carol", "ncr")
    co = closed.kernel.get(co["locator"])
    with pytest.raises(closure.ChangeOrderRequired):
        formlayout.activate(closed, co, closed.releases.current()["digest"])
    assert formlayout.active_entry(closed, "ncr") is None
    assert formlayout.activate(closed, approved(closed, co), closed.releases.current()["digest"]) == ["ncr v1"]
