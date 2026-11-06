"""The messenger: an informal chat with a cog from a window in the corner of every page."""

import json
import threading
import time
from urllib.parse import quote

from test_shell import get, post, shell  # noqa: F401  (the module-scoped live shell)


def test_the_widget_is_on_every_page_for_people_and_never_for_a_watched_cog(shell):
    status, text = get(shell, "/", **{"as": "carol"})
    assert status == 200 and 'id="messenger"' in text and "Chat with a cog" in text and '<option value="quality-assistant">' in text
    status, text = get(shell, "/inbox", **{"as": "dave"})
    assert 'id="messenger"' in text
    status, text = get(shell, "/", **{"as": "quality-assistant"})
    assert 'id="messenger"' not in text, "watching a cog: nothing is sent in its name"
    status, text = get(shell, "/", **{"as": "nobody"})
    assert 'id="messenger"' not in text


def test_a_chat_turn_is_kept_apart_from_mail_and_chatter_and_knows_the_page(shell):
    hub = shell["hub"]
    doc = hub.dms.create(title="Torque WI", doc_class="working", folder="Quality/Controlled Documents", by="carol",
                         content=b"# Torque\nM4 screws to 2 Nm.", filename="torque.md", mime="text/markdown", doc_type="wi")
    seen = {}
    real = hub.model.complete_with_tools

    def fake(**kw):
        seen.update(kw)
        return {"text": "Looks fine to me, 2 Nm matches the procedure.", "stand_in": False, "served_model": "fake", "model": "fake", "tool_calls": []}

    hub.model.complete_with_tools = fake
    try:
        status, text = get(shell, "/document/" + quote(doc["locator"], safe=":"), **{"as": "carol"})
        assert f'data-about="{doc["locator"]}"' in text, "the widget knows which item is on screen"
        status, url, body = post(shell, "/chat", {"as": "carol", "to": "quality-assistant", "body": "hey, is the torque right here?", "about": doc["locator"]})
        assert status == 200, body
        reply = json.loads(body)["reply"]
        assert reply["from"] == "quality-assistant" and "2 Nm" in reply["body"]
        assert "M4 screws to 2 Nm." in seen["system"] and "HOW TO TALK HERE" in seen["system"] and "YOU ARE CHATTING WITH carol" in seen["system"]
        # A second turn carries the conversation so far.
        post(shell, "/chat", {"as": "carol", "to": "quality-assistant", "body": "thanks, and the record step?", "about": doc["locator"]})
        assert [t["role"] for t in seen["history"]] == ["user", "assistant"] and seen["history"][0]["content"].startswith("hey")
    finally:
        hub.model.complete_with_tools = real
    status, body = get(shell, "/chat-thread", **{"as": "carol", "with": "quality-assistant", "about": doc["locator"]})
    j = json.loads(body)
    assert [m["mine"] for m in j["messages"]][-4:] == [True, False, True, False] and doc["number"] in j["context"]
    assert hub.inbox.on_record(doc["locator"]) == [], "a chat is not the item's chatter"
    status, text = get(shell, "/inbox", **{"as": "carol"})
    assert "hey, is the torque right here?" not in text, "nor a message in the inbox"
    # A fresh conversation.
    status, url, body = post(shell, "/chat-clear", {"as": "carol", "with": "quality-assistant"})
    assert json.loads(body)["cleared"] >= 4 and json.loads(get(shell, "/chat-thread", **{"as": "carol", "with": "quality-assistant"})[1])["messages"] == []


def test_bad_chat_requests_are_refused_with_a_reason(shell):
    status, url, body = post(shell, "/chat", {"as": "carol", "to": "bob", "body": "hi"})
    assert status == 400 and "not a cog you can chat with" in json.loads(body)["error"]
    status, url, body = post(shell, "/chat", {"as": "carol", "to": "quality-assistant", "body": "  "})
    assert status == 400 and "say something" in json.loads(body)["error"]
    status, url, body = post(shell, "/chat", {"as": "quality-assistant", "to": "doc-reviewer", "body": "hi"})
    assert status in (400, 403)


def test_a_slow_cog_does_not_hold_up_the_rest_of_the_hub(shell):
    hub = shell["hub"]
    real = hub.model.complete_with_tools

    def slow(**kw):
        time.sleep(2.0)
        return {"text": "done thinking", "stand_in": False, "served_model": "slow", "model": "slow", "tool_calls": []}

    hub.model.complete_with_tools = slow
    result = {}
    try:
        t = threading.Thread(target=lambda: result.update(r=post(shell, "/chat", {"as": "carol", "to": "doc-reviewer", "body": "take your time"})))
        t.start()
        time.sleep(0.3)
        t0 = time.time()
        status, text = get(shell, "/inbox", **{"as": "bob"})
        assert status == 200 and time.time() - t0 < 1.5, "another person's page was served while the cog was thinking"
        t.join(10)
    finally:
        hub.model.complete_with_tools = real
    assert json.loads(result["r"][2])["reply"]["body"] == "done thinking"


