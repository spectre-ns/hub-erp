"""Which Frames depend on which SOPs, and who layers them: scopes compile Frames and layer on wider scopes; personas
(cogs and job roles) layer scopes and Frames; a cog follows what its persona layers; a role's persona is required
reading; the release names each Frame's source; the pages show it all both ways."""

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


def test_scopes_layer_and_personas_decide_what_cogs_follow(shell):
    b = bundle(shell["hub"])
    assert b["scopes"]["quality"]["layers_on"] == ["company"]
    assert b["scopes"]["quality"]["frames"][:3] == ["org-model", "quality-manual", "tasks-sop"], "a department scope brings the company's"
    assert b["cogs"]["qa-manager"]["persona"] == "qa-manager"
    assert "document-control-sop" in b["cogs"]["qa-manager"]["follows"] and "org-model" in b["cogs"]["qa-manager"]["follows"]
    d = sc.dependents(b, "document-control-sop")
    assert "quality" in d["direct_scopes"] and {"name": "qa-manager", "of": "cog"} in d["personas"]
    assert {"name": "document-controller", "of": "role"} in d["personas"]
    im = sc.impact(b, ["finance-policy"])
    assert {p["name"] for p in im["personas"]} >= {"ap-reviewer", "finance-clerk", "controller"}


def test_the_release_names_each_frames_source(shell):
    hub = shell["hub"]
    src = sc.frame_sources(hub)
    assert src["finance-policy"]["file"].endswith("finance-policy.frame.md")
    sopdocs.convert(hub, by="carol")
    p = hub.compile(save=False)
    fin = next(f for f in p["frames"] if f["name"] == "finance-policy")
    assert fin["source"]["document"].startswith("DOC-") and fin["source"]["revision"] == "A"


def test_a_role_persona_is_required_reading(shell):
    b = bundle(shell["hub"])
    reading = {r["frame"]: r["roles"] for r in sc.required_reading(b, "carol")}
    assert "document-control-sop" in reading and "quality-manager" in reading["ncr-sop"]
    status, text = get(shell, "/user/carol", **{"as": "carol"})
    assert "Required reading" in main_of(text) and "document-control-sop" in main_of(text)


def test_the_pages_show_the_links_both_ways(shell):
    status, text = get(shell, "/scopes", **{"as": "carol"})
    body = main_of(text)
    assert status == 200 and "scope-quality" in body and "Cog personas" in body and "Role personas" in body
    status, text = get(shell, "/frame/document-control-sop", **{"as": "carol"})
    body = main_of(text)
    assert "Depends and depended on" in body and "Layered by" in body and "qa-manager" in body and "quality" in body


def test_a_persona_may_only_layer_what_exists(shell):
    from hubproto.guards import scope_guard

    rules = [{"kind": "scope", "name": "s", "level": "department", "compiles": ["nope"], "layers_on": ["missing"], "source": {"frame": "x", "line": 1}},
             {"kind": "persona", "name": "ghost", "of": "cog", "layers": ["s", "zzz"], "reads": [], "source": {"frame": "x", "line": 2}}]
    found = [f.detail for f in scope_guard(rules, {"x"})]
    assert any("compiles 'nope'" in d for d in found) and any("layers on 'missing'" in d for d in found)
    assert any("for a cog the organization does not have" in d for d in found) and any("'zzz'" in d for d in found)


def test_a_cog_with_a_persona_is_not_warned_that_it_follows_nothing(shell):
    """The cog profiles carry no Follows lines: each cog follows its persona's Frames. Only a cog with neither is warned
    that it will be routed nothing."""
    hub = shell["hub"]
    p = hub.compile(save=False)
    assert not [f for f in p["findings"] if "follows no SOP" in f["detail"]], [f["detail"] for f in p["findings"] if "follows no SOP" in f["detail"]]
    from hubproto.guards import consistency_guard

    rules = [r for r in p["rules"] if not (r["kind"] == "persona" and r["name"] == "qa-manager")]
    found = [f.detail for f in consistency_guard(rules, set(hub.registry.ids()))]
    assert any("cog qa-manager follows no SOP and has no persona" in d for d in found)
