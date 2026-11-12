"""The configuration as JSON in the shell (issue #33): ``GET /api/config`` gives everything in force as one JSON
document (``hubproto.configjson``) to those allowed to read the policy, and a change order's page takes such a document
as an upload and carries what it changes. The change order's link-vocabulary and retention changes are listed there too
(``hubproto.configdocs``)."""

from __future__ import annotations

from .. import authz, configdocs, configjson
from ..sopdocs import POLICY_FOLDER
from .forms import ShellError
from .handler import hidden_as, href, log_line, table
from .icons import icon
from .text import esc, when


class ConfigMixin:
    # ------------------------------------------------------------ GET /api/config
    def _api_error(self, status: int, text: str) -> None:
        self._json({"error": text}, status)

    def page_api_config(self):
        if not self.user:
            return self._api_error(401, "Say who you are: add ?as=<your name>.")
        if not self.bundle:
            return self._api_error(409, "No policy release is in force yet: there is no configuration to export.")
        if self.user not in (self.bundle.get("members") or {}):
            return self._api_error(403, f"{self.user} is not a member of the organization.")
        d = authz.check(self.bundle, self.user, "read", "*", POLICY_FOLDER)
        if not d["allowed"]:
            return self._api_error(403, f"You cannot read the policy ({POLICY_FOLDER}). " + self._why_text(d, "read", "*", POLICY_FOLDER))
        body = configjson.dumps(configjson.export(self.hub)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.q.get("download", [""])[0]:
            self.send_header("Content-Disposition", 'attachment; filename="hub-config.json"')
        self.end_headers()
        self.wfile.write(body)
        log_line(f"configuration exported by={self.user} bytes={len(body)}")

    # ------------------------------------------------------------ on a change order
    def _co_config_html(self, ctx: dict) -> str:
        co = ctx["co"]
        prep = ctx["mine"] and ctx["editable"]
        rows = []
        for c in configdocs.changes_of(co):
            rm = self._co_form("/co-cfg-uncarry", co, f"cfg-{esc(c.get('id'))}", f"<input type='hidden' name='change' value='{esc(c.get('id'))}'>"
                               f"<button class='ghost'>{icon('x')}Remove</button>", cls="inline") if prep else ""
            rows.append([esc(c.get("summary") or configdocs.describe(c)), self._co_who(c.get("by")), esc(c.get("reason") or ""),
                         f"<span class='why'>{esc(when(c.get('at')))}</span>", rm])
        out = ""
        if rows:
            out += f"<h3>{icon('link')}Link vocabulary and retention classes</h3>" + table(["Change", "Carried by", "Why", "When", ""], rows)
        if prep:
            out += self._co_config_upload_form(co)
        return out

    def _co_config_upload_form(self, co: dict) -> str:
        act, fid = "/co-config-upload", "config-upload"
        err = ""
        if self.err and self.route == act:
            self._err_shown = True
            err = f"<p class='err' role='alert'>{esc(self.err[0])}</p>"
        inner = ("<p class='why'>A JSON document as <a href='" + href("/api/config", self.user, download="1") + "'>the configuration in force</a> "
                 "downloads (or <code>hubproto config export</code> writes), edited: what it changes is carried on this change order the way "
                 "Settings, the forms editor and the record types page carry it. Nothing is carried when any part cannot be.</p>"
                 "<label class='f'><span>Configuration file (JSON) <b aria-hidden='true'>*</b></span><input type='file' name='file' accept='application/json,.json' required></label>"
                 "<label class='f'><span>Why <i>(optional)</i></span><input name='reason'></label>"
                 f"<div class='actions'><button class='secondary'>{icon('upload')}Carry what it changes</button></div>")
        form = (f"<form method='post' action='{act}' enctype='multipart/form-data' class='sform' id='f-{fid}' aria-label='Propose a configuration file'>"
                f"{hidden_as(self.user)}{self._once()}<input type='hidden' name='co' value='{esc(co['locator'])}'>"
                f"<input type='hidden' name='form' value='{fid}'>{err}{inner}</form>")
        from .handler import details

        return details(f"{icon('file-text')}Propose a configuration file (JSON)", form, open_=bool(self.err and self.route == act))

    def post_co_config_upload(self):
        co = self._co_post()
        if co is None:
            return
        filename, content = self._file()
        if not content:
            raise ShellError("Choose a JSON file: a configuration document as the configuration in force downloads, edited.", field="file")
        try:
            doc = configjson.loads(content)
            out = configjson.propose(self.hub, doc, by=self.user, co_ref=co["locator"], reason=self._f("reason"), name=filename)
        except configjson.Refused as e:
            raise ShellError(f"{filename} was not carried; nothing was: " + "; ".join(e.reasons) + ".", field="file") from None
        if not out["changed"]:
            return self._co_done(co, f"{filename} says what is in force: nothing to carry.", "co-carries")
        log_line(f"configuration file carried on {co['number']} by={self.user}: {len(out['summary'])} change(s) from {filename}")
        n = len(out["summary"])
        self._co_done(co, f"Carried on {co['number']} from {filename}: {n} change{'s' if n != 1 else ''}. Request review next.", "co-carries")

    def post_co_cfg_uncarry(self):
        co = self._co_post()
        if co is None:
            return
        try:
            configdocs.uncarry(self.hub, co["locator"], self._f("change"), by=self.user)
        except ValueError as e:
            raise self._co_refused(e) from None
        self._co_done(co, "The change is off the change order.", "co-carries")
