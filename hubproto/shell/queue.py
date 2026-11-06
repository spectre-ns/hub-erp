"""The cogs' queues in the shell: what each cog is working on and what waits for it, on its profile, on the item it
was asked about (the chatter shows "working on it" until the answer lands, without a reload) and on one overview."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from ..workqueue import KINDS
from .forms import ShellError
from .handler import hidden_as, href, table
from .icons import icon
from .text import esc, short, when


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _secs(a: str | None, b: str | None = None) -> int | None:
    try:
        t0 = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(str(b).replace("Z", "+00:00")) if b else datetime.now(t0.tzinfo)
        return max(0, int((t1 - t0).total_seconds()))
    except (TypeError, ValueError):
        return None


def took(s: int | None) -> str:
    if s is None:
        return ""
    return f"{s} s" if s < 90 else f"{s // 60} min {s % 60:02d} s" if s < 3600 else f"{s // 3600} h {s % 3600 // 60} min"


# The page asks every few seconds whether anything it shows has changed (a cog started or finished, a message landed)
# and swaps in the parts marked data-live from a fresh copy of itself, so what you are typing stays where it is.
LIVE_JS = r"""(function(){var meta=document.getElementById('live');if(!meta)return;var url=meta.getAttribute('data-url');var stamp=meta.getAttribute('data-stamp');
var open=+meta.getAttribute('data-open');var timer=null;
function schedule(){clearTimeout(timer);timer=setTimeout(tick,document.hidden?20000:(open>0?2500:12000))}
function refresh(){fetch(location.href,{cache:'no-store',credentials:'same-origin'}).then(function(r){return r.text()}).then(function(t){
var doc=new DOMParser().parseFromString(t,'text/html');document.querySelectorAll('[data-live]').forEach(function(el){var n=doc.getElementById(el.id);if(n&&n.outerHTML!==el.outerHTML)el.replaceWith(n)})})}
function tick(){fetch(url,{cache:'no-store',credentials:'same-origin'}).then(function(r){return r.json()}).then(function(j){if(j.stamp!==stamp){stamp=j.stamp;refresh()}open=j.open||0;schedule()}).catch(schedule)}
document.addEventListener('visibilitychange',function(){if(!document.hidden)tick()});schedule()})();"""


class QueueMixin:
    # ------------------------------------------------------------ words
    def _task_state(self, t: dict) -> str:
        if t["status"] == "working":
            return f"<span class='chip work'>{icon('activity')}working on it</span> <span class='muted'>for {esc(took(_secs(t['started_at'])))}</span>"
        if t["status"] == "queued":
            pos = self.hub.queue.position(t)
            return f"<span class='chip'>{icon('clock')}in its queue</span> <span class='muted'>{'next' if pos == 1 else ordinal(pos) + ' in line'}</span>"
        if t["status"] == "done":
            res = t.get("result") or {}
            word = "skipped" if res.get("skipped") else "done"
            return f"<span class='chip ok'>{word}</span> <span class='muted'>in {esc(took(_secs(t['started_at'], t['finished_at'])))}</span>"
        if t["status"] == "failed":
            return f"<span class='chip crit'>failed</span> <span class='muted'>{esc(short(t.get('error') or '', 160))}</span>"
        return f"<span class='chip'>{esc(t['status'])}</span> <span class='muted'>{esc(short(t.get('error') or '', 120))}</span>"

    def _task_what(self, t: dict, with_record: bool = True) -> str:
        rec = self.hub.kernel.get(t["record"]) if with_record and t.get("record") else None
        where = f" on {self._number_link(rec['locator'], rec['number'])}" if rec else ""
        res = t.get("result") or {}
        link = ""
        if res.get("run") and t["kind"] in ("task", "duty"):
            link = f" <a href='{href('/agent-run/' + res['run'], self.user)}'>see the run</a>"
        title = short(t.get("title") or "", 140)
        return f"{esc(KINDS.get(t['kind'], t['kind']))}{where}: <q>{esc(title)}</q>{link}"

    def _withdraw_button(self, t: dict, back: str) -> str:
        if t["status"] != "queued" or self._viewing_cog() or not (t.get("by") == self.user or self._policy_owner()):
            return ""
        return (f"<form method='post' action='/queue-withdraw' class='inline'>{hidden_as(self.user)}{self._once()}<input type='hidden' name='task' value='{esc(t['id'])}'>"
                f"<input type='hidden' name='return' value='{esc(back)}'><button class='ghost' title='take it out of the queue'>{icon('x')}Withdraw</button></form>")

    # ------------------------------------------------------------ live refresh
    def _live_meta(self, *, record: str | None = None, cog: str | None = None) -> str:
        stamp, n_open = self._live_stamp(record=record, cog=cog)
        url = href("/queue-status", self.user, **({"record": record} if record else {"cog": cog} if cog else {}))
        return f"<span id='live' hidden data-url='{esc(url)}' data-stamp='{esc(stamp)}' data-open='{n_open}'></span><script>{LIVE_JS}</script>"

    def _live_stamp(self, *, record: str | None = None, cog: str | None = None) -> tuple[str, int]:
        q = self.hub.queue
        if record:
            opened = q.open_tasks(record=record)
            rec = self.hub.kernel.get(record) or {}
            stamp = f"{q.stamp(record=record)}|{len(self.hub.inbox.on_record(record))}|{rec.get('updated_at', '')}|{rec.get('state', '')}"
        else:
            opened = q.open_tasks(cog=cog)
            stamp = q.stamp(cog=cog)
        return stamp, len(opened)

    def page_queue_status(self):
        """For the page's polling: a stamp that changes when what the page shows changes."""
        self._need_release()
        record, cog = self.q.get("record", [""])[0], self.q.get("cog", [""])[0]
        if record:
            rec = self.hub.kernel.get(record)
            if rec is None:
                return self._json({"error": "no such item"}, 404)
            self._require_read(rec["folder"])
        stamp, n_open = self._live_stamp(record=record or None, cog=cog or None)
        return self._json({"stamp": stamp, "open": n_open})

    # ------------------------------------------------------------ on an item's chatter
    def _queue_entries(self, loc: str, back: str) -> str:
        """Open tasks about this item, as timeline entries at the top of its chatter."""
        out = ""
        for t in reversed(self.hub.queue.open_tasks(record=loc)):
            who = t["cog"]
            name = f'<a href="{href("/user/" + who, self.user)}">{esc(who)}</a>'
            out += (f'<li class="kind-working">{self._avatar(who, size="sm")}<div><div class="tl-head"><b>{name}</b>'
                    f'<span class="kind-chip">{icon("clock")}queue</span><time datetime="{esc(t["enqueued_at"])}">{esc(when(t["enqueued_at"]))}</time></div>'
                    f'<div class="tl-body">{self._task_state(t)} &middot; {self._task_what(t, with_record=False)}'
                    f'{" &middot; asked by " + esc(t["by"]) if t.get("by") else ""} {self._withdraw_button(t, back)}</div></div></li>')
        return out

    # ------------------------------------------------------------ on a cog's profile
    def _cog_queue_section(self, cog: str) -> str:
        q = self.hub.queue
        opened = q.open_tasks(cog=cog)
        recent = [t for t in q.list(cog=cog, status=("done", "failed", "withdrawn"), limit=10, newest_first=True)]
        back = href("/user/" + cog, self.user)
        items = ""
        for t in opened:
            items += (f"<li class='{'working' if t['status'] == 'working' else ''}'><div>{self._task_state(t)}</div>"
                      f"<div>{self._task_what(t)} <span class='muted'>&middot; asked by {esc(t.get('by') or '-')} {esc(when(t['enqueued_at']))}</span> "
                      f"{self._withdraw_button(t, back)}</div></li>")
        body = (f"<section id='cog-work' data-live><h2>{icon('inbox')} Queue <span class='muted'>{len(opened)} open</span></h2>"
                f"<p class='why'>Everything asked of {esc(cog)} lands here: mentions, messages, items waiting for it at a Gate, tasks and duties. "
                f"It takes them one at a time, oldest first, and answers where it was asked.</p>")
        body += f"<ol class='cogq'>{items}</ol>" if items else "<p class='muted'>Nothing waiting: it is free.</p>"
        if recent:
            body += (f"<details><summary>Recently finished ({len(recent)})</summary>"
                     + table(["Asked", "What", "By", "Outcome"], [[esc(when(t["enqueued_at"])), self._task_what(t), esc(t.get("by") or "-"), self._task_state(t)] for t in recent])
                     + "</details>")
        return body + "</section>"

    # ------------------------------------------------------------ all cogs
    def page_queue(self):
        user = self.user
        bundle = self._need_release()
        if not self._is_member():
            raise ShellError("Only members of the organization see the cogs' queues.", 403)
        cogs = sorted(n for n, m in bundle["members"].items() if m["type"] == "cog")
        counts = self.hub.queue.counts()
        rows = []
        for c in cogs:
            n = counts.get(c, {})
            now = self.hub.queue.list(cog=c, status=("working",), limit=1)
            rows.append([f"{self._avatar(c, size='sm')} <a href='{href('/user/' + c, user)}#cog-work'>{esc(c)}</a>",
                         self._task_what(now[0]) if now else "<span class='muted'>free</span>", str(n.get("queued", 0))])
        body = (f"<h1>{icon('inbox')} Cog queues</h1><p class='why'>Each cog works from its own queue, one task at a time, oldest first, like a person's tray. "
                "Asking a cog never waits for it: the answer lands where you asked.</p><section id='queues' data-live>"
                + table(["Cog", "Working on", "Waiting"], rows, "No cogs in this release."))
        recent = self.hub.queue.list(status=("done", "failed", "withdrawn"), limit=15, newest_first=True)
        if recent:
            body += f"<h2>Recently finished</h2>" + table(["Cog", "What", "By", "Outcome"], [
                [esc(t["cog"]), self._task_what(t), esc(t.get("by") or "-"), self._task_state(t)] for t in recent])
        self._page("Cog queues", body + "</section>" + self._live_meta(), crumbs=[("People", href("/people", user)), ("Cog queues", None)])

    def post_queue_withdraw(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        t = self.hub.queue.get(self._f("task"))
        if t is None:
            raise ShellError("There is no such task in a cog's queue.", 404)
        if t.get("by") != self.user and not self._policy_owner():
            raise ShellError("Only the person who asked, or a policy owner, takes a task out of a cog's queue.", 403)
        try:
            self.hub.queue.cancel(t["id"], self.user)
        except ValueError as e:
            raise ShellError(str(e)) from None
        back = self._f("return")
        back = back if back.startswith("/") else href("/user/" + t["cog"], self.user)
        self._redirect(self._once_finish(back + ("&" if "?" in back else "?") + "done=" + quote(f"Withdrawn from {t['cog']}'s queue.")))
