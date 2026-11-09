"""Change orders in the Quality app: prepared first (the SOP and policy revisions they carry, the change requests they
implement), then sent for approval once nothing blocks them (hubproto.eco).

A change order's page shows what it carries and where each revision stands, the Frame those revisions compile into
(staged: the rule changes as sentences, wording changes, compile errors), and what still blocks its approval. An SOP or
policy in force says it is compiled into the Frame and is changed through a change order; a carried draft says which
change order carries it and that it comes into force only when that order is released."""

from __future__ import annotations

from urllib.parse import quote

from .. import authz, changecontrol, eco, sopdocs
from ..runner import RunError
from .forms import ShellError
from .handler import details, hidden_as, href, log_line, table
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
            versions = self.hub.dms.versions(d["locator"]) if hasattr(self.hub.dms, "versions") else []
            last = versions[-1] if versions else {}
            reviewed = ""
            rnd = changecontrol.current_round(co) or {}
            seen = ((rnd.get("reviewed_from") or {}).get("drafts") or {}).get(d["fields"].get("sop_name"))
            if seen is not None:
                same = seen == str(d["fields"].get("content_sha") or "")
                reviewed = f"; round {rnd.get('n')} reviewed {'it as it stands' if same else 'an earlier version'}"
            return ("<span class='chip'>being written</span>" + (f" <span class='why'>version {len(versions)} by {esc(last.get('by'))}{reviewed}</span>" if versions else ""))
        return f"<span class='chip'>{esc(state_name(d['state']).lower())}</span>"

    def _change_requests(self) -> list[dict]:
        return [r for r in self.hub.kernel.query(type_="change-request") if self._can_read(r["folder"])["allowed"]]

    # ------------------------------------------------------------ the list and the new form
    def page_change_orders(self, status: int = 200):
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
            stage = changecontrol.stage_of(co)
            rows.append([f"<a href='{self._co_href(co)}'>{esc(co['number'])}</a>", esc(f.get("title") or ""),
                         f"<span class='chip {'ok' if stage == 'released' else 'warn' if stage == 'awaiting-approval' else 'info' if stage in ('in-review', 'revising') else ''}'>"
                         f"{esc(stage.replace('-', ' '))}</span>" + (" <span class='chip warn'>baseline changed</span>" if (f.get("baseline_changed") or {}) and not
                                                                     (f.get("baseline_changed") or {}).get("dismissed") else ""),
                         esc(", ".join([c["name"] for c in carried] + [f"{t} form" for t in sorted(f.get("form_changes") or {})]) or "-"),
                         esc(", ".join(f.get("implements") or []) or "-"),
                         (f"<span class='chip warn'>{len(f['staged_errors'])} error(s)</span>" if f.get("staged_errors") else
                          (self._co_item_count(co) if f.get("staged_proposal") else "-"))])
        body = (f"<h1>{icon('layers', cls='lg')} Change orders</h1>"
                "<p class='why'>A change request asks for a change and says why. A change order carries it out: revisions of the SOPs and "
                "policies it changes, direct changes made in Settings and the editors, form, record-type and workflow changes. Review rounds "
                "compile the configuration delta and ask a panel; people decide each item; the quality manager signs; the release puts the "
                "configuration and the revised texts in force together.</p>")
        body += table(["Change order", "Title", "Stage", "Carries", "Implements", "Delta"], rows, "No change orders yet.")
        body += self._co_new_form()
        self._page("Change orders", body, status=status, crumbs=[("Quality", href("/app/quality", user)), ("Change orders", None)])

    def _co_item_count(self, co: dict) -> str:
        """The delta's items as the change order's page numbers them (a change and the removal it replaces are one)."""
        from .change_order_ui import groups

        try:
            n = len(groups(changecontrol.delta(self.hub, co["locator"])))
        except Exception:
            n = int(co["fields"].get("staged_changes") or 0)
        return f"{n} item" + ("" if n == 1 else "s")

    def _co_new_form(self) -> str:
        user = self.user
        d = authz.check(self.bundle, user, "create", "change-order", CHANGES)
        if not d["allowed"]:
            return f"<p class='why'>You cannot prepare a change order: {self._why_html(d, 'create', 'change-order', CHANGES)}</p>"
        crs = [r for r in self._change_requests() if cr_approved(r)]
        picked = set(self._multi("implements")) if self.route == "/co-new" else set()
        checks = "".join(f"<label class='check'><input type='checkbox' name='implements' value='{esc(r['number'])}'{' checked' if r['number'] in picked else ''}> {esc(r['number'])} "
                         f"{esc((r['fields'].get('title') or '')[:80])}</label>" for r in crs)
        mine = self.route == "/co-new"
        err = self._form_error(["title", "description"]) if mine else ""
        terr = self._field_error("title") if mine else None
        title = esc(self._val("title")) if mine else ""
        desc = esc(self._val("description")) if mine else ""
        return (f"<h2 id='new'>{icon('plus')}New change order</h2><form method='post' action='/co-new' class='card'>{hidden_as(user)}{self._once()}{err}"
                f"<div class='fields'><label class='f{' bad' if terr else ''}'><span>Title <b>*</b></span><input name='title' required value='{title}'"
                + (" aria-invalid='true' aria-describedby='err-title'" if terr else "") + ">"
                + (f"<span class='err' id='err-title'>{esc(terr)}</span>" if terr else "") + "</label>"
                f"<label class='f'><span>What it changes and why</span><textarea name='description'>{desc}</textarea></label></div>"
                + (f"<fieldset class='checks'><legend>Implements these approved change requests</legend>{checks}</fieldset>" if checks
                   else "<p class='why'>No approved change request to implement yet; you can add them later.</p>")
                + "<p class='why'>It is prepared first: carry what it changes, then request review.</p>"
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
        """The change order's own section on its record page: the whole loop (hubproto.shell.change_order_ui)."""
        return self._co_page(co)

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
        self._redirect(self._once_finish(self._co_href(co) + "&done=" + quote(f"{draft['number']} is the draft revision of {self._f('sop')}: edit it, then request review on {co['number']}.")))

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

    def post_co_review(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        co = self._co_get(self._f("co"))
        if not self._co_mine(co):
            raise ShellError("Only its requester or a document controller requests review of a change order.", 403)
        panel = None
        plus = [n for n in self._multi("panel")] if self._f("choose") == "plus" else []
        if self._f("choose") == "own":
            members = (self.bundle or {}).get("members") or {}
            panel = [n for n in self._multi("panel") if n in members]
            if not panel:
                raise ShellError("Tick at least one reviewer, or let the quality-assistant cog suggest the panel.", field="panel")
        try:
            rnd = changecontrol.request_review(self.hub, co["locator"], self.user, panel=panel)
        except ValueError as e:
            raise ShellError(str(e)[:1].upper() + str(e)[1:] + ("" if str(e).endswith(".") else ".")) from None
        added, later = [], []
        for who in plus:  # the suggested panel and the ones ticked: added to the round
            try:
                changecontrol.add_reviewer(self.hub, co["locator"], who, self.user, reason=f"added by {self.user}")
                added.append(who)
            except ValueError:
                later.append(who)
        log_line(f"change order review requested {co['number']} round={rnd['n']} by={self.user}")
        msg = f"Review round {rnd['n']} of {co['number']} is under way." + (f" Added {', '.join(added)}." if added else "") \
            + (f" Add {', '.join(later)} under Review once the round is compiled." if later else "")
        self._redirect(self._once_finish(self._co_href(co) + "&done=" + quote(msg) + "#co-review"))

    def post_co_send(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        co = self._co_get(self._f("co"))
        if not self._co_mine(co):
            raise ShellError("Only its requester or a document controller sends a change order for approval.", 403)
        try:
            state = changecontrol.send_for_approval(self.hub, co["locator"], self.user)
        except (ValueError, RunError) as e:
            raise ShellError(str(e)) from None
        where = (" It waits for " + ", ".join(state["pending"]["assignees"]) + ".") if state.get("pending") else f" It is {state['status']}."
        log_line(f"change order sent {co['number']} by={self.user} -> {state['status']}")
        self._redirect(self._once_finish(self._co_href(co) + "&done=" + quote(f"{co['number']} is sent for approval.{where}")))

    # ------------------------------------------------------------ SOP and policy documents
    def _sop_doc_html(self, rec: dict) -> str:
        """On an SOP or policy document: compiled into the Frame, changed through a change order; or, on a carried draft,
        which change order carries it. Divergences (the configuration in force differs from a line) are marked at the line."""
        if not str(rec["fields"].get("lineage", "")).startswith(sopdocs.LINEAGE):
            return self._sop_doc_html_inner(rec)
        return self._sop_doc_html_inner(rec) + self._sop_lines_html(rec) + self._dependency_html(rec["fields"].get("sop_name"))

    def _sop_lines_html(self, rec: dict) -> str:
        """The document's text line by line (the line numbers rules and divergences cite), each divergence marked under its
        line; on a draft revision, the divergences of the revision in force."""
        name = rec["fields"].get("sop_name") or ""
        divs = self._divergences_of(name)
        if rec["state"] != "effective":
            if not divs:
                return ""
            return (f"<div class='card warn' role='status'>{icon('alert')} The revision in force has {len(divs)} line(s) where the configuration in force "
                    "differs. A revision that states what is in force (or a change order that changes the configuration back) resolves each:"
                    + "".join(self._divergence_marker(d, with_line=True) for d in divs) + "</div>")
        try:
            lines = sopdocs.frame_text(self.hub, rec).splitlines()
        except Exception:
            return self._divergence_card(name)
        body = self._lines_with_markers(name, lines)
        return self._divergence_card(name) + details(f"The text, line by line ({len(lines)} lines" + (f", {len(divs)} marked" if divs else "") + ")",
                                                     "<p class='why'>Line numbers count the header too, as citations do (“line 34”).</p>" + body,
                                                     open_=bool(divs), cls="sop-lines")

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
            return out + f" <span class='why'>{esc(why[:1].upper() + why[1:])}.</span></div>"
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
