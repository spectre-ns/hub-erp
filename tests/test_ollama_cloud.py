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
    "team-local:latest": {"size": 1_000_000_000, "details": {"family": "qwen3"}},  # local, until a test re-points the name
}
SHOW_REMOTE = {"gemma4:cloud", "gemma4:31b-cloud", "team-reader", "team-reader:latest"}


@pytest.fixture
def ollama(monkeypatch):
    """A fake Ollama at HUBPROTO_OLLAMA_URL; ``chats`` holds every /api/chat body it was sent, ``shows`` every model
    /api/show was asked about. A test changes what it says: ``remote`` (the models /api/show names a remote host for),
    ``tags_remote`` (those /api/tags does) and ``show_fail`` (those /api/show answers 500 for)."""
    seen = {"chats": [], "shows": [], "remote": set(SHOW_REMOTE), "tags_remote": set(), "show_fail": set()}

    class Fake(BaseHTTPRequestHandler):
        def _send(self, status, body):
            out = json.dumps(body).encode()
            self.send_response(status); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

        def do_GET(self):
            if self.path == "/api/tags":
                return self._send(200, {"models": [{"name": n, "model": n, **m, **(REMOTE if n in seen["tags_remote"] else {})}
                                                   for n, m in MODELS.items()]})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
            if self.path == "/api/show":
                name = body.get("model") or ""
                seen["shows"].append(name)
                if name in seen["show_fail"]:
                    return self._send(500, {"error": "busy"})
                if name not in MODELS and f"{name}:latest" not in MODELS:
                    return self._send(404, {"error": f"model '{name}' not found"})
                where = {**REMOTE, "remote_model": name} if name in seen["remote"] else {}
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
    monkeypatch.setattr(model, "_TAGS", {"at": 0.0, "models": None, "error": ""})  # this Ollama's answers, not another test's
    monkeypatch.setattr(model, "_SHOW", {})
    monkeypatch.setattr(model, "_SHOW_MISSED", {})
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


@pytest.mark.parametrize("name", ["Ollama/Gemma4:Cloud", "ollama/gemma4:31b-cloud ", "ollama/gemma4:31b-cloud\t", "ollama/gemma4:31b-cloud\u00a0",
                                  " ollama/gemma4:cloud", "hf.co/someone/repo:cloud", "registry.ollama.ai/library/gemma4:31b-cloud"])
def test_a_cloud_tag_in_any_spelling_is_refused(ollama, name):
    """The review's probe (PR #57): case, stray whitespace and a registry path do not hide a cloud tag."""
    assert model.backend_for(name) == "none"
    with pytest.raises(ModelUnavailable, match=REFUSED):
        ask(name)
    assert ollama["chats"] == []


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
    assert local == ["ollama/qwen3:14b", "ollama/gemma4:12b-it-qat", "ollama/team-local:latest"], "only the models on this machine are labelled local, largest first"
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


# ---------------------------------------------------------------- where a model runs is asked again before each call
def test_a_name_re_pointed_at_a_cloud_model_is_never_sent_as_local(ollama, monkeypatch):
    """``ollama cp gemma4:cloud team-local`` after the hub has called team-local as a local model: the next call asks Ollama
    again before it is sent, and is refused while hosted models are off."""
    out = ask("ollama/team-local")
    assert out["backend"] == "ollama" and not out["leaves_machine"] and len(ollama["chats"]) == 1
    ollama["remote"].add("team-local")  # Ollama's /api/show now names a remote host for the name
    with pytest.raises(ModelUnavailable, match=f"ollama/team-local {REFUSED}"):
        ask("ollama/team-local")
    assert len(ollama["chats"]) == 1, "nothing was sent once the name pointed at a cloud model"
    monkeypatch.setenv("HUBPROTO_HOSTED_MODELS", "on")
    out = ask("ollama/team-local")
    assert out["backend"] == "ollama-cloud" and out["leaves_machine"] is True, "with hosted models on, the call's record says it left"
    ollama["remote"].discard("team-local")
    monkeypatch.delenv("HUBPROTO_HOSTED_MODELS")
    with pytest.raises(ModelUnavailable, match=REFUSED):
        ask("ollama/team-local")  # an answer that it runs remotely is kept a while: the safe direction
    clock = [model._clock() + model.SHOW_TTL]
    monkeypatch.setattr(model, "_clock", lambda: clock[0])
    assert ask("ollama/team-local")["backend"] == "ollama", "pointed back at a local model, it is local again once the record is old"


def test_the_listing_flag_counts_for_where_a_model_runs(ollama):
    """A fresh /api/tags that says a name runs remotely makes it hosted for the gateway too, not only for the picker."""
    assert model.backend_for("ollama/team-local") == "ollama"
    ollama["tags_remote"].add("team-local:latest")
    assert [m["cloud"] for m in model.ollama_models(refresh=True)[0] if m["name"] == "team-local:latest"] == [True]
    assert model.backend_for("ollama/team-local") == "none" and REFUSED in model.resolve_model("ollama/team-local")["note"]
    with pytest.raises(ModelUnavailable, match=REFUSED):
        ask("ollama/team-local")
    assert ollama["chats"] == []


