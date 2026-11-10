"""Document references in SOP prose (issue #8; hubproto.references): the compile reads each citation of a document (its
number, a revision of it, or a site link to its page) with the line it is on; a citation of an unknown document, or of
one not in force, is a warning at its frame and line, which a change order that makes it asks its approver to accept;
a released SOP's citations are ``references`` links on both documents' pages, and the ones it drops are unlinked; a
release that retires a cited document, or supersedes a cited revision, flags the SOPs still citing it to their owners.
A citation of a number names the document in its revision in force, so a new revision of it is no reason to flag it.
No model is called: cogs answer with their stand-ins."""

import shutil
from pathlib import Path
from urllib.parse import quote

import pytest

from conftest import ROOT, release_all
from hubproto import changecontrol as cc
from hubproto import eco, sopdocs
from hubproto.frames import load_frame_text
from test_change_control import accept_co_warnings, decide_all, hub, job, new_co, released, revise  # noqa: F401
from test_change_control_documents import COACH, coach_text
from test_change_control_documents import released as released_by
from test_change_order_pages import shell  # noqa: F401
from test_shell_views import get, main_of

INTRO = "Everything else is approved by a person."  # finance-policy line 16, before any section: prose, no rule


def doc(hub, name):
    return sopdocs.in_force(hub.kernel, name)


def cite(hub, co, *lines, sop="finance-policy", old=INTRO, by="alice"):
    """A revision of ``sop`` on the change order with ``lines`` added after ``old`` (prose that states no rule)."""
    return revise(hub, co, sop, old, old + "".join("\n" + ln for ln in lines), by=by)


def cited_by_links(hub, rec, direction="out"):
    """The documents ``rec`` cites by link ("out"), or that cite it ("in": referenced-by on its page)."""
    return sorted(hub.kernel.get(lk["target"])["number"] for lk in hub.kernel.links_of(rec["locator"])
                  if lk["rel"] == "references" and lk["direction"] == direction and hub.kernel.get(lk["target"])["type"] == "document")


def reference_findings(p):
    return [f for f in p.get("findings") or [] if f["guard"] == "document-reference"]


# ---------------------------------------------------------------- reading
CALIBRATION = """---
type: frame [0.2]
name: calibration-sop
description: How gauges are calibrated.
visibility: internal
version: "1.0.0"
scope: department
maintainer: carol
---
# Calibration SOP

Gauges are calibrated as DOC-0040 says; the old form was DOC-0012 rev A, see also DOC-0040.
The gauge list is at [the gauge list](/document/rec:document:DOC-0051?as=bob) and http://hub.example/doc/DOC-0052.
NCR-2026-0001 is a nonconformance, not a document, and XDOC-0001 is no number.
Supplier specs: https://supplier.example/specs/DOC-0040.pdf and https://other.example/document/DOC-0041; DOC-0042-A2 is a part.
The form is `DOC-0043`, and [DOC-0048 rev A](/document/DOC-0048) is one citation; so is DOC-0049 rev b.
Copies: www.supplier.example/specs/DOC-0057.pdf, [the wiki](https://wiki.example/Gauge_(DOC-0054)) and https://wiki.example/Caliper_(DOC-0055).

    DOC-0044 in an indented code block

~~~
DOC-0045 in a fence of tildes
~~~
```text
DOC-0046 in a fence of backticks
```
- a list item that cites DOC-0047
    ```
    DOC-0056 in a fence in a list
    ```

    and its indented continuation, which is prose: DOC-0050

## Cog: gauge-checker
About: checks gauges.
Prompt from: DOC-0060

### Notes for the cog
Also DOC-0061, still the cog's.

# Appendix
The appendix cites DOC-0062.

## Permissions
Quality managers may read Quality.
"""


def line_of(text, words):
    return next(n for n, ln in enumerate(text.splitlines(), start=1) if words in ln)


