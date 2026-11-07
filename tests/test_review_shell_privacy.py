"""Review of 7 Nov 2026, shell: what a member sees and may do about items in folders they may not read (security
findings 1, 5, 7, 11, 12, 13). dave (buyer, payables reviewer, staff) may not read Engineering; alice (design
engineer) and carol (quality manager) may."""

import json
import shutil
import time
from urllib.parse import quote

import pytest

from conftest import ROOT, release_all
from hubproto import wopi
from hubproto.agent import AgentStore
from hubproto.app import Hub
from hubproto.shell import free_port, serve_in_thread
from test_shell_views import get, post

SECRET = "TOPSECRET-FALCON-80"


@pytest.fixture(scope="module")
def shell(tmp_path_factory):
    root = tmp_path_factory.mktemp("hub")
    shutil.copytree(ROOT / "frames", root / "frames")
    hub = Hub(root)
    hub.kernel.seed()
    release_all(hub)
    port = free_port()
    server = serve_in_thread(hub, "127.0.0.1", port)
    yield {"hub": hub, "root": root, "base": f"http://127.0.0.1:{port}", "port": port, "server": server}
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def secret_doc(shell):
    hub = shell["hub"]
    return hub.dms.create(title=f"{SECRET} design notes", doc_class="working", folder="Engineering/Designs", by="alice",
                          content=f"# Notes\n{SECRET}\n".encode(), filename="notes.md", mime="text/markdown", doc_type="document")


def test_profiles_show_only_messages_the_viewer_may_see(shell, secret_doc):
    hub = shell["hub"]
    hub.inbox.post(sender="alice", to="bob", subject="falcon margin", body=SECRET, record=secret_doc["locator"])
    assert get(shell, "/user/alice", **{"as": "dave"})[0] == 200
    assert SECRET not in get(shell, "/user/alice", **{"as": "dave"})[1]
    assert SECRET in get(shell, "/user/alice", **{"as": "carol"})[1] or SECRET in get(shell, "/user/alice", **{"as": "bob"})[1]


def test_cog_queues_hide_what_was_asked_from_others(shell, secret_doc):
    hub = shell["hub"]
    hub.queue.mode = "service"
    try:
        hub.queue.submit("qa-manager", "assigned", by="alice", title=f"{SECRET} check the margin", record=secret_doc["locator"],
                         payload={"record": secret_doc["locator"], "instruction": SECRET})
        for page in ("/queue", "/user/qa-manager"):
            assert SECRET not in get(shell, page, **{"as": "dave"})[1], page
        assert "not yours to see" in get(shell, "/user/qa-manager", **{"as": "dave"})[1]
        assert SECRET in get(shell, "/user/qa-manager", **{"as": "alice"})[1]
    finally:
        for t in hub.queue.open_tasks(cog="qa-manager"):
            hub.queue.cancel(t["id"], "test")
        hub.queue.mode = "inline"


def test_an_agent_run_is_shown_only_to_who_asked_the_cog_and_policy_owners(shell):
    store = AgentStore(shell["root"] / "state" / "agents")
    store.append("privrun01", {"type": "started", "cog": "quality-assistant", "goal": f"read {SECRET}", "by": "alice", "trigger": "task",
                               "mode": "act", "budget": {"actions": 5}})
    store.append("privrun01", {"type": "tool", "tool": "read_document", "arguments": {}, "write": False, "ok": True, "result": SECRET})
    status, text = get(shell, "/agent-run/privrun01", **{"as": "dave"})
    assert status == 403 and SECRET not in text
    assert get(shell, "/agent-run/privrun01", **{"as": "alice"})[0] == 200
    assert get(shell, "/agent-run/privrun01", **{"as": "bob"})[0] == 200  # a policy owner