def test_when_ollama_does_not_say_where_a_model_runs_nothing_is_sent(ollama, monkeypatch):
    ollama["show_fail"].add("team-reader")  # a cloud model under a custom name; /api/show answers 500, /api/chat works
    out = ask("ollama/team-reader")
    assert out["stand_in"] and "Ollama did not say whether ollama/team-reader runs on this machine" in out["text"], out["text"]
    monkeypatch.delenv("HUBPROTO_ALLOW_STAND_IN")
    with pytest.raises(ModelUnavailable, match="Ollama did not say whether ollama/team-reader runs on this machine or on Ollama's servers"):
        ask("ollama/team-reader")
    with pytest.raises(ModelUnavailable, match="Ollama did not say whether"):
        ModelGateway().complete_with_tools(model="ollama/team-reader", system="s", user="u", tools=[], call_tool=lambda n, a: {})
    assert ollama["chats"] == [], "failing closed: nothing reached /api/chat"
    ollama["show_fail"].clear()
    ollama["remote"].discard("team-reader")
    assert ask("ollama/team-reader")["backend"] == "ollama", "when Ollama answers again, a local model is sent as before"


def test_ollamas_record_of_a_model_is_kept_a_while_and_a_failed_lookup_is_retried(ollama, monkeypatch):
    clock = [5000.0]
    monkeypatch.setattr(model, "_clock", lambda: clock[0])
    shows = ollama["shows"]
    assert model.backend_for("ollama/qwen3:14b") == "ollama" and shows == ["qwen3:14b"]
    model.backend_for("ollama/qwen3:14b")
    model.supports_images("ollama/qwen3:14b")
    assert shows == ["qwen3:14b"], "one record gives where it runs and what it can do, kept SHOW_TTL seconds"
    clock[0] += model.SHOW_TTL
    model.backend_for("ollama/qwen3:14b")
    assert shows == ["qwen3:14b"] * 2, "then asked again"
    ask("ollama/qwen3:14b")
    assert shows == ["qwen3:14b"] * 3, "a call about to be sent always asks"
    ollama["show_fail"].add("team-local")
    model.backend_for("ollama/team-local")
    model.backend_for("ollama/team-local")
    assert shows.count("team-local") == 1, "a failed lookup is not asked again at once"
    clock[0] += model.SHOW_RETRY
    model.backend_for("ollama/team-local")
    assert shows.count("team-local") == 2, "it is asked again after SHOW_RETRY seconds"


# ---------------------------------------------------------------- a person's proposal tells the policy owners
def notices(hub, who="bob"):
    return [m for m in hub.inbox.for_principal(who) if m.get("kind") == "policy-proposal" and "leaves this machine" in m.get("subject", "")]


def test_a_cloud_model_picked_on_the_cog_page_tells_the_owners_it_leaves_the_machine(released, ollama, monkeypatch):
    from hubproto.shell import serve_in_thread
    from test_shell_views import post

    monkeypatch.setenv("HUBPROTO_HOSTED_MODELS", "on")
    server = serve_in_thread(released, "127.0.0.1", 0)
    try:
        status, url, body = post({"base": f"http://127.0.0.1:{server.server_address[1]}"}, "/cog-model",
                                 {"as": "carol", "cog": "quality-assistant", "model": "ollama/gemma4:31b-cloud"})
    finally:
        server.shutdown()
        server.server_close()
    assert status == 200 and "/proposal/" in url, body[:400]
    want = f"ollama/gemma4:31b-cloud {OFF}: quality-assistant's prompts and the records in them would leave this machine"
    for owner in ("bob", "carol"):
        [n] = notices(released, owner)
        assert n["subject"].startswith("carol proposes ollama/gemma4:31b-cloud for quality-assistant") and want in n["body"], n
    draft = released.kernel.require(n["record"])
    assert want in released.dms.versions(draft["locator"])[-1]["note"], "the draft's note says it too"
    released.propose_new_cog(name="cloud-helper", roles=["staff"], model="ollama/gemma4:cloud", about="Helps.", persona="", by="carol")
    assert any("a new cog, cloud-helper" in n["subject"] and "cloud-helper's prompts" in n["body"] for n in notices(released)), notices(released)
    monkeypatch.delenv("HUBPROTO_HOSTED_MODELS")
    released.propose_cog_model("ap-reviewer", "ollama/qwen3:14b", by="carol")
    assert len(notices(released)) == 2, "a local model tells nobody it leaves the machine"


def test_on_a_change_order_the_reason_says_the_cloud_model_leaves_the_machine(released, ollama, monkeypatch):
    from hubproto import sopdocs

    sopdocs.convert(released, by="carol")
    monkeypatch.setenv("HUBPROTO_HOSTED_MODELS", "on")
    out = released.propose_cog_model("quality-assistant", "ollama/gemma4:31b-cloud", by="alice")
    co = released.kernel.get(out["locator"])
    assert f"ollama/gemma4:31b-cloud {OFF}" in co["fields"]["description"], co["fields"]
    [n] = notices(released)
    assert n["record"] == out["locator"] and out["change_order"] in n["subject"] and "would leave this machine" in n["body"]
