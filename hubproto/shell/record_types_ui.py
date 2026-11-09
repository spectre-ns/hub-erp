"""Record types under change control, on screen (change control v2, principle 3): the record types in force, what open
change orders carry for them, and a form that carries one more change on a change order (``hubproto.recordtypes``).

Nothing here changes the record types in force: a carried change is part of the change order's delta and comes into
force when the change order is released."""

from __future__ import annotations

from urllib.parse import quote

from .. import authz, changecontrol, eco, recordtypes
from .forms import ShellError
from .handler import details, hidden_as, href, log_line, table
from .icons import icon
from .text import chip, esc, type_name, when

CHANGES = "Quality/Changes"
OP_WORDS = {"add_field": "add a field", "add_enum_values": "add options to an enum field", "add_state": "add a state (with the fields it needs)",
            "require_fields": "require more fields to enter a state", "set_retention": "change the retention class",
            "set_sensitivity": "change the sensitivity", "set_default": "set a field's default", "archive_type": "archive the type",
            "remove_field": "remove a field (only while no record holds data in it)", "add_type": "add a new record type",
            "remove_type": "remove a record type (only while it has no records)"}
OP_NEEDS = {"add_field": "type, field and field type", "add_enum_values": "type, field and options", "add_state": "type, state (and fields)",
            "require_fields": "type, state and fields", "set_retention": "type and value", "set_sensitivity": "type and value",
            "set_default": "type, field and value", "archive_type": "type", "remove_field": "type and field",
            "add_type": "new type name, prefix, object and fields", "remove_type": "type"}


def _csv(text: str) -> list[str]:
    return [x.strip() for x in (text or "").replace("\n", ",").split(",") if x.strip()]


