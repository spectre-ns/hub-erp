"""The messenger: a chat window in the bottom-right corner of every page, for dropping in on a cog informally.

The cog answers in a conversational voice, knows who you are, what item you are looking at (the whole item, when
you are on one), the procedures it follows and what is in its queue, and remembers the conversation. It changes
nothing from the chat; for reviews, edits and approvals it points you at the item's chatter, comments or Ops.

A chat line goes to the cog's queue ahead of everything else waiting there, and the page polls the thread until
the answer is in: nothing waits on the model, and a chat never collides with the cog's other work on the model.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

from .handler import href, log_line
from .icons import icon
from .text import esc

MESSENGER_JS = r"""(function(){
var root=document.getElementById('messenger');if(!root)return;
var btn=root.querySelector('.mx-toggle'),panel=root.querySelector('.mx-panel'),list=root.querySelector('.mx-list'),form=root.querySelector('form'),
ta=form.querySelector('textarea'),sel=root.querySelector('select'),send=form.querySelector('button[type=submit]'),clr=root.querySelector('.mx-clear'),
closeB=root.querySelector('.mx-close'),ctxLine=root.querySelector('.mx-context');
var me=root.getAttribute('data-as'),about=root.getAttribute('data-about')||'',threadUrl=root.getAttribute('data-thread'),postUrl=root.getAttribute('data-post'),clearUrl=root.getAttribute('data-clear');
var avatars={};try{avatars=JSON.parse(root.getAttribute('data-avatars')||'{}')}catch(e){}
function store(k,v){try{localStorage.setItem('mx.'+k,v)}catch(e){}}
function load(k){try{return localStorage.getItem('mx.'+k)}catch(e){return null}}
var cog=load('cog');var opt=cog&&sel.querySelector('option[value="'+cog+'"]');if(!opt||opt.disabled){var first=sel.querySelector('option:not([disabled])');cog=first?first.value:sel.value}sel.value=cog;
function open(on,quiet){panel.hidden=!on;btn.setAttribute('aria-expanded',on?'true':'false');root.classList.toggle('open',on);store('open',on?'1':'0');if(on){refresh();if(!quiet)setTimeout(function(){ta.focus()},30)}else if(!quiet){btn.focus()}}
var NUM=/\/agent-run\/[\w-]+|\b(?:[A-Z]{2,5}-\d{4}-\d{2,5}|[A-Z]{2,5}-\d{2,6})\b/g;
function linkify(el,text){var i=0,m;NUM.lastIndex=0;while((m=NUM.exec(text))){el.appendChild(document.createTextNode(text.slice(i,m.index)));
var a=document.createElement('a');a.href=(m[0].charAt(0)==='/'?m[0]:'/record/'+encodeURIComponent(m[0]))+'?as='+encodeURIComponent(me);a.textContent=m[0].charAt(0)==='/'?'review and confirm':m[0];el.appendChild(a);i=m.index+m[0].length}
el.appendChild(document.createTextNode(text.slice(i)))}
function li(cls){var x=document.createElement('li');x.className='mx-msg '+cls;return x}
function bubble(m){var x=li(m.mine?'mine':'theirs');if(!m.mine){var a=document.createElement('span');a.className='mx-av';a.innerHTML=avatars[m.from]||'';x.appendChild(a)}
var b=document.createElement('div');b.className='mx-bubble';linkify(b,m.body);x.appendChild(b);
if(m.looked){var lk=document.createElement('span');lk.className='mx-looked';lk.textContent=m.looked;b.appendChild(lk)}
if(m.when){var t=document.createElement('span');t.className='mx-when';t.textContent=m.when+(m.stand_in?' · no model':'');b.appendChild(t)}return x}
function empty(){var x=li('mx-empty');x.textContent='Say hello to '+cog+'. It can see what you are looking at, the procedures it follows and its queue. It will not change anything from here.';return x}
function typingLi(ahead){var t=li('theirs mx-typing');t.innerHTML='<span class="mx-av">'+(avatars[cog]||'')+'</span><div class="mx-bubble"><span class="mx-dots" aria-hidden="true"><i></i><i></i><i></i></span><span class="mx-wait"></span></div>';
var w=t.querySelector('.mx-wait');w.textContent=ahead>0?(' '+(ahead===1?'1 task':ahead+' tasks')+' ahead of you in '+cog+'\'s queue'):' '+cog+' is on it';return t}
var poll=null;function busy(on){send.disabled=on;sel.disabled=on}
function render(msgs,pending){list.innerHTML='';if(!msgs.length&&!pending)list.appendChild(empty());msgs.forEach(function(m){list.appendChild(bubble(m))});
if(pending){list.appendChild(typingLi(pending.ahead||0));busy(true);if(!poll)poll=setTimeout(function(){poll=null;refresh()},2500)}else{busy(false)}list.scrollTop=list.scrollHeight}
function refresh(){fetch(threadUrl+'&with='+encodeURIComponent(cog)).then(function(r){return r.json()}).then(function(j){render(j.messages||[],j.pending);if(ctxLine)ctxLine.textContent=j.context||''}).catch(function(){if(!poll)poll=setTimeout(function(){poll=null;refresh()},5000)})}
sel.addEventListener('change',function(){cog=sel.value;store('cog',cog);refresh();ta.focus()});
btn.addEventListener('click',function(){open(panel.hidden)});
if(closeB)closeB.addEventListener('click',function(){open(false)});
root.addEventListener('keydown',function(e){if(e.key==='Escape'&&!panel.hidden){e.preventDefault();open(false)}});
ta.addEventListener('keydown',function(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();form.requestSubmit?form.requestSubmit():form.dispatchEvent(new Event('submit',{cancelable:true}))}});
if(clr)clr.addEventListener('click',function(){var fd=new FormData();fd.append('as',me);fd.append('with',cog);fetch(clearUrl,{method:'POST',body:fd}).then(function(){refresh()})});
form.addEventListener('submit',function(e){e.preventDefault();var text=ta.value.trim();if(!text||send.disabled)return;
var em=list.querySelector('.mx-empty');if(em)em.remove();list.appendChild(bubble({mine:true,body:text}));ta.value='';
var typing=typingLi(0);list.appendChild(typing);list.scrollTop=list.scrollHeight;busy(true);
var fd=new FormData();fd.append('as',me);fd.append('to',cog);fd.append('body',text);if(about)fd.append('about',about);
fetch(postUrl,{method:'POST',body:fd}).then(function(r){return r.json().then(function(j){return{ok:r.ok,j:j}})}).then(function(res){typing.remove();
if(res.ok&&res.j.queued){refresh();return}
if(res.ok&&res.j.reply){list.appendChild(bubble(res.j.reply))}else{var x=li('mx-error');x.textContent=(res.j&&res.j.error)||'The message was not delivered.';list.appendChild(x)}
list.scrollTop=list.scrollHeight;busy(false);ta.focus()}).catch(function(){typing.remove();var x=li('mx-error');x.textContent='The hub did not answer; try again.';list.appendChild(x);busy(false)})});
if(load('open')==='1')open(true,true);
})();"""


_DID = {"move_record": "moved", "update_fields": "changed", "apply_document_ops": "edited", "edit_document": "edited",
        "start_process": "started", "take_turn": "took its turn on", "comment": "commented on", "assign": "assigned", "link_records": "linked",
        "create_record": "created a record", "create_document": "created a document", "create_task": "created a task",
        "send_back_to_start": "sent back", "complete_step": "completed the step of", "hand_back": "handed back"}


def looked_words(calls: list[dict]) -> str:
    """'looked at BILL-0012, searched "torque"' for the line under a cog's answer."""
    bits = []
    for c in calls or []:
        a = c.get("arguments") or {}
        if c["tool"] in ("get_record", "read_document", "record_history") and a.get("number"):
            bits.append(("read " if c["tool"] == "read_document" else "looked at ") + str(a["number"]))
        elif c["tool"] == "search_records":
            bits.append(("searched " + (f"“{a.get('query')}”" if a.get("query") else "") + (f" {a.get('type')}s" if a.get("type") else "")).strip())
        elif c["tool"] == "waiting_for":
            bits.append("checked " + (a.get("who") or "your") + " queue")
        elif c["tool"] == "overview":
            bits.append("counted the records")
        elif c["tool"] in _DID and a.get("number"):
            bits.append(f"{_DID[c['tool']]} {a['number']}" + (f" to {a['folder']}" if c["tool"] == "move_record" and a.get("folder") else ""))
        elif c["tool"] in _DID:
            bits.append(_DID[c["tool"]])
    seen, out = set(), []
    for b in bits:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return ", ".join(out[:8])


