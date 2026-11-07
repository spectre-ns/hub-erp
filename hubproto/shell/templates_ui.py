"""Controlled templates in the document library: each template with the revision in force and any open revision.

Templates are documents of class ``template`` (hubproto.templates): edited in the online editor while a draft, sent
for approval through document-control like an SOP, read-only once effective. A change to a template in force is a
new revision, started here or on the template's page; it supersedes the earlier one when it becomes effective."""

from __future__ import annotations

from urllib.parse import quote

from .. import templates as tpl
from .. import wopi
from .forms import ShellError
from .apps import plural
from .handler import hidden_as, href, table
from .icons import icon
from .text import day, esc


class TemplatesMixin:
    def _doc_href(self, rec: dict) -> str:
        return href("/document/" + quote(rec["locator"], safe=":"), self.user)

    def _template_revision_html(self, rec: dict) -> str:
        """On an effective template's page: why it is read-only, and the button that starts a new revision."""
        why = tpl.may_revise(self.bundle, rec, self.user)
        head = (f"<span class='why'>{esc(rec['number'])} is in force and read-only; a change to a template is a new revision, approved "
                "like the first, that replaces this one when it becomes effective.</span>")
        if why:
            return head + f" <span class='why'>{esc(why)}.</span>"
        return head + (f"<form method='post' action='/template-revise' class='inline'>{hidden_as(self.user)}{self._once()}"
                       f"<input type='hidden' name='doc' value='{esc(rec['locator'])}'><button class='secondary'>{icon('edit')}Start a new revision</button></form>")

    def page_templates(self):
        user = self.user
        bundle = self._need_release()
        groups = [g for g in tpl.templates(self.hub.kernel) if self._can_read((g["open"] or g["effective"] or g["revisions"][-1])["folder"])["allowed"]]
        rows = []
        for g in groups:
            eff, cur = g["effective"], g["open"]
            latest = cur or eff or g["revisions"][-1]
            title = f"<a href='{self._doc_href(latest)}'>{esc(g['title'])}</a>"
            if g["for"]:
                kinds = plural(g["for"])
                kinds = kinds[0].lower() + kinds[1:] if kinds[1:2].islower() else kinds  # "change requests", "CAPAs"
                title += f" <span class='why'>for {esc(kinds)}</span>"
            in_force = (f"<a href='{self._doc_href(eff)}'>{esc(eff['number'])}</a> rev {esc(eff['fields'].get('revision', ''))}"
                        f" <span class='why'>since {esc(day(eff['fields'].get('effective_from')))}</span>") if eff else "<span class='muted'>not yet</span>"
            if cur:
                links = f"<a href='{self._doc_href(cur)}'>{esc(cur['number'])}</a> rev {esc(cur['fields'].get('revision', ''))} {self._state_chip(cur['state'])}"
                if wopi.is_editable(cur) and cur["state"] in tpl.EDITABLE:
                    links += f" <a class='tpl-link' href='{href('/edit/' + quote(cur['locator'], safe=':'), user)}'>{icon('edit')}Edit online</a>"
            else:
                links = "<span class='muted'>none</span>"
            n = len(latest["fields"].get("placeholders") or [])
            rows.append([title, in_force, links, f"<span class='tpl-ph'>{n}</span>" if n else "-"])
        body = (f"<h1>{icon('file-text', cls='lg')} Templates</h1>"
                "<p class='why'>Controlled templates: forms and outlines the quality system works from. A draft is edited online and sent for approval like an "
                "SOP; once effective it is read-only, and a change is a new revision that replaces it when approved. Placeholders such as "
                "<code>{{number}}</code> are filled in when a template is used.</p>"
                + table(["Template", "In force", "Open revision", "Placeholders"], rows,
                        "No templates in the library yet. An administrator brings the profile's templates in with "
                        "<code>hubproto templates import --as PERSON</code>."))
        self._page("Templates", body, crumbs=[("Files", href("/files", user)), ("Templates", None)])

    def post_template_revise(self):
        bundle = self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        rec = self.hub.kernel.get(self._f("doc"))
        if rec is None or rec["type"] != "document":
            raise ShellError("There is no such template.", 404)
        self._require_read(rec["folder"])
        try:
            new = tpl.new_revision(self.hub, rec["locator"], by=self.user, bundle=bundle)
        except ValueError as e:
            raise ShellError(str(e), 403) from None
        msg = (f"{new['number']} is the new revision ({new['fields'].get('revision')}) of {rec['number']}: edit it, then send it for approval."
               if new["fields"].get("revises") == rec["number"] and new["created_by"] == self.user else f"{new['number']} is already the open revision of this template.")
        self._redirect(self._once_finish(self._doc_href(new) + "&done=" + quote(msg)))