def test_the_compile_reads_document_numbers_revisions_and_site_links_with_their_lines(tmp_path):
    from hubproto.references import read_references, reference_guard

    frame = load_frame_text(CALIBRATION, tmp_path / "calibration-sop.frame.md")
    refs = read_references(frame, hosts=("hub.example",))
    at = lambda w: line_of(CALIBRATION, w)  # noqa: E731
    assert [(r["line"], r["document"], r["revision"], r["form"]) for r in refs] == [
        (12, "DOC-0040", None, "number"), (12, "DOC-0012", "A", "number"),
        (13, "DOC-0051", None, "link"), (13, "DOC-0052", None, "link"),
        (15, "DOC-0041", None, "other-site"),
        (16, "DOC-0048", "A", "link"), (16, "DOC-0049", "B", "number"),
        (at("a list item"), "DOC-0047", None, "number"), (at("its indented continuation"), "DOC-0050", None, "number"),
        (at("The appendix"), "DOC-0062", None, "number")], \
        "one per document a line cites; no other site's URLs, no part numbers, no code, no cog's section"
    assert all(CALIBRATION.splitlines()[r["line"] - 1].count(r["document"]) for r in refs), "the line is the file's line"
    assert refs[1]["text"] == "DOC-0012 rev A" and refs[2]["text"] == "/document/rec:document:DOC-0051"
    assert refs[5]["text"] == "DOC-0048 rev A", "a Markdown link is one citation, its revision from its text"
    [w] = reference_guard(refs, lambda ref: {"status": "in-force", "why": "in force"})
    assert (w.line, w.severity) == (15, "warning") and "another site" in w.detail, "a page link on another host is said, not dropped"


def test_a_link_counts_only_on_this_hubs_host_and_port(tmp_path, monkeypatch, capsys):
    from hubproto import references

    frame = load_frame_text(CALIBRATION, tmp_path / "calibration-sop.frame.md")

    def line13(hosts):
        return [(r["document"], r["form"]) for r in references.read_references(frame, hosts=hosts) if r["line"] == 13]

    assert line13(()) == [("DOC-0051", "link"), ("DOC-0052", "other-site")], "with no host configured, only relative links count"
    assert line13(("http://HUB.example:80/",)) == line13(("hub.example",)) == [("DOC-0051", "link"), ("DOC-0052", "link")], \
        "the scheme's default port is filled in"
    assert line13(("http://hub.example:8080",))[1] == ("DOC-0052", "other-site") and line13(("https://hub.example",))[1][1] == "other-site"
    monkeypatch.setenv("HUBPROTO_PUBLIC_URL", "hub.example:8080")
    monkeypatch.setenv("HUBPROTO_WOPI_BASE", "https://hub.example/")
    assert references.hub_hosts() == (("hub.example", 443),)
    assert "HUBPROTO_PUBLIC_URL='hub.example:8080' is not an http(s) URL" in capsys.readouterr().err, "a value with no scheme is said"


@pytest.fixture
def files_hub(tmp_path):
    """A hub whose Frame files are the source (no change orders): the compile reads the files on disk."""
    from hubproto.app import Hub

    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    h = Hub(tmp_path)
    h.kernel.seed()
    release_all(h)
    return h


def controlled(hub, title, state="effective"):
    d = hub.dms.create(title=title, doc_class="controlled", folder="Quality/Controlled Documents", by="carol", content=b"text",
                       filename="x.md", mime="text/markdown", doc_type="wi", fields={"review_date": "2027-01-01"})
    hub.kernel.transition(d["locator"], "effective", by="carol", reason="in force")
    if state != "effective":
        hub.kernel.transition(d["locator"], state, by="carol", reason="retired")
    return hub.kernel.get(d["locator"])


def test_a_citation_of_an_unknown_or_obsolete_document_is_a_warning_with_its_frame_and_line(files_hub):
    hub = files_hub
    live, gone = controlled(hub, "Gauge list"), controlled(hub, "Old gauge list", "obsolete")
    path = Path(hub.root) / "frames/finance/finance-policy.frame.md"
    path.write_text(path.read_text(encoding="utf-8").replace(
        INTRO, INTRO + f"\nFor the gauges, see {live['number']}.\nThe older list was {gone['number']}; the archive is DOC-9999."), encoding="utf-8")
    hub._frames = None  # read the files again
    p = hub.compile(save=False)
    assert [(r["line"], r["document"], r["resolved"]["status"]) for r in p["references"]] == [
        (17, live["number"], "in-force"), (18, gone["number"], "obsolete"), (18, "DOC-9999", "unknown")]
    found = reference_findings(p)
    assert [(f["frame"], f["line"], f["severity"]) for f in found] == [("finance-policy", 18, "warning")] * 2
    assert f"{gone['number']} is obsolete" in found[0]["detail"] and "DOC-9999 is not a document" in found[1]["detail"]
    assert not [c for c in p["changes"] if c["op"] not in ("text",)], "a citation is no rule: nothing to decide but the wording"


