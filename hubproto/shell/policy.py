"""Policy-owner surfaces: policy versions and what changed between them, the policies (Frames), the permission
oracle, people and roles, and the conformance checklist."""

from __future__ import annotations

from .. import authz
from ..compile import CompileError
from ..frames import FrameError
from ..rules import rule_key
from .forms import ShellError
from urllib.parse import quote

from .icons import icon
from .handler import details, hidden_as, href, log_line, table
from .text import chip, esc, friendly, join_words, money, role_words, tag, type_name, version_label, when

RULE_WORDS = {"permission": "permission", "routing": "routing", "threshold": "threshold", "sla": "deadline", "approval_criteria": "automatic approval",
              "member": "member", "role": "role", "folder": "folder", "process": "process", "sod": "segregation of duties"}


def rule_identity(rule: dict) -> tuple:
    """What a rule is about, so a removed and an added rule on the same subject read as one change."""
    k = rule.get("kind")
    keys = {"permission": ("subject", "action", "object", "resource"), "threshold": ("name",), "routing": ("process", "step", "assignee"),
            "sla": ("process", "step"), "approval_criteria": ("approver", "process"), "member": ("principal",), "process": ("name",),
            "role": ("name",), "folder": ("path",), "sod": ("object",)}.get(k, ())
    return (k,) + tuple(str(rule.get(x)) for x in keys)


def sentence_diff(old_rules: list[dict], new_rules: list[dict]) -> dict[str, list]:
    """Sentence-level diff between two rule sets: added, removed and changed (a pair on the same subject)."""
    old = {rule_key(r): r for r in old_rules}
    new = {rule_key(r): r for r in new_rules}
    added = [new[k] for k in new if k not in old]
    removed = [old[k] for k in old if k not in new]
    by_identity: dict[tuple, dict] = {}
    for r in removed:
        by_identity.setdefault(rule_identity(r), r)
    changed, plain_added = [], []
    used = set()
    for r in added:
        ident = rule_identity(r)
        o = by_identity.get(ident)
        if o is not None and id(o) not in used and (o.get("source") or {}).get("text") != (r.get("source") or {}).get("text"):
            changed.append((o, r))
            used.add(id(o))
        else:
            plain_added.append(r)
    plain_removed = [r for r in removed if id(r) not in used]
    key = lambda r: ((r.get("source") or {}).get("frame", ""), (r.get("source") or {}).get("line", 0))
    return {"added": sorted(plain_added, key=key), "removed": sorted(plain_removed, key=key), "changed": sorted(changed, key=lambda p: key(p[1]))}


def _filled(text) -> str:
    """A template placeholder ({{COMPANY_NAME}}) shown as not filled in, never as raw braces."""
    import re as _re

    t = str(text or "")
    return _re.sub(r"\{\{\s*([A-Z_]+)\s*\}\}", lambda m: f"<i class='muted'>[{esc(m.group(1).lower().replace('_', ' '))}: not filled in]</i>", esc(t))


