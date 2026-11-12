"""Ollama's cloud models (issue #52): a tag such as gemma4:cloud or gemma4:31b-cloud is called through the local Ollama
but runs on Ollama's servers, so the prompt and the records in it leave the machine. It is a hosted model: refused while
hosted models are off, and labelled as leaving the machine wherever a model is named when they are on. Local tags are
unchanged. Every test talks to a fake Ollama on port 0 (its /api/tags, /api/show and /api/chat); nothing is sent anywhere."""

import html as _html
import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from conftest import release_all
from hubproto import model
from hubproto.app import Hub
from hubproto.model import ModelGateway, ModelUnavailable

ROOT = Path(__file__).resolve().parents[1]
OFF = "runs on Ollama's servers, not this machine"
REFUSED = OFF + ", and hosted models are switched off here"
REMOTE = {"remote_host": "https://ollama.com:443"}

# What the fake Ollama has: two local models, two cloud tags, and one cloud model under a name with no cloud tag (only
# Ollama's /api/show says where it runs).
MODELS = {
    "gemma4:12b-it-qat": {"size": 7_151_003_754, "details": {"parameter_size": "12B", "family": "gemma4", "quantization_level": "Q4_0"}},
    "qwen3:14b": {"size": 9_276_198_565, "details": {"parameter_size": "14.8B", "family": "qwen3", "quantization_level": "Q4_K_M"}},
    "gemma4:cloud": {"size": 384, "details": {"family": "gemma4"}, **REMOTE, "remote_model": "gemma4"},
    "gemma4:31b-cloud": {"size": 384, "details": {"parameter_size": "31B", "family": "gemma4"}, **REMOTE, "remote_model": "gemma4:31b"},
    "team-reader:latest": {"size": 384, "details": {"family": "gemma4"}},  # /api/tags says nothing of where it runs
}
SHOW_REMOTE = {"gemma4:cloud", "gemma4:31b-cloud", "team-reader", "team-reader:latest"}