def test_sops_without_citations_compile_and_release_as_before(files_hub):
    p = files_hub.compile(save=False)
    assert p["references"] == [] and not reference_findings(p)
    assert "references" not in files_hub.releases.current(), "a release records citations only when there are some"


# ---------------------------------------------------------------- on a change order
def test_a_change_order_that_cites_a_document_not_in_force_asks_its_approver_to_accept_it(hub):
    dc = doc(hub, "document-control-sop")
    co = new_co(hub, title="Cite the document-control SOP")
    cite(hub, co, f"For the wider picture, see {dc['number']} and DOC-9999.")
    p = eco.preview(hub, co["number"])
    assert [(r["line"], r["document"]) for r in p["references"] if r["frame"] == "finance-policy"] == [(17, dc["number"]), (17, "DOC-9999")]
    [f] = reference_findings(p)
    assert (f["frame"], f["line"]) == ("finance-policy", 17) and not p["errors"], "a warning: it stops nothing on its own"
    cc.request_review(hub, co["number"], "alice", panel=[])
    assert [i["op"] for i in cc.delta(hub, co["number"])] == ["text"], "the citation is part of the wording, not an item to decide"
    [w] = [w for w in cc.co_warnings(hub, co["number"]) if w["code"] == "reference"]
    assert "finance-policy line 17 cites DOC-9999" in w["text"] and not w["accepted"]
    cc.send_for_approval(hub, co["number"], "alice")
    decide_all(hub, co)
    assert any("[reference]" in r and "DOC-9999" in r for r in cc.approval_reasons(hub, co["number"]))
    accept_co_warnings(hub, co)
    assert cc.approval_reasons(hub, co["number"]) == []


def test_released_citations_are_links_on_both_pages_and_a_dropped_one_is_unlinked(shell):
    hub = shell["hub"]
    qm, dc = doc(hub, "quality-manual"), doc(hub, "document-control-sop")
    co = new_co(hub, title="Point to the manual")
    cite(hub, co, f"The manual is at /document/{qm['number']}.", f"For documents, see {dc['number']}.")
    released(hub, co)
    fin = doc(hub, "finance-policy")
    assert cited_by_links(hub, fin) == sorted([qm["number"], dc["number"]])
    assert fin["fields"]["revision"] == "B" and cited_by_links(hub, qm, "in") == [fin["number"]]
    assert [r["document"] for r in hub.releases.current()["references"]] == [qm["number"], dc["number"]], "recorded in the release"
    [step] = [s for s in job(hub, hub.kernel.get(co["locator"]), "release-change-order")[0]["steps"] if s["name"] == "Link document references"]
    assert step["detail"] == "linked 2"
    for rec, words in ((fin, "references"), (qm, "referenced-by")):
        status, page = get(shell, "/document/" + quote(rec["locator"], safe=":"), **{"as": "carol"})
        assert status == 200 and words in main_of(page) and (qm if rec is fin else fin)["number"] in main_of(page)

    co2 = new_co(hub, title="The manual moves")
    revise(hub, co2, "finance-policy", f"\nThe manual is at /document/{qm['number']}.", "")
    released(hub, co2)
    fin2 = doc(hub, "finance-policy")
    assert cited_by_links(hub, fin2) == [dc["number"]] and cited_by_links(hub, qm, "in") == [], "the citation it dropped is unlinked"
    assert cited_by_links(hub, hub.kernel.get(fin["locator"])) == [], "the revision it superseded keeps no citation links"
    assert cited_by_links(hub, dc, "in") == [fin2["number"]]