class PolicyMixin:
    def _require_policy_owner(self, what: str) -> None:
        if not self._is_member():
            raise ShellError(f"{self.user} is not a member of the organization; {what} is shown to policy owners.", 403)
        if not self._policy_owner():
            owners = [p for p, m in self.bundle["members"].items() if "policy-owner" in m["roles"] and m["type"] == "person"]
            raise ShellError(f"{what} is for policy owners ({join_words(owners, 'and') or 'none yet'}); you are {join_words(role_words(r) for r in self.bundle['members'][self.user]['roles'])}.", 403)

    # ------------------------------------------------------------- versions
    def page_releases(self):
        user = self.user
        cur = self.meta
        if not self._is_member():
            return self._page("Policy versions", f"<h1>Policy versions</h1><div class='card'><b>{esc(user)} is not a member</b> of the organization; the policy is not shown.</div>")
        releases = self.hub.releases.list()
        rows = []
        for m in reversed(releases):
            label = version_label(m, releases)
            parent = version_label(self.hub.releases.get(m["parent"]), releases) if m.get("parent") and any(x["digest"] == m["parent"] for x in releases) else "none (first version)"
            rows.append([f'<a href="{href("/release/" + m["digest"][:16], user)}">{esc(label)}</a>' + (" " + chip("in effect", "ok") if cur and cur["digest"] == m["digest"] else ""),
                         esc(when(m["effective_from"])), esc(m["released_by"]), esc(parent),
                         f'<a href="{href("/release/" + m["digest"][:16], user)}">What changed</a>', details("digest", f"<code>{esc(m['digest'])}</code>")])
        n_open = sum(1 for q in self.hub.proposals.list() if q.get("status") == "open")
        body = "<h1>Policy versions</h1><p class='why'>Every released version of the policy, newest first. Open one to see what changed and who approved it.</p>" \
            + (f"<p><a class='button ghost' href='{href('/proposals', user)}'>{icon('layers')}Policy proposals ({n_open} open)</a></p>") \
            + table(["Version", "Effective from", "Released by", "Based on", "Changes", "Digest"], rows, "No release yet.")
        if self.bundle:
            bundle = self.bundle
            rsent = []
            for r in bundle["routing"]:
                rsent.append(f"<li>{esc(r['source']['text'])} <span class='why'>({self._rule_link(r['source'])}; {esc(self._process_name(r['process']))}{', only notifies' if r.get('mode') == 'notify' else ''})</span></li>")
            body += "<h2>Routing in effect</h2><ul>" + "".join(rsent) + "</ul>"
            body += "<h2>Automatic approvals</h2><ul>" + "".join(
                f"<li>{esc(c['source']['text'])} <span class='why'>({self._rule_link(c['source'])}; limit {esc(money(c['max_amount'], c.get('currency')) if c.get('max_amount') is not None else 'none')})</span></li>"
                for c in bundle["approval_criteria"]) + "</ul>"
            body += "<h2>Processes</h2>"
            for name, wf in bundle["workflows"].items():
                cps = "; ".join(f"{c['title']} by the {role_words(c['role'])}" + (f", due within {c['sla_days']} days" if c.get("sla_days") else "") for c in wf["checkpoints"]) or "no approvals"
                body += f"<div class='card'><b>{esc(self._process_name(name))}</b> {tag(name)} closes as {self._state_chip(wf['terminal'], wf)}<br>" \
                        + " &rarr; ".join(self._state_chip(s, wf) for s in wf["states"]) + f"<div class='why'>Approvals: {esc(cps)}</div></div>"
            unit = lambda v: money(v["value"], v["unit"]) if v["unit"] in ("USD", "EUR", "GBP", "CAD") else f"{v['value']:g} {v['unit']}"
            body += "<h2>Thresholds</h2>" + table(["Name", "Value", "Sentence"], [[esc(k.replace("-", " ")), esc(unit(v)), esc(v["source"]["text"]) + f" <span class='why'>({self._rule_link(v['source'])})</span>"] for k, v in bundle["thresholds"].items()])
        self._page("Policy versions", body, crumbs=[("Policy versions", None)])

    def page_release(self):
        user = self.user
        if not self._is_member():
            raise ShellError(f"{user} is not a member of the organization; the policy is not shown.", 403)
        try:
            meta = self.hub.releases.get(self.arg)
        except KeyError:
            raise ShellError(f"There is no policy version starting with '{self.arg}'.", 404) from None
        releases = self.hub.releases.list()
        label = version_label(meta, releases)
        in_effect = self.meta and self.meta["digest"] == meta["digest"]
        parent_meta = next((x for x in releases if x["digest"] == meta.get("parent")), None)
        new_rules = self.hub.releases.rules(meta["digest"])
        old_rules = self.hub.releases.rules(parent_meta["digest"]) if parent_meta else []
        diff = sentence_diff(old_rules, new_rules)
        proposal = None
        if meta.get("proposal_id"):
            try:
                proposal = self.hub.proposals.load(meta["proposal_id"])
            except (CompileError, Exception):
                proposal = None
        decisions = {c["id"]: c for c in (proposal or {}).get("changes", [])}

        def who(rule: dict, op: str) -> str:
            c = decisions.get(f"{op}:{rule_key(rule)}")
            if not c:
                return "<span class='muted'>no decision recorded</span>"
            ds = "; ".join(f"{d['by']} {d['verdict']}d on {when(d['at'])}: “{d.get('meaning', '')}”" for d in c.get("decisions", []))
            return esc(ds or "pending") + (" " + chip("widening", "warn") if c.get("widening") else "")

        def cite(rule: dict) -> str:
            src = rule.get("source") or {}
            return f"{self._rule_link(src)}"

        body = f"""<h1>{esc(label[0].upper() + label[1:])} {chip('in effect', 'ok') if in_effect else ''}</h1>
<div class="card">Released by {esc(meta['released_by'])}, effective from {esc(when(meta['effective_from']))}; based on {esc(version_label(parent_meta, releases)) if parent_meta else 'nothing (the first version)'};
proposal <code>{esc(meta.get('proposal_id') or '-')}</code>{(' reviewed by ' + esc(', '.join(proposal.get('reviewers') or []))) if proposal else ''}.
{details("digest", f"<code>{esc(meta['digest'])}</code>")}</div>"""
        if not parent_meta:
            body += "<h2>What changed</h2><p class='why'>This is the first version: every sentence below was added.</p>"
        else:
            body += f"<h2>What changed since {esc(version_label(parent_meta, releases))}</h2>"
        if diff["changed"]:
            body += "<h3>Changed</h3>" + table(["Policy and line", "Before", "After", "Kind", "Decisions"],
                                              [[cite(n), f"<span class='diff'><del>{esc((o.get('source') or {}).get('text', ''))}</del></span>", f"<span class='diff'><ins>{esc((n.get('source') or {}).get('text', ''))}</ins></span>",
                                                esc(RULE_WORDS.get(n.get("kind"), n.get("kind"))), who(n, "add") + "<br>" + who(o, "remove")] for o, n in diff["changed"]])
        if diff["added"]:
            body += "<h3>Added</h3>" + table(["Policy and line", "Sentence", "Kind", "Decisions"],
                                            [[cite(r), f"<span class='diff'><ins>{esc((r.get('source') or {}).get('text', ''))}</ins></span>", esc(RULE_WORDS.get(r.get("kind"), r.get("kind"))), who(r, "add")] for r in diff["added"]])
        if diff["removed"]:
            body += "<h3>Removed</h3>" + table(["Policy and line (as it was)", "Sentence", "Kind", "Decisions"],
                                              [[esc(f"{(r.get('source') or {}).get('frame')} line {(r.get('source') or {}).get('line')}"), f"<span class='diff'><del>{esc((r.get('source') or {}).get('text', ''))}</del></span>", esc(RULE_WORDS.get(r.get("kind"), r.get("kind"))), who(r, "remove")] for r in diff["removed"]])
        if not any(diff.values()):
            body += "<p class='muted'>No sentence changed; the version was released again with the same rules.</p>"
        body += self._release_divergences_html(meta)
        changed_frames = []
        old_sha = {f["name"]: f.get("sha256") for f in (parent_meta or {}).get("frames", [])}
        for f in meta.get("frames", []):
            if old_sha.get(f["name"]) != f.get("sha256"):
                changed_frames.append(f)
        body += "<h2>Policies in this version</h2>" + table(["Policy", "Version", "Scope", "Maintainer", "Changed"],
                                                          [[f'<a href="{href("/frame/" + f["name"], user)}">{esc(f["name"])}</a>', esc(f.get("version")), esc(f.get("scope")), esc(f.get("maintainer")),
                                                            chip("changed", "warn") if f in changed_frames else ""] for f in meta.get("frames", [])])
        self._page(label, body, crumbs=[("Policy versions", href("/releases", user)), (label, None)])

    # ------------------------------------------------------------- policies
    def page_frames(self):
        user = self.user
        if not self._is_member():
            raise ShellError(f"{user} is not a member of the organization; the policies are not shown.", 403)
        try:
            frames = list(self.hub.frames.frames.values())
        except FrameError as e:
            raise ShellError(f"The policies on disk could not be read: {e}. Fix that file; the release in effect still applies.", 503) from None
        rows = [[f"<a href='{href('/frame/' + f.name, user)}'>{esc(f.name)}</a>", esc(f.version), esc(f.scope), _filled(f.maintainer), esc(", ".join(f.inherits) or "-"), _filled(f.description)]
                for f in frames]
        self._page("Policies", "<h1>Policies</h1>" + self._sop_conversion_card()
                   + "<p class='why'>The policy and procedure documents (Frames) the release is compiled from, as they are on disk now.</p>"
                   + table(["Policy", "Version", "Scope", "Maintainer", "Inherits", "Description"], rows), crumbs=[("Policies", None)])

    def _sop_conversion_card(self) -> str:
        """Where the rules come from, and for a policy owner the button that makes the SOPs the documents they are compiled from."""
        from .. import sopdocs

        user = self.user
        if self.hub.sop_source():
            docs = [d for d in self.hub.kernel.query(type_="document") if str(d["fields"].get("lineage", "")).startswith(sopdocs.LINEAGE) and d["state"] == "effective"]
            return (f"<div class='card ok' role='status'>{icon('check-circle')} <b>The rules are compiled from {len(docs)} SOP and policy documents.</b> "
                    f"Change a rule by revising its SOP on a <a href='{href('/change-orders', user)}'>change order</a>. "
                    f"<a href='{href('/scopes', user)}'>Which Frames depend on which SOPs</a>.</div>")
        owner = "policy-owner" in authz.roles_of(self.bundle or {}, user)
        out = (f"<div class='card' id='convert'><h2 style='margin-top:0'>{icon('layers')}Make the SOPs the source</h2>"
               "<p>The rules are compiled from the Frame files today. Converting makes each Frame a document in the library: policies in "
               "Policy, SOPs and work instructions in Quality/Controlled Documents, personas in Policy. From then on the hub compiles its "
               "rules from those documents, and a rule changes through a change order that carries the SOP's revision.</p>")
        if not owner:
            return out + "<p class='why'>A policy owner converts them.</p></div>"
        blockers = sopdocs.conversion_blockers(self.hub)
        if blockers:
            return out + ("<p><b>Not yet:</b></p><ul>" + "".join(f"<li>{esc(b)}</li>" for b in blockers) + "</ul></div>")
        n = len(self.hub.released_frames().frames)
        return out + (f"<p class='why'>Checked: all {n} Frames read back exactly from their documents, so no rule and no wording changes.</p>"
                      f"<form method='post' action='/sops-convert'>{hidden_as(user)}{self._once()}"
                      f"<label class='check'><input type='checkbox' name='confirm' value='yes' required> I understand that rules will then change only "
                      f"through SOP revisions on change orders</label><div class='actions'><button>{icon('layers')}Convert the SOPs to documents</button></div></form></div>")

    def post_sops_convert(self):
        from .. import sopdocs

        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        if self._f("confirm") != "yes":
            raise ShellError("Tick the box to confirm.", field="confirm")
        try:
            out = sopdocs.convert_from_ui(self.hub, by=self.user)
        except ValueError as e:
            raise ShellError(str(e)[:1].upper() + str(e)[1:] + ".", 403 if "only a policy owner" in str(e) else 400) from None
        log_line(f"sops converted by={self.user} documents={len(out['made'])} changes={out['changes']}")
        self._redirect(self._once_finish(href("/frames", self.user, done=f"{len(out['made'])} SOP and policy documents are in the library; "
                                                                         f"compiling from them changes {out['changes']} rule(s).")))

    def page_frame(self):
        if not self._is_member():
            raise ShellError(f"{self.user} is not a member of the organization; the policies are not shown.", 403)
        try:
            frame = self.hub.frames.get(self.arg)
        except FrameError as e:
            if "unknown Frame" in str(e):
                raise ShellError(f"There is no policy named '{self.arg}'. The list is under Policies.", 404) from None
            raise ShellError(f"The policies on disk could not be read: {e}. Fix that file; the release in effect still applies.", 503) from None
        file_lines = frame.file_lines
        if self.hub.sop_source():  # once the SOPs are documents, the text the release in force was compiled from
            try:
                file_lines = self.hub.released_frames().get(frame.name).file_lines
            except Exception:
                pass
        body = (f"<h1>{esc(frame.name)} {tag('v' + str(frame.version))}</h1><div class='card'>{esc(frame.description)}<br><span class='why'>scope {esc(frame.scope)}; maintained by {esc(frame.maintainer)}; "
                f"visibility {esc(frame.visibility)}; inherits {esc(', '.join(frame.inherits) or 'nothing')}; file {esc(str(frame.path.name))}</span></div>"
                + self._divergence_card(frame.name)
                + f"<p class='why'>Line numbers are the file's own, frontmatter included, so a citation such as “line 34” matches an editor.</p>"
                + self._lines_with_markers(frame.name, file_lines))
        proposed = [q for q in self.hub.proposals.list() if q.get("status") == "open" and frame.name in (q.get("overrides") or {})]
        banner = "".join(f"<div class='card warn' role='status'>A change to this policy is proposed: <a href='{href('/proposal/' + q['id'], self.user)}'>"
                         f"proposal #{esc(q.get('number'))}</a>.</div>" for q in proposed)
        derived = self._frame_derived_html(frame.name)
        if derived:
            body = derived + body
        else:
            body = banner + (f"<p><a class='button' href='{href('/frame-doc/' + frame.name, self.user)}'>{icon('file')}Open as a document</a> "
                    f"<span class='why'>to propose a change: Edit this policy makes a draft the policy owners review and release.</span></p>") + body
        body = self._dependency_html(frame.name) + body
        self._page(frame.name, body, crumbs=[("Policies", href("/frames", self.user)), (frame.name, None)])

    # ----------------------------------------------------------------- why
    def _why_item(self, rec: dict, principal: str) -> str:
        """'Why not me?' for an item, from the hub's own answer (permission, segregation of duties, countersignature,
        whom the approval waits for); the shell only adds the route's citation as a link."""
        out = self.hub.why_item(principal, rec["locator"])
        run = self.hub.runner.run_for_record(rec["locator"])
        pending = (run or {}).get("pending") or {}
        head = ""
        if out.get("gate"):
            head = (f"<p>{esc(rec['number'])} waits at <b>{esc(out['gate'])}</b> for <b>{esc(', '.join(out.get('waiting_for') or []) or 'nobody')}</b>"
                    + (f", routed by {self._rule_html(pending.get('route_rule'))}" if pending.get("route_rule") else "") + ".</p>")
        if out.get("may"):
            return f"<div class='card ok'>{head}<p><b>{esc(principal)} may decide it now:</b> open it from the inbox.</p></div>"
        reasons = "".join(f"<li>{self._human(friendly(r))}</li>" for r in out.get("reasons") or [])
        return f"<div class='card'>{head}<p><b>{esc(principal)} may not decide it now:</b></p><ul>{reasons}</ul></div>"

    def page_why(self):
        user = self.user
        principal = (self.q.get("principal", [""])[0] or "").strip() or user
        about = self.q.get("record", [""])[0]
        if about:
            rec = self.hub.kernel.get(about)
            if rec is None or not self._can_read(rec["folder"])["allowed"]:
                raise ShellError("There is no item by that name you may read.", 404)
            body = f"<h1>Why not {esc(principal)}?</h1><p class='why'>What {esc(rec['number'])} waits for, and what stands between {esc(principal)} and deciding it.</p>"
            body += self._why_item(rec, principal)
            return self._page("Why", body, crumbs=[(rec["number"], href(("/document/" if rec["type"] == "document" else "/record/") + quote(rec["locator"], safe=":"), user)),
                                                   ("Why", None)])
        action = self.q.get("action", ["create"])[0] or "create"
        obj = self.q.get("object", ["bill"])[0] or "bill"
        folder = self.q.get("folder", ["Finance/Payables"])[0] or "Finance/Payables"
        d = self.hub.why(principal, action, obj, folder)
        objects = sorted({t.get("object", n) for n, t in self.hub.kernel.types.items() if n != "$comment"} | {"*"})
        folders = self.bundle["folders"] if self.bundle else [folder]
        members = sorted(self.bundle["members"]) if self.bundle else []
        if principal not in members:
            members = [principal] + members
        obj_words = {o: ("anything" if o == "*" else type_name(next((n for n, t in self.hub.kernel.types.items() if t.get("object") == o), o)).lower()) for o in objects}
        body = f"""<h1>Why?</h1><p class="why">Ask whether a person may act, and see the policy sentence behind the answer.</p><form method="get" action="/why" class="card">{hidden_as(user)}
<div class="fields"><label class="f"><span>Who</span><select name="principal">{''.join(f'<option value="{esc(m)}"{" selected" if m == principal else ""}>{esc(m)}</option>' for m in members)}</select></label>
<label class="f"><span>Action</span><select name="action">{''.join(f'<option{" selected" if a == action else ""}>{a}</option>' for a in ("create", "read", "approve", "update", "delete"))}</select></label>
<label class="f"><span>What</span><select name="object">{''.join(f'<option value="{esc(o)}"{" selected" if o == obj else ""}>{esc(obj_words[o])}</option>' for o in objects)}</select></label>
<label class="f"><span>Where</span><select name="folder">{''.join(f'<option value="{esc(f)}"{" selected" if f == folder else ""}>{esc(f)}</option>' for f in folders)}</select></label></div>
<div class="actions"><button>Ask</button></div></form>
<div class="card" role="status">May <b>{esc(principal)}</b> {esc(action)} {esc(obj_words.get(obj, obj))} in {esc(folder)}? {chip('allowed' if d['allowed'] else 'denied')}<br>{self._why_html(d, action, obj, folder, principal=principal)}</div>"""
        if d.get("rule"):
            src = d["rule"]["source"]
            body += f"<p><a href='{href('/frame/' + src['frame'], user)}#L{src['line']}'>Open {esc(src['frame'])} at line {src['line']}</a>: “{esc(src['text'])}”</p>"
        self._page("Why", body, crumbs=[("Why", None)])

    # -------------------------------------------------------- people and roles
    def page_admin(self):
        self._require_policy_owner("People and roles")
        bundle = self.bundle
        people = [(p, m) for p, m in bundle["members"].items() if m["type"] == "person"]
        cogs = [(p, m) for p, m in bundle["members"].items() if m["type"] == "cog"]
        crit = {c["approver"].partition(":")[2]: c for c in bundle.get("approval_criteria", [])}
        body = "<h1>People and roles</h1><p class='why'>Who is a member of the organization and which roles they hold, as written in the org model. To change a role, edit the org model and release a new policy version.</p>"
        body += table(["Person", "Roles", "Written in"], [[esc(p), " ".join(tag(role_words(r)) for r in m["roles"]), self._rule_link(m.get("source"))] for p, m in people], "No release in effect.", row_header=True)
        body += "<h2>Cogs</h2><p class='why'>Cogs act within fixed criteria; they never sign outside them, and the owner is told when one decides.</p>"
        body += table(["Reviewer", "Roles", "May approve", "Limit"], [[esc(p), " ".join(tag(role_words(r)) for r in m["roles"]),
                                                                        (esc(crit[p]["source"]["text"]) + f" <span class='why'>({self._rule_link(crit[p]['source'])})</span>") if p in crit else "nothing on its own",
                                                                        esc(money(crit[p]["max_amount"], crit[p].get("currency")) if p in crit and crit[p].get("max_amount") is not None else "-")] for p, m in cogs], row_header=True)
        dev = "<h3>Cog catalog</h3>" + table(["Cog", "Version", "Kind", "Reaches", "Tasks", "Description"],
                                             [[esc(c["id"]), esc(c["version"]), esc(c["kind"]), esc(", ".join(c["reaches"])), esc(", ".join(c["tasks"])), esc(c["description"])]
                                              for c in self.hub.registry.cards()])
        dev += "<h3>Record types</h3>" + table(["Type", "Prefix", "Object", "Fields", "Required by state", "System fields", "Decision fields", "Retention"],
                                               [[esc(type_name(n)), esc(t["prefix"]) + ("-YYYY" if t.get("yearly") else ""), esc(t["object"]), esc(", ".join(t["fields"])),
                                                 esc("; ".join(f"{s}: {', '.join(v)}" for s, v in t.get("required_by_state", {}).items()) or "-"),
                                                 esc(", ".join(t.get("system_fields") or []) or "-"), esc(", ".join(t.get("decision_fields") or []) or "-"), esc(t.get("retention"))]
                                                for n, t in self.hub.kernel.types.items() if n != "$comment"])
        dev += "<h3>Retention classes</h3>" + table(["Class", "Years", "Evidence a run must hold"], [[esc(n), esc(r["years"]), esc(", ".join(r["evidence"]))] for n, r in self.hub.kernel.retention.items()])
        dev += "<h3>Link vocabulary</h3>" + table(["Relation", "Inverse"], [[esc(k), esc(v)] for k, v in self.hub.kernel.link_vocab.items()])
        body += details("Developer details: cog catalog, record types, retention, link vocabulary", dev)
        self._page("People and roles", body, crumbs=[("People and roles", None)])

    # ------------------------------------------------------------ conformance
    def _divergence_rows(self, divs: list[dict]) -> list[list[str]]:
        return [[esc(d.get("id")), self._co_frame_line(d.get("frame"), d.get("line")) + (f"<div class='why'>“{esc(d.get('prose') or '')}”</div>" if d.get("prose") else ""),
                 esc(self._say(d.get("in_force")) if d.get("in_force") else "nothing (the rule the line states is not in force)"),
                 f"{esc(d.get('accepted_by') or '-')} on {self._co_number_link(d.get('co'))}" + (f"<div class='why'>“{esc(d.get('meaning') or '')}”</div>" if d.get("meaning") else ""),
                 esc(d.get("change_request") or "-"), "<span style='white-space:nowrap'>" + chip(d.get("status", "open"), "warn" if d.get("status", "open") == "open" else "ok") + "</span>"] for d in divs]

    def _release_divergences_html(self, meta: dict) -> str:
        """On the release diff: the change order that released it, its divergences (configuration differing from the prose)."""
        divs = list(meta.get("divergences") or [])
        resolved = list(meta.get("divergences_resolved") or [])
        if not divs and not resolved and not meta.get("change_order"):
            return ""
        out = (f"<h2 id='divergences'>Divergences <span class='muted'>{len(divs)}</span></h2>"
               + (f"<p class='why'>Released with {self._co_number_link(meta.get('change_order'))}.</p>" if meta.get("change_order") else ""))
        out += table(["Divergence", "SOP line", "In force", "Accepted by", "Change request", "Status"], self._divergence_rows(divs),
                     "None: the configuration in force is what the prose says.")
        if resolved:
            out += f"<p class='why'>Resolved by this release: {esc(', '.join(resolved))}.</p>"
        return out

    def _conformance_divergences_html(self) -> str:
        divs = self._divergences_of()
        out = (f"<h2 id='divergences'>Divergences <span class='muted'>{len(divs)}</span></h2><p class='why'>Lines of the SOPs where the configuration in "
               "force differs from the prose, accepted on a change order. Each stays until a change order resolves it.</p>")
        return out + table(["Divergence", "SOP line", "In force", "Accepted by", "Change request", "Status"], self._divergence_rows(divs),
                           "None: the configuration in force is what the SOPs say.")

    def page_conformance(self):
        try:
            from .. import profile  # type: ignore
        except ImportError:
            profile = None
        if profile is None or not hasattr(profile, "conformance"):
            return self._page("Conformance", "<h1>Conformance</h1><div class='card'>The profile module (<code>hubproto.profile</code>) is not installed; no conformance checklist is available.</div>")
        result = profile.conformance(self.hub)
        if not isinstance(result, dict) or "titles" not in result:
            return self._page("Conformance", "<h1>Conformance</h1>" + f"<pre>{esc(str(result))}</pre>")
        titles, covered = result.get("titles", {}), result.get("covered", {})
        rows = []
        for clause, title in titles.items():
            frames = covered.get(clause) or []
            rows.append([esc(clause), esc(title), chip("covered", "ok") if frames else chip("not covered", "crit"),
                         ", ".join(f'<a href="{href("/frame/" + f, self.user)}">{esc(f)}</a>' for f in frames) or "<span class='muted'>no policy claims it</span>"])
        n_cov, n_all = len(covered), len(titles)
        body = (f"<h1>Conformance</h1><p class='why'>Profile {esc(result.get('profile', ''))}: {n_cov} of {n_all} clauses are claimed by a policy under "
                f"{self._version_html(result.get('release'))}.</p>" + self._conformance_divergences_html()
                + table(["Clause", "Title", "Status", "Covered by"], rows, row_header=True))
        self._page("Conformance", body, crumbs=[("Conformance", None)])
