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
from urllib.parse import quote

from ..compile import CompileError, change_state
from ..frames import FrameError
from ..kernel import KernelGuardError
from .forms import ShellError
from .handler import hidden_as, href, log_line, table
from .icons import icon
from .text import esc, when

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
            (f.get("doc_class") == "controlled" and rec["state"] not in ("open", "draft", "returned", "failed") and
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
        own = [c for c in preview["changes"] if (c["rule"].get("source") or {}).get("frame") == name or c["op"] == "remove"]
        body = f"<h1>Check of {esc(rec['number'])} <span class='muted'>draft of {esc(name)}</span></h1>"
        body += ("<div class='card crit' role='alert'><b>The draft cannot be released yet.</b><ul>" + "".join(f"<li>{esc(x['detail'])} <span class='muted'>({esc(x.get('frame'))} line {esc(x.get('line'))})</span></li>" for x in errors) + "</ul></div>") if errors \
            else "<div class='card ok' role='status'>The draft compiles. The policy owners will review the changes below.</div>"
        body += "<h2>What the policy would change</h2>" + table(["Change", "Needs"], [[esc(c["reason"]), "two policy owners" if c["widening"] else "one policy owner"] for c in own], "No rule changes: the text changes but the policy reads the same.")
        if warnings:
            body += "<h2>Warnings</h2><ul>" + "".join(f"<li>{esc(x['detail'])}</li>" for x in warnings[:20]) + "</ul>"
        if notes:
            body += "<h2>Lines the compiler did not read as rules</h2><ul>" + "".join(f"<li>line {esc(n['line'])}: {esc(n['text'])} <span class='muted'>({esc(n['note'])})</span></li>" for n in notes[:30]) + "</ul>"
        body += "<h2>The text, compared with the policy in force</h2>" + diff_html(self.hub.frames.get(name).text, text, name)
        body += (f"<div class='actions'><a class='button ghost' href='{href('/text-edit/' + quote(rec['locator'], safe=':'), user)}'>{icon('edit')}Edit the text</a>"
                 f"<form method='post' action='/frame-propose' class='inline'>{hidden_as(user)}{self._once()}<input type='hidden' name='doc' value='{esc(rec['locator'])}'>"
                 f"<button{' disabled' if errors else ''}>{icon('send')}Propose for release</button></form></div>")
        self._page(f"Check {rec['number']}", body, crumbs=[("Documents", href("/files", user)), (rec["number"], href("/document/" + quote(rec["locator"], safe=":"), user)), ("Check", None)])

    def _propose(self, rec: dict) -> str:
        name = rec["fields"]["frame_name"]
        try:
            proposal = self.hub.compile(overrides={name: self.hub.draft_text(rec)}, drafts={name: rec["locator"]})
        except FrameError as e:
            raise ShellError(f"The draft is not a readable Frame: {e}") from None
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

    def page_proposal(self):
        user = self.user
        self._need_release()
        if not self._is_member():
            raise ShellError("Only members of the organization see policy proposals.", 403)
        p = self._proposal(self.arg)
        ready = self.hub.readiness(p["id"]) if p.get("status") == "open" else []
        owner = user in (p.get("reviewers") or [])
        status = p.get("status")
        body = f"<h1>Policy proposal #{esc(p.get('number'))} <span class='chip {'ok' if status == 'released' else 'warn' if status == 'open' else ''}'>{esc(status)}</span></h1>"
        body += f"<p class='why'>Compiled {esc(when(p.get('created_at')))} against the policy in force; reviewed by the policy owners {esc(', '.join(p.get('reviewers') or []))}.</p>"
        if status == "superseded":
            body += f"<div class='card warn'>A newer proposal replaced this one: <a href='{href('/proposal/' + (p.get('superseded_by') or ''), user)}'>open it</a>.</div>"
        for name, o in (p.get("overrides") or {}).items():
            d = self.hub.kernel.get(o["draft"]) if o.get("draft") else None
            body += (f"<h2>{icon('file')} Draft of {esc(name)}" + (f" <span class='muted'>from <a href='{href('/document/' + quote(d['locator'], safe=':'), user)}'>{esc(d['number'])}</a> by {esc(d['fields'].get('owner'))}</span>" if d else "") + "</h2>"
                     + diff_html(self.hub.frames.get(name).text, o["text"], name))
        errors = [x for x in p["findings"] if x["severity"] == "error"]
        if errors:
            body += "<div class='card crit' role='alert'><b>Errors block the release.</b><ul>" + "".join(f"<li>{esc(x['detail'])}</li>" for x in errors) + "</ul></div>"
        rows = []
        for c in p["changes"]:
            decided = "; ".join(f"{d['by']} {d['verdict']}d" + (f": “{d.get('meaning')}”" if d.get("meaning") else "") for d in c.get("decisions") or [])
            rows.append([esc(c["reason"]), "<span class='chip warn'>widening</span>" if c["widening"] else "", esc(change_state(c)), esc(decided or "-")])
        body += "<h2>Changes</h2>" + table(["Change", "", "State", "Decisions"], rows, "No rule changes.")
        if status == "open" and owner:
            mine = [c for c in p["changes"] if not any(d["by"] == user for d in c.get("decisions") or [])]
            if mine:
                body += (f"<div class='card'><h3 style='margin-top:0'>Your review</h3><form method='post' action='/proposal-review'>{hidden_as(user)}{self._once()}"
                         f"<input type='hidden' name='proposal' value='{esc(p['id'])}'><label class='f'><span>What you are confirming <b>*</b></span>"
                         f"<textarea name='meaning' rows='2' required>{esc(self._val('meaning') if self.route == '/proposal-review' else '')}</textarea></label>"
                         f"<div class='actions'><button name='verdict' value='approve'>{icon('check')}Approve all {len(mine)} change(s)</button>"
                         f"<button class='reject' name='verdict' value='reject' formnovalidate>{icon('x')}Reject all</button></div></form></div>")
            if not ready:
                body += (f"<form method='post' action='/proposal-release' class='card ok'>{hidden_as(user)}{self._once()}<input type='hidden' name='proposal' value='{esc(p['id'])}'>"
                         f"<p>Every change has the approvals it needs. Releasing makes this the policy in force"
                         + (" and writes the drafts into their Frames" if p.get("overrides") else "") + ".</p>"
                         f"<button>{icon('send')}Release</button></form>")
            else:
                body += "<div class='card quiet'><b>Before it can be released:</b><ul>" + "".join(f"<li>{esc(x)}</li>" for x in ready[:12]) + "</ul></div>"
        elif status == "open":
            body += "<p class='why'>The policy owners review and release proposals; you can follow it here.</p>"
        if p.get("release"):
            body += f"<p><a href='{href('/release/' + p['release'], user)}'>The release this became</a></p>"
        self._page(f"Proposal #{p.get('number')}", body, crumbs=[("Policy versions", href("/releases", user)), (f"Proposal #{p.get('number')}", None)])

    def post_proposal_review(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        p = self._proposal(self._f("proposal"))
        self.arg = p["id"]
        verdict = self._f("verdict") or "approve"
        meaning = self._f("meaning").strip()
        if verdict == "approve" and not meaning:
            raise ShellError("Say what you are confirming with your approval.", field="meaning")
        mine = [c["id"] for c in p["changes"] if not any(d["by"] == self.user for d in c.get("decisions") or [])]
        try:
            self.hub.review(p["id"], self.user, decisions={cid: verdict for cid in mine}, meaning=meaning or "rejected")
        except CompileError as e:
            raise ShellError(str(e)) from None
        log_line(f"proposal review proposal={p['id']} by={self.user} verdict={verdict} changes={len(mine)}")
        self._redirect(self._once_finish(href("/proposal/" + p["id"], self.user, done=f"You {verdict}d {len(mine)} change(s) in proposal #{p.get('number')}.")))

    def post_proposal_release(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        p = self._proposal(self._f("proposal"))
        self.arg = p["id"]
        try:
            meta = self.hub.release(p["id"], self.user)
        except CompileError as e:
            raise ShellError(str(e)) from None
        self._frames_stamp = -1.0  # the Frames on disk changed: the shell reads them again
        log_line(f"proposal released proposal={p['id']} by={self.user} release={meta['digest'][:12]}")
        self._redirect(self._once_finish(href("/release/" + meta["digest"], self.user, done=f"Released proposal #{p.get('number')}; it is the policy in force.")))