def test_retiring_a_cited_document_warns_the_change_order_and_flags_the_citing_sop_to_its_owner(shell):
    hub = shell["hub"]
    co = new_co(hub, title="A coach persona, and the finance policy points to it")
    coach = eco.add_new_sop(hub, co["number"], "persona-coach", coach_text(), by="alice", relpath=COACH)
    cite(hub, co, f"Coaching follows {coach['number']}.")
    cc.request_review(hub, co["number"], "alice", panel=[])
    assert not [w for w in cc.co_warnings(hub, co["number"]) if w["code"] == "reference"], \
        "a document the same change order brings into force is cited in force"
    released_by(hub, co)
    fin = doc(hub, "finance-policy")
    assert cited_by_links(hub, fin) == [coach["number"]]

    co2 = new_co(hub, by="carol", title="Retire the coach persona")
    eco.retire_sop(hub, co2["number"], "persona-coach", by="carol", reason="no cog behaves by it")
    cc.request_review(hub, co2["number"], "carol", panel=[])
    [w] = [w for w in cc.co_warnings(hub, co2["number"]) if w["code"] == "reference"]
    assert w["text"] == f"finance-policy line 17 cites {coach['number']}: {coach['number']} is retired by this change: obsolete once it is released"
    released_by(hub, co2, deciders=("bob",))
    assert hub.kernel.get(coach["locator"])["state"] == "obsolete"
    fin = hub.kernel.get(fin["locator"])
    [flag] = fin["fields"]["citation_flags"]
    assert (flag["document"], flag["line"], flag["by"], flag["status"]) == (coach["number"], 17, co2["number"], "obsolete")
    [note] = [m for m in hub.inbox.for_principal("alice") if fin["number"] in (m.get("subject") or "") and "cites documents" in m["subject"]]
    assert co2["number"] in note["subject"] and f"line 17 cites {coach['number']}" in note["body"], "its owner, alice, is told"
    [step] = [s for s in job(hub, hub.kernel.get(co2["locator"]), "release-change-order")[0]["steps"] if s["name"] == "Link document references"]
    assert f"flagged {fin['number']} (finance-policy line 17 cites {coach['number']}) to alice" in step["detail"]
    status, page = get(shell, "/document/" + quote(fin["locator"], safe=":"), **{"as": "alice"})
    assert status == 200 and "Cites documents no longer in force" in main_of(page)
    assert cited_by_links(hub, fin) == [coach["number"]], "the prose still cites it: the link stays, flagged"
    # Working the step again (a retry) flags nothing twice.
    from hubproto.references import flag_citations

    again = flag_citations(hub, hub.kernel.get(co2["locator"]))
    assert again["flagged"] == [] and again["told"] == [] and len(hub.kernel.get(fin["locator"])["fields"]["citation_flags"]) == 1


def test_a_new_revision_follows_a_citation_of_its_number_but_flags_one_of_the_revision_it_supersedes(hub):
    dc = doc(hub, "document-control-sop")
    co = new_co(hub, title="Cite the document-control SOP")
    cite(hub, co, f"For documents, see {dc['number']}.", f"The form in use came with {dc['number']} rev A.")
    released(hub, co)
    fin = doc(hub, "finance-policy")
    assert cited_by_links(hub, fin) == [dc["number"]]

    co2 = new_co(hub, title="Document control, reissued")
    new = revise(hub, co2, "document-control-sop", "# Document control SOP\n", "# Document control SOP\n\nIssued for the whole site.\n")
    cc.request_review(hub, co2["number"], "alice")
    ws = [w for w in cc.co_warnings(hub, co2["number"]) if w["code"] == "reference"]
    assert [w["ref"] for w in ws] == [f"ref:finance-policy:18:{dc['number']}@A"], "only the citation of revision A: the number follows"
    assert f"superseded by {new['number']} in this change" in ws[0]["text"]
    released(hub, co2)
    assert doc(hub, "document-control-sop")["locator"] == new["locator"] and hub.kernel.get(dc["locator"])["state"] == "obsolete"
    fin = doc(hub, "finance-policy")
    assert [(f["line"], f["revision"], f["status"]) for f in fin["fields"]["citation_flags"]] == [(18, "A", "superseded")]
    assert cited_by_links(hub, fin) == sorted([dc["number"], new["number"]]), \
        "the number is linked to the revision in force; revision A stays linked, as cited"
    assert cited_by_links(hub, new, "in") == [fin["number"]]
    from hubproto.references import resolve

    r = resolve(hub.kernel, new["number"], "A")
    assert r["status"] == "mismatch" and r["why"] == f"{new['number']} is revision B, not revision A (revision A is {dc['number']})", \
        "a number names its own revision: no quiet reading as the lineage's revision A"


# ---------------------------------------------------------------- review findings on #12
def release_job(hub, co):
    [j] = job(hub, hub.kernel.get(co["locator"]), "release-change-order")
    return j


