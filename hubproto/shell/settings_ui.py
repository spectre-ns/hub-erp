"""Settings on screen: every rule the hub enforces, edited as data with no SOP sentences and no Python.

Each person edits their own draft (hubproto.settings, Hub.settings_*): it starts from the policy in force, every
edit says what it changed in a sentence, Check runs the same guards as a compile, and Propose makes a proposal the
policy owners review and release (two owners for widenings). Members who are not people (cogs) and anyone watching
a cog can read the settings but not edit them. Routing and processes are shown read-only here; a graphical workflow
builder edits them."""

from __future__ import annotations

import re
from urllib.parse import quote

from ..rules import CONDITION_VOCABULARY, rule_key
from ..settings import sentence
from .forms import ShellError
from .handler import hidden_as, href, log_line, table
from .icons import icon
from .text import chip, day, esc, version_label

SECTIONS = [("overview", "Your changes", "edit"), ("people", "People and roles", "users"), ("permissions", "Permissions", "lock"),
            ("limits", "Limits and due dates", "clock"), ("rails", "Cog rails", "robot"), ("sod", "Segregation of duties", "shield"),
            ("workflows", "Workflows and routing", "activity")]
SECTION_NAMES = {k: n for k, n, _ in SECTIONS}
ACTIONS = ("create", "read", "approve", "update", "delete")
UNITS = ("USD", "EUR", "GBP", "percent", "days")
CURRENCIES = ("USD", "EUR", "GBP")
_PHRASES: dict[str, str] = {}
for _p, _k in CONDITION_VOCABULARY.items():
    _PHRASES.setdefault(_k, _p)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")


def _num(text: str, field: str, *, integer: bool = False, positive: bool = True) -> float | int | None:
    t = (text or "").replace(",", "").strip()
    if not t:
        return None
    try:
        v = int(t) if integer else float(t)
    except ValueError:
        raise ShellError(f"'{text}' is not a {'whole ' if integer else ''}number.", field=field) from None
    if positive and v <= 0:
        raise ShellError("Give a number above zero.", field=field)
    return v


