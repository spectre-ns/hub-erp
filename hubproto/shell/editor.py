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
{f"<span class='why'>{esc(reason)}</span>" if reason else "<span class='why'>Every save becomes a version; others may join and edit with you; the last to finish releases the document.</span>"} <span class='why' id='editstatus' role='status'></span> <span class='why' id='cogwork' role='status' aria-live='polite'></span>
<form method="post" action="/finish-edit" class="inline">{hidden_as(user)}<input type="hidden" name="doc" value="{esc(rec['locator'])}"><button class="{'primary' if writable else 'secondary'}">{icon('check' if writable else 'x')}{'Finish editing' if writable else 'Close'}</button></form></div>"""
        name = rec["fields"].get("sop_name") if str(rec["fields"].get("lineage", "")).startswith("sop:") else None
        if name and rec["state"] == "effective":  # the viewer of an SOP in force says where the configuration differs from its lines
            bar += self._divergence_card(name)
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
        # A comment reaches the hub when it is saved (issue #67): Collabora says so (Inserted_Comment, on Save or Reply,
        # and when it keeps a comment someone left the box of), and the page then saves the document. It never saves on
        # every change: that made a version of every pause in typing and brought a cog in mid-sentence.
        bundle = self.bundle or {}
        public = (os.environ.get("HUBPROTO_PUBLIC_URL") or "").rstrip("/")
        people = [{"username": n, "profile": f"{public}/user/{n}", "kind": m.get("type", "person")} for n, m in sorted((bundle.get("members") or {}).items())]
        roles = [{"username": r, "profile": f"{public}/people", "kind": "role"} for r in sorted(bundle.get("roles") or [])]
        mentions = json.dumps(people + roles)
        parts = browser_url.split("/")
        origin = parts[0] + "//" + parts[2] if len(parts) > 2 and "//" in browser_url else browser_url
        from .seat_ops import SEAT_OPS_JS

        body += "<script>" + SEAT_OPS_JS + "</script>"
        body += rf"""<script>(function(){{var frame=document.getElementById('editor');var list={mentions};var origin={json.dumps(origin)};
function post(o){{try{{frame.contentWindow.postMessage(JSON.stringify(o),'*')}}catch(e){{}}}}
window.addEventListener('message',function(ev){{if(origin&&ev.origin!==origin)return;var m;try{{m=JSON.parse(ev.data)}}catch(e){{return}}
if(m.MessageId==='App_LoadingStatus'&&m.Values&&m.Values.Status==='Document_Loaded'){{post({{MessageId:'Host_PostmessageReady'}})}}
if(m.MessageId==='UI_Mention'){{var v=m.Values||{{}};if(v.type==='autocomplete'){{var q=String(v.text||'').replace(/^@/,'').toLowerCase();
var out=list.filter(function(x){{return x.username.toLowerCase().indexOf(q)===0}}).slice(0,10).map(function(x){{return {{username:x.username,profile:x.profile}}}});
post({{MessageId:'Action_Mention',SendTime:Date.now(),Values:{{list:out}}}})}}}}
if(m.MessageId==='Action_Save_Resp'){{if(window.__applyAfterSave){{window.__applyAfterSave=false;applyReplies()}}}}
if(m.MessageId==='Inserted_Comment'){{clearTimeout(window.__saveSoon);window.__saveSoon=setTimeout(function(){{post({{MessageId:'Action_Save',Values:{{DontTerminateEdit:true,DontSaveIfUnmodified:true,Notify:true}}}})}},300)}}}});
var repliesUrl={json.dumps(href("/editor-replies/" + quote(rec["locator"], safe=":"), user))};var applyUrl={json.dumps(href("/apply-replies", user))};var loc={json.dumps(rec["locator"])};var busy=false;var loadedVersion={len(self.hub.dms.versions(rec["locator"]))};
function reloadFrame(why){{var s=document.getElementById('editstatus');if(s)s.textContent=why+'; reloading…';loadedVersion=1e9;frame.src=frame.src.replace(/&reload=\d+/,'')+'&reload='+Date.now();setTimeout(function(){{fetch(repliesUrl).then(function(r){{return r.json()}}).then(function(j){{loadedVersion=j.version;busy=false}})}},3000)}}
function applyReplies(){{var fd=new FormData();fd.append('as',{json.dumps(user)});fd.append('doc',loc);fetch(applyUrl,{{method:'POST',body:fd}}).then(function(r){{return r.json()}}).then(function(j){{busy=false;if(j.applied>0){{var s=document.getElementById('editstatus');if(s)s.textContent='Replies from '+j.by.join(', ')+' written into the document; reloading…';frame.src=frame.src.replace(/&reload=\d+/,'')+'&reload='+Date.now()}}}}).catch(function(){{busy=false}})}}
var verifyUrl={json.dumps(href("/verify-edit", user))};var seatUrl={json.dumps(href("/cog-seat/" + quote(rec["locator"], safe=":"), user))};var markUrl={json.dumps(href("/mark-applied", user))};var debugUrl={json.dumps(href("/editor-debug", user))};
var seats={{}};var inflight={{}};
function dbg(line){{var fd=new FormData();fd.append('as',{json.dumps(user)});fd.append('line',line);fetch(debugUrl,{{method:'POST',body:fd}}).catch(function(){{}})}}
function seatPost(cog,o){{var f=seats[cog]&&seats[cog].frame;if(f)try{{f.contentWindow.postMessage(JSON.stringify(o),'*')}}catch(e){{}}}}
function seat(cog,cb){{var st=seats[cog];if(st&&st.ready){{cb(st);return}}if(st){{st.waiting.push(cb);return}}
st=seats[cog]={{ready:false,waiting:[cb],frame:null,pendingComments:null}};
fetch(seatUrl+'&cog='+encodeURIComponent(cog)).then(function(r){{return r.json()}}).then(function(j){{if(!j.frame){{dbg('seat refused for '+cog+': '+(j.error||'?'));delete seats[cog];cb(null);return}}
var f=document.createElement('iframe');f.setAttribute('aria-hidden','true');f.style.cssText='position:absolute;width:900px;height:600px;left:-10000px;top:0;border:0';f.src=j.frame;f.title=cog+' (seat)';document.body.appendChild(f);st.frame=f;
st.timer=setTimeout(function(){{if(!st.ready){{dbg('seat for '+cog+' did not load in time');var w=st.waiting;st.waiting=[];w.forEach(function(x){{x(null)}})}}}},25000)}}).catch(function(e){{dbg('seat error '+e);delete seats[cog];cb(null)}})}}
window.addEventListener('message',function(ev){{if(origin&&ev.origin!==origin)return;var m;try{{m=JSON.parse(ev.data)}}catch(e){{return}}
var cog=null;for(var k in seats){{if(seats[k].frame&&ev.source===seats[k].frame.contentWindow)cog=k}}if(!cog)return;var st=seats[cog];
if(m.MessageId==='App_LoadingStatus'&&m.Values&&m.Values.Status==='Document_Loaded'){{seatPost(cog,{{MessageId:'Host_PostmessageReady'}});st.ready=true;clearTimeout(st.timer);dbg('seat ready for '+cog);var w=st.waiting;st.waiting=[];w.forEach(function(x){{x(st)}})}}
if(m.MessageId==='Get_Comments_Resp'){{var cb=st.onComments;st.onComments=null;if(cb)cb((m.Values&&m.Values.Comments)||[])}}}});
function norm(t){{return String(t||'').replace(/\s+/g,' ').trim().toLowerCase()}}
function actLive(reply,done){{seat(reply.from,function(st){{if(!st){{done(false);return}}
var finish=function(ok){{done(ok)}};
if(reply.edit&&reply.edit.ops){{if(!HubSeatOps.liveCapable(reply.edit)){{dbg('seat '+reply.from+': '+reply.edit.ops.length+' change(s) done by the hub in the file');done(false);return}}
HubSeatOps.run(reply.edit,function(c,a){{seatPost(reply.from,{{MessageId:'Send_UNO_Command',Values:a?{{Command:c,Args:a}}:{{Command:c}}}})}},function(){{dbg('seat '+reply.from+' made '+reply.edit.ops.length+' change(s) live');replyLive()}});return}}
if(reply.edit&&reply.edit.kind==='sheet'&&reply.edit.rows&&reply.edit.rows.length){{var rows=reply.edit.rows;var r0=reply.edit.start_row||1;var uno=function(c,a){{seatPost(reply.from,{{MessageId:'Send_UNO_Command',Values:a?{{Command:c,Args:a}}:{{Command:c}}}})}};var col=function(n){{var s='';n++;while(n){{var q=Math.floor((n-1)/26);var r=(n-1)%26;s=String.fromCharCode(65+r)+s;n=q}}return s}};
var ri=0;var rowStep=function(){{if(ri>=rows.length){{dbg('seat '+reply.from+' wrote '+rows.length+' row(s) live into the sheet from row '+r0);replyLive();return}}var cells=rows[ri];var rr=r0+ri;ri++;var ci=0;var cellStep=function(){{if(ci>=cells.length){{setTimeout(rowStep,120);return}}var addr=col(ci)+rr;var val=cells[ci];ci++;uno('.uno:GoToCell',{{ToPoint:{{type:'string',value:addr}}}});setTimeout(function(){{uno('.uno:EnterString',{{StringName:{{type:'string',value:val}}}});setTimeout(cellStep,110)}},90)}};cellStep()}};setTimeout(rowStep,300)}}
else if(reply.edit&&reply.edit.position==='replace'){{dbg('seat '+reply.from+': a revision replaces the body; written by the hub');done(false);return}}
else if(reply.edit&&reply.edit.paragraphs&&reply.edit.paragraphs.length){{seatPost(reply.from,{{MessageId:'Send_UNO_Command',Values:{{Command:reply.edit.position==='end'?'.uno:GoToEndOfDoc':'.uno:GoToStartOfDoc'}}}});
var lines=reply.edit.paragraphs.slice();var i=0;var uno=function(c,a){{seatPost(reply.from,{{MessageId:'Send_UNO_Command',Values:a?{{Command:c,Args:a}}:{{Command:c}}}})}};var styleArg=function(name){{return {{Style:{{type:'string',value:name}},FamilyName:{{type:'string',value:'ParagraphStyles'}}}}}};
var typeRuns=function(text){{var parts=text.split(/(\*\*[^*]+\*\*)/);var d=0;parts.forEach(function(pt){{if(!pt)return;var bold=pt.length>4&&pt.slice(0,2)==='**'&&pt.slice(-2)==='**';var t=bold?pt.slice(2,-2):pt;setTimeout(function(){{if(bold)uno('.uno:Bold');uno('.uno:InsertText',{{Text:{{type:'string',value:t}}}});if(bold)setTimeout(function(){{uno('.uno:Bold')}},60)}},d);d+=120}});return d}};
var isRow=function(t){{return /^\|.*\|$/.test(t)}};var isSep=function(t){{return /^\|[\s:|-]+\|$/.test(t)}};
var step=function(){{if(i>=lines.length){{uno('.uno:StyleApply',styleArg('Default Paragraph Style'));dbg('seat '+reply.from+' wrote '+lines.length+' line(s) live with formatting');replyLive();return}}var raw=lines[i];var line=raw.trim();
if(isRow(line)){{var rows=[];while(i<lines.length&&isRow(lines[i].trim())){{var t=lines[i++].trim();if(!isSep(t))rows.push(t.replace(/^\||\|$/g,'').split('|').map(function(x){{return x.trim().replace(/\*\*/g,'')}}))}}
var cols=0;rows.forEach(function(r){{if(r.length>cols)cols=r.length}});uno('.uno:StyleApply',styleArg('Default Paragraph Style'));uno('.uno:InsertTable',{{Columns:{{type:'unsigned short',value:cols}},Rows:{{type:'unsigned short',value:rows.length}}}});
var cells=[];rows.forEach(function(r){{for(var c=0;c<cols;c++)cells.push(r[c]||'')}});var k=0;var cellStep=function(){{if(k>=cells.length){{setTimeout(function(){{uno('.uno:GoDown');uno('.uno:InsertPara');setTimeout(step,160)}},160);return}}var v=cells[k++];uno('.uno:InsertText',{{Text:{{type:'string',value:v}}}});setTimeout(function(){{if(k<cells.length)uno('.uno:JumpToNextCell');setTimeout(cellStep,110)}},90)}};setTimeout(cellStep,400);return}}
i++;var style='Default Paragraph Style';var bullet=false;if(line==='---'){{uno('.uno:InsertPagebreak');setTimeout(step,150);return}}
if(line.indexOf('# ')===0){{style='Heading 1';line=line.slice(2)}}else if(line.indexOf('## ')===0){{style='Heading 2';line=line.slice(3)}}else if(line.indexOf('### ')===0){{style='Heading 3';line=line.slice(4)}}else if(/^[-*•] /.test(line)){{bullet=true;line=line.slice(2)}}else if(/^\d+[.)] /.test(line)){{bullet='num';line=line.replace(/^\d+[.)] /,'')}}
uno('.uno:StyleApply',styleArg(style));if(bullet==='num')uno('.uno:DefaultNumbering');else if(bullet)uno('.uno:DefaultBullet');var wait=typeRuns(line);setTimeout(function(){{uno('.uno:InsertPara');if(bullet)setTimeout(function(){{uno(bullet==='num'?'.uno:DefaultNumbering':'.uno:DefaultBullet')}},80);setTimeout(step,bullet?220:140)}},wait+120)}};setTimeout(step,300)}}else{{replyLive()}}
function replyLive(){{if(reply.comment_id===null||reply.comment_id===undefined||reply.comment_id===''){{finish(true);return}}
var attempts=0;var ask=function(){{attempts++;
st.onComments=function(list){{var target=null;for(var i=0;i<list.length;i++){{var c=list[i];if(norm(c.Text)===norm(reply.comment_text)&&(!reply.comment_author||norm(c.Author)===norm(reply.comment_author))){{target=c}}}}
if(!target){{for(var i=0;i<list.length;i++){{if(norm(list[i].Text).indexOf(norm(reply.comment_text).slice(0,40))===0)target=list[i]}}}}
if(!target){{if(attempts<8){{setTimeout(ask,2000);return}}dbg('seat '+reply.from+': comment not found among '+list.length+' comment(s): '+JSON.stringify(list.map(function(c){{return [c.Id,c.Author,String(c.Text||'').slice(0,40)]}})));finish(false);return}}
seatPost(reply.from,{{MessageId:'Send_UNO_Command',Values:{{Command:'.uno:ReplyComment',Args:{{Id:{{type:'string',value:String(target.Id)}},Text:{{type:'string',value:reply.body}}}}}}}});dbg('seat '+reply.from+' replied live to comment '+target.Id+' after '+attempts+' lookup(s)');setTimeout(function(){{finish(true)}},800)}};
seatPost(reply.from,{{MessageId:'Get_Comments'}});setTimeout(function(){{if(st.onComments&&attempts>=8){{st.onComments=null;dbg('seat '+reply.from+': no Get_Comments_Resp');finish(false)}}}},6000)}};
setTimeout(ask,2500)}}}})}}
function poll(){{if(busy)return;fetch(repliesUrl).then(function(r){{return r.json()}}).then(function(j){{
var cw=document.getElementById('cogwork');if(cw){{var t=(j.queue||[]).join('; ');cw.textContent=t?'⏳ '+t+'…':''}}
if(j.pending>0&&!busy){{busy=true;var list=j.replies.filter(function(x){{return !inflight[x.id]}});if(!list.length){{busy=false;return}}var r0=list[0];inflight[r0.id]=true;
actLive(r0,function(ok){{if(ok&&r0.edit&&r0.edit.ops){{var s0=document.getElementById('editstatus');if(s0)s0.textContent=r0.from+' made the change; checking it…';
HubSeatOps.verifyAfterSave({{repliesUrl:repliesUrl,verifyUrl:verifyUrl,user:{json.dumps(user)},loc:loc,id:r0.id,post:post}},function(v){{var st2=document.getElementById('editstatus');
if(v&&v.fixed>0){{reloadFrame(r0.from+' finished '+v.fixed+' change(s) in the file')}}else{{busy=false;if(v&&v.version)loadedVersion=v.version;if(st2)st2.textContent=r0.from+' made the change'+(v&&v.failed&&v.failed.length?'; some of it could not be done (see the chatter)':'')+'.'}}}});return}}
if(ok){{var fd=new FormData();fd.append('as',{json.dumps(user)});fd.append('doc',loc);fd.append('ids',r0.id);fetch(markUrl,{{method:'POST',body:fd}}).then(function(){{busy=false;var s=document.getElementById('editstatus');if(s)s.textContent=r0.from+' answered in the document.';setTimeout(function(){{post({{MessageId:'Action_Save',Values:{{DontTerminateEdit:true,DontSaveIfUnmodified:false,Notify:true}}}})}},1500)}}).catch(function(){{busy=false}})}}
else{{delete inflight[r0.id];window.__applyAfterSave=true;post({{MessageId:'Action_Save',Values:{{DontTerminateEdit:true,DontSaveIfUnmodified:true,Notify:true}}}});setTimeout(function(){{if(window.__applyAfterSave){{window.__applyAfterSave=false;applyReplies()}}}},2500)}}}})}}
else if(j.version>loadedVersion&&j.machine&&!busy){{busy=true;reloadFrame(j.version_by+' wrote version '+j.version)}}else if(j.version>loadedVersion){{loadedVersion=j.version}}}}).catch(function(){{}})}}
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
        versions = self.hub.dms.versions(rec["locator"])
        latest = versions[-1] if versions else {}
        machine = latest.get("by") in set(self.hub.registry.ids()) | {"hub"}
        from .queue import ordinal

        work = []
        for t in self.hub.queue.open_tasks(record=rec["locator"]):
            pos = self.hub.queue.position(t)
            work.append(f"{t['cog']} is " + ("working on " + ("your comment" if t["kind"] == "mention" else "it") if pos == 0 else
                                             ("next to look at it" if pos == 1 else f"{ordinal(pos)} in line to look at it")))
        return self._json({"pending": len(pending), "by": sorted({m["from"] for m in pending}), "queue": work,
                           "replies": [{"id": m["id"], "from": m["from"], "body": m.get("body") or "", "comment_id": m.get("comment_id"),
                                        "comment_text": m.get("comment_text") or "", "comment_author": m.get("comment_author") or "",
                                        "edit": m.get("edit")} for m in pending],
                           "version": latest.get("version") or 0, "version_by": latest.get("by") or "", "machine": machine})

    def page_cog_seat(self):
        """A seat for a cog in the live editing session: the editor URL with the cog's own token."""
        self._need_release()
        rec = self.hub.kernel.get(self.arg)
        if rec is None or rec["type"] != "document":
            return self._json({"error": "no such document"}, 404)
        self._require_read(rec["folder"])
        cog_id = self.q.get("cog", [""])[0]
        if cog_id not in self.hub.registry.ids():
            return self._json({"error": f"{cog_id!r} is not a cog"}, 400)
        try:
            token = wopi.WopiHost(self.hub).seat_token(rec, cog_id, self.user)
        except KernelGuardError as e:
            return self._json({"error": e.detail}, 403)
        browser_url, host_url = wopi.collabora_urls()
        frame = wopi.editor_frame_url(rec, token, browser_url=browser_url, host_url=host_url,
                                      discovery_url=os.environ.get("HUBPROTO_COLLABORA_DISCOVERY", ""))
        log_line(f"editor seat doc={rec['number']} cog={cog_id} by={self.user}")
        return self._json({"frame": frame, "cog": cog_id})

    def post_mark_applied(self):
        """The cog's seat wrote these answers live; the hub must not write them into the file as well."""
        self._need_release()
        rec = self.hub.kernel.require(self._f("doc"))
        self._require_read(rec["folder"])
        may, _why = self._may_edit_document(rec)
        if not may:  # only someone who may change the document writes pending answers into it, or marks them done
            return self._json({"error": f"you may read {rec['number']} but not change it"}, 403)
        ids = [x for x in (self._f("ids") or "").split(",") if x]
        pending = {m["id"] for m in wopi.WopiHost(self.hub).pending_replies(rec)}
        done = [i for i in ids if i in pending]
        for i in done:
            self.hub.inbox.mark(i, applied_in="live", applied_by=self.user)
        log_line(f"editor live-applied doc={rec['number']} ids={','.join(done)} by={self.user}")
        return self._json({"applied": len(done)})

    def post_verify_edit(self):
        """The cog's seat made its changes live and the session saved: check each in the file, finish the rest."""
        self._need_release()
        rec = self.hub.kernel.require(self._f("doc"))
        self._require_read(rec["folder"])
        may, _why = self._may_edit_document(rec)
        if not may:  # only someone who may change the document writes pending answers into it, or marks them done
            return self._json({"error": f"you may read {rec['number']} but not change it"}, 403)
        try:
            out = wopi.WopiHost(self.hub).verify_edit(rec, self._f("id") or "", self.user)
        except KernelGuardError as e:
            return self._json({"error": str(e)}, 400)
        log_line(f"editor verify doc={rec['number']} id={self._f('id')} fixed={out['fixed']} failed={len(out['failed'])} by={self.user}")
        return self._json(out)

    def post_editor_debug(self):
        """The editor page reports what the cog seat did (for the log; no state changes)."""
        log_line(f"editor page as={self.user}: {(self._f('line') or '')[:400]}")
        return self._json({"ok": True})

    def post_apply_replies(self):
        """Write the waiting replies into the document as reply comments (a new version by the hub)."""
        self._need_release()
        rec = self.hub.kernel.require(self._f("doc"))
        if rec["type"] != "document":
            return self._json({"error": "not a document"}, 400)
        self._require_read(rec["folder"])
        may, _why = self._may_edit_document(rec)
        if not may:  # only someone who may change the document writes pending answers into it, or marks them done
            return self._json({"error": f"you may read {rec['number']} but not change it"}, 403)
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
        saved = [v for v in versions if v.get("by") == self.user and v.get("note") == "saved in the editor"]
        after = self.hub.kernel.get(rec["locator"])["fields"]
        if saved and saved[-1] is versions[-1]:
            msg = f"Finished editing {rec['number']}: your changes are version {versions[-1]['version']}."
        else:
            msg = f"Closed the editor for {rec['number']}; nothing was changed."
        if after.get("checked_out_by"):
            msg += f" {after['checked_out_by']} is still editing it."
        self._redirect(href("/document/" + quote(rec["locator"], safe=":"), self.user, done=msg))

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
        if kind in wopi.HUB_TEXT_EXTENSIONS or not editor_configured():
            where = href("/document/" + quote(doc["locator"], safe=":"), user, done=f"Created {doc['number']}." + (" Markdown is edited by the hub: check it out, edit, check it in, or ask a cog to write it." if kind in wopi.HUB_TEXT_EXTENSIONS else " Download it to edit, then check it in."))
        else:
            where = href("/edit/" + quote(doc["locator"], safe=":"), user)
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
                machines = {n for n, m in ((self.bundle or {}).get("members") or {}).items() if m.get("type") == "cog"}
                if wopi.is_stale_save(latest, seen_stamp or "", machines):
                    # The hub or a cog wrote into the document after this session last saved: the editor reloads first.
                    return self._json({"COOLStatusCode": 1010, "LOOLStatusCode": 1010}, 409)
                result = host.put_file(rec, claims, self.raw_body)
                log_line(f"editor save doc={rec['number']} by={claims['u']} bytes={len(self.raw_body)}")
                return self._json(result)
            if method == "POST" and not tail:
                lock_id = self.headers.get("X-WOPI-Lock") or ""
                if override in ("LOCK", "REFRESH_LOCK", "UNLOCK") and not claims.get("w"):
                    # A view-only session never locks: only an editor's token may take, refresh or clear a lock.
                    self.send_response(409)
                    self.send_header("X-WOPI-Lock", "")
                    self.send_header("X-WOPI-LockFailureReason", "this session may only view the document")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
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