def step(j, name):
    return next(s for s in j["steps"] if s["name"] == name)


def test_the_same_frames_release_the_same_digest_with_or_without_citations_in_their_prose(tmp_path):
    """Citations are kept in the release meta, never in the bundle: prose that cites a document (on a line that states
    no rule) leaves the release's identity where it was."""
    from hubproto.app import Hub

    digests, metas = [], []
    for name, intro in (("plain", INTRO), ("citing", INTRO.replace("person.", "person (see DOC-0001 and /document/DOC-0002)."))):
        root = tmp_path / name
        shutil.copytree(ROOT / "frames", root / "frames")
        path = root / "frames/finance/finance-policy.frame.md"
        path.write_text(path.read_text(encoding="utf-8").replace(INTRO, intro), encoding="utf-8")
        h = Hub(root)
        h.kernel.seed()
        release_all(h)
        metas.append(h.releases.current())
        digests.append(metas[-1]["digest"])
    assert "references" not in metas[0] and [r["document"] for r in metas[1]["references"]] == ["DOC-0001", "DOC-0002"]
    assert digests[0] == digests[1], "the same Frames, citing or not, are the same release"


def test_links_people_made_survive_and_a_second_sync_changes_nothing(hub):
    from hubproto.references import sync_links

    qm, dc = doc(hub, "quality-manual"), doc(hub, "document-control-sop")
    co = new_co(hub, title="Cite document control")
    cite(hub, co, f"For documents, see {dc['number']}.")
    released(hub, co)
    fin = doc(hub, "finance-policy")
    hub.kernel.link(fin["locator"], "references", qm["locator"], by="carol")  # by hand, not from the prose
    events = len(hub.kernel.events())
    assert sync_links(hub) == {"linked": [], "unlinked": [], "held": []} and len(hub.kernel.events()) == events, \
        "a second run changes nothing"
    assert cited_by_links(hub, fin) == sorted([qm["number"], dc["number"]]) and hub.kernel.get(fin["locator"])["fields"]["cites"] == [dc["locator"]]

    co2 = new_co(hub, title="Drop the citation")
    revise(hub, co2, "finance-policy", f"\nFor documents, see {dc['number']}.", "")
    released(hub, co2)
    assert cited_by_links(hub, hub.kernel.get(fin["locator"])) == [qm["number"]], "the hub takes off its own link, never a person's"
    assert cited_by_links(hub, doc(hub, "finance-policy")) == []


def test_a_held_citing_document_does_not_stop_a_release_that_does_not_involve_it(hub):
    dc = doc(hub, "document-control-sop")
    co = new_co(hub, title="Cite document control")
    cite(hub, co, f"For documents, see {dc['number']}.", f"The form in use came with {dc['number']} rev A.")
    released(hub, co)
    fin = doc(hub, "finance-policy")
    hub.kernel.hold(fin["locator"], True, "legal hold", by="carol")

    co2 = new_co(hub, title="Document control, reissued")
    revise(hub, co2, "document-control-sop", "# Document control SOP\n", "# Document control SOP\n\nIssued for the whole site.\n")
    released(hub, co2)
    j = release_job(hub, co2)
    assert j["status"] == "success" and hub.kernel.get(co2["locator"])["state"] == "released", j.get("outcome")
    detail = step(j, "Link document references")["detail"]
    assert f"{fin['number']} is on hold: link not updated" in detail and f"{fin['number']} is on hold: not flagged" in detail
    assert step(j, "Tell the people involved")["status"] == "done"
    held = hub.kernel.get(fin["locator"])
    assert cited_by_links(hub, held) == [dc["number"]] and not held["fields"].get("citation_flags"), "left as it was"
    notes = lambda: [m for m in hub.inbox.on_record(fin["locator"]) if "cites documents" in (m.get("subject") or "")]  # noqa: E731
    [note] = notes()
    assert note["to"] == "alice" and "line 18 cites" in note["body"], "a message writes nothing on the document: its owner is told"

    hub.kernel.hold(fin["locator"], False, "released from the hold", by="carol")
    co3 = new_co(hub, title="Change control, reworded")
    revise(hub, co3, "change-control-sop", "# Change control SOP\n", "# Change control SOP\n\nFor every site.\n")
    released(hub, co3)
    assert release_job(hub, co3)["status"] == "success"
    flags = hub.kernel.get(fin["locator"])["fields"]["citation_flags"]
    assert [(f["line"], f["status"], f["by"]) for f in flags] == [(18, "superseded", co3["number"])], \
        "the flag a hold held back follows at the next release"
    assert len(notes()) == 1, "its owner was told already: not twice"
    assert cited_by_links(hub, hub.kernel.get(fin["locator"])) == sorted([dc["number"], doc(hub, "document-control-sop")["number"]]), \
        "and the links catch up"