def test_unreadable_items_are_not_named_in_chat_pickers_or_links(shell, secret_doc):
    j = json.loads(get(shell, "/chat-thread", **{"as": "dave", "with": "quality-assistant", "about": secret_doc["locator"]})[1])
    assert SECRET not in j["context"] and secret_doc["number"] not in j["context"]
    assert SECRET not in get(shell, "/", **{"as": "dave"})[1]
    hub = shell["hub"]
    ncr = [r for r in hub.kernel.query() if r["folder"].startswith("Quality") and r["type"] != "document"]
    target = ncr[0] if ncr else hub.dms.create(title="Readable", doc_class="working", folder="Quality/Controlled Documents", by="carol",
                                               content=b"x", filename="r.md", mime="text/markdown", doc_type="document")
    status, url, text = post(shell, "/link", {"as": "dave", "record": target["locator"], "rel": "references", "target": secret_doc["locator"]})
    assert SECRET not in text
    assert secret_doc["locator"] not in [l["target"] for l in hub.kernel.get(target["locator"])["links"]]


def test_start_on_does_not_move_an_unreadable_record(shell, secret_doc):
    hub = shell["hub"]
    status, url, text = post(shell, "/start-on", {"as": "dave", "process": "task", "record": secret_doc["locator"]})
    assert status == 403 and hub.kernel.get(secret_doc["locator"])["folder"] == "Engineering/Designs"
    status, url, text = post(shell, "/start-on", {"as": "alice", "process": "task", "record": secret_doc["locator"]})
    assert status == 400 and "runs on a task" in text and hub.kernel.get(secret_doc["locator"])["folder"] == "Engineering/Designs"


def test_only_someone_who_may_change_a_document_writes_or_suppresses_pending_answers(shell):
    hub = shell["hub"]
    content, mime = wopi.blank_document("docx")
    doc = hub.dms.create(title="carol's doc", doc_class="working", folder="Quality/Controlled Documents", by="carol", content=content,
                         filename="c.docx", mime=mime, doc_type="document")
    for path, extra in (("/apply-replies", {}), ("/mark-applied", {"ids": "x"}), ("/verify-edit", {"id": "x"})):
        status, url, text = post(shell, path, {"as": "dave", "doc": doc["locator"], **extra})
        assert status == 403 and "not change it" in text, path
    assert post(shell, "/apply-replies", {"as": "carol", "doc": doc["locator"]})[0] == 200


def test_a_policy_draft_is_changed_only_by_its_author_or_a_policy_owner(shell):
    hub = shell["hub"]
    draft = hub.frame_draft("org-model", by="bob")
    text = hub.draft_text(draft) + "\n- dave (person): policy-owner\n"
    status, url, page = post(shell, "/text-save", {"as": "dave", "doc": draft["locator"], "text": text})
    assert status == 403
    assert len(hub.dms.versions(draft["locator"])) == 1


def test_withdraw_never_redirects_off_the_hub(shell):
    hub = shell["hub"]
    hub.queue.mode = "service"
    try:
        t = hub.queue.submit("purchasing-assistant", "task", by="dave", title="count POs", payload={"goal": "count POs"})
        status, url, text = post(shell, "/queue-withdraw", {"as": "dave", "task": t["id"], "return": "//evil.example/phish"})
        assert status == 200 and url.startswith(shell["base"]) and "evil.example" not in url
    finally:
        hub.queue.mode = "inline"


def test_a_view_only_editor_token_cannot_lock_a_document(shell):
    from test_editor import wopi_call

    hub = shell["hub"]
    content, mime = wopi.blank_document("docx")
    doc = hub.dms.create(title="carol's lockable", doc_class="working", folder="Quality/Controlled Documents", by="carol", content=content,
                         filename="l.docx", mime=mime, doc_type="document")
    token = wopi.issue_token("dave", doc["number"], can_write=False, root=shell["root"])
    for op in ("LOCK", "REFRESH_LOCK", "UNLOCK"):
        status, body, headers = wopi_call(shell, f"/wopi/files/{doc['number']}", token, "POST", b"", {"X-WOPI-Override": op, "X-WOPI-Lock": "davelock"})
        assert status == 409, op
    assert not hub.kernel.get(doc["locator"])["fields"].get("checked_out_by")
