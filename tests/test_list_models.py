"""The list_models tool (issue #79): a cog is told which models this installation has (each installed Ollama model with
its size, family and quantization, whether it reads pictures, whether it runs here or is one of Ollama's cloud models),
the Claude models only while hosted models are on, and which model each cog uses now. It is a look tool offered to every
cog; the review of a change order that changes a cog profile is given the same listing. Ollama is stubbed: nothing is
sent anywhere."""

import pytest

from hubproto import changecontrol as cc
from hubproto import model, sopdocs
from hubproto.agent import AgentStore, AgentTools, look_specs
from hubproto.cogs import RunContext, capabilities_text, knowledge_base_text

OFF = "runs on Ollama's servers, not this machine"

# What the stubbed Ollama lists (as model.ollama_models gives it) and what its /api/show says of each model.
INSTALLED = [
    {"name": "gemma4:12b-it-qat", "params": "12B", "family": "gemma4", "quant": "Q4_0", "size_gb": 7.2, "cloud": False},
    {"name": "qwen3:14b", "params": "14.8B", "family": "qwen3", "quant": "Q4_K_M", "size_gb": 9.3, "cloud": False},
    {"name": "gemma4:31b-cloud", "params": "31B", "family": "gemma4", "quant": "", "size_gb": 0.0, "cloud": True},
]
SHOW = {"gemma4:12b-it-qat": {"capabilities": ["completion", "vision", "tools"]},
        "qwen3:14b": {"capabilities": ["completion", "tools"]},
        "gemma4:31b-cloud": {"capabilities": ["completion", "vision"], "remote_host": "https://ollama.com:443", "remote_model": "gemma4:31b"}}


