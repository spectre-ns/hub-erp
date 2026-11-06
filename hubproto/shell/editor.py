"""The integrated editor: documents open in Collabora Online inside the hub (the hub is the WOPI host), new
blank documents are created in a folder or on an item, and saves become DMS versions under the check-out rules."""

from __future__ import annotations

import json
import os
from urllib.parse import quote

from .. import wopi
from ..kernel import KernelGuardError
from .forms import ShellError, text_input
from .handler import hidden_as, href, log_line
from .icons import icon
from .text import esc, type_name

NEW_KINDS = [("docx", "Word document (.docx)"), ("xlsx", "Spreadsheet (.xlsx)"), ("odt", "OpenDocument text (.odt)"),
             ("ods", "OpenDocument spreadsheet (.ods)"), ("md", "Markdown (.md)"), ("txt", "Plain text (.txt)")]


def editor_configured() -> bool:
    browser, host = wopi.collabora_urls()
    return bool(browser and host)


def new_document_form(user: str, *, folder: str = "", attach_to: str = "", once: str = "", error: str = "") -> str:
    """The 'New document' form: a blank file of the chosen kind, opened in the editor straight away."""
    kinds = "".join(f'<option value="{k}">{esc(label)}</option>' for k, label in NEW_KINDS)
    target = f'<input type="hidden" name="attach_to" value="{esc(attach_to)}">' if attach_to else ""
    where = f'<input type="hidden" name="folder" value="{esc(folder)}">' if folder else ""
    note = "" if editor_configured() else "<p class='why'>No online editor is configured (HUBPROTO_COLLABORA_URL); the document is created and can be downloaded, checked out and checked in.</p>"
    return f"""<h2 id="new-document">{icon('plus')} New document</h2><div class="card">{note}{error}
<form method="post" action="/new-document">{hidden_as(user)}{once}{where}{target}
<div class="fields">{text_input("title", "Title", required=True)}<label class="f"><span>Kind</span><select name="kind">{kinds}</select></label></div>
<div class="actions"><button>{icon('edit')}Create and open</button></div></form></div>"""


