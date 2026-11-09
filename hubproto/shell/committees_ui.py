"""Groups and committees on screen (change control v2, section 11; the backend is workstream 9's ``hubproto.groups`` and
``hubproto.runner``).

* **The committee tally** on a decision card: each member and how they voted (with their meaning), who is still to vote,
  who is paused and why, the chair and the due date. A member votes with the committee's options (the transitions the
  committee decides); there is no abstention.
* **The chair's intervention**: when a decision is paused (a cog member's criteria do not hold, a member is unavailable,
  late, kept out, or the members chose different ways), the chair (or whoever the pause escalated to) decides in a
  member's place with a meaning, extends the due date, or returns the item (``runner.intervene``). A chair who presses a
  vote is refused and pointed here.
* **Settings > Groups and committees**: groups (listed members, everyone holding some roles, or both) and committees (on a
  group, with a chair and a purpose), each edit a direct change on the person's settings change order once the SOPs are
  documents.
* **What a member may do**: every permission that reaches it (``authz.permissions_of``), with what it comes through: a
  role, a group or its own name.
"""

from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import quote

from .. import authz
from ..rules import rule_key
from ..runner import RunError
from .forms import ShellError
from .handler import details, hidden_as, href, log_line, table
from .icons import icon
from .text import chip, esc, short, when

PAUSE_WORDS = {"criteria": "its approval criteria do not hold", "unavailable": "is not available", "excluded": "may not decide this item",
               "late": "has not answered by the due date", "differ": "the members chose different ways forward"}