@pytest.fixture
def ollama(monkeypatch):
    """A fake Ollama at HUBPROTO_OLLAMA_URL; ``chats`` holds every /api/chat body it was sent."""
    seen = {"chats": [], "shows": []}

    class Fake(BaseHTTPRequestHandler):
        def _send(self, status, body):
            out = json.dumps(body).encode()
            self.send_response(status); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

        def do_GET(self):
            if self.path == "/api/tags":
                return self._send(200, {"models": [{"name": n, "model": n, **m} for n, m in MODELS.items()]})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
            if self.path == "/api/show":
                name = body.get("model") or ""
                seen["shows"].append(name)
                if name not in MODELS and f"{name}:latest" not in MODELS:
                    return self._send(404, {"error": f"model '{name}' not found"})
                where = {**REMOTE, "remote_model": name} if name in SHOW_REMOTE else {}
                return self._send(200, {"capabilities": ["completion", "tools"], "model_info": {"x.context_length": 32768}, **where})
            if self.path == "/api/chat":
                seen["chats"].append(body)
                return self._send(200, {"message": {"role": "assistant", "content": "Looks fine.\nVerdict: pass"}, "prompt_eval_count": 3, "eval_count": 2})
            self._send(404, {"error": "not found"})

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("HUBPROTO_OLLAMA_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    for var in ("HUBPROTO_MODEL_FAKE", "HUBPROTO_HOSTED_MODELS", "ANTHROPIC_API_KEY", "HUBPROTO_EXTRACTOR_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(model, "_TAGS", {"at": 0.0, "models": None, "error": ""})  # this Ollama's list, not another test's
    try:
        yield seen
    finally:
        srv.shutdown()
        srv.server_close()


def ask(name):
    return ModelGateway().complete(model=name, system="You review.", user="Review this.", by="quality-assistant")


@pytest.mark.parametrize("name", ["ollama/gemma4:cloud", "ollama/gemma4:31b-cloud", "gemma4:31b-cloud"])
def test_a_cloud_tag_is_refused_without_hosted_models(ollama, name):
    assert model.backend_for(name) == "none", "a cloud tag is hosted: with hosted models off nothing serves it, not even the stand-in"
    res = model.resolve_model(name)
    assert not res["available"] and REFUSED in res["note"] and res["note"].startswith(name), res
    with pytest.raises(ModelUnavailable, match=f"{name} {REFUSED}"):
        ask(name)
    with pytest.raises(ModelUnavailable, match=REFUSED):
        ModelGateway().complete_with_tools(model=name, system="s", user="u", tools=[], call_tool=lambda n, a: {})
    assert ollama["chats"] == [], "nothing was sent to Ollama for the cloud model"


def test_a_cloud_tag_fails_closed_without_an_ollama_to_ask(monkeypatch):
    monkeypatch.delenv("HUBPROTO_OLLAMA_URL", raising=False)
    monkeypatch.delenv("HUBPROTO_HOSTED_MODELS", raising=False)
    monkeypatch.delenv("HUBPROTO_MODEL_FAKE", raising=False)
    assert model.backend_for("ollama/gemma4:cloud") == "none", "the tag alone makes it hosted: never stood in for"
    assert REFUSED in model.resolve_model("ollama/gemma4:cloud")["note"]
    assert model.backend_for("ollama/gemma4:12b-it-qat") == "stand-in", "a local tag still falls back to the development stand-in"


def test_ollama_saying_a_model_runs_remotely_makes_it_hosted_whatever_its_name(ollama, monkeypatch):
    assert model.backend_for("ollama/team-reader") == "none", "Ollama's /api/show names a remote host for it"
    assert REFUSED in model.resolve_model("ollama/team-reader")["note"]
    listed = {m["name"]: m for m in model.ollama_models(refresh=True)[0]}
    assert listed["team-reader:latest"]["cloud"] and not listed["qwen3:14b"]["cloud"]
    monkeypatch.setenv("HUBPROTO_HOSTED_MODELS", "on")
    assert model.backend_for("ollama/team-reader") == "ollama-cloud"


def test_with_hosted_models_on_a_cloud_model_is_labelled_as_leaving_the_machine(ollama, monkeypatch):
    monkeypatch.setenv("HUBPROTO_HOSTED_MODELS", "on")
    assert model.backend_for("ollama/gemma4:31b-cloud") == "ollama-cloud"
    res = model.resolve_model("ollama/gemma4:31b-cloud")
    assert res["available"] and res["leaves_machine"] and res["backend"] == "ollama-cloud" and res["note"] == f"hosted: {OFF}", res
    out = ask("ollama/gemma4:31b-cloud")
    assert out["backend"] == "ollama-cloud" and out["leaves_machine"] is True and not out["stand_in"], "the call's record says it left"
    assert [c["model"] for c in ollama["chats"]] == ["gemma4:31b-cloud"]
    choices = {c["value"]: c for c in model.available_models(refresh=True)["choices"]}
    cloud = choices["ollama/gemma4:31b-cloud"]
    assert cloud["available"] and cloud["cloud"] and OFF in cloud["label"] and "local" not in cloud["label"], cloud
    order = list(choices)
    assert order.index("ollama/gemma4:cloud") > order.index("ollama/gemma4:12b-it-qat"), "a cloud model is listed after the local ones"


@pytest.mark.parametrize("hosted", ["", "on"])
@pytest.mark.parametrize("name", ["ollama/gemma4:12b-it-qat", "ollama/qwen3:14b", "qwen3:14b"])
def test_local_tags_are_unchanged(ollama, monkeypatch, hosted, name):
    monkeypatch.setenv("HUBPROTO_HOSTED_MODELS", hosted)
    assert model.backend_for(name) == "ollama"
    res = model.resolve_model(name)
    assert res["available"] and res["backend"] == "ollama" and res["note"] == "local" and res["leaves_machine"] is False, res
    out = ask(name)
    assert out["backend"] == "ollama" and out["leaves_machine"] is False and out["text"].startswith("Looks fine")
    assert [c["model"] for c in ollama["chats"]] == [model.served_name(name)]
    choices = model.available_models(refresh=True)["choices"]
    local = [c["value"] for c in choices if c["label"].endswith(", local)")]
    assert local == ["ollama/qwen3:14b", "ollama/gemma4:12b-it-qat"], "only the models on this machine are labelled local, largest first"
    assert all(c["available"] for c in choices if c["value"] in local)


def test_the_sop_reader_is_never_a_cloud_model_in_place_of_a_local_one(ollama, monkeypatch):
    from hubproto.llm_extract import local_model

    assert local_model() == "ollama/qwen3:14b"
    monkeypatch.setenv("HUBPROTO_EXTRACTOR_MODEL", "ollama/gemma4:cloud")
    assert local_model() == "ollama/qwen3:14b", "a cloud model named for reading SOPs is refused while hosted models are off"
    monkeypatch.setenv("HUBPROTO_HOSTED_MODELS", "on")
    assert local_model() == "ollama/gemma4:cloud"


# ---------------------------------------------------------------- where a person sees who answers for a cog
@pytest.fixture
def cloud_hub(tmp_path, ollama):
    """A released hub whose quality-assistant is bundled with ollama/gemma4:31b-cloud."""
    root = tmp_path / "hub"
    shutil.copytree(ROOT / "frames", root / "frames")
    cogs = root / "frames" / "company" / "cogs.frame.md"
    text = cogs.read_text()
    old = "## Cog: quality-assistant\n"
    at = text.index(old)
    line = text.index("Model: ollama/gemma4:12b-it-qat", at)
    cogs.write_text(text[:line] + "Model: ollama/gemma4:31b-cloud" + text[line + len("Model: ollama/gemma4:12b-it-qat"):])
    hub = Hub(root)
    hub.kernel.seed()
    release_all(hub)
    assert hub.releases.bundle(hub.releases.current()["digest"])["cogs"]["quality-assistant"]["model"] == "ollama/gemma4:31b-cloud"
    return hub


def test_the_models_command_says_a_cloud_model_runs_on_ollamas_servers(cloud_hub, monkeypatch):
    from test_cli import run_cli

    rc, out = run_cli(cloud_hub.root, "models")
    lines = {ln.split()[0]: ln for ln in out.splitlines() if ln.strip()}
    assert rc == 0, out
    assert f"cloud: {OFF} (not available: hosted models are off)" in lines["ollama/gemma4:31b-cloud"], out
    assert "GB" in lines["ollama/gemma4:12b-it-qat"] and "cloud" not in lines["ollama/gemma4:12b-it-qat"], "a local model is listed as before"
    assert f"NOT AVAILABLE: ollama/gemma4:31b-cloud {REFUSED}" in lines["quality-assistant"], out
    assert "-> gemma4:12b-it-qat (ollama)" in lines["ap-reviewer"], "a cog on a local model is served as before"
    monkeypatch.setenv("HUBPROTO_HOSTED_MODELS", "on")
    rc, out = run_cli(cloud_hub.root, "models")
    lines = {ln.split()[0]: ln for ln in out.splitlines() if ln.strip()}
    assert f"-> gemma4:31b-cloud (ollama-cloud: {OFF})" in lines["quality-assistant"], out
    assert lines["ollama/gemma4:31b-cloud"].endswith(f"cloud: {OFF}"), out


def test_the_cog_profile_page_says_a_cloud_model_leaves_the_machine(cloud_hub, monkeypatch):
    from hubproto.shell import serve_in_thread
    from test_shell_views import get, main_of, plain

    server = serve_in_thread(cloud_hub, "127.0.0.1", 0)
    shell = {"base": f"http://127.0.0.1:{server.server_address[1]}"}
    try:
        status, html = get(shell, "/user/quality-assistant", **{"as": "carol"})
        page = _html.unescape(plain(main_of(html)))
        assert status == 200
        assert "quality-assistant is not available." in page and f"ollama/gemma4:31b-cloud {REFUSED}" in page, page
        assert f"Ollama cloud model: it {OFF}" in page, "the Model row says where it would run"
        assert f"Ollama cloud: {OFF} · not available while hosted models are off" in page, "the picker offers no cloud tag as local"
        assert "value='ollama/gemma4:cloud' disabled" in html
        monkeypatch.setenv("HUBPROTO_HOSTED_MODELS", "on")
        page = _html.unescape(plain(main_of(get(shell, "/user/quality-assistant", **{"as": "carol"})[1])))
        assert "quality-assistant is not available" not in page and f"Ollama cloud model: it {OFF}; its prompts and the records in them leave this machine" in page, page
        page = _html.unescape(plain(main_of(get(shell, "/user/ap-reviewer", **{"as": "carol"})[1])))
        assert "12B gemma4, local via Ollama" in page and f"gemma4:31b-cloud Ollama cloud: {OFF}" in page, page
    finally:
        server.shutdown()
        server.server_close()


def test_proposing_a_cloud_model_for_a_cog_says_it_leaves_the_machine(released, ollama, monkeypatch):
    from hubproto.agent import AgentStore, AgentTools

    tools = AgentTools(released, "qa-manager", "t-cloud", "act", 8, AgentStore(released.root / "state" / "agents"), requester="carol")
    out = tools.execute("propose_cog_model", {"cog": "quality-assistant", "model": "ollama/gemma4:31b-cloud", "reason": "bigger"})
    assert "cannot be used here" in out["error"] and REFUSED in out["error"], out
    with pytest.raises(ValueError, match=REFUSED):
        released.propose_cog_model("quality-assistant", "ollama/gemma4:31b-cloud", by="carol")
    with pytest.raises(ValueError, match=REFUSED):
        released.propose_new_cog(name="cloud-helper", roles=["staff"], model="ollama/gemma4:cloud", about="Helps.", persona="", by="carol")
    out = tools.execute("propose_cog_model", {"cog": "quality-assistant", "model": "ollama/qwen3:14b", "reason": "local"})
    assert out["ok"] and out["leaves_machine"] is False and "where" not in out, "a local model is proposed as before"
    monkeypatch.setenv("HUBPROTO_HOSTED_MODELS", "on")
    out = tools.execute("propose_cog_model", {"cog": "ap-reviewer", "model": "ollama/gemma4:31b-cloud", "reason": "bigger"})
    assert out["ok"] and out["leaves_machine"] is True and out["where"].startswith(f"ollama/gemma4:31b-cloud {OFF}"), out
