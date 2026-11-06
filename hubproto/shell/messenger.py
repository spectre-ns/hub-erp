"""The messenger: a chat window in the bottom-right corner of every page, for dropping in on a cog informally.

The cog answers in a conversational voice, knows who you are, what item you are looking at (the whole item, when
you are on one), the procedures it follows and what is in its queue, and remembers the conversation. It changes
nothing from the chat; for reviews, edits and approvals it points you at the item's chatter, comments or Ops.

The model call is the slow part of a turn, so it runs with the hub's lock released: the rest of the hub keeps
answering while a cog is thinking.
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
function open(on){panel.hidden=!on;btn.setAttribute('aria-expanded',on?'true':'false');root.classList.toggle('open',on);store('open',on?'1':'0');if(on){refresh();setTimeout(function(){ta.focus()},30)}else{btn.focus()}}
function li(cls){var x=document.createElement('li');x.className='mx-msg '+cls;return x}
function bubble(m){var x=li(m.mine?'mine':'theirs');if(!m.mine){var a=document.createElement('span');a.className='mx-av';a.innerHTML=avatars[m.from]||'';x.appendChild(a)}
var b=document.createElement('div');b.className='mx-bubble';b.textContent=m.body;x.appendChild(b);
if(m.looked){var lk=document.createElement('span');lk.className='mx-looked';lk.textContent=m.looked;b.appendChild(lk)}
if(m.when){var t=document.createElement('span');t.className='mx-when';t.textContent=m.when+(m.stand_in?' · no model':'');b.appendChild(t)}return x}
function empty(){var x=li('mx-empty');x.textContent='Say hello to '+cog+'. It can see what you are looking at, the procedures it follows and its queue. It will not change anything from here.';return x}
function render(msgs){list.innerHTML='';if(!msgs.length)list.appendChild(empty());msgs.forEach(function(m){list.appendChild(bubble(m))});list.scrollTop=list.scrollHeight}
function refresh(){fetch(threadUrl+'&with='+encodeURIComponent(cog)).then(function(r){return r.json()}).then(function(j){render(j.messages||[]);if(ctxLine)ctxLine.textContent=j.context||''}).catch(function(){})}
sel.addEventListener('change',function(){cog=sel.value;store('cog',cog);refresh();ta.focus()});
btn.addEventListener('click',function(){open(panel.hidden)});
if(closeB)closeB.addEventListener('click',function(){open(false)});
root.addEventListener('keydown',function(e){if(e.key==='Escape'&&!panel.hidden){e.preventDefault();open(false)}});
ta.addEventListener('keydown',function(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();form.requestSubmit?form.requestSubmit():form.dispatchEvent(new Event('submit',{cancelable:true}))}});
if(clr)clr.addEventListener('click',function(){var fd=new FormData();fd.append('as',me);fd.append('with',cog);fetch(clearUrl,{method:'POST',body:fd}).then(function(){refresh()})});
form.addEventListener('submit',function(e){e.preventDefault();var text=ta.value.trim();if(!text||send.disabled)return;
var em=list.querySelector('.mx-empty');if(em)em.remove();list.appendChild(bubble({mine:true,body:text}));ta.value='';
var typing=li('theirs mx-typing');typing.innerHTML='<span class="mx-av">'+(avatars[cog]||'')+'</span><div class="mx-bubble"><span class="mx-dots" aria-label="'+cog+' is thinking"><i></i><i></i><i></i></span></div>';
list.appendChild(typing);list.scrollTop=list.scrollHeight;send.disabled=true;sel.disabled=true;
var fd=new FormData();fd.append('as',me);fd.append('to',cog);fd.append('body',text);if(about)fd.append('about',about);
fetch(postUrl,{method:'POST',body:fd}).then(function(r){return r.json().then(function(j){return{ok:r.ok,j:j}})}).then(function(res){typing.remove();
if(res.ok&&res.j.reply){list.appendChild(bubble(res.j.reply))}else{var x=li('mx-error');x.textContent=(res.j&&res.j.error)||'The message was not delivered.';list.appendChild(x)}
list.scrollTop=list.scrollHeight}).catch(function(){typing.remove();var x=li('mx-error');x.textContent='The hub did not answer; try again.';list.appendChild(x)}).then(function(){send.disabled=false;sel.disabled=false;ta.focus()})});
if(load('open')==='1')open(true);
})();"""


def looked_words(calls: list[dict]) -> str:
    """'looked at BILL-0012, searched "torque"' for the line under a cog's answer."""
    bits = []
    for c in calls or []:
        a = c.get("arguments") or {}
        if c["tool"] in ("get_record", "read_document", "record_history") and a.get("number"):
            bits.append(("read " if c["tool"] == "read_document" else "looked at ") + str(a["number"]))
        elif c["tool"] == "search_records":
            bits.append("searched " + (f"“{a.get('query')}”" if a.get("query") else "") + (f" {a.get('type')}" if a.get("type") else "")).strip()
        elif c["tool"] == "waiting_for":
            bits.append("checked " + (a.get("who") or "your") + " queue")
        elif c["tool"] == "overview":
            bits.append("counted the records")
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
        msgs = [{"from": m["from"], "mine": m["from"] == self.user, "body": m["body"], "when": (m.get("at") or "")[11:16],
                 "stand_in": bool(m.get("stand_in")), "looked": looked_words(m.get("tools") or [])} for m in self.hub.inbox.chat_thread(self.user, cog)]
        rec = self.hub.kernel.get(about) if about else None
        context = (f"{cog} can see {rec['number']} ({(rec['fields'].get('title') or '')[:60]}) and the procedures it follows."
                   if rec else f"{cog} can see the procedures it follows and its queue.")
        return self._json({"messages": msgs, "context": context})

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
        self.hub.inbox.chat_append(sender=self.user, to=cog, body=text, about=prep["about"])
        from ..tools import RecordTools

        tools = RecordTools(self.hub, self.user)

        def call_tool(name, args):  # the model runs unlocked; each look-up takes the lock for its own moment
            with self.lock:
                return tools.call(name, args)

        with self._unlocked():  # the slow part: other requests are served meanwhile
            ans = self.hub.chat_answer(prep, self.user, text, call_tool=call_tool)
        line = self.hub.inbox.chat_append(sender=cog, to=self.user, body=ans["text"], about=prep["about"], model=ans["model"], stand_in=ans["stand_in"],
                                          tools=ans["tool_calls"])
        log_line(f"chat {self.user} -> {cog} about={prep['about'] or '-'} model={ans['model']} stand_in={ans['stand_in']} tools={len(ans['tool_calls'])}")
        return self._json({"reply": {"from": cog, "mine": False, "body": line["body"], "when": line["at"][11:16], "stand_in": ans["stand_in"],
                                     "looked": looked_words(ans["tool_calls"])}})

    def post_chat_clear(self):
        cog = self._f("with")
        if cog not in self.hub.chat_cogs():
            return self._json({"error": f"{cog!r} is not a cog you can chat with"}, 400)
        return self._json({"cleared": self.hub.inbox.chat_clear(self.user, cog)})