class CommitteesMixin:
    # ------------------------------------------------------------ the tally on a decision card
    def _committee_tally_html(self, run: dict) -> str:
        p = run.get("pending") or {}
        cm = p.get("committee") or {}
        if not cm:
            return ""
        votes = cm.get("votes") or {}
        paused = {x.get("member"): x for x in cm.get("paused") or [] if x.get("member")}
        names = {o["id"]: o["name"] for o in p.get("options") or []}
        rows = []
        for m in cm.get("members") or []:
            kind = (cm.get("types") or {}).get(m) or "person"
            v = votes.get(m)
            if v:
                agree = v.get("verdict") == "agree"
                where = (chip("agrees" if agree else "objects", "ok" if agree else "crit")
                         + (f" <span class='why'>({esc(names.get(v.get('option'), v.get('option') or ''))})</span>" if v.get("option") else "")
                         + f" “{esc(short(v.get('meaning') or '', 200))}” <span class='why'>{esc(when(v.get('at')))}"
                         + (f"; decided by {esc(v.get('by'))} in its place" if v.get("intervention") else "") + "</span>")
            elif m in paused:
                x = paused[m]
                where = (chip("paused", "warn") + f" {esc(x.get('reason') or PAUSE_WORDS.get(x.get('kind'), x.get('kind') or ''))}"
                         + f" <span class='why'>({esc(PAUSE_WORDS.get(x.get('kind'), x.get('kind') or ''))}; {esc(', '.join(x.get('interveners') or []) or 'nobody')} intervenes)</span>")
            else:
                where = chip("to vote", "info")
            rows.append([f"{self._avatar(m, size='sm', named=True)} <span class='chip'>{esc(kind)}</span>", where])
        outstanding = [m for m in cm.get("members") or [] if m not in votes]
        general = [x for x in cm.get("paused") or [] if not x.get("member")]
        out = (f"<div class='card quiet' id='tally-{esc(run['id'])}'><h4 style='margin-top:0'>{icon('users')}The {esc(cm.get('name'))} committee decides by consensus</h4>"
               f"<p class='why'>{esc(cm.get('purpose') or '')} Every member agrees on one way forward, or one objection returns it; there is no "
               f"standing aside and nothing passes by default. Chair: <b>{esc(cm.get('chair') or '-')}</b>"
               + (f"; due {self._due_html(cm.get('due'))}" if cm.get("due") else "") + "</p>"
               + table(["Member", "Vote"], rows)
               + f"<p class='why'>{len(votes)} of {len(cm.get('members') or [])} voted"
               + (f"; still to vote: {esc(', '.join(outstanding))}" if outstanding else "") + ".</p>")
        for x in general:
            out += f"<p class='co-marker'><b>Paused:</b> {esc(x.get('reason') or PAUSE_WORDS.get(x.get('kind'), ''))}</p>"
        return out + "</div>"

    def _committee_vote_note(self, run: dict) -> str:
        cm = (run.get("pending") or {}).get("committee") or {}
        return (f"<p class='why'>You vote as a member of the {esc(cm.get('name'))} committee: choose the way forward you agree to, or object with "
                "your reason; your vote is your signature.</p>")

    # ------------------------------------------------------------ the chair's intervention
    def _intervention_html(self, run: dict) -> str:
        """The forms for whoever intervenes in a paused committee decision: decide in a member's place, extend the due
        date, or return the item. Every intervention needs its meaning."""
        user = self.user
        p = run.get("pending") or {}
        cm = p.get("committee") or {}
        if user not in (cm.get("interveners") or []) or self._viewing_cog():
            return ""
        digest = run.get("seen_digest") or self.hub.runner.seen_digest(run)
        mine = self.err is not None and self.route == "/intervene" and self.vals.get("run") == run["id"]
        val = (lambda k, d="": self.vals.get(k, d)) if mine else (lambda k, d="": d)
        err = ""
        if mine:
            self._err_shown = True
            err = f"<p class='err' role='alert'>{esc(self.err[0])}</p>"
        hidden = f"{hidden_as(user)}{self._once()}<input type='hidden' name='run' value='{esc(run['id'])}'><input type='hidden' name='digest' value='{esc(digest)}'>"
        role = "its chair" if user == cm.get("chair") else "the person this pause was escalated to"
        paused = [x for x in cm.get("paused") or [] if x.get("member")]
        fwd = next((o for o in p.get("options") or [] if o.get("verdict") == "approve"), None)
        back = next((o for o in p.get("options") or [] if o.get("verdict") == "reject"), None)
        out = (f"<div class='card warn' id='intervene-{esc(run['id'])}'><h4 style='margin-top:0'>{icon('alert')}The decision is paused: you intervene as {esc(role)}</h4>"
               "<p class='why'>You do not vote here. Decide in a paused member's place (recorded as an intervention with your meaning), extend the "
               "due date (late members and paused cogs are asked again), or return the item for revision.</p>" + err)
        if paused:
            opts = "".join(f"<option value='{esc(x['member'])}'{' selected' if val('member') == x['member'] else ''}>{esc(x['member'])}: "
                           f"{esc(short(x.get('reason') or PAUSE_WORDS.get(x.get('kind'), ''), 90))}</option>" for x in paused)
            v = val("verdict", "agree")
            out += (f"<form method='post' action='/intervene' class='sform card'>{hidden}<input type='hidden' name='action' value='decide'>"
                    f"<fieldset class='checks'><legend>Decide in a member's place</legend>"
                    f"<label class='f'><span>Member</span><select name='member' required>{opts}</select></label>"
                    f"<label class='check'><input type='radio' name='verdict' value='agree'{' checked' if v == 'agree' else ''}> agree"
                    + (f": {esc(fwd['name'])}" if fwd else "") + "</label>"
                    f"<label class='check'><input type='radio' name='verdict' value='object'{' checked' if v == 'object' else ''}> object"
                    + (f": {esc(back['name'])}" if back else "") + "</label></fieldset>"
                    f"<label class='f'><span>Meaning <b aria-hidden='true'>*</b> <i>(why you decide in its place)</i></span>"
                    f"<input name='meaning' required value='{esc(val('meaning') if val('action') == 'decide' else '')}'></label>"
                    f"<div class='actions'><button>{icon('check')}Decide in the member's place</button></div></form>")
        if any(not x.get("member") for x in cm.get("paused") or []):
            ways = "".join(f"<label class='check'><input type='radio' name='transition' value='{esc(o['id'])}'> {esc(o['name'])}</label>" for o in p.get("options") or [])
            out += (f"<form method='post' action='/intervene' class='sform card'>{hidden}<input type='hidden' name='action' value='decide'>"
                    f"<fieldset class='checks'><legend>The members chose different ways: choose the committee's</legend>{ways}</fieldset>"
                    f"<label class='f'><span>Meaning <b aria-hidden='true'>*</b></span><input name='meaning' required></label>"
                    f"<div class='actions'><button>{icon('check')}Decide for the committee</button></div></form>")
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        out += (f"<form method='post' action='/intervene' class='sform card'>{hidden}<input type='hidden' name='action' value='extend'>"
                f"<div class='fields'><label class='f'><span>New due date <b aria-hidden='true'>*</b></span><input type='date' name='due' min='{tomorrow}' required "
                f"value='{esc(val('due'))}'></label>"
                f"<label class='f'><span>Meaning <b aria-hidden='true'>*</b></span><input name='meaning' required value='{esc(val('meaning') if val('action') == 'extend' else '')}'></label></div>"
                f"<div class='actions'><button class='secondary'>{icon('clock')}Extend the due date</button></div></form>")
        out += (f"<form method='post' action='/intervene' class='sform card'>{hidden}<input type='hidden' name='action' value='return'>"
                f"<label class='f'><span>Why it goes back <b aria-hidden='true'>*</b></span><input name='meaning' required value='{esc(val('meaning') if val('action') == 'return' else '')}'></label>"
                f"<div class='actions'><button class='reject' formnovalidate>{icon('x')}Return the item"
                + (f" ({esc(back['name'])})" if back else "") + "</button></div></form>")
        return out + "</div>"

    def post_intervene(self):
        user = self.user
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        run_id = self._f("run")
        if not run_id:
            raise ShellError("No item was named. Go back to your inbox and open the item again.")
        action = self._f("action")
        meaning = self._f("meaning")
        if not meaning:
            raise ShellError("An intervention needs its meaning: say why you intervene.", field="meaning")
        try:
            state = self.hub.runner.intervene(run_id, user, action, member=self._f("member") or None, meaning=meaning,
                                              verdict=self._f("verdict") or None, transition=self._f("transition") or None,
                                              due=self._f("due") or None, payload_digest=self._f("digest") or None)
        except RunError as e:
            raise ShellError(str(e)[:1].upper() + str(e)[1:]) from None
        rec = self.hub.kernel.get(state["record"]) if state.get("record") else None
        words = {"decide": "Decided in the member's place", "extend": f"Due date extended to {self._f('due')}", "return": "Returned for revision"}.get(action, "Done")
        log_line(f"committee intervention run={run_id} action={action} by={user}")
        where = href("/record/" + quote(rec["locator"], safe=":"), user) if rec else href("/inbox", user)
        self._redirect(self._once_finish(where + "&done=" + quote(f"{words}: {rec['number'] if rec else run_id[:8]} is now {state.get('at') or state['status']}.")))

    def _interventions_inbox_html(self) -> str:
        """Decisions paused for this person (the chair, or whoever the pause was escalated to)."""
        user = self.user
        rows = []
        for r in self.hub.runner.pending_for(user):
            cm = (r.get("pending") or {}).get("committee") or {}
            if not cm or user not in (cm.get("interveners") or []):
                continue
            rec = self.hub.kernel.get(r["record"]) if r.get("record") else None
            link = href("/inbox", user, open=r["id"]) + f"#r-{r['id']}"
            why = "; ".join(f"{x.get('member') or 'the members'}: {x.get('reason') or PAUSE_WORDS.get(x.get('kind'), '')}" for x in cm.get("paused") or [])
            rows.append(f"<li>{icon('alert')}<span><b>Intervene</b> on {esc(rec['number'] if rec else r['process'])} ({esc(cm.get('name'))} committee): "
                        f"{esc(short(why, 200))} <a href='{esc(link)}'>Open</a></span></li>")
        if not rows:
            return ""
        return f"<h2 id='interventions'>Committees waiting for you to intervene <span class='muted'>{len(rows)}</span></h2><ul class='notes'>{''.join(rows)}</ul>"

    # ------------------------------------------------------------ what a member may do
    def _member_permissions_html(self, name: str) -> str:
        from ..settings import sentence

        bundle = self.bundle or {}
        perms = authz.permissions_of(bundle, name)
        if not perms:
            return "<p class='muted'>No permission sentence names it, one of its roles or one of its groups.</p>"
        rows = []
        for r in perms:
            kind, _, who = str(r.get("via") or "").partition(":")
            via = {"role": f"its role {who.replace('-', ' ')}", "group": f"the {who} group", "cog": "its own name", "person": "its own name"}.get(kind, r.get("via") or "")
            eff = {"allow": chip("acts", "ok"), "propose": chip("proposes: a person confirms", "warn"), "deny": chip("may not", "crit")}.get(r.get("effect"), chip(r.get("effect")))
            try:
                words = sentence({k: v for k, v in r.items() if k != "via"} | {"kind": "permission"}, bundle.get("processes") or {})
            except Exception:
                words = (r.get("source") or {}).get("text") or ""
            rows.append([esc(words), eff, esc(via), self._rule_link(r.get("source")) if r.get("source") else ""])
        return ("<p class='why'>Every permission that reaches it, and what it comes through. A widening of any of these needs both policy "
                "owners.</p>" + table(["Permission", "", "Through", "Stated in"], rows))

    # ------------------------------------------------------------ Settings > Groups and committees
    def _settings_groups(self, rules, draft, released, check) -> str:
        from .. import groups as gr
        from ..settings import sentence

        members = sorted((r for r in rules if r["kind"] == "member"), key=lambda r: (r["principal_type"], r["principal"]))
        roles = sorted(r["name"] for r in rules if r["kind"] == "role")
        people = [m["principal"] for m in members if m["principal_type"] == "person"]
        groups = sorted((r for r in rules if r["kind"] == "group"), key=lambda r: r["name"])
        committees = sorted((r for r in rules if r["kind"] == "committee"), key=lambda r: r["name"])
        holding = {m["principal"]: set(m.get("roles") or []) for m in members}

        def now(g: dict) -> list[str]:
            out = [x for x in g.get("members") or [] if x in holding]
            out += [p for p in sorted(holding) if set(g.get("roles") or []) & holding[p] and p not in out]
            return out

        def group_fields(fid: str, g: dict | None) -> str:
            chosen = self._smulti(fid, "members", (g or {}).get("members") or [])
            chosen_roles = self._smulti(fid, "roles", (g or {}).get("roles") or [])
            return ((self._input("name", self._sv(fid, "name"), label="Name", hint="e.g. quality-board", size="16em") if g is None else
                     f"<input type='hidden' name='name' value='{esc(g['name'])}'>")
                    + self._checks("members", [(m["principal"], f"{m['principal']} ({m['principal_type']})") for m in members], chosen, legend="Members listed")
                    + self._checks("roles", [(r, r.replace("-", " ")) for r in roles], chosen_roles, legend="And everyone holding"))

        procs = self._processes()
        rows = []
        for g in groups:
            k = rule_key(g)
            fid = f"g-{k}"
            perms = [r for r in rules if r["kind"] == "permission" and r.get("subject") == f"group:{g['name']}"]
            edit = self._sform(fid, "groups", "group", "replace", group_fields(fid, g), f"{icon('check')}Save the group", key=k, cls="sform card")
            body = (f"<details><summary><b>{esc(g['name'])}</b> <span class='muted'>{esc(sentence(g, procs).lstrip('- '))}</span></summary>{edit}"
                    f"{self._remove('groups', g, 'Remove the group')}</details>" if self._settings_editor() else f"<b>{esc(g['name'])}</b>")
            rows.append([body, esc(", ".join(now(g)) or "nobody"),
                         "<br>".join(esc(sentence(r, procs)) for r in perms) or "<span class='muted'>none</span>", self._origin(g, released)])
        out = ("<p>A group is a named set of members, people and cogs: listed, or everyone holding some roles (it follows role changes), or "
               "both. A group may carry permissions as a role does, and be named in a Roles and responsibilities table. A committee is defined on a "
               "group, with a person as its chair, and decides by consensus.</p>")
        out += f"<h2>Groups <span class='muted'>{len(groups)}</span></h2>" + table(["Group", "Members now", "Permissions it carries", "From"], rows, "No group yet.")
        out += self._sform("add-group", "groups", "group", "add", group_fields("add-group", None), f"{icon('plus')}Add the group", cls="sform card")
        out += "<p class='why'>Give a group a permission under Permissions (choose it as the role), or name it in an SOP. A group change is a widening: both policy owners approve it.</p>"
        rows = []
        gnames = [g["name"] for g in groups]
        for c in committees:
            k = rule_key(c)
            fid = f"c-{k}"
            g = next((x for x in groups if x["name"] == c.get("group")), None)
            members_now = now(g) if g else list(c.get("members") or [])
            edit = self._sform(fid, "groups", "committee", "replace", self._committee_fields(fid, c, gnames, people), f"{icon('check')}Save the committee",
                               key=k, cls="sform card")
            body = (f"<details><summary><b>{esc(c['name'])}</b> <span class='muted'>{esc(c.get('purpose') or '')}</span></summary>{edit}"
                    f"{self._remove('groups', c, 'Remove the committee')}</details>" if self._settings_editor() else f"<b>{esc(c['name'])}</b>")
            rows.append([body, esc(c.get("group") or "-"), esc(c.get("chair") or "-"), esc(", ".join(members_now) or "nobody"), self._origin(c, released)])
        out += f"<h2>Committees <span class='muted'>{len(committees)}</span></h2>" + table(["Committee", "Group", "Chair", "Members now", "From"], rows, "No committee yet.")
        out += self._sform("add-committee", "groups", "committee", "add", self._committee_fields("add-committee", None, gnames, people),
                           f"{icon('plus')}Add the committee", cls="sform card")
        out += ("<p class='why'>Every member agrees, or one objection returns the item; there is no standing aside. When a member cannot answer, "
                "or a cog's criteria do not hold, the decision pauses and the chair intervenes.</p>")
        return out

    def _committee_fields(self, fid: str, c: dict | None, gnames: list[str], people: list[str]) -> str:
        return ("<div class='fields'>"
                + (self._input("name", self._sv(fid, "name"), label="Name", hint="e.g. quality-board", size="16em") if c is None else
                   f"<input type='hidden' name='name' value='{esc(c['name'])}'>")
                + self._select("group", [(g, g) for g in gnames], self._sv(fid, "group", (c or {}).get("group") or ""), label="On the group", blank="choose…")
                + self._select("chair", [(p, p) for p in people], self._sv(fid, "chair", (c or {}).get("chair") or ""), label="Chair (a person)", blank="choose…")
                + "</div>" + self._input("purpose", self._sv(fid, "purpose", (c or {}).get("purpose") or ""), label="Purpose", hint="what it decides"))

    def _group_rule_from_form(self, kind: str) -> dict:
        from .settings_ui import _slug

        f = self._f
        name = _slug(f("name"))
        if not name:
            raise ShellError("Name it.", field="name")
        if kind == "group":
            members, roles = self._multi("members"), self._multi("roles")
            if not members and not roles:
                raise ShellError("A group lists its members or says everyone holding a role: tick at least one.", field="members")
            return {"kind": "group", "name": name, "members": members, "roles": roles}
        group, chair = f("group"), f("chair")
        if not group:
            raise ShellError("Choose the group the committee is defined on.", field="group")
        if not chair:
            raise ShellError("Choose its chair: a person who intervenes when a decision pauses.", field="chair")
        return {"kind": "committee", "name": name, "group": group, "chair": chair, "purpose": " ".join((f("purpose") or "").split()),
                "members": [], "quorum": 0}
