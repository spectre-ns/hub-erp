"""Live check of targeted edits: a docx that carol has open in the editor, and a request on its chatter asking the
quality cog to replace a word and delete a paragraph. The cog answers from its queue (the local model); the editor page (loaded
by headless Chrome) has the cog's seat do the changes live, then the hub verifies them after the save."""
import sys
import time

from hubproto.app import Hub
from hubproto import office, wopi

hub = Hub("/hub")
lines = ["# Supplier audit", "## Purpose", "Audit suppliers yearly.", "## Scope", "All suppliers shall be audited.",
         "Old line to delete.", "## Records", "Keep records for 3 years."]
blank, mime = wopi.blank_document("docx")
content = office.insert_paragraphs(blank, "docx", lines, "replace")
doc = hub.dms.create(title="Live edit check", doc_class="working", folder="Quality/Controlled Documents", by="carol", content=content,
                     filename="live-edit.docx", mime=mime, doc_type="document")
loc = doc["locator"]
hub.dms.checkout(loc, by="carol", via="editor")
request = sys.argv[1] if len(sys.argv) > 1 else "@quality-assistant please change 'shall' to 'must' and delete the paragraph 'Old line to delete.'"
hub.send("carol", "carol", "Edit request", request, record=loc)
print("doc", doc["number"], loc, flush=True)
for _ in range(120):
    pend = wopi.WopiHost(hub).pending_replies(hub.kernel.get(loc))
    if pend:
        print("reply:", pend[0]["body"][:300], flush=True)
        print("ops:", pend[0].get("edit", {}).get("ops"), flush=True)
        break
    time.sleep(3)
else:
    print("no reply after 6 minutes", flush=True)