class MessengerMixin:
    # ------------------------------------------------------------ the widget on every page
    def _messenger_about(self) -> str:
        if self.route.startswith(("/record/", "/document/", "/edit/")) and self.arg.startswith("rec:"):
            return self.arg
        return ""

    def _messenger(self) -> str:
        """The chat button and panel, for people who are members (never when watching a cog)."""
        if not self.user or not self._is_member() or self._viewing_cog():
            return ""
        cogs = self.hub.chat_cogs()
        if not cogs:
            return ""
        about = self._messenger_about()
        from ..model import cog_availability

        status = {c: cog_availability(self.bundle, c) for c in cogs}
        options = "".join(f'<option value="{esc(c)}"{"" if status[c][0] else " disabled"}>{esc(c)}{"" if status[c][0] else " (not available)"}</option>' for c in cogs)
        if not any(ok for ok, _ in status.values()):
            return ""
        avatars = json.dumps({c: self._avatar(c, size="sm") for c in cogs})
        return f"""<aside id="messenger" class="mx" aria-label="Messenger" data-as="{esc(self.user)}" data-about="{esc(about)}"
data-thread="{esc(href('/chat-thread', self.user, about=about))}" data-post="{esc('/chat')}" data-clear="{esc('/chat-clear')}" data-avatars="{esc(avatars)}">
<button type="button" class="mx-toggle" aria-expanded="false" aria-controls="mx-panel">{icon("message")}<span>Chat with a cog</span></button>
<section id="mx-panel" class="mx-panel" role="dialog" aria-label="Chat with a cog" hidden>
<header class="mx-head"><label class="mx-pick"><span class="vh">Chat with</span><select>{options}</select></label>
<button type="button" class="mx-clear ghost" title="start a new conversation">{icon("plus")}<span class="vh">New conversation</span></button>
<button type="button" class="mx-close ghost" title="close (Esc)">{icon("x")}<span class="vh">Close</span></button></header>
<p class="mx-context" aria-live="polite"></p>
<ol class="mx-list" aria-live="polite" aria-label="Conversation"></ol>
<form class="mx-form"><label class="vh" for="mx-text">Message</label><textarea id="mx-text" rows="2" placeholder="Ask anything; Enter sends, Shift+Enter for a new line"></textarea>
<button type="submit">{icon("send") if "send" in __import__("hubproto.shell.icons", fromlist=["ICONS"]).ICONS else icon("message")}<span class="vh">Send</span></button></form>
</section><script>{MESSENGER_JS}</script></aside>"""

    # ------------------------------------------------------------ the thread (JSON)
    def page_chat_thread(self):
        self._need_release()
        cog = self.q.get("with", [""])[0]
        about = self.q.get("about", [""])[0]
        if cog not in self.hub.chat_cogs():
            return self._json({"error": f"{cog!r} is not a cog you can chat with"}, 400)
        msgs = [self._chat_line(m) for m in self.hub.inbox.chat_thread(self.user, cog)]
        waiting = [t for t in self.hub.queue.open_tasks(cog=cog) if t["kind"] == "chat" and t.get("by") == self.user]
        rec = self.hub.kernel.get(about) if about else None
        if rec is not None and not self._can_read(rec["folder"])["allowed"]:
            rec = None  # an item the person may not read is not named, not even its number or title
        context = (f"{cog} can see {rec['number']} ({(rec['fields'].get('title') or '')[:60]}) and the procedures it follows."
                   if rec else f"{cog} can see the procedures it follows and its queue.")
        # Like the stack on a person's desk: what is ahead of this question in this cog's own queue.
        pending = ({"task": waiting[0]["id"], "status": waiting[0]["status"], "ahead": self._chat_ahead(waiting[0])} if waiting else None)
        return self._json({"messages": msgs, "context": context, "pending": pending})

    # ------------------------------------------------------------ a turn (JSON); the model call runs unlocked
    @contextmanager
    def _unlocked(self):
        self.lock.release()
        try:
            yield
        finally:
            self.lock.acquire()

    def post_chat(self):
        if self._viewing_cog():
            return self._json({"error": "nothing can be sent in a cog's name"}, 403)
        self._need_release()
        cog, text, about = self._f("to"), (self._f("body") or "").strip(), self._f("about") or None
        try:
            prep = self.hub.chat_prepare(self.user, cog, text, about)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        if about and prep.get("rec"):
            try:
                self._require_read(prep["rec"]["folder"])
            except Exception:
                prep = self.hub.chat_prepare(self.user, cog, text, None)  # not theirs to read: the cog does not see it either
        mine = self.hub.inbox.chat_append(sender=self.user, to=cog, body=text, about=prep["about"])
        # The cog answers from its queue, ahead of everything else waiting there: the page never waits on the model.
        task = self.hub.queue.submit(cog, "chat", by=self.user, title=text[:300], record=None,
                                     payload={"text": text, "about": prep["about"], "line": mine["id"]})
        log_line(f"chat {self.user} -> {cog} about={prep['about'] or '-'} task={task['id']} status={task['status']}")
        if task["status"] == "done":  # worked at once (inline queue)
            line = next((m for m in reversed(self.hub.inbox.chat_thread(self.user, cog)) if m["from"] == cog), None)
            if line is not None:
                return self._json({"reply": self._chat_line(line)})
        if task["status"] == "failed":
            return self._json({"error": f"{cog} could not answer: {task.get('error') or 'unknown error'}"}, 502)
        return self._json({"queued": True, "task": task["id"], "ahead": self._chat_ahead(task)})

    def _chat_line(self, m: dict) -> dict:
        return {"from": m["from"], "mine": m["from"] == self.user, "body": m["body"], "when": (m.get("at") or "")[11:16],
                "stand_in": bool(m.get("stand_in")), "looked": looked_words(m.get("tools") or [])}

    def _chat_ahead(self, task: dict) -> int:
        """Tasks before this chat: the one its cog is working on now (a chat never interrupts it), and earlier chats."""
        return max(0, self.hub.queue.position(task) - 1) if task["status"] == "queued" else 0

    def post_chat_clear(self):
        cog = self._f("with")
        if cog not in self.hub.chat_cogs():
            return self._json({"error": f"{cog!r} is not a cog you can chat with"}, 400)
        return self._json({"cleared": self.hub.inbox.chat_clear(self.user, cog)})
