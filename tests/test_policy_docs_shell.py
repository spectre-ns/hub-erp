"""Over HTTP: open a Frame as a document, draft a change in the text editor, check it, propose it, review it as two
policy owners and release it; the Frame file and its document follow. Its own hub, since it changes the policy."""

import json
import shutil
from urllib.parse import quote

import pytest

from conftest import ROOT, release_all
from hubproto.app import Hub
from hubproto.shell import serve_in_thread
from test_shell import get, post


@pytest.fixture(scope="module")
def pshell(tmp_path_factory):
    root = tmp_path_factory.mktemp("policyhub")
    shutil.copytree(ROOT / "frames", root / "frames")
    hub = Hub(root)
    hub.kernel.seed()
    release_all(hub)
    server = serve_in_thread(hub, "127.0.0.1", 0)  # port 0: bound at once, no race with other test workers
    port = server.server_address[1]
    yield {"hub": hub, "root": root, "base": f"http://127.0.0.1:{port}"}
    server.shutdown()
    server.server_close()


def test_a_policy_change_from_draft_to_release_in_the_shell(pshell):
    hub = pshell["hub"]
    status, text = get(pshell, "/frame-doc/finance-policy", **{"as": "alice"})
    assert status == 200 and "Edit this policy" in text and "Send for approval" not in text, "a Frame changes by release, not by document control"
    doc = next(d for d in hub.kernel.query(type_="document") if d["fields"].get("frame_name") == "finance-policy" and d["fields"].get("frame_mirror"))
    status, url, text = post(pshell, "/frame-draft", {"as": "alice", "frame": "finance-policy"})
    assert status == 200 and "/text-edit/" in url and "<textarea" in text and "Save and check the change" in text
    draft = next(d for d in hub.kernel.query(type_="document") if d["fields"].get("doc_type") == "frame-draft")
    current = hub.draft_text(draft)
    changed = current.replace("Finance clerks may create bills in Finance/Payables.",
                              "Finance clerks may create bills in Finance/Payables.\nStaff may read Finance/Payables.")
    status, url, text = post(pshell, "/text-save", {"as": "alice", "doc": draft["locator"], "text": changed, "next": "check"})
    assert status == 200 and "/frame-check/" in url and "The draft compiles" in text and "staff" in text.lower() and "two policy owners" in text
    assert "<pre class='diff'>" in text and "+Staff may read Finance/Payables." in text
    status, url, text = post(pshell, "/frame-propose", {"as": "alice", "doc": draft["locator"]})
    assert status == 200 and "/proposal/" in url and "Draft of finance-policy" in text
    pid = url.split("/proposal/")[1].split("?")[0]
    status, text = get(pshell, "/proposal/" + pid, **{"as": "alice"})
    assert "Your review" not in text, "alice is not a policy owner"
    for owner in ("bob", "carol"):
        status, url, text = post(pshell, "/proposal-review", {"as": owner, "proposal": pid, "verdict": "approve", "meaning": "reviewed the sentence"})
        assert status == 200 and "You approved" in text
    assert "Release" in text
    status, url, text = post(pshell, "/proposal-release", {"as": "carol", "proposal": pid})
    assert status == 200 and "/release/" in url and "it is the policy in force" in text
    frame_file = (pshell["root"] / "frames" / "finance" / "finance-policy.frame.md").read_text()
    assert "Staff may read Finance/Payables." in frame_file
    assert hub.dms.content(doc["locator"])[0].decode() == frame_file and hub.kernel.get(draft["locator"])["state"] == "released"
    status, text = get(pshell, "/frame/finance-policy", **{"as": "alice"})
    assert "Staff may read Finance/Payables." in text, "the shell reads the Frame as released"


def test_a_frame_document_in_force_is_not_edited_in_place_and_markdown_has_a_text_editor(pshell):
    hub = pshell["hub"]
    doc = hub.frame_document("org-model")
    status, text = get(pshell, "/text-edit/" + quote(doc["locator"], safe=":"), **{"as": "carol"})
    assert status == 200 and "use Edit this policy to make a draft" in text and "<textarea id='te'" not in text
    note = hub.dms.create(title="Notes", doc_class="working", folder="Policy", by="alice", content=b"# Notes\n", filename="notes.md", mime="text/markdown", doc_type="note")
    status, text = get(pshell, "/document/" + quote(note["locator"], safe=":"), **{"as": "alice"})
    assert "Edit the text" in text
    status, url, text = post(pshell, "/text-save", {"as": "alice", "doc": note["locator"], "text": "# Notes\nSecond line.\n"})
    assert status == 200 and hub.dms.content(note["locator"])[0] == b"# Notes\nSecond line.\n"
    assert "Saved " + note["number"] in text, text[text.find("<main"):text.find("<main") + 400]


def test_the_cog_profile_page_shows_its_documents_and_where_to_change_it(pshell):
    status, text = get(pshell, "/user/quality-assistant", **{"as": "bob"})
    assert status == 200 and ("Edit this cog&#x27;s profile" in text or "Edit this cog's profile" in text)
    assert "/frame-doc/cog-profiles" in text and "/frame-doc/org-model" in text