class SettingsMixin:
    # ------------------------------------------------------------ who may edit, what they see
    def _settings_editor(self) -> bool:
        return bool(self.bundle) and (self.bundle["members"].get(self.user) or {}).get("type") == "person" and not self._viewing_cog()

    def _settings_rules(self) -> tuple[list[dict], dict | None]:
        """The rules shown: the person's draft when they may edit, the policy in force otherwise."""
        if self._settings_editor():
            d = self.hub.settings_draft(self.user)
            return d["rules"], d
        return self.hub.settings_rules(), None

    def _processes(self) -> dict:
        return (self.bundle or {}).get("processes") or {}

    def _objects(self) -> list[str]:
        types = self.hub.kernel.types
        return sorted({t.get("object", n) for n, t in types.items() if n != "$comment"})

    # ------------------------------------------------------------ small builders
    def _sv(self, form_id: str, name: str, default=""):
        """A value for a control: what was typed when this form was just refused, else the stored value."""
        if self.vals and self.vals.get("form") == form_id:
            return self.vals.get(name, default)
        return default

    def _smulti(self, form_id: str, name: str, default: list[str]) -> list[str]:
        if self.vals and self.vals.get("form") == form_id:
            return list((getattr(self, "multi", None) or {}).get(name) or [])
        return list(default or [])

    def _sform(self, form_id: str, section: str, kind: str, action: str, inner: str, button: str, *, key: str = "",
               cls: str = "sform", danger: bool = False) -> str:
        if not self._settings_editor():
            return ""
        err = ""
        if self.err and self.vals.get("form") == form_id:
            self._err_shown = True
            err = f'<p class="err" role="alert">{esc(self.err[0])}</p>'
        return (f"<form method='post' action='/settings-edit' class='{cls}' id='f-{esc(form_id)}'>{hidden_as(self.user)}{self._once()}"
                f"<input type='hidden' name='form' value='{esc(form_id)}'><input type='hidden' name='section' value='{esc(section)}'>"
                f"<input type='hidden' name='kind' value='{esc(kind)}'><input type='hidden' name='action' value='{esc(action)}'>"
                f"<input type='hidden' name='key' value='{esc(key)}'>{err}{inner}"
                f"<button class='{'ghost reject' if danger else ''}'>{button}</button></form>")

    def _remove(self, section: str, rule: dict, label: str = "Remove") -> str:
        k = rule_key(rule)
        return self._sform(f"rm-{k}", section, rule["kind"], "remove", "", f"{icon('x')}{esc(label)}", key=k, cls="sform inline", danger=True)

    def _origin(self, rule: dict, released: set[str]) -> str:
        src = rule.get("source") or {}
        changed = "" if rule_key(rule) in released else " " + chip("in your draft", "warn")
        if src.get("frame") == "settings":
            return f"<span class='muted'>settings, by {esc(src.get('by') or '?')} on {esc(day(str(src.get('at') or '')[:10]))}</span>{changed}"
        if src.get("frame"):
            return (f"<a class='muted' href='{href('/frame/' + src['frame'], self.user)}#L{src.get('line')}'>{esc(src['frame'])} line "
                    f"{esc(src.get('line'))}</a>{changed}")
        return changed

    def _select(self, name: str, options: list[tuple[str, str]], value: str, *, label: str, blank: str = "") -> str:
        opts = (f"<option value=''>{esc(blank)}</option>" if blank else "") + "".join(
            f"<option value='{esc(v)}'{' selected' if v == value else ''}>{esc(t)}</option>" for v, t in options)
        return f"<label class='f'><span>{esc(label)}</span><select name='{esc(name)}'>{opts}</select></label>"

    def _input(self, name: str, value, *, label: str, kind: str = "text", hint: str = "", size: str = "") -> str:
        style = f" style='max-width:{size}'" if size else ""
        return (f"<label class='f'><span>{esc(label)}{(' <i>(' + esc(hint) + ')</i>') if hint else ''}</span>"
                f"<input name='{esc(name)}' type='{kind}' value='{esc(value if value is not None else '')}'{style}></label>")

    def _checks(self, name: str, options: list[tuple[str, str]], chosen: list[str], *, legend: str) -> str:
        boxes = "".join(f"<label class='check'><input type='checkbox' name='{esc(name)}' value='{esc(v)}'{' checked' if v in chosen else ''}> {esc(t)}</label>"
                        for v, t in options)
        return f"<fieldset class='checks'><legend>{esc(legend)}</legend>{boxes}</fieldset>"

    # ------------------------------------------------------------ the page
    def page_settings(self, status: int = 200, check: dict | None = None):
        user = self.user
        self._need_release()
        if not self._is_member():
            raise ShellError("Only members of the organization see the settings.", 403)
        section = (self.arg or "overview").strip("/") or "overview"
        if section not in SECTION_NAMES:
            raise ShellError(f"There is no settings section '{section}'.", 404)
        rules, draft = self._settings_rules()
        released = {rule_key(r) for r in self.hub.settings_rules()}
        nav = "".join(f"<li><a href='{href('/settings/' + k, user)}'{' aria-current=\"page\"' if k == section else ''}>{icon(ic)}<span>{esc(n)}</span>"
                      + (f" <b class='count'>{len(draft['edits'])}</b>" if k == "overview" and draft and draft.get("edits") else "") + "</a></li>"
                      for k, n, ic in SECTIONS)
        top = ""
        if not self.hub.settings_source():
            top += ("<div class='card quiet'>The rules in force came from the Frames (the policy documents). Proposing a change from "
                    "Settings makes Settings the source of truth from then on: the rules are edited here, and the Frames become the SOPs "
                    "people and cogs read.</div>")
        if not self._settings_editor():
            top += "<div class='card quiet'>You can read the settings. Changing them is for people who are members; a change is a proposal the policy owners release.</div>"
        elif draft and draft.get("stale"):
            top += (f"<div class='card warn' role='status'><b>The policy changed since you started.</b> Your {len(draft.get('edits') or [])} edit(s) are "
                    f"against an older version; discard them and start again from the policy in force. "
                    f"{self._discard_form()}</div>")
        body = getattr(self, f"_settings_{section}")(rules, draft, released, check)
        page = (f"<h1>{icon('sliders')} Settings <span class='muted'>{esc(SECTION_NAMES[section])}</span></h1>{top}"
                f"<div class='settings-layout'><nav class='settings-nav' aria-label='Settings sections'><ul>{nav}</ul></nav>"
                f"<div class='settings-main'>{body}</div></div>")
        self._page(f"Settings: {SECTION_NAMES[section]}", page, status=status,
                   crumbs=[("Settings", href("/settings", user)), (SECTION_NAMES[section], None)])

    # ------------------------------------------------------------ overview
    def _discard_form(self) -> str:
        return (f"<form method='post' action='/settings-discard' class='inline'>{hidden_as(self.user)}{self._once()}"
                f"<button class='ghost reject'>{icon('x')}Discard my draft</button></form>")

    def _settings_overview(self, rules, draft, released, check) -> str:
        user = self.user
        meta = self.hub.releases.current()
        base = version_label(meta, self.hub.releases.list()) if meta else "no policy"
        if not draft:
            return f"<p>The settings of {esc(base)}. Pick a section to read them.</p>"
        edits = draft.get("edits") or []
        out = f"<p>Your draft is based on <b>{esc(base)}</b>. Edits stay yours until you propose them.</p>"
        if edits:
            out += f"<h2>{len(edits)} edit(s) in your draft</h2><ol class='edits'>" + "".join(
                f"<li>{esc(e['said'])} <span class='muted'>{esc(day(str(e.get('at'))[:10]))}</span></li>" for e in edits) + "</ol>"
        else:
            out += "<p class='muted'>No edits yet: change something in a section, then come back here to check and propose it.</p>"
        if check is not None:
            out += self._check_html(check)
        if edits:
            err = ""
            if self.err and self.route in ("/settings-propose",):
                self._err_shown = True
                err = f'<p class="err" role="alert">{esc(self.err[0])}</p>'
            out += (f"<div class='actions'><form method='post' action='/settings-check' class='inline'>{hidden_as(user)}"
                    f"<button class='ghost'>{icon('check-circle')}Check</button></form>{self._discard_form()}</div>"
                    f"<form method='post' action='/settings-propose' class='card'>{hidden_as(user)}{self._once()}{err}"
                    f"<label class='f'><span>Why (the policy owners read this)</span><textarea name='reason' rows='2'>{esc(self._val('reason'))}</textarea></label>"
                    f"<div class='actions'><button>{icon('send')}Propose to the policy owners</button>"
                    f"<span class='why'>A widening (more someone or a cog may do) needs both policy owners.</span></div></form>")
        return out

    def _check_html(self, check: dict) -> str:
        procs = self._processes()
        rows = []
        for c in check.get("changes") or []:
            if c["op"] == "remove" and c.get("changed_to"):
                continue
            what = ("changed" if c.get("changes_from") else "added") if c["op"] == "add" else "removed"
            text = sentence(c["rule"], procs)
            if c.get("changes_from"):
                text = f"{sentence(c['changes_from'], procs)} → {text}"
            rows.append([chip(what, "warn" if what != "removed" else None), f"<span class='pre'>{esc(text)}</span>",
                         chip("widening", "crit") if c.get("widening") else ""])
        errors = [f for f in check.get("findings") or [] if f.get("severity") == "error"]
        out = "<h2>Check</h2>" + table(["", "Change", ""], rows, "Your draft changes nothing.")
        if errors:
            out += "<div class='card crit' role='alert'><b>These stop a release:</b><ul>" + "".join(f"<li>{esc(f.get('detail'))}</li>" for f in errors) + "</ul></div>"
        else:
            out += "<p class='ok'>" + icon("check") + "No problem stops a release.</p>"
        return out

    # ------------------------------------------------------------ people, roles, folders
    def _settings_people(self, rules, draft, released, check) -> str:
        roles = sorted((r for r in rules if r["kind"] == "role"), key=lambda r: r["name"])
        role_names = [r["name"] for r in roles]
        members = sorted((r for r in rules if r["kind"] == "member"), key=lambda r: (r["principal_type"], r["principal"]))
        folders = sorted((r for r in rules if r["kind"] == "folder"), key=lambda r: r["path"])
        out = f"<h2>Roles <span class='muted'>{len(roles)}</span></h2><ul class='chips-list'>" + "".join(
            f"<li>{chip(r['name'].replace('-', ' '))} {self._remove('people', r)}</li>" for r in roles) + "</ul>"
        out += self._sform("add-role", "people", "role", "add", self._input("name", self._sv("add-role", "name"), label="New role", hint="e.g. buyer", size="16em"),
                           f"{icon('plus')}Add role", cls="sform row")
        rows = []
        for m in members:
            k = rule_key(m)
            fid = f"m-{k}"
            edit = self._sform(fid, "people", "member", "replace",
                               f"<input type='hidden' name='principal' value='{esc(m['principal'])}'><input type='hidden' name='principal_type' value='{esc(m['principal_type'])}'>"
                               + self._checks("roles", [(r, r.replace("-", " ")) for r in role_names], self._smulti(fid, "roles", m["roles"]), legend="Roles"),
                               f"{icon('check')}Save roles", key=k)
            who = f"<b>{esc(m['principal'])}</b> {chip(m['principal_type'])}"
            roles_html = " ".join(chip(r.replace('-', ' '), "") for r in m["roles"])
            body = f"<details><summary>{who} {roles_html}</summary>{edit}{self._remove('people', m, 'Remove member')}</details>" if self._settings_editor() else f"{who} {roles_html}"
            rows.append([body, self._origin(m, released)])
        out += f"<h2>Members <span class='muted'>{len(members)}</span></h2>" + table(["Member and roles", "From"], rows)
        out += self._sform("add-person", "people", "member", "add",
                           "<input type='hidden' name='principal_type' value='person'>"
                           + self._input("principal", self._sv("add-person", "principal"), label="New person", hint="their user name", size="16em")
                           + self._checks("roles", [(r, r.replace("-", " ")) for r in role_names], self._smulti("add-person", "roles", ["staff"] if "staff" in role_names else []), legend="Roles"),
                           f"{icon('plus')}Add person", cls="sform card")
        out += "<p class='why'>A new cog also needs a profile (model, prompt, the SOPs it follows): add it from the People page, then set its rails here.</p>"
        out += f"<h2>Folders <span class='muted'>{len(folders)}</span></h2><ul class='chips-list'>" + "".join(
            f"<li><code>{esc(f['path'])}</code> {self._remove('people', f)}</li>" for f in folders) + "</ul>"
        out += self._sform("add-folder", "people", "folder", "add", self._input("path", self._sv("add-folder", "path"), label="New folder", hint="e.g. Quality/Suppliers", size="20em"),
                           f"{icon('plus')}Add folder", cls="sform row")
        return out

    # ------------------------------------------------------------ permissions
    def _settings_permissions(self, rules, draft, released, check) -> str:
        perms = sorted((r for r in rules if r["kind"] == "permission"), key=lambda r: (r["resource"], r["subject"], r["action"], r["object"]))
        roles = sorted(r["name"] for r in rules if r["kind"] == "role")
        folders = sorted(r["path"] for r in rules if r["kind"] == "folder")
        procs = self._processes()
        rows = [[f"<span class='pre'>{esc(sentence(p, procs))}</span>" + (" " + chip("deny", "crit") if p["effect"] == "deny" else ""),
                 self._origin(p, released), self._remove("permissions", p)] for p in perms]
        out = f"<p>Who may do what, where. A role may do something in a folder and every folder under it.</p><h2>Permissions <span class='muted'>{len(perms)}</span></h2>"
        out += table(["Rule", "From", ""], rows)
        fid = "add-perm"
        out += self._sform(fid, "permissions", "permission", "add",
                           "<div class='fields'>"
                           + self._select("subject", [(r, r.replace("-", " ")) for r in roles], self._sv(fid, "subject"), label="Role", blank="choose…")
                           + self._select("effect", [("allow", "may"), ("deny", "may not")], self._sv(fid, "effect", "allow"), label="May or may not")
                           + self._select("action_word", [(a, a) for a in ACTIONS], self._sv(fid, "action_word", "read"), label="Do")
                           + self._select("object", [("*", "anything")] + [(o, o.replace("-", " ")) for o in self._objects()], self._sv(fid, "object", "*"), label="What")
                           + self._select("folder", [(f, f) for f in folders], self._sv(fid, "folder"), label="In folder", blank="choose…")
                           + "</div>", f"{icon('plus')}Add permission", cls="sform card")
        return out

    # ------------------------------------------------------------ thresholds and due dates
    def _settings_limits(self, rules, draft, released, check) -> str:
        procs = self._processes()
        ths = sorted((r for r in rules if r["kind"] == "threshold"), key=lambda r: r["name"])
        rows = []
        for t in ths:
            k = rule_key(t)
            fid = f"t-{k}"
            edit = self._sform(fid, "limits", "threshold", "replace",
                               f"<input type='hidden' name='name' value='{esc(t['name'])}'>"
                               + self._input("value", self._sv(fid, "value", f"{t['value']:g}"), label="Value", size="9em")
                               + self._select("unit", [(u, u) for u in UNITS], self._sv(fid, "unit", t["unit"]), label="Unit"),
                               f"{icon('check')}Save", key=k, cls="sform row")
            rows.append([f"<b>{esc(t['name'].replace('-', ' '))}</b><br><span class='pre muted'>{esc(sentence(t, procs))}</span>",
                         edit or esc(f"{t['value']:g} {t['unit']}"), self._origin(t, released), self._remove("limits", t)])
        out = "<h2>Limits and thresholds</h2>" + table(["Name", "Value", "From", ""], rows)
        fid = "add-threshold"
        out += self._sform(fid, "limits", "threshold", "add",
                           "<div class='fields'>" + self._input("name", self._sv(fid, "name"), label="Name", hint="e.g. cog purchase limit", size="18em")
                           + self._input("value", self._sv(fid, "value"), label="Value", size="9em")
                           + self._select("unit", [(u, u) for u in UNITS], self._sv(fid, "unit", "USD"), label="Unit") + "</div>",
                           f"{icon('plus')}Add limit", cls="sform card")
        slas = sorted((r for r in rules if r["kind"] == "sla"), key=lambda r: (r["process"], r["step"]))
        rows = []
        for s in slas:
            k = rule_key(s)
            fid = f"s-{k}"
            edit = self._sform(fid, "limits", "sla", "replace",
                               f"<input type='hidden' name='step_ref' value='{esc(s['process'] + '|' + s['step'])}'>"
                               + self._input("days", self._sv(fid, "days", s["days"]), label="Days", size="6em"),
                               f"{icon('check')}Save", key=k, cls="sform row")
            rows.append([f"<span class='pre'>{esc(sentence(s, procs))}</span>", edit or esc(s["days"]), self._origin(s, released), self._remove("limits", s)])
        out += "<h2>Due dates</h2>" + table(["Rule", "Days", "From", ""], rows)
        steps = []
        for name, p in sorted(procs.items()):
            steps.append((f"{name}|approve", f"{name}: approvals"))
            for st in p.get("steps") or []:
                if st.get("kind") == "checkpoint":
                    steps.append((f"{name}|{st['id']}", f"{name}: {st['title']}"))
        fid = "add-sla"
        out += self._sform(fid, "limits", "sla", "add",
                           "<div class='fields'>" + self._select("step_ref", steps, self._sv(fid, "step_ref"), label="Step", blank="choose…")
                           + self._input("days", self._sv(fid, "days"), label="Due within (days)", size="6em") + "</div>",
                           f"{icon('plus')}Add due date", cls="sform card")
        return out

    # ------------------------------------------------------------ cog rails and profiles
    def _criteria_fields(self, fid: str, c: dict, roles: list[str]) -> str:
        procs = self._processes()
        conds = [(k, p) for k, p in _PHRASES.items() if k != "adversarial_reviews_pass"]  # set by the number below
        return ("<div class='fields'>"
                + self._select("process", [(p, p) for p in sorted(procs)], self._sv(fid, "process", c.get("process", "")), label="May approve in", blank="choose…")
                + self._input("max_amount", self._sv(fid, "max_amount", "" if c.get("max_amount") is None else f"{c['max_amount']:g}"), label="Under (amount)", hint="blank: no limit", size="9em")
                + self._select("currency", [(x, x) for x in CURRENCIES], self._sv(fid, "currency", c.get("currency") or "USD"), label="Currency")
                + self._input("max_per_day", self._sv(fid, "max_per_day", c.get("max_per_day") or ""), label="At most a day", hint="approvals", size="6em")
                + self._input("max_amount_per_month", self._sv(fid, "max_amount_per_month", "" if not c.get("max_amount_per_month") else f"{c['max_amount_per_month']:g}"),
                              label="At most a month", hint="amount", size="9em")
                + self._select("countersign", [(r, r.replace("-", " ")) for r in roles], self._sv(fid, "countersign", c.get("countersign") or ""), label="Countersigned by", blank="nobody")
                + self._input("adversarial_reviews", self._sv(fid, "adversarial_reviews", c.get("adversarial_reviews") or ""), label="Adversarial reviews",
                              hint="independent lenses that must all pass; blank: none", size="6em")
                + "</div>" + self._checks("conditions", conds, self._smulti(fid, "conditions", c.get("conditions") or []), legend="Only when"))

    def _settings_rails(self, rules, draft, released, check) -> str:
        procs = self._processes()
        roles = sorted(r["name"] for r in rules if r["kind"] == "role")
        cogs = sorted(r["principal"] for r in rules if r["kind"] == "member" and r["principal_type"] == "cog")
        profiles = {r["name"]: r for r in rules if r["kind"] == "cog"}
        personas = sorted(r["name"] for r in rules if r["kind"] == "persona")
        from ..model import available_models

        models = available_models()
        model_opts = [(c["value"], c["label"]) for c in models["choices"] if c["available"]]
        out = ("<p>What each cog may approve on its own, and within which limits. Outside its rails a cog declines and the item goes to a "
               "person. Rails with 'its review passes' make the cog review the item with its model first; adversarial reviews make it read the "
               "item several times over, each time with one brief and trying to find a reason not to approve, and sign only if all pass; a "
               "countersignature makes a person sign after it.</p>")
        for cog in cogs:
            crits = [r for r in rules if r["kind"] == "approval_criteria" and r["approver"] == f"cog:{cog}"]
            cards = ""
            for c in crits:
                k = rule_key(c)
                fid = f"c-{k}"
                cards += (f"<div class='card rail'><p class='pre'><b>{esc(sentence(c, procs))}</b></p><p>{self._origin(c, released)}</p>"
                          + (f"<details><summary>{icon('edit')}Edit</summary>"
                             + self._sform(fid, "rails", "approval_criteria", "replace", f"<input type='hidden' name='cog' value='{esc(cog)}'>" + self._criteria_fields(fid, c, roles),
                                           f"{icon('check')}Save rail", key=k) + "</details>" + self._remove("rails", c, "Remove rail") if self._settings_editor() else "")
                          + "</div>")
            if not crits:
                cards = "<p class='muted'>No rails: it approves nothing on its own.</p>"
            fid = f"add-c-{cog}"
            add = self._sform(fid, "rails", "approval_criteria", "add", f"<input type='hidden' name='cog' value='{esc(cog)}'>" + self._criteria_fields(fid, {}, roles),
                              f"{icon('plus')}Add rail")
            prof = profiles.get(cog)
            prof_html = ""
            if prof:
                k = rule_key(prof)
                fid = f"p-{k}"
                opts = model_opts if any(v == prof.get("model") for v, _ in model_opts) else [(prof.get("model") or "", f"{prof.get('model')} (not served now)")] + model_opts
                popts = [(n, n) for n in personas] if prof.get("persona") in personas or not prof.get("persona") else [(prof["persona"], prof["persona"])] + [(n, n) for n in personas]
                prof_html = (f"<details><summary>{icon('robot')}Profile: model {esc(prof.get('model') or 'none')}, persona {esc(prof.get('persona') or 'none')}"
                             f"</summary><p>{self._origin(prof, released)}</p><p class='why'>What it follows, what it may do and its duties come from "
                             "the SOPs that name it or its roles.</p>"
                             + self._sform(fid, "rails", "cog", "replace",
                                           f"<input type='hidden' name='name' value='{esc(cog)}'><div class='fields'>"
                                           + self._select("model", opts, self._sv(fid, "model", prof.get("model") or ""), label="Model (what Ollama serves now)")
                                           + self._select("persona", popts, self._sv(fid, "persona", prof.get("persona") or ""), label="Persona (how it behaves)") + "</div>"
                                           + f"<label class='f'><span>About (what it does)</span><textarea name='about' rows='3'>{esc(self._sv(fid, 'about', prof.get('about') or ''))}</textarea></label>",
                                           f"{icon('check')}Save profile", key=k)
                             + "</details>")
            out += (f"<section class='cog-rails'><h2>{self._avatar(cog, size='sm')} {esc(cog)} <span class='muted'>{len(crits)} rail(s)</span></h2>"
                    f"{prof_html}{cards}"
                    + (f"<details><summary>{icon('plus')}Add a rail for {esc(cog)}</summary>{add}</details>" if add else "") + "</section>")
        return out

    # ------------------------------------------------------------ segregation of duties
    def _settings_sod(self, rules, draft, released, check) -> str:
        procs = self._processes()
        sods = [r for r in rules if r["kind"] == "sod"]
        rows = [[f"<span class='pre'>{esc(sentence(s, procs))}</span>", self._origin(s, released), self._remove("sod", s)] for s in sods]
        out = "<p>Two parts of the same item that must be done by different people (cogs included).</p>" + table(["Rule", "From", ""], rows)
        fid = "add-sod"
        out += self._sform(fid, "sod", "sod", "add",
                           "<div class='fields'>" + self._input("role_a", self._sv(fid, "role_a"), label="The", hint="e.g. author", size="12em")
                           + self._input("role_b", self._sv(fid, "role_b"), label="and the", hint="e.g. approver", size="12em")
                           + self._select("object", [(o, o.replace("-", " ")) for o in self._objects()], self._sv(fid, "object"), label="of a", blank="choose…") + "</div>",
                           f"{icon('plus')}Add rule", cls="sform card")
        return out

    # ------------------------------------------------------------ workflows (read-only here)
    def _settings_workflows(self, rules, draft, released, check) -> str:
        procs = self._processes()
        out = ("<div class='card quiet'>Workflows and their routing are shown here as they read; they are edited in the workflow builder "
               "(coming next), as a proposal like every other setting.</div>")
        for p in sorted((r for r in rules if r["kind"] == "process"), key=lambda r: r["name"]):
            routes = [r for r in rules if r["kind"] in ("routing", "sla") and r["process"] == p["name"]]
            out += (f"<details><summary><b>{esc(p['name'])}</b> <span class='muted'>{len(p.get('steps') or [])} steps, {len(routes)} routes</span></summary>"
                    f"<pre class='pre'>{esc(sentence(p, procs))}</pre><ul>" + "".join(f"<li>{esc(sentence(r, procs))}</li>" for r in routes) + "</ul></details>")
        return out

    # ------------------------------------------------------------ actions
    def _settings_require_edit(self) -> None:
        self._need_release()
        if not self._settings_editor():
            raise ShellError("Only people who are members change the settings (a cog proposes changes with its tools).", 403)

    def _rule_from_form(self, kind: str, key: str, rules: list[dict]) -> dict:
        f = self._f
        if kind == "role":
            name = _slug(f("name"))
            if not name:
                raise ShellError("Name the role.", field="name")
            return {"kind": "role", "name": name}
        if kind == "folder":
            path = "/".join(p.strip() for p in (f("path") or "").split("/") if p.strip())
            if not path:
                raise ShellError("Name the folder.", field="path")
            return {"kind": "folder", "path": path}
        if kind == "member":
            who = _slug(f("principal"))
            if not who:
                raise ShellError("Give the person's user name.", field="principal")
            roles = self._multi("roles")
            if not roles:
                raise ShellError("Give them at least one role.", field="roles")
            return {"kind": "member", "principal": who, "principal_type": f("principal_type") or "person", "roles": roles}
        if kind == "permission":
            role = f("subject")
            if not role:
                raise ShellError("Choose a role.", field="subject")
            if not f("folder"):
                raise ShellError("Choose a folder.", field="folder")
            action = f("action_word") or "read"
            if action not in ACTIONS:
                raise ShellError("Choose what they may do.", field="action_word")
            return {"kind": "permission", "subject": f"role:{role}", "action": action, "object": f("object") or "*",
                    "resource": f"folder:{f('folder')}", "effect": "deny" if f("effect") == "deny" else "allow"}
        if kind == "threshold":
            name = _slug(f("name"))
            if not name:
                raise ShellError("Name the limit.", field="name")
            value = _num(f("value"), "value", positive=False)
            if value is None:
                raise ShellError("Give the value.", field="value")
            unit = f("unit") or "USD"
            if unit not in UNITS:
                raise ShellError("Choose a unit.", field="unit")
            return {"kind": "threshold", "name": name, "value": float(value), "unit": unit}
        if kind == "sla":
            proc, _, step = (f("step_ref") or "").partition("|")
            if not proc or not step:
                raise ShellError("Choose the step.", field="step_ref")
            days = _num(f("days"), "days", integer=True)
            if days is None:
                raise ShellError("Give the number of days.", field="days")
            return {"kind": "sla", "process": proc, "step": step, "days": int(days)}
        if kind == "approval_criteria":
            cog, proc = f("cog"), f("process")
            if not proc:
                raise ShellError("Choose what it may approve.", field="process")
            amount = _num(f("max_amount"), "max_amount")
            per_day = _num(f("max_per_day"), "max_per_day", integer=True)
            per_month = _num(f("max_amount_per_month"), "max_amount_per_month")
            lenses = _num(f("adversarial_reviews"), "adversarial_reviews", integer=True)
            if lenses is not None and not 1 <= int(lenses) <= 6:
                raise ShellError("Adversarial reviews: from 1 to 6, or blank for none.", field="adversarial_reviews")
            rule = {"kind": "approval_criteria", "approver": f"cog:{cog}", "process": proc,
                    "max_amount": float(amount) if amount is not None else None,
                    "currency": (f("currency") or "USD") if (amount is not None or per_month) else None,
                    "conditions": self._multi("conditions"), "countersign": f("countersign") or None}
            if per_day:
                rule["max_per_day"] = int(per_day)
            rule["conditions"] = [c for c in rule["conditions"] if c != "adversarial_reviews_pass"]
            if lenses:
                rule["conditions"].append("adversarial_reviews_pass")
                rule["adversarial_reviews"] = int(lenses)
            if per_month:
                rule["max_amount_per_month"] = float(per_month)
            return rule
        if kind == "sod":
            a, b, obj = _slug(f("role_a")), _slug(f("role_b")), f("object")
            if not a or not b:
                raise ShellError("Name both parts (for example author and approver).", field="role_a" if not a else "role_b")
            if not obj:
                raise ShellError("Choose the kind of item.", field="object")
            return {"kind": "sod", "object": obj, "roles": [a, b]}
        if kind == "cog":
            old = next((r for r in rules if rule_key(r) == key), None) or {}
            if not f("model"):
                raise ShellError("Choose the model the cog comes with.", field="model")
            return {"kind": "cog", "name": f("name"), "about": " ".join((f("about") or old.get("about") or "").split()), "model": f("model"),
                    "persona": f("persona") or old.get("persona") or "", "prompt_from": old.get("prompt_from") or "", "references": old.get("references") or []}
        raise ShellError(f"Settings of kind '{kind}' are not edited here.", 400)

    def post_settings_edit(self):
        self._settings_require_edit()
        done = self._once_done()
        if done:
            return self._redirect(done)
        section = self._f("section") or "overview"
        kind, action, key = self._f("kind"), self._f("action"), self._f("key")
        if action not in ("add", "remove", "replace"):
            raise ShellError("That is not a settings change.", 400)
        rules = self.hub.settings_draft(self.user)["rules"]
        edit = {"action": action, "key": key}
        if action != "remove":
            edit["rule"] = self._rule_from_form(kind, key, rules)
        try:
            d = self.hub.settings_edit(self.user, edit)
        except ValueError as e:
            raise ShellError(str(e)[:1].upper() + str(e)[1:] + ("" if str(e).endswith(".") else "."), 400) from None
        said = d["edits"][-1]["said"]
        log_line(f"settings edit by={self.user} {said[:160]}")
        self._redirect(self._once_finish(href("/settings/" + section, self.user, done=said[:1].upper() + said[1:])))

    def post_settings_check(self):
        self._settings_require_edit()
        self.arg = "overview"
        return self.page_settings(check=self.hub.settings_check(self.user))

    def post_settings_propose(self):
        self._settings_require_edit()
        done = self._once_done()
        if done:
            return self._redirect(done)
        try:
            p = self.hub.settings_propose(self.user, reason=self._f("reason") or "")
        except ValueError as e:
            raise ShellError(str(e)[:1].upper() + str(e)[1:] + ".", 400) from None
        if p.get("change_order"):
            bad = [r for r in p["report"] if not r["ok"]]
            msg = (f"Written into the SOPs on {p['change_order']}: {len(p['report']) - len(bad)} sentence(s) changed"
                   + (f"; {len(bad)} could not be placed, see the change order" if bad else "")
                   + ". Get each revision approved, then send the change order for approval.")
            log_line(f"settings change order {p['change_order']} by={self.user} edits={len(p['report'])}")
            return self._redirect(self._once_finish(href("/record/" + quote(p["change_order"]), self.user, done=msg)))
        errors = sum(1 for x in p["findings"] if x.get("severity") == "error")
        msg = f"Proposed as #{p['number']}: {len(p['changes'])} change(s); the policy owners review it." + (f" {errors} problem(s) stop a release until fixed." if errors else "")
        log_line(f"settings proposal #{p['number']} by={self.user} changes={len(p['changes'])}")
        self._redirect(self._once_finish(href("/proposal/" + quote(p["id"]), self.user, done=msg)))

    def post_settings_discard(self):
        self._settings_require_edit()
        done = self._once_done()
        if done:
            return self._redirect(done)
        self.hub.settings_discard(self.user)
        self._redirect(self._once_finish(href("/settings", self.user, done="Your draft is discarded; the settings show the policy in force.")))