class EditorMixin:
    # ------------------------------------------------------------ pages
    def page_edit(self):
        user = self.user
        self._need_release()
        rec = self.hub.kernel.get(self.arg)
        if rec is None or rec["type"] != "document":
            raise ShellError(f"There is no document '{self.arg}'.", 404)
        self._require_read(rec["folder"])
        host = wopi.WopiHost(self.hub)
        if not wopi.is_viewable(rec):
            raise ShellError(f"{rec['number']} ({rec['fields'].get('filename') or 'no file'}) is not a kind of file the editor opens; download it instead.", 400)
        may, why_not = self._may_edit_document(rec)
        writable = may and wopi.is_editable(rec) and host.can_write(rec, user)[0]
        if writable:
            self.hub.dms.checkout(rec["locator"], by=user, via="editor")  # opens the session, or joins one that is open
            rec = self.hub.kernel.get(rec["locator"])
        browser_url, host_url = wopi.collabora_urls()
        number, title = rec["number"], rec["fields"].get("title") or rec["number"]
        back = href("/document/" + quote(rec["locator"], safe=":"), user)
        mode = "editing" if writable else "read-only"
        reason = "" if writable else (why_not or host.can_write(rec, user)[1] or ("the editor shows this kind of file but does not change it" if not wopi.is_editable(rec) else ""))
        others = [e for e in (rec["fields"].get("editors") or []) if e != user]
        if writable and others:
            reason = "also editing: " + ", ".join(others) + "; everyone's changes merge in the editor"
        bar = f"""<div class="editbar"><a class="button ghost" href="{back}">{icon('chevron-right')}{esc(number)}</a> {icon('edit' if writable else 'eye')} <b>{esc(title)}</b> <span class="chip {'ok' if writable else 'warn'}">{mode}</span>
{f"<span class='why'>{esc(reason)}</span>" if reason else "<span class='why'>Every save becomes a version; others may join and edit with you; the last to finish releases the document.</span>"} <span class='why' id='editstatus' role='status'></span>
<form method="post" action="/finish-edit" class="inline">{hidden_as(user)}<input type="hidden" name="doc" value="{esc(rec['locator'])}"><button class="{'primary' if writable else 'secondary'}">{icon('check' if writable else 'x')}{'Finish editing' if writable else 'Close'}</button></form></div>"""
        if not (browser_url and host_url):
            body = bar + ("<div class='card'><b>No online editor is configured.</b> Set <code>HUBPROTO_COLLABORA_URL</code> (the editor's address for the browser, "
                          "e.g. http://localhost:9980) and <code>HUBPROTO_WOPI_BASE</code> (this hub's address as the editor reaches it, e.g. http://hub:8080); "
                          "docker-compose.yml starts Collabora Online with both set. Until then, download the file, edit it and check it in.</div>")
            return self._page(f"Edit {number}", body)
        token = wopi.issue_token(user, number, can_write=writable, root=self.hub.root)
        frame = wopi.editor_frame_url(rec, token, browser_url=browser_url, host_url=host_url,
                                      discovery_url=os.environ.get("HUBPROTO_COLLABORA_DISCOVERY", ""))
        body = bar + f'<iframe class="editor" id="editor" src="{esc(frame)}" title="{esc(title)} in the editor" allow="clipboard-read; clipboard-write"></iframe>'
        # @mentions inside the editor: Collabora asks the host page for suggestions (UI_Mention) and the page answers
        # (Action_Mention) with the members and roles of the release; a chosen name is inserted as "@name".
        bundle = self.bundle or {}
        public = (os.environ.get("HUBPROTO_PUBLIC_URL") or "").rstrip("/")
        people = [{"username": n, "profile": f"{public}/user/{n}", "kind": m.get("type", "person")} for n, m in sorted((bundle.get("members") or {}).items())]
        roles = [{"username": r, "profile": f"{public}/people", "kind": "role"} for r in sorted(bundle.get("roles") or [])]
        mentions = json.dumps(people + roles)
        parts = browser_url.split("/")
        origin = parts[0] + "//" + parts[2] if len(parts) > 2 and "//" in browser_url else browser_url
        body += f"""<script>(function(){{var frame=document.getElementById('editor');var list={mentions};var origin={json.dumps(origin)};
function post(o){{try{{frame.contentWindow.postMessage(JSON.stringify(o),'*')}}catch(e){{}}}}
window.addEventListener('message',function(ev){{if(origin&&ev.origin!==origin)return;var m;try{{m=JSON.parse(ev.data)}}catch(e){{return}}
if(m.MessageId==='App_LoadingStatus'&&m.Values&&m.Values.Status==='Document_Loaded'){{post({{MessageId:'Host_PostmessageReady'}})}}
if(m.MessageId==='UI_Mention'){{var v=m.Values||{{}};if(v.type==='autocomplete'){{var q=String(v.text||'').replace(/^@/,'').toLowerCase();
var out=list.filter(function(x){{return x.username.toLowerCase().indexOf(q)===0}}).slice(0,10).map(function(x){{return {{username:x.username,profile:x.profile}}}});
post({{MessageId:'Action_Mention',SendTime:Date.now(),Values:{{list:out}}}})}}}}
if(m.MessageId==='Action_Save_Resp'){{if(window.__applyAfterSave){{window.__applyAfterSave=false;applyReplies()}}}}}});
var repliesUrl={json.dumps(href("/editor-replies/" + quote(rec["locator"], safe=":"), user))};var applyUrl={json.dumps(href("/apply-replies", user))};var loc={json.dumps(rec["locator"])};var busy=false;
function applyReplies(){{var fd=new FormData();fd.append('as',{json.dumps(user)});fd.append('doc',loc);fetch(applyUrl,{{method:'POST',body:fd}}).then(function(r){{return r.json()}}).then(function(j){{busy=false;if(j.applied>0){{var s=document.getElementById('editstatus');if(s)s.textContent='Replies from '+j.by.join(', ')+' written into the document; reloading…';frame.src=frame.src.replace(/&reload=\d+/,'')+'&reload='+Date.now()}}}}).catch(function(){{busy=false}})}}
function poll(){{if(busy)return;fetch(repliesUrl).then(function(r){{return r.json()}}).then(function(j){{if(j.pending>0&&!busy){{busy=true;window.__applyAfterSave=true;post({{MessageId:'Action_Save',Values:{{DontTerminateEdit:true,DontSaveIfUnmodified:true,Notify:true}}}});setTimeout(function(){{if(window.__applyAfterSave){{window.__applyAfterSave=false;applyReplies()}}}},2500)}}}}).catch(function(){{}})}}
setInterval(poll,4000);}})();</script>"""
        self._page(f"Edit {number}", body, head="<style>main#main{padding:0;max-width:none} .editbar{display:flex;gap:.6em;align-items:center;flex-wrap:wrap;padding:.5em .8em;border-bottom:1px solid var(--line);background:var(--panel)} .editbar form{margin-left:auto} .editor{width:100%;height:calc(100vh - 9.5em);border:0;display:block;background:var(--panel)}</style>")

    def page_editor_replies(self):
        """For the editor page's polling: how many chatter replies to comments wait to be written into the document."""
        self._need_release()
        rec = self.hub.kernel.get(self.arg)
        if rec is None or rec["type"] != "document":
            return self._json({"error": "no such document"}, 404)
        self._require_read(rec["folder"])
        pending = wopi.WopiHost(self.hub).pending_replies(rec)
        return self._json({"pending": len(pending), "by": sorted({m["from"] for m in pending})})

    def post_apply_replies(self):
        """Write the waiting replies into the document as reply comments (a new version by the hub)."""
        self._need_release()
        rec = self.hub.kernel.require(self._f("doc"))
        if rec["type"] != "document":
            return self._json({"error": "not a document"}, 400)
        self._require_read(rec["folder"])
        try:
            applied = wopi.WopiHost(self.hub).apply_replies(rec, self.user)
        except KernelGuardError as e:
            return self._json({"applied": 0, "error": e.detail}, 409)
        return self._json({"applied": len(applied), "by": sorted({m["from"] for m in applied})})

    # ------------------------------------------------------------ actions
    def post_finish_edit(self):
        rec = self.hub.kernel.require(self._f("doc"))
        if rec["type"] != "document":
            raise ShellError(f"{rec['number']} is not a document.")
        try:
            wopi.WopiHost(self.hub).finish(rec, self.user)
        except KernelGuardError as e:
            raise ShellError(f"{e.detail}.") from None
        versions = self.hub.dms.versions(rec["locator"])
        self._redirect(href("/document/" + quote(rec["locator"], safe=":"), self.user,
                            done=f"Finished editing {rec['number']}; it is at version {versions[-1]['version'] if versions else 0} and released for others."))

    def post_new_document(self):
        user = self.user
        bundle = self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        title = self._f("title").strip()
        kind = self._f("kind") or "docx"
        attach_to = self._f("attach_to") or None
        folder = self._f("folder")
        target = None
        if attach_to:
            target = self.hub.kernel.require(attach_to)
            folder = folder or target["folder"]
        if not title:
            raise ShellError("Give the document a title.", field="title")
        if folder not in bundle["folders"]:
            raise ShellError(f"There is no folder '{folder}' in the release.", 404)
        from .pages import may_file  # pages imports this module's form; import here to avoid a cycle

        d = may_file(bundle, user, folder)
        if not d["allowed"]:
            raise ShellError(f"You cannot file documents in {folder}. " + self._why_text(d, "create", "document", folder), 403)
        try:
            content, mime = wopi.blank_document(kind)
        except KernelGuardError as e:
            raise ShellError(f"{e.detail}.", field="kind") from None
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip() or "document"
        doc = self.hub.dms.create(title=title, doc_class="working", folder=folder, by=user, content=content, filename=f"{safe}.{kind}",
                                  mime=mime, doc_type="document", attach_to=target["locator"] if target else None)
        where = href("/edit/" + quote(doc["locator"], safe=":"), user) if editor_configured() else \
            href("/document/" + quote(doc["locator"], safe=":"), user, done=f"Created {doc['number']}; download it to edit, then check it in.")
        self._redirect(self._once_finish(where))

    def post_working_copy(self):
        """A received or effective document cannot change; a working copy of its content can, and stays linked."""
        from .pages import may_file

        user = self.user
        bundle = self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        rec = self.hub.kernel.require(self._f("doc"))
        if rec["type"] != "document":
            raise ShellError(f"{rec['number']} is not a document.")
        self._require_read(rec["folder"])
        d = may_file(bundle, user, rec["folder"])
        if not d["allowed"]:
            raise ShellError(f"You cannot file documents in {rec['folder']}. " + self._why_text(d, "create", "document", rec["folder"]), 403)
        if not wopi.is_editable(rec):
            raise ShellError(f"{rec['number']} ({rec['fields'].get('filename') or 'no file'}) is not a kind of file the editor edits.")
        content, version = self.hub.dms.content(rec["locator"])
        f = rec["fields"]
        copy = self.hub.dms.create(title=f"{f.get('title') or rec['number']} (working copy)", doc_class="working", folder=rec["folder"], by=user,
                                   content=content, filename=version.get("filename") or f.get("filename") or "", mime=version.get("mime") or f.get("mime") or "",
                                   doc_type=f.get("doc_type") or "document")
        self.hub.kernel.link(copy["locator"], "references", rec["locator"], by=user)
        for l in self.hub.kernel.links_of(rec["locator"]):
            if l["direction"] == "in" and l["rel"] == "has-document":
                try:
                    self.hub.dms.attach(l["target"], copy["locator"], by=user)
                except Exception:
                    pass
        where = href("/edit/" + quote(copy["locator"], safe=":"), user) if editor_configured() else \
            href("/document/" + quote(copy["locator"], safe=":"), user, done=f"Created {copy['number']} as a working copy of {rec['number']}.")
        self._redirect(self._once_finish(where))

    # ------------------------------------------------------------ the WOPI side (token-authenticated; no `as`)
    def handle_wopi(self, method: str) -> None:
        host = wopi.WopiHost(self.hub)
        token = self.q.get("access_token", [""])[0]
        claims = wopi.read_token(token, root=self.hub.root)
        if claims is None:
            return self._json({"error": "invalid or expired access token"}, 401)
        rest = self.route[len("/wopi/files/"):]
        number, _, tail = rest.partition("/")
        rec = host.document(number)
        if rec is None or rec["number"] != claims.get("f"):
            return self._json({"error": "no such file for this token"}, 404)
        override = (self.headers.get("X-WOPI-Override") or "").upper()
        origin = (os.environ.get("HUBPROTO_PUBLIC_URL") or "").rstrip("/")
        try:
            if method == "GET" and not tail:
                return self._json(host.check_file_info(rec, claims, post_message_origin=origin))
            if method == "GET" and tail == "contents":
                content, version = host.get_file(rec)
                self.send_response(200)
                self.send_header("Content-Type", version.get("mime") or "application/octet-stream")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("X-WOPI-ItemVersion", str(version.get("version")))
                self.end_headers()
                self.wfile.write(content)
                return None
            if method == "POST" and tail == "contents" and override in ("PUT", ""):
                seen_stamp = self.headers.get("X-COOL-WOPI-Timestamp") or self.headers.get("X-LOOL-WOPI-Timestamp")
                _, latest = host.get_file(rec)
                if seen_stamp and wopi.wopi_time(latest.get("at")) != seen_stamp and latest.get("by") == "hub":
                    # The hub wrote a reply into the document after this session last saved: the editor reloads first.
                    return self._json({"COOLStatusCode": 1010, "LOOLStatusCode": 1010}, 409)
                result = host.put_file(rec, claims, self.raw_body)
                log_line(f"editor save doc={rec['number']} by={claims['u']} bytes={len(self.raw_body)}")
                return self._json(result)
            if method == "POST" and not tail:
                lock_id = self.headers.get("X-WOPI-Lock") or ""
                if override == "LOCK":
                    old = self.headers.get("X-WOPI-OldLock")
                    status, headers = host.unlock(rec, claims, old) if old else (200, {})
                    if status == 200:
                        status, headers = host.lock(self.hub.kernel.get(rec["locator"]), claims, lock_id)
                elif override == "REFRESH_LOCK":
                    status, headers = host.lock(rec, claims, lock_id)
                elif override == "UNLOCK":
                    status, headers = host.unlock(rec, claims, lock_id)
                elif override == "GET_LOCK":
                    status, headers = host.get_lock(rec)
                else:
                    return self._json({"error": f"unsupported operation {override or 'none'}"}, 501)
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
        except KernelGuardError as e:
            self.send_response(409 if e.guard in ("checked-out", "read-only", "controlled", "immutable") else 400)
            self.send_header("X-WOPI-LockFailureReason", e.detail)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        return self._json({"error": "no such WOPI endpoint"}, 404)

    def _json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