@pytest.fixture
def ollama(monkeypatch):
    """A stubbed Ollama: HUBPROTO_OLLAMA_URL is set (never contacted), ``ollama_models`` lists INSTALLED and ``_ollama_show``
    answers from SHOW. Hosted models are off and no Anthropic key is set unless a test says so."""
    monkeypatch.setenv("HUBPROTO_OLLAMA_URL", "http://127.0.0.1:9")
    for var in ("HUBPROTO_MODEL_FAKE", "HUBPROTO_HOSTED_MODELS", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(model, "_TAGS", {"at": 0.0, "models": None, "error": ""})
    monkeypatch.setattr(model, "ollama_models", lambda refresh=False: ([dict(m) for m in INSTALLED], ""))

    def show(name, max_age=model.SHOW_TTL, need=""):
        rec = SHOW.get(name)
        return None if rec is None else {"at": 0.0, "remote_host": "", "remote_model": "", "context_length": 32768, **rec}
    monkeypatch.setattr(model, "_ollama_show", show)


def tools(hub, cog="qa-manager"):
    return AgentTools(hub, cog, "t-models", "derived", 8, AgentStore(hub.root / "state" / "agents"), requester="carol")


def by_model(rows):
    return {r["model"]: r for r in rows}


def test_the_tool_lists_the_installed_models_with_size_family_and_quantization(released, ollama):
    out = tools(released).execute("list_models", {})
    assert "error" not in out, out
    ollama_rows = by_model(out["ollama"])
    assert set(ollama_rows) == {"ollama/gemma4:12b-it-qat", "ollama/qwen3:14b", "ollama/gemma4:31b-cloud"}, out["ollama"]
    qwen = ollama_rows["ollama/qwen3:14b"]
    assert (qwen["params"], qwen["family"], qwen["quantization"], qwen["size_gb"]) == ("14.8B", "qwen3", "Q4_K_M", 9.3), qwen
    assert qwen["runs"] == "local" and qwen["hosted"] is False and qwen["available"] is True
    assert qwen["reads_pictures"] is False, "qwen3:14b reads text only (Ollama reports no vision)"
    assert ollama_rows["ollama/gemma4:12b-it-qat"]["reads_pictures"] is True, "Ollama reports vision for gemma4"


def test_a_cloud_model_is_marked_hosted(released, ollama, monkeypatch):
    cloud = by_model(tools(released).execute("list_models", {})["ollama"])["ollama/gemma4:31b-cloud"]
    assert cloud["hosted"] is True and cloud["runs"] == f"hosted: {OFF}", cloud
    assert cloud["available"] is False and "hosted models are off" in cloud["note"], "a cloud model cannot be used while hosted models are off"
    assert "size_gb" not in cloud, "nothing of a cloud model is on this machine"
    monkeypatch.setenv("HUBPROTO_HOSTED_MODELS", "on")
    cloud = by_model(tools(released).execute("list_models", {})["ollama"])["ollama/gemma4:31b-cloud"]
    assert cloud["hosted"] is True and cloud["available"] is True and "note" not in cloud, cloud


def test_claude_models_are_listed_only_when_hosted_models_are_on(released, ollama, monkeypatch):
    out = tools(released).execute("list_models", {})
    assert "claude" not in out and out["hosted_models"] == "off", out
    assert "hosted models are switched off" in out["claude_note"]
    monkeypatch.setenv("HUBPROTO_HOSTED_MODELS", "on")
    out = tools(released).execute("list_models", {})
    claude = by_model(out["claude"])
    assert set(claude) == set(model.CLAUDE_MODELS) and out["hosted_models"] == "on", out
    assert all(not c["available"] and "ANTHROPIC_API_KEY" in c["note"] for c in claude.values()), "no key: listed but not available"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-key")
    claude = by_model(tools(released).execute("list_models", {})["claude"])
    assert all(c["available"] and c["hosted"] and c["reads_pictures"] for c in claude.values()), claude


def test_each_cogs_current_model_is_shown(released, ollama):
    cogs = {c["cog"]: c for c in tools(released).execute("list_models", {})["cogs"]}
    profiles = released.current_bundle()["cogs"]
    assert set(cogs) == set(profiles), "every cog with a profile in the release in force"
    ap = cogs["ap-reviewer"]
    assert ap["model"] == profiles["ap-reviewer"]["model"] == "ollama/gemma4:12b-it-qat" and ap["available"] and ap["runs"] == "local", ap
    senior = next(c for n, c in cogs.items() if profiles[n]["model"].startswith("claude"))
    assert senior["available"] is False and "Anthropic key" in senior["note"], senior


def test_the_tool_only_reads(released, ollama):
    t = tools(released)
    before = len(released.kernel.query())
    out = t.execute("list_models", {})
    assert "error" not in out and out["ollama"], out
    assert t.actions == 0 and len(released.kernel.query()) == before, "a look tool: no action spent, nothing written"


def test_the_tool_is_offered_to_cogs(released):
    assert "list_models" in {s["name"] for s in look_specs()}, "a look tool, with the other record tools"
    _, specs, _, _ = released.chat_tools("qa-manager", "carol", "chat-t-models", "which models are installed?")
    assert "list_models" in {s["name"] for s in specs}, "offered in a chat turn"
    bundle = released.current_bundle()
    ctx = RunContext(run_id="t", release="", bundle=bundle, owner="qa-manager", kernel=released.kernel, runner=released.runner,
                     inbox=released.inbox, record=None, dms=released.dms, frames=lambda: released.released_frames(), model=released.model,
                     extras={"cog_profile": bundle["cogs"]["qa-manager"]})
    assert "list_models" in capabilities_text(ctx) and "list_models" in knowledge_base_text(ctx), "the cog is told it has the tool"


def test_the_review_of_a_cog_profile_change_is_given_the_installed_models(released, ollama, monkeypatch):
    sopdocs.convert(released, by="carol")
    out = released.propose_cog_model("ap-reviewer", "ollama/qwen3:14b", by="alice")
    seen = {}

    def reviewer(hub_, co_, cog, brief):
        seen[cog] = brief["text"]
        return "- [advisory] general: checked the model"
    monkeypatch.setattr(cc, "REVIEWER", reviewer)
    cc.request_review(released, out["change_order"], "alice", panel=["qa-manager"])
    text = seen["qa-manager"]
    assert "This change order changes a cog profile" in text, text
    assert "- ollama/qwen3:14b (14.8B qwen3, Q4_K_M, 9.3 GB; local; reads text only)" in text, text
    assert f"- ollama/gemma4:31b-cloud (31B gemma4; hosted: {OFF}; reads pictures): not available while hosted models are off" in text, text
    assert "- ap-reviewer: ollama/gemma4:12b-it-qat (local)" in text, "the cog's model in force"
    assert "no Claude model is offered" in text


def test_a_review_of_another_change_is_not_given_the_models(released, ollama, monkeypatch):
    from test_change_control import revise

    sopdocs.convert(released, by="carol")
    co = released.kernel.create("change-order", "Quality/Changes", {"title": "Approvals", "description": "why", "requested_by": "carol"}, by="carol")
    revise(released, co, "document-control-sop", "Approvals in document-control go to the reviewer.",
           "Approvals in document-control go to the quality manager.", by="carol")
    seen = {}
    monkeypatch.setattr(cc, "REVIEWER", lambda hub_, co_, cog, brief: seen.setdefault(cog, brief["text"]) and "")
    cc.request_review(released, co["number"], "carol", panel=["qa-manager"])
    assert "qa-manager" in seen and "changes a cog profile" not in seen["qa-manager"], seen
