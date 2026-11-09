"""Which Frames depend on which SOPs, and who works from them: scopes compile Frames and layer on wider scopes; a cog
follows the company scope and every SOP whose rules name it or its roles; a person's required reading is derived the same
way (and from the work instructions written for their roles); the release names each Frame's source; the pages show it all
both ways."""

import shutil

import pytest

from conftest import ROOT, release_all
from hubproto import scopes as sc
from hubproto import sopdocs
from hubproto.app import Hub
from hubproto.shell import free_port, serve_in_thread
from test_shell_views import get, main_of


@pytest.fixture
def shell(tmp_path):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    hub = Hub(tmp_path)
    hub.kernel.seed()
    release_all(hub)
    port = free_port()
    server = serve_in_thread(hub, "127.0.0.1", port)
    yield {"hub": hub, "base": f"http://127.0.0.1:{port}"}
    server.shutdown()
    server.server_close()


def bundle(hub):
    return hub.releases.bundle(hub.releases.current()["digest"])


def test_scopes_layer_and_the_sops_that_name_a_cog_decide_what_it_follows(shell):
    b = bundle(shell["hub"])
    assert b["scopes"]["quality"]["layers_on"] == ["company"]
    assert b["scopes"]["quality"]["frames"][:3] == ["org-model", "quality-manual", "tasks-sop"], "a department scope brings the company's"
    assert b["cogs"]["qa-manager"]["persona"] == "author"
    assert "document-control-sop" in b["cogs"]["qa-manager"]["follows"] and "org-model" in b["cogs"]["qa-manager"]["follows"]
    d = sc.dependents(b, "document-control-sop")
    assert "quality" in d["direct_scopes"] and "qa-manager" in d["cogs"] and "doc-reviewer" in d["cogs"]
    assert "document-controller" in d["roles"] and "ap-reviewer" not in d["cogs"]
    im = sc.impact(b, ["finance-policy"])
    assert {p["name"] for p in im["readers"]} >= {"ap-reviewer", "finance-clerk", "controller"}
    persona = sc.dependents(b, "persona-reviewer")
    assert persona["persona"] == "reviewer" and set(persona["behave"]) == {"ap-reviewer", "doc-reviewer", "quality-assistant"}


def test_the_release_names_each_frames_source(shell):
    hub = shell["hub"]
    src = sc.frame_sources(hub)
    assert src["finance-policy"]["file"].endswith("finance-policy.frame.md")
    sopdocs.convert(hub, by="carol")
    p = hub.compile(save=False)
    fin = next(f for f in p["frames"] if f["name"] == "finance-policy")
    assert fin["source"]["document"].startswith("DOC-") and fin["source"]["revision"] == "A"


def test_required_reading_is_derived_from_the_roles(shell):
    b = bundle(shell["hub"])
    reading = {r["frame"]: r["roles"] for r in sc.required_reading(b, "carol")}
    assert "document-control-sop" in reading and "quality-manager" in reading["ncr-sop"]
    assert reading["org-model"] and list(reading)[:3] == ["org-model", "quality-manual", "tasks-sop"]
    assert "wi-review-a-document" in reading and "reviewer" in reading["wi-review-a-document"], "a work instruction's For: line"
    assert "it-sop" not in reading, "carol is no system administrator"
    assert "it-sop" in {r["frame"] for r in sc.required_reading(b, "bob")}, "bob is"
    status, text = get(shell, "/user/carol", **{"as": "carol"})
    assert "Required reading" in main_of(text) and "document-control-sop" in main_of(text)


def test_the_pages_show_the_links_both_ways(shell):
    status, text = get(shell, "/scopes", **{"as": "carol"})
    body = main_of(text)
    assert status == 200 and "scope-quality" in body and "<h2>Personas" in body and "<h2>Cogs" in body and "<h2>Roles" in body
    assert "persona-reviewer" in body and "scope-it" in body
    status, text = get(shell, "/frame/document-control-sop", **{"as": "carol"})
    body = main_of(text)
    assert "Depends and depended on" in body and "Followed by" in body and "qa-manager" in body and "quality" in body
    assert "Required reading for" in body and "document controller" in body


def test_personas_must_be_used_and_scopes_refer_to_what_exists(shell):
    from hubproto.guards import scope_guard

    rules = [{"kind": "scope", "name": "s", "level": "department", "compiles": ["nope"], "layers_on": ["missing"], "source": {"frame": "x", "line": 1}},
             {"kind": "persona", "name": "ghost", "stance": "reviewer", "behaviour": "b", "source": {"frame": "x", "line": 2}}]
    found = [f.detail for f in scope_guard(rules, {"x"})]
    assert any("compiles 'nope'" in d for d in found) and any("layers on 'missing'" in d for d in found)
    assert any("persona ghost is used by no cog" in d for d in found)


def test_a_cog_named_by_no_sop_is_warned_that_it_will_be_routed_nothing(shell):
    hub = shell["hub"]
    p = hub.compile(save=False)
    assert not [f for f in p["findings"] if "named by no SOP" in f["detail"]], [f["detail"] for f in p["findings"] if "named by no SOP" in f["detail"]]
    from hubproto.guards import consistency_guard

    rules = [r for r in p["rules"] if not (r["kind"] == "permission" and r["subject"] == "role:policy-reader")
             and not (r["kind"] == "committee")]
    found = [f.detail for f in consistency_guard(rules, set(hub.registry.ids()))]
    assert any("cog organization is named by no SOP" in d for d in found)
