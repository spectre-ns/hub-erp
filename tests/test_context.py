"""What a cog has in front of it: the window comes from the model, the governing process (its steps and the cog's part
in it, quoted) rather than whole SOPs, an index of the SOPs it follows, and its prompt discloses its tools, what it may do
(derived from the SOPs), what it may sign and how people reach it."""

import shutil

import pytest

from conftest import ROOT, release_all
from hubproto import model as model_mod
from hubproto.app import Hub
from hubproto.cogs import RunContext, capabilities_text, cog_context, governing_sop


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    root = tmp_path_factory.mktemp("hub")
    shutil.copytree(ROOT / "frames", root / "frames")
    h = Hub(root)
    h.kernel.seed()
    release_all(h)
    return h


def ctx_for(hub, cog: str, **extras) -> RunContext:
    bundle = hub.releases.bundle(hub.releases.current()["digest"])
    return RunContext(run_id="t", release="", bundle=bundle, owner=cog, kernel=hub.kernel, runner=hub.runner, inbox=hub.inbox,
                      dms=hub.dms, frames=lambda: hub.frames, model=hub.model, extras={"cog_profile": bundle["cogs"][cog], **extras})


def test_the_window_comes_from_the_model(monkeypatch):
    monkeypatch.setattr(model_mod, "backend_for", lambda m: "ollama")
    monkeypatch.setenv("HUBPROTO_OLLAMA_URL", "http://127.0.0.1:9")  # nothing answers there: Ollama's record of the model is this one
    monkeypatch.setitem(model_mod._SHOW, ("http://127.0.0.1:9", "qwen3:14b"), {"at": model_mod._clock(), "capabilities": ["completion"], "remote_host": "",
                                                                             "remote_model": "", "context_length": 40960})
    monkeypatch.delenv("HUBPROTO_MODEL_CTX", raising=False)
    monkeypatch.delenv("HUBPROTO_MODEL_MAX_CHARS", raising=False)
    assert model_mod.num_ctx_for("ollama/qwen3:14b") == 40960
    assert model_mod.prompt_budget_chars("ollama/qwen3:14b", 1200) == int((40960 - 1200 - 1024) * 3.2)
    monkeypatch.setenv("HUBPROTO_MODEL_CTX", "16384")
    assert model_mod.num_ctx_for("ollama/qwen3:14b") == 16384
    monkeypatch.setenv("HUBPROTO_MODEL_CTX", "999999")
    assert model_mod.num_ctx_for("ollama/qwen3:14b") == 40960  # never past what the model was trained for


def test_a_cog_works_from_its_process_not_whole_sops(hub):
    ctx = ctx_for(hub, "qa-manager")
    follows = ctx.profile["follows"]
    texts = ctx.sop_texts()
    doc = hub.dms.create(title="Context check", doc_class="controlled", folder="Quality/Controlled Documents", by="alice",
                         content=b"# Context check\n", filename="c.md", mime="text/markdown", doc_type="sop")
    rec = hub.kernel.get(doc["locator"])
    assert governing_sop(ctx, rec) == "document-control-sop"
    prompt = cog_context(ctx, rec, task="review")
    assert "THE PROCESS THAT GOVERNS THIS WORK: document-control" in prompt and "Its steps:" in prompt
    mine = prompt.split("YOUR PART IN IT")[1].split("THE PROCEDURES YOU ARE TRAINED ON")[0]
    assert "The qa-manager cog may approve document-control documents when 3 adversarial reviews pass, countersigned by a quality manager." in mine
    assert "(document-control-sop line " in mine, "each sentence with where it is, to cite"
    index = prompt.split("THE PROCEDURES YOU ARE TRAINED ON")[1].split("== YOUR AUTHORITY ==")[0]
    assert all(f"- {n}: " in index for n in follows if n != "document-control-sop") and "read_sop_section" in index
    # Not whole SOPs: the prose of an SOP it follows, outside the governing process, is not in front of it.
    capa = texts["capa-sop"].split("## ")[0].strip().splitlines()[-1]
    assert capa and capa not in prompt
    assert len(prompt) < sum(len(t) for t in texts.values()) / 2


def test_a_small_window_trims_and_says_so(hub):
    ctx = ctx_for(hub, "qa-manager")
    prompt = cog_context(ctx, None, sop_chars=400)
    assert "the window is full" in prompt


def test_the_prompt_discloses_tools_criteria_and_how_to_reach_the_cog(hub):
    ctx = ctx_for(hub, "qa-manager")
    text = capabilities_text(ctx)
    assert "derived from the SOPs, action by action" in text and "In this reply you cannot call tools" in text
    for name in ("search_records", "read_document", "comment", "take_turn", "draft_document", "complete_step", "read_sop_section", "finish"):
        assert f"- {name}:" in text
    assert "its review passes, countersigned by a quality manager" in text and "document-control-sop line" in text
    assert "@mention" in text and "Assign" in text
    from hubproto.tools import TOOLS

    now = capabilities_text(ctx, tools_now=TOOLS)
    assert "Tools you can call now" in now and "Not available in this conversation:" in now and "draft_document" in now.split("Not available")[1]
    reader = capabilities_text(ctx_for(hub, "doc-reviewer"))
    assert "Only when someone confirms" in reader and "The doc-reviewer cog may comment on anything in Quality when a person confirms." in reader
    it = capabilities_text(ctx_for(hub, "it-admin"))
    assert "What the SOPs forbid you" in it and "The it-admin cog may not approve documents in Policy." in it


def test_a_cog_without_criteria_is_told_it_does_not_sign(hub):
    ctx = ctx_for(hub, "purchasing-assistant")
    bundle = dict(ctx.bundle)
    bundle["approval_criteria"] = [c for c in bundle["approval_criteria"] if c["approver"] != "cog:purchasing-assistant"]
    ctx.bundle = bundle
    assert "You may not sign approvals" in capabilities_text(ctx)


def test_agent_runs_and_chat_get_the_tools_they_can_call(hub, monkeypatch):
    from hubproto.agent import Agent

    prep = Agent(hub, "quality-assistant").prepare("list the open nonconformances", by="carol")
    names = {t["name"] for t in prep["specs"]}
    assert {"search_records", "comment", "take_turn", "finish"} <= names
    assert "Tools you can call now" in prep["system"] and all(f"- {n}:" in prep["system"] for n in names)


def test_models_are_listed_as_ollama_serves_them_largest_first(monkeypatch):
    served = [{"name": "granite4.2:3b", "params": "3.7B", "family": "granite", "quant": "Q4_K_M", "size_gb": 2.2},
              {"name": "qwen3:14b", "params": "14.8B", "family": "qwen3", "quant": "Q4_K_M", "size_gb": 9.3}]
    monkeypatch.setattr(model_mod, "ollama_models", lambda refresh=False: (served, ""))
    choices = model_mod.available_models()["choices"]
    assert [c["value"] for c in choices[:2]] == ["ollama/qwen3:14b", "ollama/granite4.2:3b"]