def test_without_a_model_the_cog_says_so_plainly(shell):
    hub = shell["hub"]
    line = hub.chat("carol", "doc-reviewer", "hello!")
    assert line["kind"] == "chat" and line["from"] == "doc-reviewer" and "No model is configured" in line["body"]


def test_the_cog_looks_records_up_as_the_person_chatting(shell):
    """Without a model the stand-in still uses the record tools; with one, the model calls them. Either way a look-up
    sees only what the person may read."""
    hub = shell["hub"]
    from conftest import SMALL_BILL, dated

    hub.runner.submit("purchase-to-pay", {"record": dated(SMALL_BILL)}, owner="alice")
    bill = next(r for r in hub.kernel.query(type_="bill"))
    status, url, body = post(shell, "/chat", {"as": "alice", "to": "ap-reviewer", "body": f"what is going on with {bill['number']}?"})
    reply = json.loads(body)["reply"]
    assert bill["number"] in reply["body"] and bill["state"] in reply["body"] and ("looked at " + bill["number"]) in reply["looked"]
    # A look-up never shows a record in a folder the person may not read.
    from hubproto.tools import RecordTools

    dave = RecordTools(hub, "dave")
    bundle = dave.bundle
    closed = next(f for f in bundle["folders"] if not dave.may_read(f))
    rec = hub.kernel.create("task", closed, {"title": "Hidden from dave"}, by="carol")
    assert "error" in dave.get_record(rec["number"]) and all(r["number"] != rec["number"] for r in dave.search_records("hidden")["records"])
    reader = next(n for n, m in bundle["members"].items() if m["type"] == "person" and RecordTools(hub, n).may_read(closed))
    assert RecordTools(hub, reader).get_record(rec["number"])["number"] == rec["number"]


def test_a_model_drives_the_tools_through_function_calling(monkeypatch):
    """The gateway's Ollama loop: the model asks for a tool, the result goes back, the model answers."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from hubproto.model import ModelGateway

    turns = []

    class Fake(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            turns.append(body)
            if len(turns) == 1:
                msg = {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "get_record", "arguments": {"number": "BILL-0001"}}}]}
            else:
                msg = {"role": "assistant", "content": "BILL-0001 is posted."}
            out = json.dumps({"message": msg, "prompt_eval_count": 10, "eval_count": 5}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("HUBPROTO_OLLAMA_URL", f"http://127.0.0.1:{srv.server_address[1]}")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("HUBPROTO_MODEL_FAKE", raising=False)
        from hubproto.tools import tool_specs

        calls = []
        out = ModelGateway().complete_with_tools(model="ollama/qwen3:14b", system="s", user="what about BILL-0001?", tools=tool_specs(),
                                                 call_tool=lambda n, a: calls.append((n, a)) or {"number": "BILL-0001", "state": "posted"})
        assert out["text"] == "BILL-0001 is posted." and calls == [("get_record", {"number": "BILL-0001"})] and out["tool_calls"][0]["tool"] == "get_record"
        assert turns[0]["tools"] and turns[1]["messages"][-1]["role"] == "tool" and "posted" in turns[1]["messages"][-1]["content"]
    finally:
        srv.shutdown()


def test_the_record_tools_over_mcp(shell):
    from urllib.request import Request, urlopen

    def rpc(payload, who="carol"):
        req = Request(shell["base"] + "/mcp?as=" + who, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=10) as r:
            return r.status, (json.loads(r.read() or b"null"))

    status, init = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}})
    assert status == 200 and init["result"]["capabilities"]["tools"] is not None
    status, _ = rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert status == 202
    status, listed = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert {t["name"] for t in listed["result"]["tools"]} >= {"search_records", "get_record", "read_document", "waiting_for", "overview"}
    status, called = rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "overview", "arguments": {}}})
    assert called["result"]["isError"] is False and "types" in json.loads(called["result"]["content"][0]["text"])
    status, refused = rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/list"}, who="nobody")
    assert refused["error"]["code"] == -32001


def test_search_forgives_how_a_model_names_types_and_states(shell):
    from hubproto.tools import RecordTools

    hub = shell["hub"]
    ncr = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "Loose bracket", "description": "found at inspection"}, by="carol")
    hub.kernel.transition(ncr["locator"], "awaiting-disposition-the-nonconformance", by="hub")
    t = RecordTools(hub, "carol")
    for args in ({"query": "nonconformance", "type": "ncr", "state": "open"}, {"type": "nonconformances", "state": "awaiting disposition"},
                 {"query": "loose", "type": "NCRs"}):
        assert ncr["number"] in [r["number"] for r in t.search_records(**args, limit=25)["records"]], args
    miss = t.search_records(type="ncr", state="frozen")
    assert miss["count"] == 0 and "awaiting-disposition-the-nonconformance" in miss["hint"]