def test_retrying_an_old_release_job_after_a_later_release_keeps_what_the_later_one_linked(hub, monkeypatch):
    from hubproto import automation, references

    qm, dc = doc(hub, "quality-manual"), doc(hub, "document-control-sop")
    co = new_co(hub, title="Cite document control, reissued with it")
    cite(hub, co, f"For documents, see {dc['number']}.", f"The form in use came with {dc['number']} rev A.")
    revise(hub, co, "document-control-sop", "# Document control SOP\n", "# Document control SOP\n\nIssued for the whole site.\n")

    def boom(*a, **k):
        raise RuntimeError("the link step stopped")

    monkeypatch.setattr(references, "sync_links", boom)
    released(hub, co)
    j = release_job(hub, co)
    assert j["status"] == "some-errors" and step(j, "Link document references")["status"] == "failed"
    monkeypatch.undo()

    co2 = new_co(hub, title="Cite the manual instead")
    revise(hub, co2, "finance-policy", f"see {dc['number']}.\nThe form in use came with {dc['number']} rev A.", f"see {qm['number']}.")
    released(hub, co2)
    fin = doc(hub, "finance-policy")
    assert cited_by_links(hub, fin) == [qm["number"]]

    j = automation.retry(hub, j["id"], by="alice")
    j = hub.automation.load(j["id"])
    assert j["status"] == "success" and hub.kernel.get(co["locator"])["state"] == "released", j.get("outcome")
    fin = hub.kernel.get(fin["locator"])
    assert cited_by_links(hub, fin) == [qm["number"]], "the later release's links stay"
    assert not fin["fields"].get("citation_flags"), "a citation the later release took out is not flagged"


def test_flags_go_in_one_message_per_document_to_a_person_and_survive_a_failed_message(hub, monkeypatch):
    from hubproto import automation

    dc = doc(hub, "document-control-sop")
    co = new_co(hub, title="Cite the form")
    cite(hub, co, f"The form in use came with {dc['number']} rev A.", f"The register is kept as {dc['number']} rev a says.")
    released(hub, co)
    fin = doc(hub, "finance-policy")
    hub.kernel.set_fields(fin["locator"], {"owner": "ap-reviewer"}, by="setup")  # a cog owns it: its maintainer, bob, is told

    co2 = new_co(hub, title="Document control, reissued")
    revise(hub, co2, "document-control-sop", "# Document control SOP\n", "# Document control SOP\n\nIssued for the whole site.\n")
    notify = hub.inbox.notify

    def fails(to, **kw):
        if "cites documents" in kw.get("subject", ""):
            raise OSError("the inbox is full")
        return notify(to, **kw)

    monkeypatch.setattr(hub.inbox, "notify", fails)
    released(hub, co2)
    j = release_job(hub, co2)
    assert j["status"] == "some-errors" and len(hub.kernel.get(fin["locator"])["fields"]["citation_flags"]) == 2, "flagged, not told"
    monkeypatch.undo()

    j = hub.automation.load(automation.retry(hub, j["id"], by="alice")["id"])
    assert j["status"] == "success", j.get("outcome")
    fin = hub.kernel.get(fin["locator"])
    assert [f["line"] for f in fin["fields"]["citation_flags"]] == [17, 18], "one flag per line, none twice"
    notes = [m for m in hub.inbox.all() if fin["number"] in (m.get("subject") or "") and "cites documents" in m["subject"]]
    assert [m["to"] for m in notes] == ["bob"], "one message for the document, to a person"
    assert "line 17 cites" in notes[0]["body"] and "line 18 cites" in notes[0]["body"]
    assert step(j, "Link document references")["detail"] == \
        f"flagged {fin['number']} (finance-policy line 17 cites {dc['number']} rev A; line 18 cites {dc['number']} rev a) to bob"


