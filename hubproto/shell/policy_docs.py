"""Frames and cog settings as documents, in the shell.

- Every Frame has a controlled document in Policy/Frames; "Edit this policy" opens a draft (a working Markdown
  document) the person writes in the hub's text editor.
- "Check the change" compiles the draft in place of the Frame and shows the rule changes and findings; "Propose for
  release" makes it a proposal the policy owners review and release from the proposal page. Releasing writes the
  draft into the Frame and makes it the effective version of the Frame's document.
- Any Markdown or text document is edited with the same text editor.
"""

from __future__ import annotations

import difflib
import re
from urllib.parse import quote

from ..compile import CompileError, change_state
from ..frames import FrameError
from ..kernel import KernelGuardError
from .forms import ShellError
from .handler import hidden_as, href, log_line, table
from .icons import icon
from .text import esc, friendly, when

TEXT_EXTENSIONS = ("md", "txt", "csv")


def text_kind(rec: dict) -> bool:
    fn = (rec.get("fields") or {}).get("filename") or ""
    return fn.rsplit(".", 1)[-1].lower() in TEXT_EXTENSIONS if "." in fn else False


def diff_html(old: str, new: str, name: str) -> str:
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), f"{name} (in force)", f"{name} (draft)", lineterm="", n=2))
    if not lines:
        return "<p class='muted'>No change to the text.</p>"
    out = []
    for ln in lines:
        cls = "add" if ln.startswith("+") and not ln.startswith("+++") else "del" if ln.startswith("-") and not ln.startswith("---") else "hunk" if ln.startswith("@@") else ""
        out.append(f"<span class='{cls}'>{esc(ln)}</span>")
    return "<pre class='diff'>" + "\n".join(out) + "</pre>"