class RecordTypesMixin:
    def _rt_cos(self) -> list[dict]:
        """The change orders this person may carry a record-type change on (theirs, or any for a document controller),
        while they are being prepared, reviewed or revised."""
        out = []
        for co in self.hub.kernel.query(type_="change-order"):
            if eco.finished(co) or co["state"].startswith("awaiting") or changecontrol.stage_of(co) not in changecontrol.EDITABLE:
                continue
            if self._co_mine(co):
                out.append(co)
        return sorted(out, key=lambda r: r.get("created_at") or "", reverse=True)

    def _rv(self, name: str, default: str = "") -> str:
        return self.vals.get(name, default) if (self.err and self.route == "/record-types-carry") else default

    def page_record_types(self, status: int = 200):
        user = self.user
        self._need_release()
        if not self._is_member():
            raise ShellError("Only members of the organization see the record types.", 403)
        rt = recordtypes.in_force(self.hub)
        types = {k: v for k, v in (rt.get("types") or {}).items() if isinstance(v, dict)}
        body = f"<h1>{icon('grid')} Record types</h1>"
        src = rt.get("source") or "the file"
        body += (f"<div class='card quiet'>The record types in force: <b>version {esc(rt.get('version'))}</b> ({esc(src)}"
                 + (f", released with {esc(rt['released_with'])}" if rt.get("released_with") else "") + "). "
                 "They change only through a change order: carry a change below, and it comes into force when that change order is released."
                 + ("" if rt.get("version") else " Until the SOPs are documents, the file config/record-types.json is still the source.") + "</div>")
        drift = recordtypes.drift(self.hub)
        if drift and drift.get("in_file"):
            body += ("<div class='card warn' role='status'><b>The file differs from the record types in force</b> and is not used: "
                     + esc("; ".join(drift["in_file"][:6])) + ". To bring what it adds into force, carry it on a change order "
                     "(hubproto record-types carry CO --from-file).</div>")
        rows = []
        for name, td in sorted(types.items()):
            fields = td.get("fields") or {}
            states = td.get("required_by_state") or {}
            flist = "".join(f"<li><code>{esc(k)}</code> {esc(v)}</li>" for k, v in sorted(fields.items()))
            slist = "".join(f"<li><code>{esc(k)}</code> needs {esc(', '.join(v))}</li>" for k, v in sorted(states.items()))
            more = details(f"{len(fields)} field(s), {len(states)} state rule(s)", f"<ul>{flist}</ul>" + (f"<h4>Entering a state needs</h4><ul>{slist}</ul>" if slist else ""))
            rows.append([f"<b>{esc(type_name(name))}</b> <code>{esc(name)}</code>" + (" " + chip("archived") if td.get("archived") else ""),
                         esc(td.get("prefix") or ""), esc(td.get("object") or name), more, esc(td.get("retention") or ""), esc(td.get("sensitivity") or "")])
        body += f"<h2>In force <span class='muted'>{len(types)}</span></h2>" + table(["Record type", "Prefix", "Object", "Fields", "Retention", "Sensitivity"], rows,
                                                                                   "No record types.", row_header=True)
        pending = []
        for co in self.hub.kernel.query(type_="change-order"):
            if eco.finished(co) or not recordtypes.changes_of(co) or not self._can_read(co["folder"])["allowed"]:
                continue
            pending.append([f"<a href='{self._co_href(co)}#co-carries'>{esc(co['number'])}</a> <span class='why'>{esc(co['fields'].get('title') or '')}</span>",
                            "<ul>" + "".join(f"<li>{esc(c.get('summary') or recordtypes.describe(c))} <span class='why'>by {esc(c.get('by'))}, {esc(when(c.get('at')))}</span></li>"
                                             for c in recordtypes.changes_of(co)) + "</ul>",
                            esc(changecontrol.stage_of(co).replace("-", " "))])
        body += "<h2>Carried on change orders</h2>" + table(["Change order", "Changes", "Stage"], pending, "No open change order carries a record-type change.")
        body += self._rt_form(types)
        self._page("Record types", body, status=status, crumbs=[("Settings", href("/settings", user)), ("Record types", None)])

    def _rt_form(self, types: dict) -> str:
        user = self.user
        if self._viewing_cog() or (self.bundle["members"].get(user) or {}).get("type") != "person":
            return ""
        cos = self._rt_cos()
        may_new = authz.check(self.bundle, user, "create", "change-order", CHANGES)["allowed"]
        if not cos and not may_new:
            return "<p class='why'>You have no change order to carry a record-type change on, and may not prepare one.</p>"
        want = self._rv("co", self.q.get("co", [""])[0])
        co_opts = "".join(f"<option value='{esc(c['locator'])}'{' selected' if want in (c['locator'], c['number']) else ''}>{esc(c['number'])} "
                          f"{esc((c['fields'].get('title') or '')[:60])}</option>" for c in cos)
        if may_new:
            co_opts += f"<option value=''{' selected' if not want and not cos else ''}>a new change order</option>"
        op = self._rv("op", "add_field")
        op_opts = "".join(f"<option value='{o}'{' selected' if o == op else ''}>{esc(OP_WORDS[o])}</option>" for o in OP_WORDS)
        t = self._rv("type")
        t_opts = "".join(f"<option value='{esc(n)}'{' selected' if n == t else ''}>{esc(type_name(n))} ({esc(n)})</option>" for n in sorted(types))
        spec = self._rv("spec", "text")
        spec_opts = "".join(f"<option value='{s}'{' selected' if s == spec else ''}>{s}</option>" for s in recordtypes.SPECS + ("enum",))
        err = ""
        if self.err and self.route == "/record-types-carry":
            self._err_shown = True
            err = f"<p class='err' role='alert'>{esc(self.err[0])}</p>"

        def inp(name, label, hint=""):
            return (f"<label class='f'><span>{esc(label)}" + (f" <i>({esc(hint)})</i>" if hint else "") + f"</span>"
                    f"<input name='{name}' value='{esc(self._rv(name))}'></label>")

        needs = "".join(f"<li><b>{esc(OP_WORDS[o])}</b>: {esc(OP_NEEDS[o])}</li>" for o in OP_WORDS)
        return (f"<h2 id='carry'>{icon('plus')}Carry a record-type change</h2><form method='post' action='/record-types-carry' class='card'>{hidden_as(user)}{self._once()}{err}"
                "<div class='fields'>"
                f"<label class='f'><span>On change order</span><select name='co'>{co_opts}</select></label>"
                f"<label class='f'><span>Change <b aria-hidden='true'>*</b></span><select name='op' required>{op_opts}</select></label>"
                f"<label class='f'><span>Record type</span><select name='type'><option value=''>choose…</option>{t_opts}</select></label>"
                + inp("field", "Field", "lower-case, e.g. batch")
                + f"<label class='f'><span>Field type</span><select name='spec'>{spec_opts}</select></label>"
                + inp("values", "Options", "for enum fields: a, b, c")
                + inp("state", "State", "e.g. awaiting-disposition")
                + inp("fields", "Fields", "comma-separated")
                + inp("value", "Value", "retention, sensitivity or default")
                + "</div>"
                + details("A new record type", "<div class='fields'>" + inp("new_type", "Name", "lower-case, e.g. calibration") + inp("prefix", "Number prefix", "e.g. CAL")
                          + inp("object", "Object noun", "what the SOPs call it") + inp("new_fields", "Fields", "name: type, name: type") + "</div>")
                + "<label class='f'><span>Why <i>(the reviewers read this)</i></span>"
                f"<textarea name='reason' rows='2'>{esc(self._rv('reason'))}</textarea></label>"
                f"<div class='actions'><button>{icon('plus')}Carry on the change order</button>"
                "<span class='why'>Checked against the record types in force and what the change order already carries; a destructive change "
                "(a rename, a retype, removing options or data) is refused.</span></div>"
                + details("Which change needs what", f"<ul>{needs}</ul>") + "</form>")

    def _rt_change_from_form(self) -> dict:
        op = self._f("op")
        if op not in recordtypes.OPS:
            raise ShellError("Choose the change to make.", field="op")
        if op == "add_type":
            name = self._f("new_type")
            fields = {}
            for part in _csv(self._f("new_fields")):
                k, _, v = part.partition(":")
                if k.strip():
                    fields[k.strip()] = v.strip() or "text"
            return {"op": op, "type": name, "definition": {"prefix": self._f("prefix"), "digits": 4, "yearly": True, "object": self._f("object") or name,
                                                         "fields": fields or {"title": "text"}, "retention": "standard"}}
        c: dict = {"op": op, "type": self._f("type")}
        if op in ("add_field",):
            spec = self._f("spec") or "text"
            if spec == "enum":
                vals = _csv(self._f("values"))
                if not vals:
                    raise ShellError("An enum field needs its options (a, b, c).", field="values")
                spec = "enum:" + ",".join(vals)
            c.update({"field": self._f("field"), "spec": spec})
        elif op == "add_enum_values":
            c.update({"field": self._f("field"), "values": _csv(self._f("values"))})
        elif op in ("add_state", "require_fields"):
            c.update({"state": self._f("state"), "fields": _csv(self._f("fields"))})
        elif op in ("set_retention", "set_sensitivity"):
            c["value"] = self._f("value")
        elif op == "set_default":
            c.update({"field": self._f("field"), "value": self._f("value")})
        elif op == "remove_field":
            c["field"] = self._f("field")
        return c

    def post_record_types_carry(self):
        user = self.user
        bundle = self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        if (bundle["members"].get(user) or {}).get("type") != "person":
            raise ShellError("Only people who are members carry record-type changes.", 403)
        change = self._rt_change_from_form()
        ref = self._f("co")
        if ref:
            co = self._co_get(ref)
        else:
            if not authz.check(bundle, user, "create", "change-order", CHANGES)["allowed"]:
                raise ShellError("You may not prepare a change order: choose one of yours.", 403)
            co = None
        try:
            if co is None:  # checked before a change order is started for it, so a refusal leaves nothing behind
                _, errors, _ = recordtypes.check(self.hub, [{**change, "id": "rt-1"}])
                if errors:
                    raise ValueError("refused, nothing carried: " + "; ".join(errors))
                co = self.hub.kernel.create("change-order", CHANGES, {"title": f"Record types: {recordtypes.describe(change)}"[:120],
                                                                      "description": self._f("reason") or "A record-type change.", "requested_by": user,
                                                                      "stage": "preparing"}, by=user)
            recordtypes.carry(self.hub, co["locator"], [change], by=user, reason=self._f("reason"))
        except ValueError as e:
            s = str(e)
            raise ShellError(s[:1].upper() + s[1:] + ("" if s.endswith(".") else ".")) from None
        log_line(f"record-type change carried on {co['number']} by={user}: {recordtypes.describe(change)}")
        self._redirect(self._once_finish(self._co_href(co) + "&done=" + quote(f"Carried on {co['number']}: {recordtypes.describe(change)}. "
                                                                              "It comes into force when the change order is released.") + "#co-carries"))
