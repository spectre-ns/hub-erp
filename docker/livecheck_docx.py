"""Prepare a docx with a comment asking the quality cog for a table and a checklist (live seat check)."""
import io, zipfile
from hubproto.app import Hub
from hubproto import wopi

hub = Hub("/hub")
content, mime = wopi.blank_document("docx")
doc = hub.dms.create(title="Live table check", doc_class="working", folder="Quality/Controlled Documents", by="carol", content=content,
                     filename="live-table.docx", mime=mime, doc_type="document")
loc = doc["locator"]
hub.dms.checkout(loc, by="carol", via="editor")
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/></Types>')
    z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    z.writestr("word/_rels/document.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" Target="comments.xml"/></Relationships>')
    z.writestr("word/document.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Inspection plan for the sensor board. </w:t></w:r><w:commentRangeStart w:id="0"/><w:r><w:t>Torque 2 Nm.</w:t></w:r><w:commentRangeEnd w:id="0"/><w:r><w:commentReference w:id="0"/></w:r></w:p><w:sectPr/></w:body></w:document>')
    z.writestr("word/comments.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:comment w:id="0" w:author="carol" w:date="2026-11-06T19:10:00Z" w:initials="CA"><w:p><w:r><w:t>@quality-assistant please add a table of inspection items with columns item, requirement and result, followed by a short numbered checklist.</w:t></w:r></w:p></w:comment></w:comments>')
wopi.WopiHost(hub).put_file(doc, {"u": "carol", "w": True, "f": doc["number"]}, buf.getvalue())
rec = hub.kernel.get(loc)
pending = wopi.WopiHost(hub).pending_replies(rec)
print(loc, "pending:", [(m["from"], m["comment_id"], (m.get("edit") or {}).get("paragraphs", [])[:4]) for m in pending])