def test_an_sop_citing_its_own_lineage_gets_no_warning_flag_or_link(hub):
    old = doc(hub, "finance-policy")
    co = new_co(hub, title="A revision history")
    cite(hub, co, f"Revision history: {old['number']} rev A, see /document/{old['number']}.")
    p = eco.preview(hub, co["number"])
    assert [r["resolved"]["status"] for r in p["references"] if r["frame"] == "finance-policy"] == ["self", "self"]
    assert not reference_findings(p)
    released(hub, co)
    assert not [w for w in cc.co_warnings(hub, co["number"]) if w["code"] == "reference"]
    fin = doc(hub, "finance-policy")
    assert cited_by_links(hub, fin) == [] and not fin["fields"].get("citation_flags")


def test_jobs_run_their_steps_by_name_and_an_unknown_step_stops_the_job(hub, monkeypatch):
    """A job saved before "Link document references" existed lists the 12 steps it had; a retry runs them by name, with
    the new step in its place. A step name the hub does not know stops the job."""
    from hubproto import automation

    dc = doc(hub, "document-control-sop")
    co = new_co(hub, title="Cite document control")
    cite(hub, co, f"For documents, see {dc['number']}.")

    def boom(*a, **k):
        raise RuntimeError("no model")

    monkeypatch.setattr(cc, "find_related", boom)
    released(hub, co)
    monkeypatch.undo()
    j = release_job(hub, co)
    assert j["status"] == "some-errors" and step(j, "Send related changes back")["status"] == "failed"
    assert step(j, "Link document references")["status"] == "done" and cited_by_links(hub, doc(hub, "finance-policy")) == [dc["number"]], \
        "the links are made before the model step, which may fail"
    old = [s for s in j["steps"] if s["name"] != "Link document references"]  # the job as it was saved before the upgrade
    for s in old[9:]:
        s.update({"status": "waiting", "detail": ""})
    assert [s["name"] for s in old] == [n for n in automation.AUTOMATIONS["release-change-order"]["steps"] if n != "Link document references"]
    step(j, "Compile every open change order")["name"] = "Recompile everything"  # a step this hub does not know
    j["steps"] = old
    hub.automation.save(j)
    j = hub.automation.load(automation.retry(hub, j["id"], by="alice")["id"])
    assert j["status"] == "some-errors" and "unknown step 'Recompile everything'" in j["outcome"]
    assert hub.kernel.get(co["locator"])["state"] == "approved", "nothing ran in its place"

    step(j, "Recompile everything")["name"] = "Compile every open change order"
    hub.automation.save(j)
    j = hub.automation.load(automation.retry(hub, j["id"], by="alice")["id"])
    assert j["status"] == "success" and [s["name"] for s in j["steps"]] == automation.AUTOMATIONS["release-change-order"]["steps"], j.get("outcome")
    assert step(j, "Link document references")["status"] == "done", "a step added since the job was saved is run in its place"
    assert step(j, "Send related changes back")["detail"].startswith("no related change") and step(j, "Tell the people involved")["status"] == "done"
    assert hub.kernel.get(co["locator"])["state"] == "released"


def test_a_failed_write_of_cites_never_leaves_a_hub_link_looking_like_a_persons(hub, monkeypatch):
    from hubproto import automation

    dc = doc(hub, "document-control-sop")
    co = new_co(hub, title="Cite document control")
    cite(hub, co, f"For documents, see {dc['number']}.")
    set_fields, failed = hub.kernel.set_fields, []

    def once(ref, fields, **kw):
        if "cites" in fields and not failed:
            failed.append(ref)
            raise OSError("disk full")
        return set_fields(ref, fields, **kw)

    monkeypatch.setattr(hub.kernel, "set_fields", once)
    released(hub, co)
    j = release_job(hub, co)
    assert failed and j["status"] == "some-errors" and step(j, "Link document references")["status"] == "failed"
    monkeypatch.undo()
    j = hub.automation.load(automation.retry(hub, j["id"], by="alice")["id"])
    assert j["status"] == "success", j.get("outcome")
    fin = doc(hub, "finance-policy")
    assert cited_by_links(hub, fin) == [dc["number"]] and fin["fields"]["cites"] == [dc["locator"]], "the hub's link, managed as such"

    co2 = new_co(hub, title="Drop the citation")
    revise(hub, co2, "finance-policy", f"\nFor documents, see {dc['number']}.", "")
    released(hub, co2)
    assert cited_by_links(hub, hub.kernel.get(fin["locator"])) == [], "so it is taken off with the citation"