class PolicyDocsMixin:
    # ------------------------------------------------------------ the Frame's document
    def page_frame_doc(self):
        """/frame-doc/<name>: the controlled document carrying a Frame (made on first use)."""
        self._need_release()
        name = self.arg
        try:
            doc = self.hub.frame_document(name, by="hub")
        except FrameError:
            raise ShellError(f"There is no policy Frame {name!r}.", 404) from None
        self._redirect(href("/document/" + quote(doc["locator"], safe=":"), self.user))

    def _policy_doc_buttons(self, rec: dict) -> str:
        """Buttons for a Frame's document and for a Frame draft, on the document page."""
        user, f, loc = self.user, rec["fields"], rec["locator"]
        out = ""
        if f.get("doc_type") == "frame":
            out += (f'<form method="post" action="/frame-draft" class="inline">{hidden_as(user)}{self._once()}<input type="hidden" name="frame" value="{esc(f.get("frame_name"))}">'
                    f'<button>{icon("edit")}Edit this policy</button></form>'
                    f'<a class="button ghost" href="{href("/frame/" + (f.get("frame_name") or ""), user)}">{icon("book")}Read the Frame with line numbers</a>')
        elif f.get("doc_type") == "frame-draft" and f.get("withdrawn"):
            out += f'<span class="chip">withdrawn by {esc(f.get("withdrawn_by"))}</span>'
        elif f.get("doc_type") == "frame-draft" and rec["state"] == "open":
            out += (f'<a class="button" href="{href("/text-edit/" + quote(loc, safe=":"), user)}">{icon("edit")}Edit the text</a>'
                    f'<a class="button ghost" href="{href("/frame-check/" + quote(loc, safe=":"), user)}">{icon("check-circle")}Check the change</a>'
                    f'<form method="post" action="/frame-propose" class="inline">{hidden_as(user)}{self._once()}<input type="hidden" name="doc" value="{esc(loc)}">'
                    f'<button class="ghost">{icon("send")}Propose for release</button></form>')
            if f.get("proposal"):
                out += f'<a class="button ghost" href="{href("/proposal/" + f["proposal"], user)}">{icon("layers")}Proposal #{esc(f.get("proposal_number"))}</a>'
            if user == f.get("owner") or self._policy_owner():
                out += (f'<details class="inline restart"><summary class="button ghost">{icon("x")}Withdraw the draft</summary>'
                        f'<form method="post" action="/frame-withdraw" class="card">{hidden_as(user)}{self._once()}<input type="hidden" name="doc" value="{esc(loc)}">'
                        f'<p class="why">The draft leaves any open proposal and is no longer compiled; the Frame stays as it is in force.</p>'
                        f'<label class="f"><span>Why</span><input name="reason"></label>'
                        f'<div class="actions"><button class="reject">{icon("x")}Withdraw it</button></div></form></details>')
        elif text_kind(rec):
            can, _ = self._may_edit_document(rec)
            if can and f.get("doc_class") not in ("evidence", "external") and (f.get("doc_class") != "controlled" or rec["state"] in ("open", "draft", "returned", "failed")):
                out += f'<a class="button" href="{href("/text-edit/" + quote(loc, safe=":"), user)}">{icon("edit")}Edit the text</a>'
        return out

    def post_frame_draft(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        name = self._f("frame")
        try:
            draft = self.hub.frame_draft(name, by=self.user)
        except (FrameError, KeyError):
            raise ShellError(f"There is no policy Frame {name!r}.", 404) from None
        log_line(f"frame draft frame={name} by={self.user} doc={draft['number']}")
        self._redirect(self._once_finish(href("/text-edit/" + quote(draft["locator"], safe=":"), self.user,
                                              done=f"{draft['number']} is your draft of {name}. Write the change, then check it and propose it for release.")))

    # ------------------------------------------------------------ the text editor
    def page_text_edit(self):
        user = self.user
        self._need_release()
        rec = self.hub.kernel.get(self.arg)
        if rec is None or rec["type"] != "document":
            raise ShellError(f"There is no document '{self.arg}'.", 404)
        self._require_read(rec["folder"])
        f = rec["fields"]
        if not (text_kind(rec) or f.get("doc_type") == "frame-draft"):
            raise ShellError(f"{rec['number']} is not a text document; open it in the editor instead.", 400)
        can, why = self._may_edit_document(rec)
        locked = f.get("checked_out_by")
        blocked = (not can and f"you cannot change it: {why}") or (f.get("doc_class") in ("evidence", "external") and "it is kept as received") or \
            (f.get("doc_class") in ("controlled", "template") and rec["state"] not in ("open", "draft", "returned", "failed") and
             ("it is the policy in force; use Edit this policy to make a draft" if f.get("doc_type") == "frame" else "it is in force; a controlled document changes through a new revision")) or \
            (locked and locked != user and f"{locked} has it checked out")
        text = self.hub.draft_text(rec) if self.hub.dms.versions(rec["locator"]) else ""
        if self.route == "/text-save" and self.err:
            text = self._val("text") or text
        back = href("/document/" + quote(rec["locator"], safe=":"), user)
        draft = f.get("doc_type") == "frame-draft"
        actions = "<button name='next' value='save'>{}Save</button>".format(icon("check"))
        if draft:
            actions += (f"<button class='ghost' name='next' value='check'>{icon('check-circle')}Save and check the change</button>"
                        f"<button class='ghost' name='next' value='propose'>{icon('send')}Save and propose for release</button>")
        note = (f"<p class='why'>A draft of the policy Frame <a href='{href('/frame/' + (f.get('frame_name') or ''), user)}'>{esc(f.get('frame_name'))}</a>, "
                f"based on version {esc(f.get('based_on_version'))}. The sentences are the policy: the compiler turns them into rules, and the policy owners "
                "review the change before it takes effect. Leave the version line alone; the hub raises it.</p>") if draft else ""
        err = self._form_error(["text"]) if self.route == "/text-save" else ""
        if blocked:
            body = f"<h1>{esc(rec['number'])} {esc(f.get('title'))}</h1>{note}<div class='card warn'>You can read it here but not change it: {esc(blocked)}.</div><pre class='cell'>{esc(text)}</pre>"
        else:
            body = (f"<h1>Edit {esc(rec['number'])} <span class='muted'>{esc(f.get('title'))}</span></h1>{note}{err}"
                    f"<form method='post' action='/text-save' class='texteditor'>{hidden_as(user)}{self._once()}<input type='hidden' name='doc' value='{esc(rec['locator'])}'>"
                    f"<label class='vh' for='te'>Text of {esc(rec['number'])}</label><textarea id='te' name='text' spellcheck='true'>{esc(text)}</textarea>"
                    f"<div class='actions'>{actions}<a class='button ghost' href='{back}'>Close</a></div></form>")
        self._page(f"Edit {rec['number']}", body, crumbs=[("Documents", href("/files", user)), (rec["number"], back), ("Edit", None)],
                   head="<style>.texteditor textarea{width:100%;min-height:60vh;font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;padding:10px;border:1px solid var(--ctl);border-radius:6px;background:var(--panel);color:var(--fg);tab-size:4}</style>")

    def post_text_save(self):
        user = self.user
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        rec = self.hub.kernel.require(self._f("doc"))
        self.arg = rec["locator"]
        self._require_read(rec["folder"])
        can, why = self._may_edit_document(rec)
        if not can:
            raise ShellError(f"You cannot change {rec['number']}.", 403)
        text = (self.form.get("text") or "").replace("\r\n", "\n")  # the text as written: not stripped
        if text and not text.endswith("\n"):
            text += "\n"
        if not text.strip():
            raise ShellError("The text is empty; a document keeps at least a line.", field="text")
        current = self.hub.draft_text(rec) if self.hub.dms.versions(rec["locator"]) else None
        if text != current:
            latest = (self.hub.dms.versions(rec["locator"]) or [{}])[-1]
            try:
                self.hub.dms.add_version(rec["locator"], text.encode("utf-8"), by=user, filename=latest.get("filename") or rec["fields"].get("filename") or "",
                                         mime=latest.get("mime") or "text/markdown", note="edited in the hub")
            except KernelGuardError as e:
                raise ShellError(f"{e.detail}.", field="text") from None
        nxt = self._f("next") or "save"
        if nxt == "check":
            return self._redirect(self._once_finish(href("/frame-check/" + quote(rec["locator"], safe=":"), user)))
        if nxt == "propose":
            return self._redirect(self._once_finish(self._propose(self.hub.kernel.get(rec["locator"]))))
        msg = f"Saved {rec['number']}" + ("." if text != current else "; nothing had changed.")
        self._redirect(self._once_finish(href("/document/" + quote(rec["locator"], safe=":"), user, done=msg)))

    # ------------------------------------------------------------ check and propose a draft
    def _draft(self, ref: str) -> dict:
        rec = self.hub.kernel.get(ref)
        if rec is None or rec["fields"].get("doc_type") != "frame-draft":
            raise ShellError(f"'{ref}' is not a draft of a policy Frame.", 404)
        self._require_read(rec["folder"])
        return rec

    def page_frame_check(self):
        user = self.user
        self._need_release()
        rec = self._draft(self.arg)
        name = rec["fields"]["frame_name"]
        text = self.hub.draft_text(rec)
        try:
            preview = self.hub.compile(overrides={name: text}, save=False)
        except FrameError as e:
            body = f"<h1>Check of {esc(rec['number'])}</h1><div class='card crit' role='alert'>The draft is not a readable Frame: {esc(str(e))}</div>"
            return self._page(f"Check {rec['number']}", body)
        errors = [x for x in preview["findings"] if x["severity"] == "error"]
        warnings = [x for x in preview["findings"] if x["severity"] == "warning"]
        notes = [n for n in preview.get("notes") or [] if n.get("frame") == name]
        own = [c for c in preview["changes"] if (c["rule"].get("source") or {}).get("frame") == name or c["op"] == "remove"
               or (c["op"] == "text" and c.get("frame") == name)]
        body = f"<h1>Check of {esc(rec['number'])} <span class='muted'>draft of {esc(name)}</span></h1>"
        body += ("<div class='card crit' role='alert'><b>The draft cannot be released yet.</b><ul>" + "".join(f"<li>{esc(x['detail'])} <span class='muted'>({esc(x.get('frame'))} line {esc(x.get('line'))})</span></li>" for x in errors) + "</ul></div>") if errors \
            else "<div class='card ok' role='status'>The draft compiles. The policy owners will review the changes below.</div>"
        def change_words(c: dict) -> str:
            if c["op"] == "text":
                return (f"Wording of {esc(name)} changed (<a href='#text'>see below</a>)"
                        + ("; a cog follows it, so it changes what the cog judges against" if "a cog follows it" in (c.get("reason") or "") else ""))
            return self._human(c["reason"])
        body += "<h2>What the policy would change</h2>" + table(["Change", "Needs"], [[change_words(c), "two policy owners" if c["widening"] else "one policy owner"] for c in own],
                                                               "No change: the draft reads the same as the policy in force.")
        if warnings:
            changed = {(c.get("rule") or {}).get("key") for c in preview.get("changes") or []}
            mine = [x for x in warnings if x.get("rule_key") in changed or (x.get("frame") and x.get("frame") in (preview.get("overrides") or {}))]
            other = [x for x in warnings if x not in mine]
            if mine:
                body += "<h2>Warnings about this change</h2><ul>" + "".join(f"<li>{self._human(x['detail'])}</li>" for x in mine[:20]) + "</ul>"
            if other:
                body += (f"<details><summary>Other warnings in the policy ({len(other)}), not caused by this change</summary><ul>"
                         + "".join(f"<li>{self._human(x['detail'])}</li>" for x in other[:20]) + "</ul></details>")
        if notes:
            body += "<h2>Lines the compiler did not read as rules</h2><ul>" + "".join(f"<li>line {esc(n['line'])}: {esc(n['text'])} <span class='muted'>({esc(n['note'])})</span></li>" for n in notes[:30]) + "</ul>"
        body += "<h2 id='text'>The text, compared with the policy in force</h2>" + diff_html(self.hub.frames.get(name).text, text, name)
        body += (f"<div class='actions'><a class='button ghost' href='{href('/text-edit/' + quote(rec['locator'], safe=':'), user)}'>{icon('edit')}Edit the text</a>"
                 f"<form method='post' action='/frame-propose' class='inline'>{hidden_as(user)}{self._once()}<input type='hidden' name='doc' value='{esc(rec['locator'])}'>"
                 f"<button{' disabled' if errors else ''}>{icon('send')}Propose for release</button></form></div>")
        self._page(f"Check {rec['number']}", body, crumbs=[("Documents", href("/files", user)), (rec["number"], href("/document/" + quote(rec["locator"], safe=":"), user)), ("Check", None)])

    def _propose(self, rec: dict) -> str:
        name = rec["fields"]["frame_name"]
        try:
            proposal = self.hub.compile(overrides={name: self.hub.draft_text(rec)}, drafts={name: rec["locator"]}, by=self.user)
        except FrameError as e:
            raise ShellError(f"The draft is not a readable Frame: {e}") from None
        except CompileError as e:
            raise ShellError(friendly(str(e)), 403 if "only its author" in str(e) else 400) from None
        log_line(f"frame proposal frame={name} draft={rec['number']} proposal={proposal['id']} by={self.user}")
        return href("/proposal/" + proposal["id"], self.user, done=f"Proposed {rec['number']} as proposal #{proposal['number']}; the policy owners review it here.")

    def post_frame_withdraw(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        try:
            out = self.hub.withdraw_frame_draft(self._f("doc"), self.user, self._f("reason") or "")
        except (ValueError, KeyError) as e:
            raise ShellError(str(e)) from None
        rec = self.hub.kernel.require(self._f("doc"))
        msg = f"Withdrew {out['draft']}" + (f"; proposal #{', #'.join(map(str, out['proposals_withdrawn']))} withdrawn with it" if out["proposals_withdrawn"] else "") + "."
        self._redirect(self._once_finish(href("/document/" + quote(rec["locator"], safe=":"), self.user, done=msg)))

    def post_frame_propose(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        rec = self._draft(self._f("doc"))
        self._redirect(self._once_finish(self._propose(rec)))

    # ------------------------------------------------------------ the proposal: review and release
    def _proposal(self, ref: str) -> dict:
        try:
            return self.hub.proposals.load(ref)
        except CompileError:
            raise ShellError(f"There is no proposal '{ref}'.", 404) from None

    # ------------------------------------------------------------ proposals: in sentences, decided per change
    def _change_rows(self, p: dict) -> list[dict]:
        """One row per change as people read it: a rule's sentence, with 'was …' when it replaces another (the paired
        removal is folded into it), never ids or compiler output."""
        from ..settings import sentence

        procs = (self.bundle or {}).get("processes") or {}
        rows = []
        for c in p.get("changes") or []:
            if c["op"] == "remove" and c.get("changed_to"):
                continue  # shown with the change that replaces it
            if c["op"] == "text":
                frame = c.get("frame") or (c.get("rule") or {}).get("frame") or ""
                follows = "; a cog follows it, so it changes what the cog judges against" if "a cog follows it" in (c.get("reason") or "") else ""
                rows.append({"change": c, "verb": "Wording", "text": f"Wording of {frame} changed" + follows, "was": "", "frame": frame})
                continue
            rule = c.get("rule") or {}
            try:
                text = sentence(rule, procs)
            except Exception:
                text = (rule.get("source") or {}).get("text") or c.get("reason") or ""
            was = ""
            if c.get("changes_from"):
                try:
                    was = sentence(c["changes_from"], procs)
                except Exception:
                    was = ((c["changes_from"].get("source") or {}).get("text")) or ""
            verb = "Changed" if was else ("Added" if c["op"] == "add" else "Removed")
            rows.append({"change": c, "verb": verb, "text": text, "was": was})
        return rows

    def _proposal_link(self, pid: str | None) -> str:
        if not pid:
            return ""
        try:
            q = self.hub.proposals.load(pid)
        except CompileError:
            return ""
        return f"<a href='{href('/proposal/' + q['id'], self.user)}'>#{esc(q.get('number'))}</a>"

    def _blocking_reasons(self, p: dict) -> list[str]:
        """Why a proposal cannot be released yet, in words (no ids)."""
        out = []
        cur = self.hub.releases.current()
        if p.get("status") == "open" and (cur["digest"] if cur else None) != p.get("base"):
            out.append("The policy in force changed after this proposal was made, so it would undo that change. Propose the drafts again "
                       "(from each draft's page) so they are compared with the policy in force.")
        for f in p.get("findings") or []:
            if f.get("severity") == "error":
                out.append(self._human(f.get("detail")) + (f" ({esc(f.get('frame'))} line {esc(f.get('line'))})" if f.get("frame") else ""))
        for r in self._change_rows(p):
            c = r["change"]
            if change_state(c) == "pending":
                have = len({d["by"] for d in c.get("decisions") or [] if d["verdict"] == "approve"})
                need = 2 if c.get("widening") else 1
                what = esc(r["text"]) if r["verb"] == "Wording" else f"{esc(r['verb'])}: {esc(r['text'])}"
                out.append(f"{what}: needs {need} approval{'s' if need > 1 else ''} (has {have})")
        return out

    def page_proposal(self):
        user = self.user
        self._need_release()
        if not self._is_member():
            raise ShellError("Only members of the organization see policy proposals.", 403)
        p = self._proposal(self.arg)
        owner = user in (p.get("reviewers") or [])
        status = p.get("status")
        shown = {"stale": "out of date"}.get(status, status)
        body = f"<h1>Policy proposal #{esc(p.get('number'))} <span class='chip {'ok' if status == 'released' else 'warn' if status in ('open', 'stale') else ''}'>{esc(shown)}</span></h1>"
        made = "Made from Settings" if p.get("source") == "settings" else "Compiled from the policy drafts"
        body += (f"<p class='why'>{made} {esc(when(p.get('created_at')))}" + (f" by {esc(p.get('proposed_by'))}" if p.get("proposed_by") else "")
                 + f"; reviewed by the policy owners {esc(', '.join(p.get('reviewers') or []))}.</p>")
        if p.get("reason"):
            body += f"<p><b>Why:</b> {esc(p['reason'])}</p>"
        if status == "superseded":
            link = self._proposal_link(p.get("superseded_by"))
            try:
                newer = self.hub.proposals.load(p.get("superseded_by") or "")
            except CompileError:
                newer = {}
            if newer.get("status") == "released":
                body += (f"<div class='card warn'>Proposal {link} was released first, so the policy in force changed after this proposal was made. "
                         "Propose your change again so it is compared with the policy in force.</div>")
            else:
                body += f"<div class='card warn'>A newer proposal replaced this one{(': ' + link) if link else ''}. Decisions on the same changes carried over to it.</div>"
        if status == "stale":
            drafts = [self.hub.kernel.get(o["draft"]) for o in (p.get("overrides") or {}).values() if o.get("draft")]
            drafts = [d for d in drafts if d]
            mine = [d for d in drafts if user == d["fields"].get("owner") or self._policy_owner()]
            body += (f"<div class='card warn' role='status'><b>Out of date.</b> {self._human(p.get('stale_reason') or 'a release changed the same policy')}. "
                     "Nobody can approve or release it as it stands.")
            for d in mine:
                loc = quote(d["locator"], safe=":")
                body += (f"<p>Redo {esc(d['number'])} on the policy in force: <a class='button ghost' href='{href('/text-edit/' + loc, user)}'>{icon('edit')}Edit the draft</a> "
                         f"then Check and Propose again, or withdraw it:</p>"
                         f"<form method='post' action='/frame-withdraw' class='inline'>{hidden_as(user)}{self._once()}<input type='hidden' name='doc' value='{esc(d['locator'])}'>"
                         f"<input name='reason' placeholder='why (optional)' aria-label='Why you withdraw it'> <button class='secondary'>{icon('x')}Withdraw {esc(d['number'])}</button></form>")
            if not mine and drafts:
                body += f"<p class='why'>Its author ({esc(', '.join(sorted({str(d['fields'].get('owner')) for d in drafts})))}) redoes or withdraws it.</p>"
            body += "</div>"
        if status == "withdrawn":
            w = p.get("withdrawn") or {}
            body += f"<div class='card warn'>Withdrawn by {esc(w.get('by') or '?')}{(': ' + esc(w.get('reason'))) if w.get('reason') else ''}.</div>"
        for name, o in (p.get("overrides") or {}).items():
            d = self.hub.kernel.get(o["draft"]) if o.get("draft") else None
            authors = []
            if d:
                for v in self.hub.dms.versions(d["locator"]):
                    if v.get("by") and v["by"] not in authors:
                        authors.append(v["by"])
            byline = ""
            if d:
                first, rest = (authors[:1] or [d["fields"].get("owner")]), authors[1:]
                byline = (f" <span class='muted'>from <a href='{href('/document/' + quote(d['locator'], safe=':'), user)}'>{esc(d['number'])}</a>, "
                          f"written by {esc(first[0])}" + (f", changed by {esc(', '.join(rest))}" if rest else "") + "</span>")
            try:
                base_text = self.hub.frames.get(name).text
            except Exception:
                base_text = ""
            body += f"<h2 id='draft-{esc(name)}'>{icon('file')} Draft of {esc(name)}{byline}</h2>" + diff_html(base_text, o["text"], name)
        blocking = self._blocking_reasons(p) if status == "open" else []
        errors = [x for x in p.get("findings") or [] if x["severity"] == "error"]
        if errors:
            body += "<div class='card crit' role='alert'><b>Errors block the release.</b><ul>" + "".join(
                f"<li>{self._human(x['detail'])}</li>" for x in errors) + "</ul></div>"
        changed_keys = {(c.get("rule") or {}).get("key") for c in p.get("changes") or []}
        warnings = [x for x in p.get("findings") or [] if x["severity"] == "warning"]
        mine_w = [x for x in warnings if x.get("rule_key") and x.get("rule_key") in changed_keys]
        other_w = [x for x in warnings if x not in mine_w]
        if mine_w:
            body += "<div class='card warn'><b>Warnings about these changes</b><ul>" + "".join(f"<li>{self._human(x['detail'])}</li>" for x in mine_w) + "</ul></div>"
        if other_w:
            body += (f"<details><summary>Other warnings in the policy ({len(other_w)}), not caused by this proposal</summary><ul>"
                     + "".join(f"<li>{self._human(x['detail'])}</li>" for x in other_w) + "</ul></details>")
        rows = self._change_rows(p)
        can_decide = status == "open" and owner
        trs = ""
        for r in rows:
            c = r["change"]
            decided = "; ".join(f"{esc(d['by'])} {esc(d['verdict'])}d" + (f": “{esc(d.get('meaning'))}”" if d.get("meaning") else "") for d in c.get("decisions") or [])
            mine = can_decide and not any(d["by"] == user for d in c.get("decisions") or [])
            pick = ""
            if mine:
                cid = esc(c["id"])
                pick = (f"<fieldset class='decide'><legend class='vh'>Your decision on this change</legend>"
                        f"<label><input type='radio' name='d_{cid}' value='approve'> Approve</label> "
                        f"<label><input type='radio' name='d_{cid}' value='reject'> Reject</label> "
                        f"<label><input type='radio' name='d_{cid}' value='' checked> Not now</label></fieldset>")
            state = change_state(c)
            see = ""
            if r.get("frame") and r["frame"] in (p.get("overrides") or {}):
                see = f" <a href='#draft-{esc(r['frame'])}'>see the wording</a>"
            label = "" if r["verb"] == "Wording" else f"<b>{esc(r['verb'])}</b>: "
            trs += (f"<tr><td>{label}{esc(r['text'])}{see}" + (f"<div class='muted'>was: {esc(r['was'])}</div>" if r["was"] else "")
                    + f"</td><td>{'<span class=\'chip warn\'>widening: two owners</span>' if c.get('widening') else ''}</td>"
                    f"<td><span class='chip {'ok' if state == 'approved' else 'crit' if state == 'rejected' else ''}'>{esc(state)}</span></td>"
                    f"<td>{decided or '-'}</td>" + (f"<td>{pick}</td>" if can_decide else "") + "</tr>")
        head = "<tr><th>Change</th><th></th><th>State</th><th>Decisions</th>" + ("<th>Yours</th>" if can_decide else "") + "</tr>"
        changes_table = f"<div class='tablewrap'><table><thead>{head}</thead><tbody>{trs}</tbody></table></div>" if rows else "<p class='muted'>No rule changes.</p>"
        undecided = [r for r in rows if can_decide and not any(d["by"] == user for d in r["change"].get("decisions") or [])]
        if undecided:
            err = self._form_error(["meaning"]) if self.route == "/proposal-review" else ""
            body += (f"<h2>Changes</h2><form method='post' action='/proposal-review' class='card'>{hidden_as(user)}{self._once()}"
                     f"<input type='hidden' name='proposal' value='{esc(p['id'])}'>{err}{changes_table}"
                     f"<label class='f'><span>What you are confirming <b>*</b> <i>(kept with each decision)</i></span>"
                     f"<textarea name='meaning' rows='2'>{esc(self._val('meaning') if self.route == '/proposal-review' else '')}</textarea></label>"
                     f"<div class='actions'><button name='verdict' value='each'>{icon('check')}Record my decisions</button>"
                     f"<button class='secondary' name='verdict' value='approve'>Approve all {len(undecided)} remaining</button>"
                     f"<span class='why'>A rejected change is left out when the rest is released; it can be proposed again.</span></div></form>")
        else:
            body += "<h2>Changes</h2>" + changes_table
        if status == "open" and owner:
            if not blocking:
                body += (f"<form method='post' action='/proposal-release' class='card ok'>{hidden_as(user)}{self._once()}<input type='hidden' name='proposal' value='{esc(p['id'])}'>"
                         f"<p>Every change has the decisions it needs. Releasing makes the approved changes the policy in force (rejected ones are left out)"
                         + (" and writes the drafts into their Frames" if p.get("overrides") else "") + ".</p>"
                         f"<button>{icon('send')}Release</button></form>")
            else:
                body += "<div class='card quiet'><b>Before it can be released:</b><ul>" + "".join(f"<li>{x}</li>" for x in blocking[:15]) + "</ul></div>"
        elif status == "open":
            body += "<p class='why'>The policy owners review and release proposals; you can follow it here.</p>"
        if p.get("release"):
            body += f"<p><a href='{href('/release/' + p['release'], user)}'>The release this became</a></p>"
        self._page(f"Proposal #{p.get('number')}", body, crumbs=[("Policy versions", href("/releases", user)), ("Proposals", href("/proposals", user)),
                                                               (f"Proposal #{p.get('number')}", None)])

    def post_proposal_review(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        p = self._proposal(self._f("proposal"))
        self.arg = p["id"]
        verdict = self._f("verdict") or "approve"
        meaning = self._f("meaning").strip()
        mine = [c["id"] for c in p["changes"] if not any(d["by"] == self.user for d in c.get("decisions") or [])]
        if verdict == "each":
            decisions = {cid: self._f("d_" + cid) for cid in mine if self._f("d_" + cid) in ("approve", "reject")}
            if not decisions:
                raise ShellError("Choose Approve or Reject on at least one change.", field="meaning")
        elif verdict in ("approve", "reject"):
            decisions = {cid: verdict for cid in mine}
        else:
            raise ShellError("Choose a decision.", field="meaning")
        # A paired change (one rule replacing another) is decided as one: the removal follows its addition.
        by_id = {c["id"]: c for c in p["changes"]}
        for cid, v in list(decisions.items()):
            c = by_id.get(cid) or {}
            if c.get("changes_from"):
                for other in p["changes"]:
                    if other["op"] == "remove" and other.get("changed_to") == c.get("rule") and other["id"] in mine:
                        decisions.setdefault(other["id"], v)
        if any(v == "approve" for v in decisions.values()) and not meaning:
            raise ShellError("Say what you are confirming with your approval.", field="meaning")
        try:
            self.hub.review(p["id"], self.user, decisions=decisions, meaning=meaning or "rejected")
        except CompileError as e:
            raise ShellError(friendly(str(e))) from None
        n_ok = sum(1 for v in decisions.values() if v == "approve")
        n_no = len(decisions) - n_ok
        log_line(f"proposal review proposal={p['id']} by={self.user} approve={n_ok} reject={n_no}")
        said = ", ".join(x for x in (f"approved {n_ok}" if n_ok else "", f"rejected {n_no}" if n_no else "") if x)
        self._redirect(self._once_finish(href("/proposal/" + p["id"], self.user, done=f"You {said} change(s) in proposal #{p.get('number')}.")))

    # ------------------------------------------------------------ all proposals
    def _awaiting_me(self) -> list[dict]:
        """Open proposals with a change still waiting for this policy owner's decision."""
        out = []
        for q in self.hub.proposals.list():
            if q.get("status") != "open" or self.user not in (q.get("reviewers") or []):
                continue
            todo = [c for c in q.get("changes") or [] if change_state(c) == "pending" and not any(d["by"] == self.user for d in c.get("decisions") or [])]
            if todo:
                out.append({"proposal": q, "todo": todo})
        return sorted(out, key=lambda x: x["proposal"].get("created_at") or "", reverse=True)

    def page_proposals(self):
        user = self.user
        self._need_release()
        if not self._is_member():
            raise ShellError("Only members of the organization see policy proposals.", 403)
        props = sorted(self.hub.proposals.list(), key=lambda q: int(q.get("number") or 0), reverse=True)
        mine = {x["proposal"]["id"] for x in self._awaiting_me()}
        body = f"<h1>{icon('layers')} Policy proposals</h1><p class='why'>Changes to the policy wait here for the policy owners; a change that lets someone do more needs two of them.</p>"
        sections = (("Open", [q for q in props if q.get("status") == "open"]), ("Out of date: redo or withdraw", [q for q in props if q.get("status") == "stale"]),
                    ("Released", [q for q in props if q.get("status") == "released"][:15]),
                    ("Replaced or withdrawn", [q for q in props if q.get("status") in ("superseded", "withdrawn")][:15]))
        for title, qs in sections:
            rows = []
            for q in qs:
                rows.append([f"<a href='{href('/proposal/' + q['id'], user)}'>#{esc(q.get('number'))}</a>"
                             + (" <span class='chip warn'>your decision</span>" if q["id"] in mine else ""),
                             esc(when(q.get("created_at"))), esc(q.get("proposed_by") or ", ".join(sorted((q.get("overrides") or {}).keys())) or "compile"),
                             str(len([c for c in q.get("changes") or [] if not (c["op"] == "remove" and c.get("changed_to"))])),
                             str(sum(1 for c in q.get("changes") or [] if c.get("widening")))])
            body += f"<h2>{esc(title)} <span class='muted'>{len(qs)}</span></h2>" + table(["Proposal", "Made", "From", "Changes", "Widenings"], rows, "None.")
        self._page("Policy proposals", body, crumbs=[("Policy versions", href("/releases", user)), ("Proposals", None)])

    def post_proposal_release(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        p = self._proposal(self._f("proposal"))
        self.arg = p["id"]
        blocking = self._blocking_reasons(p)
        if blocking:
            raise ShellError("Proposal #" + str(p.get("number")) + " cannot be released yet: " + " ".join(re.sub(r"<[^>]+>", "", b) for b in blocking[:3]))
        try:
            meta = self.hub.release(p["id"], self.user)
        except CompileError as e:
            raise ShellError(friendly(str(e))) from None
        self._frames_stamp = -1.0  # the Frames on disk changed: the shell reads them again
        log_line(f"proposal released proposal={p['id']} by={self.user} release={meta['digest'][:12]}")
        self._redirect(self._once_finish(href("/release/" + meta["digest"], self.user, done=f"Released proposal #{p.get('number')}; it is the policy in force.")))
