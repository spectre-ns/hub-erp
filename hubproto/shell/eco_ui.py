"""Change orders in the Quality app: prepared first (the SOP and policy revisions they carry, the change requests they
implement), then sent for approval once nothing blocks them (hubproto.eco).

A change order's page shows what it carries and where each revision stands, the Frame those revisions compile into
(staged: the rule changes as sentences, wording changes, compile errors), and what still blocks its approval. An SOP or
policy in force says it is compiled into the Frame and is changed through a change order; a carried draft says which
change order carries it and that it comes into force only when that order is released."""

from __future__ import annotations

from urllib.parse import quote

from .. import authz, eco, sopdocs, wopi
from ..runner import RunError
from .forms import ShellError
from .handler import hidden_as, href, log_line, table
from .icons import icon
from .text import esc, state_name

CHANGES = "Quality/Changes"
OPEN = ("open", "draft", "returned", "failed")


def cr_approved(cr: dict) -> bool:
    return cr["state"] not in OPEN and not cr["state"].startswith("awaiting-approve") and cr["state"] not in ("rejected", "cancelled")


class EcoMixin:
    # ------------------------------------------------------------ helpers
    def _co_href(self, rec: dict) -> str:
        return href("/record/" + quote(rec["locator"], safe=":"), self.user)

    def _doc_page(self, rec: dict) -> str:
        return href("/document/" + quote(rec["locator"], safe=":"), self.user)

    def _doc_controller(self) -> bool:
        return "document-controller" in authz.roles_of(self.bundle or {}, self.user)

    def _co_mine(self, co: dict) -> bool:
        return self.user in (co["fields"].get("requested_by"), co.get("created_by")) or self._doc_controller()

    def _co_preparing(self, co: dict) -> bool:
        run = self.hub.runner.run_for_record(co["locator"])
        return co["state"] in OPEN and not (run and run["status"] in ("waiting", "submitted", "running"))

    def _co_get(self, ref: str) -> dict:
        rec = self.hub.kernel.get(ref) if ref else None
        if rec is None or rec["type"] != "change-order" or not self._can_read(rec["folder"])["allowed"]:
            raise ShellError("There is no such change order you may read.", 404)
        return rec

    def _draft_words(self, d: dict, co: dict) -> str:
        """Where a carried revision stands, in words."""
        f = d["fields"]
        if d["state"] == "effective":
            return "<span class='chip ok'>in force</span>"
        if d["state"] == "approved" and f.get("held_for") == co["number"]:
            return "<span class='chip info'>approved, held for this change order</span>"
        run = self.hub.runner.run_for_record(d["locator"])
        if (run and run["status"] in ("waiting", "submitted", "running")) or d["state"].startswith("awaiting"):
            who = ", ".join(((run or {}).get("pending") or {}).get("assignees") or [])
            return "<span class='chip warn'>waiting for approval</span>" + (f" <span class='why'>by {esc(who)}</span>" if who else "")
        if d["state"] in ("draft", "open", "returned"):
            return "<span class='chip'>being written</span>"
        return f"<span class='chip'>{esc(state_name(d['state']).lower())}</span>"

    def _change_requests(self) -> list[dict]:
        return [r for r in self.hub.kernel.query(type_="change-request") if self._can_read(r["folder"])["allowed"]]

    # ------------------------------------------------------------ the list and the new form
    def page_change_orders(self):
        user = self.user
        self._need_release()
        if not self._is_member():
            raise ShellError("Only members of the organization see change orders.", 403)
        self._require_read(CHANGES)
        cos = sorted(self.hub.kernel.query(type_="change-order"), key=lambda r: r.get("created_at") or "", reverse=True)
        rows = []
        for co in cos:
            if not self._can_read(co["folder"])["allowed"]:
                continue
            f = co["fields"]
            carried = eco.carried(self.hub, co)
            rows.append([f"<a href='{self._co_href(co)}'>{esc(co['number'])}</a>", esc(f.get("title") or ""), self._state_chip(co["state"]),
                         esc(", ".join([c["name"] for c in carried] + [f"{t} form" for t in sorted(f.get("form_changes") or {})]) or "-"),
                         esc(", ".join(f.get("implements") or []) or "-"),
                         (f"<span class='chip warn'>{len(f['staged_errors'])} error(s)</span>" if f.get("staged_errors") else
                          (f"{int(f.get('staged_changes') or 0)} change(s)" if f.get("staged_proposal") else "-"))])
        body = (f"<h1>{icon('layers', cls='lg')} Change orders</h1>"
                "<p class='why'>A change request asks for a change and says why. A change order carries it out: a draft revision of every SOP and "
                "policy it changes, each approved through document control, and the Frame those revisions compile into. The revisions and the "
                "Frame come into force together when the change order is released.</p>")
        body += table(["Change order", "Title", "State", "Carries", "Implements", "Staged Frame"], rows, "No change orders yet.")
        body += self._co_new_form()
        self._page("Change orders", body, crumbs=[("Quality", href("/app/quality", user)), ("Change orders", None)])

    def _co_new_form(self) -> str:
        user = self.user
        d = authz.check(self.bundle, user, "create", "change-order", CHANGES)
        if not d["allowed"]:
            return f"<p class='why'>You cannot prepare a change order: {self._why_html(d, 'create', 'change-order', CHANGES)}</p>"
        crs = [r for r in self._change_requests() if cr_approved(r)]
        checks = "".join(f"<label class='check'><input type='checkbox' name='implements' value='{esc(r['number'])}'> {esc(r['number'])} "
                         f"{esc((r['fields'].get('title') or '')[:80])}</label>" for r in crs)
        return (f"<h2 id='new'>{icon('plus')}New change order</h2><form method='post' action='/co-new' class='card'>{hidden_as(user)}{self._once()}"
                "<div class='fields'><label class='f'><span>Title <b>*</b></span><input name='title' required></label>"
                "<label class='f'><span>What it changes and why</span><textarea name='description'></textarea></label></div>"
                + (f"<fieldset class='checks'><legend>Implements these approved change requests</legend>{checks}</fieldset>" if checks
                   else "<p class='why'>No approved change request to implement yet; you can add them later.</p>")
                + "<p class='why'>It is prepared first: add the SOPs and policies it changes, then send it for approval.</p>"
                f"<div class='actions'><button>{icon('plus')}Prepare the change order</button></div></form>")

    def post_co_new(self):
        user = self.user
        bundle = self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        d = authz.check(bundle, user, "create", "change-order", CHANGES)
        if not d["allowed"]:
            raise ShellError(f"You cannot prepare a change order: {d['why']}", 403)
        title = self._f("title")
        if not title:
            raise ShellError("Give the change order a title.", field="title")
        known = {r["number"]: r for r in self._change_requests()}
        implements = [n for n in self._multi("implements") if n]
        bad = [n for n in implements if n not in known or not cr_approved(known[n])]
        if bad:
            raise ShellError(f"Only approved change requests you may read can be implemented: {', '.join(bad)}.")
        rec = self.hub.kernel.create("change-order", CHANGES, {"title": title, "description": self._f("description"), "implements": implements,
                                                              "requested_by": user}, by=user)
        log_line(f"change order prepared {rec['number']} by={user}")
        self._redirect(self._once_finish(self._co_href(rec) + "&done=" + quote(f"{rec['number']} is being prepared: add the SOPs and policies it changes.")))

    # ------------------------------------------------------------ the change order page
    def _eco_panel(self, co: dict) -> str:
        """The change order's own section on its record page: what it carries, what it implements, the staged Frame,
        what blocks it, and Send for approval."""
        user = self.user
        st = eco.status(self.hub, co["locator"])
        co = st["co"]
        f = co["fields"]
        mine, preparing = self._co_mine(co), self._co_preparing(co)
        out = "<section class='card eco' id='change-order'>"
        # Carries
        rows = []
        for c in st["carried"]:
            dft = c["draft"]
            versions = self.hub.dms.versions(dft["locator"]) if hasattr(self.hub.dms, "versions") else []
            links = []
            if wopi.is_editable(dft) and dft["state"] in ("draft", "open", "returned"):
                links.append(f"<a href='{href('/edit/' + quote(dft['locator'], safe=':'), user)}'>{icon('edit')}Edit</a>")
            if len(versions) > 1:
                links.append(f"<a href='{href('/compare/' + quote(dft['locator'], safe=':'), user, a=1, b=len(versions))}'>compare with the version in force</a>")
            if preparing and mine and dft["state"] in ("draft", "open", "returned"):
                links.append(f"<form method='post' action='/co-remove-sop' class='inline'>{hidden_as(user)}{self._once()}"
                             f"<input type='hidden' name='co' value='{esc(co['locator'])}'><input type='hidden' name='sop' value='{esc(c['name'])}'>"
                             f"<button class='ghost'>{icon('x')}Remove</button></form>")
            kind = {"policy": "Policy", "sop": "SOP", "wi": "Work instruction"}.get(dft["fields"].get("doc_type"), "Document")
            rows.append([f"{esc(c['name'])} <span class='why'>{esc(kind)}</span>",
                         f"<a href='{self._doc_page(dft)}'>{esc(dft['number'])}</a> rev {esc(dft['fields'].get('revision') or '')}"
                         + (f" <span class='why'>revises {esc(dft['fields'].get('revises'))}</span>" if dft["fields"].get("revises") else ""),
                         self._draft_words(dft, co), " ".join(links)])
        out += f"<h2>{icon('file-text')}Carries</h2>" + table(["SOP or policy", "Revision", "Where it stands", ""], rows,
                                                               "Nothing yet: add the SOPs and policies this change order changes.")
        if preparing and mine:
            carried = {c["name"] for c in st["carried"]}
            opts = []
            for d in self.hub.kernel.query(type_="document"):
                fd = d["fields"]
                name = fd.get("sop_name")
                if (d["state"] == "effective" and str(fd.get("lineage", "")).startswith(sopdocs.LINEAGE) and name not in carried
                        and self._can_read(d["folder"])["allowed"] and not eco.may_change_sop(self.bundle or {}, d, user)):
                    opts.append((name, f"{name} ({d['number']}, rev {fd.get('revision') or ''})"))
            if opts:
                sel = "".join(f"<option value='{esc(v)}'>{esc(t)}</option>" for v, t in sorted(opts))
                out += (f"<form method='post' action='/co-add-sop' class='sform row'>{hidden_as(user)}{self._once()}<input type='hidden' name='co' value='{esc(co['locator'])}'>"
                        f"<label class='f'><span>Add an SOP or policy</span><select name='sop'>{sel}</select></label>"
                        f"<button class='secondary'>{icon('plus')}Start its revision here</button></form>")
        # Implements
        crs = {r["number"]: r for r in self._change_requests()}
        imp_rows = []
        for n in f.get("implements") or []:
            r = crs.get(n)
            rm = (f"<form method='post' action='/co-implements' class='inline'>{hidden_as(user)}{self._once()}<input type='hidden' name='co' value='{esc(co['locator'])}'>"
                  f"<input type='hidden' name='cr' value='{esc(n)}'><input type='hidden' name='action' value='remove'><button class='ghost'>{icon('x')}Remove</button></form>"
                  if preparing and mine else "")
            imp_rows.append([self._record_link(r["locator"]) if r else esc(n), self._state_chip(r["state"]) if r else "<span class='muted'>not found</span>", rm])
        out += f"<h2>{icon('workflow')}Implements</h2>" + table(["Change request", "State", ""], imp_rows, "No change request named.")
        if preparing and mine:
            cands = [r for r in crs.values() if cr_approved(r) and r["number"] not in (f.get("implements") or [])]
            if cands:
                sel = "".join(f"<option value='{esc(r['number'])}'>{esc(r['number'])} {esc((r['fields'].get('title') or '')[:60])}</option>" for r in cands)
                out += (f"<form method='post' action='/co-implements' class='sform row'>{hidden_as(user)}{self._once()}<input type='hidden' name='co' value='{esc(co['locator'])}'>"
                        f"<input type='hidden' name='action' value='add'><label class='f'><span>Add an approved change request</span><select name='cr'>{sel}</select></label>"
                        f"<button class='secondary'>{icon('plus')}Add</button></form>")
        # Forms
        out += self._forms_co_section(co, mine, preparing)
        # Staged Frame
        out += self._staged_html(co, st)
        # What blocks it, and Send for approval
        run = self.hub.runner.run_for_record(co["locator"])
        if co["state"] == "released":
            out += f"<p class='status'>Released: its revisions and Frame are in force (policy {esc((f.get('released_release') or '')[:12])}).</p>"
        elif run and run["status"] in ("waiting", "submitted", "running"):
            out += "<p class='why'>Sent for approval: it is in the change-order process.</p>"
        else:
            blocked = eco.check_approval(self.hub, co["locator"])
            if blocked:
                out += ("<h3>Before it can be sent for approval</h3><ul class='blockers'>" + "".join(f"<li>{esc(b)}</li>" for b in blocked) + "</ul>")
            elif mine:
                out += (f"<form method='post' action='/co-send' class='inline'>{hidden_as(user)}{self._once()}<input type='hidden' name='co' value='{esc(co['locator'])}'>"
                        f"<button>{icon('send')}Send for approval</button></form> <span class='why'>Every revision it carries is approved and its Frame is clean.</span>")
        return out + "</section>"

    def _staged_html(self, co: dict, st: dict) -> str:
        user = self.user
        f = co["fields"]
        out = f"<h2>{icon('layers')}Staged Frame</h2>" + self._impact_html([c["name"] for c in st.get("carried") or [] if c.get("name")])
        if f.get("staged_errors"):
            out += ("<div class='card crit' role='alert'><p><b>The staged Frame does not compile.</b> Fix these in the revisions:</p><ul>"
                    + "".join(f"<li>{esc(e)}</li>" for e in f["staged_errors"]) + "</ul></div>")
        p = st.get("proposal")
        current = p is not None and not [r for r in st.get("reasons") or [] if "compile it again" in r or "not made of the approved" in r]
        out += self._automation_line(co)
        if not current:
            pv = eco.preview(self.hub, co["locator"]) if eco.carried(self.hub, co) and self._co_preparing(co) else None
            if pv is None:
                return out + ("<p class='muted'>Nothing compiled yet: the Frame is compiled when a revision this change order carries is approved.</p>"
                              if not f.get("staged_errors") else "")
            if pv.get("errors"):
                out += ("<div class='card warn' role='status'><p><b>The drafts do not compile as they stand.</b> Fix these before they go for approval:</p><ul>"
                        + "".join(f"<li>{esc(e)}</li>" for e in pv["errors"]) + "</ul></div>")
            rows = [f"<li><b>{esc(r['verb'])}:</b> {esc(r['text'])}" + (f" <span class='why'>was: {esc(r['was'])}</span>" if r.get("was") else "") + "</li>"
                    for r in self._change_rows(pv)]
            return out + ("<p class='why'>Preview from the drafts as they stand. The Frame that counts is compiled when each revision is approved "
                          "(the compile-frame automation), and the change order goes for approval once it is ready.</p>"
                          + ("<ul class='staged'>" + "".join(rows) + "</ul>" if rows else "<p class='muted'>No rule changes in the drafts.</p>"))
        from ..compile import change_state

        items = []
        for row in self._change_rows(p):
            c = row["change"]
            chips = ""
            if c.get("widening") and c.get("op") != "text":
                have = sorted({d["by"] for d in c.get("decisions") or [] if d["verdict"] == "approve"})
                chips = ("<span class='chip ok'>both policy owners approved</span>" if change_state(c) == "approved" else
                         "<span class='chip warn'>widening: needs both policy owners</span>" + (f" <span class='why'>approved by {esc(', '.join(have))}</span>" if have else ""))
            elif change_state(c) == "rejected":
                chips = "<span class='chip crit'>rejected</span>"
            items.append(f"<li><b>{esc(row['verb'])}:</b> {esc(row['text'])}" + (f" <span class='why'>was: {esc(row['was'])}</span>" if row.get("was") else "")
                         + (f" {chips}" if chips else "") + "</li>")
        link = f"<a href='{href('/proposal/' + p['id'], user)}'>proposal #{esc(p.get('number'))}</a>"
        out += (f"<p class='why'>What the policy becomes when this change order is released ({link}"
                + ("; the policy owners approve each widening there" if any(c.get("widening") and c.get("op") != "text" for c in p["changes"]) else "")
                + ").</p>" + ("<ul class='staged'>" + "".join(items) + "</ul>" if items else "<p class='muted'>No rule changes.</p>"))
        return out

    # ------------------------------------------------------------ actions
    def post_co_add_sop(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        co = self._co_get(self._f("co"))
        try:
            draft = eco.add_sop(self.hub, co["locator"], self._f("sop"), by=self.user)
        except ValueError as e:
            raise ShellError(str(e), 403) from None
        log_line(f"change order {co['number']} carries {draft['number']} ({self._f('sop')}) by={self.user}")
        self._redirect(self._once_finish(self._co_href(co) + "&done=" + quote(f"{draft['number']} is the draft revision of {self._f('sop')}: edit it, then send it for approval.")))

    def post_co_remove_sop(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        co = self._co_get(self._f("co"))
        if not self._co_mine(co):
            raise ShellError("Only its requester or a document controller changes what a change order carries.", 403)
        try:
            eco.remove_sop(self.hub, co["locator"], self._f("sop"), by=self.user)
        except ValueError as e:
            raise ShellError(str(e)) from None
        self._redirect(self._once_finish(self._co_href(co) + "&done=" + quote(f"{self._f('sop')} is no longer carried; its draft is withdrawn.")))

    def post_co_implements(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        co = self._co_get(self._f("co"))
        if not (self._co_mine(co) and self._co_preparing(co)):
            raise ShellError("Only its requester or a document controller changes a change order, while it is being prepared.", 403)
        n, action = self._f("cr"), self._f("action")
        current = list(co["fields"].get("implements") or [])
        if action == "add":
            cr = next((r for r in self._change_requests() if r["number"] == n), None)
            if cr is None or not cr_approved(cr):
                raise ShellError(f"{n} is not an approved change request you may read.")
            if n not in current:
                current.append(n)
        elif action == "remove":
            current = [x for x in current if x != n]
        else:
            raise ShellError("Say add or remove.")
        self.hub.kernel.set_fields(co["locator"], {"implements": current}, by=self.user)
        self._redirect(self._once_finish(self._co_href(co)))

    def post_co_send(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        co = self._co_get(self._f("co"))
        if not self._co_mine(co):
            raise ShellError("Only its requester or a document controller sends a change order for approval.", 403)
        blocked = eco.check_approval(self.hub, co["locator"])
        if blocked:
            raise ShellError("It cannot be sent for approval yet: " + "; ".join(blocked))
        try:
            run_id = self.hub.runner.submit("change-order", {"record": {}}, owner=self.user, record=co["locator"])
        except RunError as e:
            raise ShellError(str(e)) from None
        state = self.hub.runner.load(run_id)
        where = (" It waits for " + ", ".join(state["pending"]["assignees"]) + ".") if state.get("pending") else f" It is {state['status']}."
        log_line(f"change order sent {co['number']} by={self.user} -> {state['status']}")
        self._redirect(self._once_finish(self._co_href(co) + "&done=" + quote(f"{co['number']} is sent for approval.{where}")))

    # ------------------------------------------------------------ SOP and policy documents
    def _sop_doc_html(self, rec: dict) -> str:
        """On an SOP or policy document: compiled into the Frame, changed through a change order; or, on a carried draft,
        which change order carries it."""
        return self._sop_doc_html_inner(rec) + (self._dependency_html(rec["fields"].get("sop_name"))
                                                 if str(rec["fields"].get("lineage", "")).startswith(sopdocs.LINEAGE) else "")

    def _sop_doc_html_inner(self, rec: dict) -> str:
        user, f = self.user, rec["fields"]
        if not str(f.get("lineage", "")).startswith(sopdocs.LINEAGE):
            return ""
        co_no = f.get("change_order")
        if co_no and rec["state"] != "effective":
            co = next((r for r in self.hub.kernel.query(type_="change-order") if r["number"] == co_no), None)
            link = f"<a href='{self._co_href(co)}'>{esc(co_no)}</a>" if co and self._can_read(co["folder"])["allowed"] else esc(co_no)
            held = " It is approved and held." if rec["state"] == "approved" else ""
            return (f"<div class='card info' role='status'>{icon('layers')} A revision of <b>{esc(f.get('sop_name'))}</b>, carried by change order {link}.{held} "
                    "It comes into force only when that change order is released, together with the Frame it compiles into.</div>")
        if rec["state"] != "effective":
            return ""
        out = (f"<div class='card' role='status'>{icon('layers')} <b>Compiled into the Frame.</b> The rules the hub enforces are read from this "
               f"{'policy' if f.get('doc_type') == 'policy' else 'document'}; change it through a change order, which carries the revision and "
               "stages the Frame it makes.")
        why = eco.may_change_sop(self.bundle or {}, rec, user)
        if why:
            return out + f" <span class='why'>{esc(why)}.</span></div>"
        mine = [c for c in self.hub.kernel.query(type_="change-order")
                if self._co_preparing(c) and user in (c["fields"].get("requested_by"), c.get("created_by"))]
        opts = "".join(f"<option value='{esc(c['locator'])}'>{esc(c['number'])} {esc((c['fields'].get('title') or '')[:60])}</option>" for c in mine)
        return out + (f"<form method='post' action='/sop-revise' class='sform row'>{hidden_as(user)}{self._once()}<input type='hidden' name='doc' value='{esc(rec['locator'])}'>"
                      f"<label class='f'><span>Revise on a change order</span><select name='co'>{opts}<option value=''>a new change order</option></select></label>"
                      f"<button class='secondary'>{icon('edit')}Revise on a change order</button></form></div>")

    def post_sop_revise(self):
        user = self.user
        bundle = self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        rec = self.hub.kernel.get(self._f("doc"))
        if rec is None or not str(rec["fields"].get("lineage", "")).startswith(sopdocs.LINEAGE):
            raise ShellError("That is not an SOP or policy in the library.", 404)
        self._require_read(rec["folder"])
        name = rec["fields"]["sop_name"]
        ref = self._f("co")
        if ref:
            co = self._co_get(ref)
        else:
            d = authz.check(bundle, user, "create", "change-order", CHANGES)
            if not d["allowed"]:
                raise ShellError(f"You cannot prepare a change order: {d['why']}", 403)
            co = self.hub.kernel.create("change-order", CHANGES, {"title": f"Revise {rec['fields'].get('title') or name}", "description": "",
                                                                  "implements": [], "requested_by": user}, by=user)
        try:
            draft = eco.add_sop(self.hub, co["locator"], name, by=user)
        except ValueError as e:
            raise ShellError(str(e), 403) from None
        self._redirect(self._once_finish(self._doc_page(draft) + "&done=" + quote(f"{draft['number']} is the draft revision of {name}, carried by {co['number']}.")))

    def _frame_derived_html(self, name: str) -> str:
        """On a Frame page once the SOPs are documents: the Frame is derived; where to change it."""
        if not self.hub.sop_source():
            return ""
        doc = sopdocs.in_force(self.hub.kernel, name)
        link = f"<a href='{self._doc_page(doc)}'>{esc(doc['number'])}</a>" if doc and self._can_read(doc["folder"])["allowed"] else "its document"
        return (f"<div class='card info' role='status'>{icon('layers')} This Frame is compiled from {link}. It is not edited here: change the document "
                "through a change order, and the Frame follows when the change order is released.</div>")