def test_a_link_a_person_made_is_not_taken_over_when_the_prose_cites_it_too(hub, monkeypatch):
    from hubproto import automation, references

    dc = doc(hub, "document-control-sop")
    co = new_co(hub, title="Cite document control")
    cite(hub, co, f"For documents, see {dc['number']}.")
    monkeypatch.setattr(references, "sync_links", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stopped")))
    released(hub, co)
    monkeypatch.undo()
    fin = doc(hub, "finance-policy")
    hub.kernel.link(fin["locator"], "references", dc["locator"], by="carol")  # carol links it first, by hand
    j = hub.automation.load(automation.retry(hub, release_job(hub, co)["id"], by="alice")["id"])
    assert j["status"] == "success" and step(j, "Link document references")["detail"] == "no citation changed"
    assert not hub.kernel.get(fin["locator"])["fields"].get("cites"), "carol's link stays carol's"

    co2 = new_co(hub, title="Drop the citation")
    revise(hub, co2, "finance-policy", f"\nFor documents, see {dc['number']}.", "")
    released(hub, co2)
    assert cited_by_links(hub, hub.kernel.get(fin["locator"])) == [dc["number"]], "the hub never takes off a person's link"


def link_events(hub, rec):
    return [e["by"] for e in hub.kernel.events(aggregate=rec["locator"])
            if e["type"] in ("record.linked", "record.unlinked") and e["data"].get("rel") == "references"]


def test_the_hub_makes_the_citation_links_not_the_person_releasing(hub, tmp_path):
    from hubproto.app import Hub

    dc = doc(hub, "document-control-sop")
    co = new_co(hub, title="Cite document control")
    cite(hub, co, f"For documents, see {dc['number']}.")
    released(hub, co)
    assert link_events(hub, doc(hub, "finance-policy")) == ["hub"], "a change order's release"

    root = tmp_path / "first"  # a new hub's first release (by bob, without a change order) converts the SOPs and links them
    shutil.copytree(ROOT / "frames", root / "frames")
    path = root / "frames/finance/finance-policy.frame.md"
    path.write_text(path.read_text(encoding="utf-8").replace(INTRO, INTRO.replace("person.", "person (see DOC-0001).")), encoding="utf-8")
    h = Hub(root)
    h.kernel.seed()
    release_all(h, keep_frame_files=False)
    fin = doc(h, "finance-policy")
    assert h.releases.current()["released_by"] == "bob" and cited_by_links(h, fin) == ["DOC-0001"] and link_events(h, fin) == ["hub"]


def test_a_stale_citation_in_an_sop_the_change_order_does_not_touch_is_not_put_to_its_approver(hub):
    co = new_co(hub, title="Cite an archive")
    cite(hub, co, "The archive is DOC-9999.")
    released(hub, co)
    co2 = new_co(hub, title="Document control, reissued")
    revise(hub, co2, "document-control-sop", "# Document control SOP\n", "# Document control SOP\n\nIssued for the whole site.\n")
    cc.request_review(hub, co2["number"], "alice", panel=[])
    assert [f["frame"] for f in reference_findings(eco.preview(hub, co2["number"]))] == ["finance-policy"], "still a finding on the compile"
    assert not [w for w in cc.co_warnings(hub, co2["number"]) if w["code"] == "reference"], \
        "its owner heard of it with the release that made it: co2's approver is not asked"


def test_a_link_or_a_flag_is_no_new_content_for_a_reviewing_cog(hub):
    from types import SimpleNamespace

    from hubproto.cogs import ReviewerCog

    fin = doc(hub, "finance-policy")
    ctx = SimpleNamespace(kernel=hub.kernel, record=fin["locator"], dms=None)
    before = ReviewerCog._content_key(ctx)
    hub.kernel.set_fields(fin["locator"], {"cites": ["rec:document:DOC-0001"], "citation_flags": [{"document": "DOC-0001", "line": 1}]}, by="hub")
    assert ReviewerCog._content_key(ctx) == before, "the cog's judgment of the document still stands"
