"""The change-order page (change control v2, docs/design/change-control.md sections 3, 5, 6, 7 and 8): the loop on one page.

The sections follow the loop: where it stands and what to do next; what it carries; the review rounds (the compile-frame
job and its steps, the suggested panel with reasons, reviewers added and removed, each reviewer's status and results, a
person's own review form); questions; findings; suggested changes; the delta item by item (where each came from, the
hub's sentence beside the quote, warnings ticked one by one, approve or reject with a meaning, override, widenings,
moved and wording items shown but not decided); workflows (workstream 8's visual diff); the state map; what the change
does to each cog; related changes; and approval (send for approval, or what still stops the signature).

Everything is read through ``hubproto.changecontrol`` (``overview``, once per page) and changed through its API. No page
calls a model. A refusal re-renders the page with what was typed kept beside the form it came from.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from .. import eco, sopdocs, wopi
from .. import changecontrol as cc
from ..release import follows_for, principal_names, render_light
from ..rules import rule_key
from .forms import ShellError
from .handler import details, hidden_as, href, log_line, table
from .icons import icon
from .text import chip, esc, short, when

STAGE_NAMES = {"preparing": "Preparing", "in-review": "In review", "revising": "Revising", "awaiting-approval": "Awaiting approval",
               "approved": "Approved", "released": "Released"}
STAGE_HINTS = {"preparing": "changes carried", "in-review": "compile, panel, reviews", "revising": "answer, accept, reject",
               "awaiting-approval": "items decided, Gate signed", "approved": "release under way", "released": "in force"}
SIGN = {"add": ("+", "Added", "add"), "remove": ("−", "Removed", "del"), "change": ("~", "Changed", "chg")}
WARNING_WORDS = {"finding": "blocking finding", "question": "open question", "round-trip": "prose does not read back", "override": "override",
                 "divergence": "divergence", "related": "related change", "record-type": "record type"}
ORIGIN_WORDS = {"prose": "read from the prose", "direct": "made directly", "suggested": "suggested by a reviewer", "override": "an override",
                "divergence": "a divergence kept"}
RT_OPS = ("add_field", "add_enum_values", "add_state", "require_fields", "set_retention", "set_sensitivity", "set_default", "archive_type",
          "remove_field", "add_type", "remove_type")

CO_CSS = """
.co-toc{display:flex;flex-wrap:wrap;gap:var(--sp-1) var(--sp-3);margin:var(--sp-2) 0 var(--sp-3);font-size:var(--fs-s)}
.co-toc a{white-space:nowrap}
ol.co-stages{display:flex;flex-wrap:wrap;gap:0;list-style:none;margin:0 0 var(--sp-3);padding:0;counter-reset:st}
ol.co-stages li{flex:1 1 9rem;min-width:8rem;position:relative;padding:var(--sp-2) var(--sp-3) var(--sp-2) 2.4rem;border:1px solid var(--line);background:var(--panel);margin:0 -1px -1px 0}
ol.co-stages li::before{counter-increment:st;content:counter(st);position:absolute;left:var(--sp-2);top:var(--sp-2);width:1.5rem;height:1.5rem;border-radius:50%;
 display:flex;align-items:center;justify-content:center;font-size:var(--fs-s);font-weight:600;border:1.5px solid var(--ctl);color:var(--muted);background:var(--panel)}
ol.co-stages li.done::before{content:"✓";border-color:var(--ok);color:var(--ok);background:var(--ok-bg)}
ol.co-stages li[aria-current]{background:var(--accent-soft);border-color:var(--accent);z-index:1}
ol.co-stages li[aria-current]::before{border-color:var(--accent);background:var(--accent);color:var(--accent-fg)}
ol.co-stages b{display:block;font-weight:600}ol.co-stages .why{display:block}
.co-next{margin:var(--sp-2) 0}.co-next .actions{margin-top:var(--sp-2)}
.co section.co-sec{margin-top:var(--sp-6);padding-top:var(--sp-2);border-top:1px solid var(--line)}
.co h2 svg.i,.co h3 svg.i{margin-right:6px}.co td .chip,.co td .named,.co td form.inline button{white-space:nowrap}
.co .was{display:block;font-weight:400;color:var(--muted);font-size:var(--fs-s)}.co section.co-sec>h2{display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap}
ol.co-delta{list-style:none;margin:0;padding:0}ol.co-delta>li{background:var(--panel);border:1px solid var(--line);border-radius:var(--r-lg);padding:var(--sp-3) var(--sp-4);margin:0 0 var(--sp-3)}
ol.co-delta>li.st-approved{box-shadow:inset 3px 0 0 var(--ok)}ol.co-delta>li.st-rejected{box-shadow:inset 3px 0 0 var(--crit)}ol.co-delta>li.st-pending{box-shadow:inset 3px 0 0 var(--warn)}
.co-item-head{display:flex;flex-wrap:wrap;gap:var(--sp-2);align-items:baseline}.co-item-head .what{font-weight:600;flex:1 1 20rem;min-width:0;overflow-wrap:anywhere}
.co-sign{display:inline-block;min-width:1.6em;text-align:center;font-weight:700;border-radius:4px}.co-sign.add{color:var(--ok);background:var(--ok-bg)}
.co-sign.del{color:var(--crit);background:var(--crit-bg)}.co-sign.chg{color:var(--warn);background:var(--warn-bg)}
.co-reads{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,18rem),1fr));gap:var(--sp-2) var(--sp-4);margin:var(--sp-2) 0}
.co-reads>div{border-left:3px solid var(--line-strong);padding:2px var(--sp-3)}.co-reads h4{margin:0 0 2px;font-size:var(--fs-s);color:var(--muted);font-weight:500}
.co-reads q{quotes:"“" "”";font-style:italic}
ul.co-warn{list-style:none;padding:0;margin:var(--sp-2) 0}ul.co-warn li{margin:2px 0}
fieldset.co-accept{border:1px solid color-mix(in srgb,var(--warn) 40%,var(--line));border-radius:var(--r);padding:var(--sp-2) var(--sp-3);margin:var(--sp-2) 0}
fieldset.co-accept legend{font-size:var(--fs-s);font-weight:600;padding:0 var(--sp-1)}fieldset.co-accept label.check{display:flex;gap:var(--sp-2);align-items:flex-start;margin:4px 0}
fieldset.co-accept label.check input{margin-top:.25em;flex:none}
form.co-decide{border-top:1px solid var(--line);margin-top:var(--sp-3);padding-top:var(--sp-3)}
form.co-decide .row2{display:flex;flex-wrap:wrap;gap:var(--sp-2);align-items:flex-end}form.co-decide .row2 label.f{flex:1 1 16rem}
details.co-override{margin-top:var(--sp-2)}details.co-override .fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,12rem),1fr));gap:var(--sp-2)}
ul.co-decisions{margin:var(--sp-2) 0;padding-left:1.2em;font-size:var(--fs-s)}
.co-cogs{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,22rem),1fr));gap:var(--sp-3)}
.co-cogs .card{margin:0}.co-cogs dl{margin:var(--sp-2) 0 0}.co-cogs dt{font-size:var(--fs-s);color:var(--muted);font-weight:500;margin-top:var(--sp-2)}
.co-cogs dd{margin:0}.co-cogs ul{margin:2px 0;padding-left:0;list-style:none}.co-cogs li{overflow-wrap:anywhere}
.co-gain{color:var(--ok);font-weight:700}.co-loss{color:var(--crit);font-weight:700}
ol.co-steps{list-style:none;padding:0;margin:var(--sp-1) 0;font-size:var(--fs-s)}ol.co-steps li{display:flex;gap:var(--sp-2)}ol.co-steps .m{min-width:1.2em;text-align:center;font-weight:700}
.co-review-rows{display:grid;gap:var(--sp-2)}fieldset.co-rows{border:1px solid var(--line);border-radius:var(--r);margin:0 0 var(--sp-3);padding:var(--sp-2) var(--sp-3)}
fieldset.co-rows legend{font-weight:600;padding:0 var(--sp-1)}.co-review-rows .r{display:grid;grid-template-columns:minmax(8rem,10rem) minmax(8rem,14rem) minmax(0,1fr);gap:var(--sp-2);align-items:end}
@media(max-width:700px){.co-review-rows .r{grid-template-columns:1fr}}
.co-marker{background:var(--warn-bg);color:var(--fg);border-left:3px solid var(--warn);padding:var(--sp-1) var(--sp-3);font-size:var(--fs-s)}
.co-marker b{color:var(--warn)}
tr.co-div td{background:color-mix(in srgb,var(--warn-bg) 60%,var(--panel))}
@media print{.co-toc,form.co-decide{display:none!important}}
"""


def anchor(item_id: str) -> str:
    return "item-" + re.sub(r"[^A-Za-z0-9_-]+", "-", str(item_id)).strip("-")


def item_op(c: dict) -> str:
    """add, remove or change, as people read it (an add that replaces a rule is a change)."""
    if c.get("op") == "change" or (c.get("op") == "add" and (c.get("changes_from") or c.get("was"))):
        return "change"
    return "remove" if c.get("op") == "remove" else "add"


def groups(delta: list[dict]) -> list[list[dict]]:
    """The delta's rule items, each change with the removal it supersedes folded in (both are decided together)."""
    items = [d for d in delta if d.get("op") != "text"]
    ids = {d["id"] for d in items}
    paired: dict[str, list[dict]] = {}
    for d in items:
        if d.get("op") == "remove" and d.get("changed_to"):
            aid = "add:" + rule_key(d["changed_to"])
            if aid in ids:
                paired.setdefault(aid, []).append(d)
    folded = {x["id"] for xs in paired.values() for x in xs}
    return [[d] + paired.get(d["id"], []) for d in items if d["id"] not in folded]


def group_state(g: list[dict]) -> str:
    states = {d.get("state") for d in g}
    if "rejected" in states:
        return "rejected"
    if states == {"approved"}:
        return "approved"
    return "pending"


def divergence_words(d: dict, say) -> str:
    """The marker's sentence: "In force differs from this line: ... (accepted by bob on CO-2026-0007)"."""
    inf = say(d.get("in_force")) if d.get("in_force") else "nothing: the rule this line states is not in force"
    return f"In force differs from this line: {inf} (accepted by {d.get('accepted_by') or '-'} on {d.get('co') or '-'})"


# ---------------------------------------------------------------- what a change order does to each cog
CATEGORIES = (("roles", "Roles"), ("context", "Context (what it follows)"), ("permissions", "Permissions"), ("routes", "Routes"),
              ("criteria", "Approval criteria"), ("duties", "Duties"), ("steps", "Process steps"))


def _light(rules: list[dict]) -> dict:
    b = render_light(rules)
    b["responsibilities"] = [r for r in rules if r.get("kind") == "responsibility"]
    return b


def _cog_view(b: dict, cog: str, say) -> dict[str, dict[str, dict]]:
    """{category: {text: rule}} for one cog in a (light) bundle."""
    names = principal_names(b, cog)
    m = (b.get("members") or {}).get(cog) or {}
    out: dict[str, dict[str, dict]] = {k: {} for k, _ in CATEGORIES}
    for r in m.get("roles") or []:
        out["roles"][r.replace("-", " ")] = {"kind": "member", "principal": cog, "principal_type": "cog", "roles": m.get("roles") or []}
    try:
        follows = follows_for(b, cog)
    except Exception:
        follows = {}
    for frame, why in follows.items():
        out["context"][frame] = {"kind": "responsibility", "frame": frame, "why": sorted(why)}
    for p in b.get("permissions") or []:
        if p.get("subject") in names:
            r = {"kind": "permission", **p}
            out["permissions"][say(r)] = r
    for p in b.get("routing") or []:
        if p.get("assignee") in names:
            r = {"kind": "routing", **p}
            out["routes"][say(r)] = r
    for p in b.get("approval_criteria") or []:
        if p.get("approver") == f"cog:{cog}":
            r = {"kind": "approval_criteria", **p}
            out["criteria"][say(r)] = r
    for p in b.get("duties") or []:
        if p.get("cog") == cog:
            r = {"kind": "duty", **p}
            out["duties"][say(r)] = r
    for name, proc in (b.get("processes") or {}).items():
        for st in proc.get("steps") or []:
            if st.get("cog") == cog or cog in (st.get("swarm") or []):
                out["steps"][f"{name}: {st.get('title') or st.get('id')}"] = {"kind": "process", "name": name, **{k: v for k, v in proc.items() if k != "name"}}
    return out


def after_rules(hub, co: dict, p: dict | None) -> list[dict]:
    """The rules in force if the change order were released as it stands: what the compiled prose says, with the direct
    changes the prose does not state applied and every override in place."""
    base = hub.settings_rules()
    if p is None:
        return cc._apply(base, cc._items(co))
    rules = {rule_key(r): r for r in p.get("rules") or []}
    for c in p.get("changes") or []:
        if c.get("op") == "text":
            continue
        if c.get("unstated"):
            old, new = cc._old_new(c)
            cc._with(rules, old, (c.get("override") or {}).get("rule") or new)
        elif c.get("override") and c.get("op") == "add":
            rules.pop(rule_key(c["rule"]), None)
            rules[rule_key(c["override"]["rule"])] = c["override"]["rule"]
    return list(rules.values())


def cog_effects(hub, co: dict, p: dict | None, delta: list[dict], say) -> list[dict]:
    """Per cog: what it gains and loses (roles, context, permissions, routes, approval criteria, duties, steps), each gain
    marked when a widening item of the delta brings it. Derived from the delta: the rules in force against the rules as
    the change order would leave them."""
    before = _light(hub.settings_rules())
    after = _light(after_rules(hub, co, p))
    widened = set()
    for c in delta:
        if c.get("op") == "text" or not cc._widening(c):
            continue
        widened.add(rule_key(cc._effective(c)))
    cogs = sorted({n for b in (before, after) for n, m in (b.get("members") or {}).items() if m.get("type") == "cog"})
    out = []
    for cog in cogs:
        a, z = _cog_view(before, cog, say), _cog_view(after, cog, say)
        gained, lost = {}, {}
        for key, _ in CATEGORIES:
            g = [(t, z[key][t]) for t in sorted(set(z[key]) - set(a[key]))]
            ls = sorted(set(a[key]) - set(z[key]))
            if g:
                gained[key] = [{"text": t, "widening": rule_key(r) in widened} for t, r in g]
            if ls:
                lost[key] = ls
        if gained or lost:
            m = (after.get("members") or {}).get(cog) or (before.get("members") or {}).get(cog) or {}
            out.append({"cog": cog, "roles": m.get("roles") or [], "gained": gained, "lost": lost,
                        "new": cog not in (before.get("members") or {}), "gone": cog not in (after.get("members") or {})})
    return out


# ---------------------------------------------------------------- the state map
def state_rows(hub, co: dict, p: dict | None, sq: list[dict], bundle: dict) -> list[dict]:
    """Every state a change removes that items are in: {"process", "state", "name", "count", "record_type", "answer",
    "options": [(id, name)], "consequences"}. From the compile's questions (numbered steps) and the workflow editor's
    carried graphs."""
    from .. import process_graph
    from .. import workflow_model as wm
    from .. import workflows as wfs

    smap = co["fields"].get("state_map") or {}
    rows: dict[tuple, dict] = {}
    new_rules = {r["name"]: r for r in (p or {}).get("rules") or [] if r.get("kind") == "process"}

    def consequences(base: dict | None, new: dict | None, old: str, to: str | None) -> str:
        if base is None or new is None:
            return ""
        cons = []
        waiting = [t["name"] for a, t, b in wm.edges(base) if a == old and wm.gated(t)]
        if waiting:
            cons.append("the waiting decision (" + ", ".join(waiting) + ") is withdrawn")
        if to:
            nxt = [t for a, t, b in wm.edges(new) if a == to and wm.gated(t)]
            if nxt:
                cons.append("then waits for " + "; ".join(wm.gate_words(new, t.get("gate")) for t in nxt[:2]))
            need = sorted({f for t in new.get("transitions") or [] if t.get("to") == to for v in t.get("validators") or [] for f in v.get("fields_set") or []})
            if need:
                cons.append("items that lack " + ", ".join(need) + " move without them")
        return "; ".join(cons) or "no decision is waiting there"

    def options(g: dict | None) -> list[tuple[str, str]]:
        return [(s["id"], s["name"]) for s in (g or {}).get("statuses") or [] if not wm.is_system(s)]

    for q in sq:
        proc = q["process"]
        try:
            base = wfs.graph_in_force(hub, proc, bundle)
        except KeyError:
            base = None
        new = None
        if proc in new_rules:
            try:
                new = process_graph.build(new_rules[proc], {}, q.get("record_type") or "")
            except Exception:
                new = None
        name = wm.name_of(base, q["state"]) if base else q["state"]
        rows[(proc, q["state"])] = {"process": proc, "state": q["state"], "name": name, "count": int(q.get("count") or 0),
                                    "record_type": q.get("record_type"), "answer": q.get("answer"), "options": options(new),
                                    "consequences": consequences(base, new, q["state"], q.get("answer")), "from": "the compiled prose"}
    for proc, c in sorted(wfs.changes_of(co).items()):
        try:
            base = wfs.graph_in_force(hub, proc, bundle)
        except KeyError:
            continue
        counts = wfs.items_by_status(hub, base)
        new_ids = wm.status_map(c["graph"])
        for s in base.get("statuses") or []:
            if s["id"] in new_ids or wm.is_system(s) or not counts.get(s["id"]) or (proc, s["id"]) in rows:
                continue
            ans = (smap.get(proc) or {}).get(s["id"]) or (c.get("state_map") or {}).get(s["id"])
            rows[(proc, s["id"])] = {"process": proc, "state": s["id"], "name": s["name"], "count": int(counts[s["id"]]),
                                     "record_type": base.get("record_type"), "answer": ans, "options": options(c["graph"]),
                                     "consequences": consequences(base, c["graph"], s["id"], ans), "from": "the workflow editor"}
    return list(rows.values())


class ChangeOrderMixin:
    # ------------------------------------------------------------ small helpers
    def _say(self, rule: dict | None) -> str:
        """A rule as the sentence a person would write (the hub's reading of it)."""
        if not rule:
            return ""
        if rule.get("kind") == "process" and rule.get("statuses"):  # a workflow: its summary, never its whole process text
            return cc._sentence(rule)
        from ..settings import sentence

        try:
            return sentence({k: v for k, v in rule.items() if k != "source"}, (self.bundle or {}).get("processes") or {}).replace("\n", " / ")
        except Exception:
            return (rule.get("source") or {}).get("text") or cc._sentence(rule)

    def _co_cv(self, action: str, form_id: str, name: str, default=""):
        """What was typed in this form when it was just refused, else ``default``."""
        if self.err and self.route == action and self.vals.get("form") == form_id:
            return self.vals.get(name, default)
        return default

    def _co_cmulti(self, action: str, form_id: str, name: str, default=None) -> list[str]:
        if self.err and self.route == action and self.vals.get("form") == form_id:
            return list((getattr(self, "multi", None) or {}).get(name) or [])
        return list(default or [])

    def _co_form(self, action: str, co: dict, form_id: str, inner: str, *, cls: str = "sform", label: str = "") -> str:
        err = ""
        if self.err and self.route == action and self.vals.get("form") == form_id:
            self._err_shown = True
            err = f"<p class='err' role='alert'>{esc(self.err[0])}</p>"
        aria = f" aria-label='{esc(label)}'" if label else ""
        return (f"<form method='post' action='{action}' class='{cls}' id='f-{esc(form_id)}'{aria}>{hidden_as(self.user)}{self._once()}"
                f"<input type='hidden' name='co' value='{esc(co['locator'])}'><input type='hidden' name='form' value='{esc(form_id)}'>{err}{inner}</form>")

    def _co_frame_line(self, frame: str | None, line) -> str:
        if not frame:
            return ""
        if line:
            return f"<a href='{href('/frame/' + frame, self.user)}#L{esc(line)}'>{esc(frame)} line {esc(line)}</a>"
        return f"<a href='{href('/frame/' + frame, self.user)}'>{esc(frame)}</a>"

    def _co_who(self, who: str | None) -> str:
        return self._avatar(who, size="sm", named=True) if who else "<span class='muted'>-</span>"

    def _co_members(self) -> dict:
        return (self.bundle or {}).get("members") or {}

    # ------------------------------------------------------------ the page
    def _co_ctx(self, co: dict) -> dict:
        ov = cc.overview(self.hub, co["locator"])
        co = ov["co"]
        run = self.hub.runner.run_for_record(co["locator"])
        waiting = bool(run and run["status"] in ("waiting", "submitted", "running"))
        stage = ov["stage"]
        mine = self._co_mine(co)
        p = ov["proposal"]
        current = p is not None and p.get("status") == "open" and not [r for r in ov["send_reasons"] if "request review again" in r
                                                                         or "no review round" in r or "not compiled" in r]
        rnd = ov["round"] or {}
        me = (rnd.get("results") or {}).get(self.user) or {}
        on_panel = self.user in {e["who"] for e in rnd.get("panel") or []}
        return {"ov": ov, "co": co, "f": co["fields"], "stage": stage, "mine": mine, "editable": stage in cc.EDITABLE and not eco.finished(co),
                "decider": not cc._may_decide(self.hub, co, self.user) and not eco.finished(co), "run": run, "waiting": waiting,
                "p": p, "current": current, "round": ov["round"], "reviewing_me": on_panel and me.get("status") == "queued" and not self._viewing_cog(),
                "answerer": (mine or self.user in ov["contributors"]) and not eco.finished(co), "groups": groups(ov["delta"]),
                "requester": cc.requester(co)}

    def _co_preview(self, ctx: dict) -> dict | None:
        """What the carried drafts compile into as they stand (not saved, not staged), once per page: for the author
        before a round has run on them."""
        if "preview" not in ctx:
            co = ctx["co"]
            ctx["preview"] = eco.preview(self.hub, co["locator"]) if eco.carried(self.hub, co) and ctx["editable"] else None
        return ctx["preview"]

    def _co_page(self, co: dict) -> str:
        """The change order's own section on its record page: the whole loop."""
        ctx = self._co_ctx(co)
        co = ctx["co"]
        ov = ctx["ov"]
        open_q = sum(1 for q in ov["questions"] if q.get("status", "open") == "open")
        open_s = sum(1 for s in ov["suggestions"] if s.get("status") == "open")
        pend = sum(1 for g in ctx["groups"] if group_state(g) == "pending")
        toc = [("co-stage", "Where it stands"), ("co-carries", "What it carries"), ("co-review", "Review"),
               ("co-questions", f"Questions ({open_q} open)"), ("co-findings", f"Findings ({len(ov['findings'])})"),
               ("co-suggestions", f"Suggestions ({open_s} open)"), ("delta", f"The delta ({len(ctx['groups'])} item{'s' if len(ctx['groups']) != 1 else ''}"
                                                                   + (f", {pend} to decide" if ctx["stage"] == "awaiting-approval" and pend else "") + ")"),
               ("co-workflows", "Workflows"), ("co-state-map", "State map"), ("co-cogs", "Each cog"), ("co-related", "Related"),
               ("co-approval", "Approval")]
        out = (f"<style>{CO_CSS}</style><section class='co' id='change-order' aria-label='Change order {esc(co['number'])}'>"
               "<nav class='co-toc' aria-label='On this change order'>" + " ".join(f"<a href='#{a}'>{esc(t)}</a>" for a, t in toc) + "</nav>")
        out += self._co_stage_html(ctx)
        out += self._co_carries_html(ctx)
        out += self._co_review_html(ctx)
        out += self._co_questions_html(ctx)
        out += self._co_findings_html(ctx)
        out += self._co_suggestions_html(ctx)
        out += self._co_delta_html(ctx)
        out += f"<section class='co-sec' id='co-wf'>{self._workflows_co_section(co, ctx['mine'], ctx['editable'] and ctx['mine'])}</section>"
        out += self._co_state_map_html(ctx)
        out += self._co_cogs_html(ctx)
        out += self._co_related_html(ctx)
        out += self._co_approval_html(ctx)
        return out + "</section>"

    # ------------------------------------------------------------ 1. where it stands
    def _co_stage_html(self, ctx: dict) -> str:
        co, stage, f = ctx["co"], ctx["stage"], ctx["f"]
        stages = list(cc.STAGES)
        cur = stages.index(stage)
        rnd = ctx["round"] or {}
        lis = []
        for i, s in enumerate(stages):
            label = STAGE_NAMES[s] + (f" (round {rnd['n']})" if s == "in-review" and rnd and stage in ("in-review", "revising") else "")
            state = " aria-current='step'" if i == cur else ""
            cls = " class='done'" if i < cur else ""
            vh = "<span class='vh'> (done)</span>" if i < cur else ("<span class='vh'> (now)</span>" if i == cur else "")
            lis.append(f"<li{cls}{state}><b>{esc(label)}</b>{vh}<span class='why'>{esc(STAGE_HINTS[s])}</span></li>")
        out = (f"<div class='card' id='co-stage' data-live><h2 style='margin-top:0'>{icon('activity')}Where {esc(co['number'])} stands</h2>"
               f"<ol class='co-stages' aria-label='Stages of the change order'>{''.join(lis)}</ol>")
        bc = f.get("baseline_changed") or {}
        if bc and not bc.get("dismissed"):
            out += (f"<div class='card warn' role='status'>{icon('alert')} <b>The baseline changed:</b> {esc(bc.get('by'))} was released and "
                    f"is related {'by definition' if bc.get('kind') == 'definition' else 'by meaning' + (' (' + esc(bc.get('cog')) + ')' if bc.get('cog') else '')}: "
                    f"{esc('; '.join(bc.get('reasons') or []))}. Its reviews and item decisions are stale until another round runs. "
                    "<a href='#co-related'>Related changes</a></div>")
        rej = f.get("rejected") or {}
        if rej and stage in cc.EDITABLE:
            out += (f"<div class='card warn' role='status'>{icon('x')} {esc(rej.get('by'))} rejected its approval"
                    + (f": “{esc(rej.get('meaning'))}”" if rej.get("meaning") else "") + ". It is back to revising.</div>")
        unav = [q for q in self._co_unavailable_questions(ctx) if q.get("status", "open") == "open"]
        if unav and stage in cc.EDITABLE + ("awaiting-approval",):
            out += (f"<div class='card warn' role='status'>{icon('alert')} <b>The compile-frame committee could not read part of the prose</b> "
                    f"({len(unav)} question(s): its members are not available here). <a href='#co-unavailable'>What to do</a></div>")
        for c in ctx["ov"].get("committees") or []:
            if c["status"] == "paused" and self.user in (c.get("interveners") or []):
                role = "its chair" if self.user == c.get("chair") else "in its chair's stead"
                out += (f"<div class='card warn' role='status'>{icon('users')} <b>The {esc(c['committee'])} committee needs you, {role}:</b> "
                        f"{esc(c['reason'])}. <a href='#co-review'>Intervene</a></div>")
        out += f"<div class='co-next'>{self._co_next_html(ctx)}</div></div>"
        return out

    def _co_request_form(self, ctx: dict, again: bool) -> str:
        co = ctx["co"]
        fid = "request-review"
        members = self._co_members()
        last = {e["who"] for e in (ctx["round"] or {}).get("panel") or []}
        chosen = self._co_cmulti("/co-review", fid, "panel", sorted(last))
        own = self._co_cv("/co-review", fid, "choose", "") == "own"
        boxes = "".join(f"<label class='check'><input type='checkbox' name='panel' value='{esc(n)}'{' checked' if n in chosen else ''}> {esc(n)} "
                        f"<span class='why'>{esc(m.get('type') or 'person')}, {esc(', '.join(r.replace('-', ' ') for r in m.get('roles') or []))}</span></label>"
                        for n, m in sorted(members.items(), key=lambda x: (x[1].get("type") != "cog", x[0])))
        inner = (f"<button>{icon('users')}{'Request review again' if again else 'Request review'}</button>"
                 f"<details class='inline-details'{' open' if own else ''}><summary>Choose the panel yourself</summary>"
                 "<fieldset class='checks'><legend>Panel for this round</legend>"
                 f"<label class='check'><input type='radio' name='choose' value='suggested'{'' if own else ' checked'}> the reviewers the quality-assistant cog suggests "
                 "from the change-control SOP (with their reasons)</label>"
                 f"<label class='check'><input type='radio' name='choose' value='own'{' checked' if own else ''}> only the reviewers ticked below</label></fieldset>"
                 f"<fieldset class='checks'><legend>Reviewers</legend>{boxes}</fieldset></details>")
        return self._co_form("/co-review", co, fid, inner, cls="inline", label="Request review")

    def _co_send_form(self, ctx: dict) -> str:
        co = ctx["co"]
        return self._co_form("/co-send", co, "send", f"<button>{icon('send')}Send for approval</button>", cls="inline", label="Send for approval")

    def _co_next_html(self, ctx: dict) -> str:
        """What to do next, for the person looking, with the action itself where there is one."""
        co, stage, mine = ctx["co"], ctx["stage"], ctx["mine"]
        ov = ctx["ov"]
        rnd = ctx["round"] or {}
        who = ctx["requester"] or "its requester"
        f = co["fields"]
        if stage == "released":
            return (f"<p class='status'>Released: its revisions and configuration are in force "
                    f"({self._version_html(f.get('released_release')) if f.get('released_release') else 'the release in force'}).</p>")
        if stage == "approved":
            dec = next((d for d in reversed(co.get("decisions") or []) if d.get("verdict") == "approve"), {})
            return (f"<p><b>Approved</b>{' by ' + esc(dec.get('by')) if dec.get('by') else ''}: the release automation is bringing its configuration "
                    "and revisions into force.</p>" + self._automation_line(co))
        if stage == "awaiting-approval":
            pending = (ctx["run"] or {}).get("pending") or {}
            signer = ", ".join(pending.get("assignees") or []) or "the quality manager"
            todo = [g for g in ctx["groups"] if group_state(g) == "pending"]
            head = f"<p class='status'>Sent for approval: it waits for {esc(signer)} at its Gate.</p>"
            if ctx["decider"]:
                mine_todo = [g for g in todo if not any(d["by"] == self.user and not d.get("stale") for d in g[0].get("decisions") or [])]
                head += (f"<p><b>Next:</b> decide each item of <a href='#delta'>the delta</a> ({len(mine_todo)} without your decision"
                         + (f", {len(todo)} still pending" if todo else ", none pending") + "), ticking <i>I accept</i> for each warning; "
                         "a widening needs both policy owners.</p>")
            if self.user in (pending.get("assignees") or []):
                head += ("<p><b>Then sign it</b> in the approval card above once <a href='#co-approval'>nothing stops it</a>. Your signature also "
                         "approves every revision it carries.</p>" if ov["approval_reasons"] else
                         "<p><b>Ready to sign:</b> every item is decided and every warning accepted. Sign it in the approval card above; your "
                         "signature also approves every revision it carries.</p>")
            return head
        if stage == "preparing":
            if not mine:
                return f"<p>{esc(who)} is preparing it: carrying the revisions and changes it makes.</p>"
            carries = eco.carried(self.hub, co) or cc._items(co) or f.get("form_changes") or f.get("record_type_changes") or f.get("workflow_changes")
            if not carries:
                return ("<p><b>Next:</b> carry what it changes: start a revision of an SOP or policy below, make a direct change in "
                        f"<a href='{href('/settings', self.user)}'>Settings</a>, the <a href='{href('/forms', self.user)}'>forms editor</a>, "
                        f"the <a href='{href('/workflows', self.user)}'>workflow editor</a> or on the <a href='{href('/record-types', self.user)}'>record types</a> page. "
                        "Then request review.</p>")
            return ("<p><b>Next:</b> when the revisions say what you mean, request review. Round 1 compiles the delta from the prose, drafts "
                    "the prose for direct changes, suggests the panel and asks it.</p><div class='actions'>" + self._co_request_form(ctx, False) + "</div>")
        # in review or revising
        if rnd.get("status") == "compiling":
            return f"<p><b>Round {esc(rnd.get('n'))} is being compiled</b> (the compile-frame job under <a href='#co-review'>Review</a>).</p>"
        if rnd.get("status") == "failed":
            return (f"<p><b>Round {esc(rnd.get('n'))} stopped:</b> {esc(rnd.get('error') or '')}</p>"
                    + (("<p>Fix that in the revisions, then request review again.</p><div class='actions'>" + self._co_request_form(ctx, True) + "</div>") if mine else ""))
        out = ""
        waiting_for = [w for w, r in (rnd.get("results") or {}).items() if r.get("status") == "queued"]
        if stage == "in-review" and waiting_for:
            out += f"<p>Round {esc(rnd.get('n'))}: waiting for {esc(', '.join(waiting_for))} to review.</p>"
        if ctx["reviewing_me"]:
            out += f"<p><b>You are on the panel:</b> <a href='#co-your-review'>post your review</a> (findings, questions, suggested changes).</p>"
        if mine:
            sendable = not ov["send_reasons"]
            open_q = sum(1 for q in ov["questions"] if q.get("status", "open") == "open")
            open_s = sum(1 for s in ov["suggestions"] if s.get("status") == "open")
            todo = []
            if open_q:
                todo.append(f"<a href='#co-questions'>answer {open_q} question{'s' if open_q != 1 else ''}</a> in the prose")
            if open_s:
                todo.append(f"<a href='#co-suggestions'>accept or reject {open_s} suggestion{'s' if open_s != 1 else ''}</a>")
            if stage == "in-review" and waiting_for:
                out += ("<p><b>Next:</b> wait for the panel" + (f" ({esc(', '.join(waiting_for))})" if waiting_for else "")
                        + (" and meanwhile " + ", ".join(todo) if todo else "") + "; you may add or take off reviewers under "
                        "<a href='#co-review'>Review</a>. A new round restarts the review.</p>")
            else:
                out += ("<p><b>Next:</b> " + (", ".join(todo) + "; then " if todo else "") +
                        ("request review again, or send it for approval (open findings and questions become warnings the approver accepts).</p>" if sendable else
                         "request review again: " + esc(ov["send_reasons"][0]) + ".</p>"))
            out += "<div class='actions'>" + (self._co_send_form(ctx) + " " if sendable else "") + self._co_request_form(ctx, True) + "</div>"
        elif not out:
            out = f"<p>{esc(who)} is revising it after round {esc(rnd.get('n'))}.</p>"
        return out

    # ------------------------------------------------------------ 2. what it carries
    def _co_carries_html(self, ctx: dict) -> str:
        co, f = ctx["co"], ctx["f"]
        prep = ctx["mine"] and ctx["editable"]
        out = f"<section class='co-sec' id='co-carries'><h2>{icon('file-text')}What it carries</h2>"
        out += self._co_revisions_html(co, ctx)
        out += self._co_direct_html(ctx)
        out += self._forms_co_section(co, ctx["mine"], prep)
        out += self._co_rt_html(ctx)
        wfc = f.get("workflow_changes") or {}
        if wfc:
            out += (f"<h3>{icon('workflow')}Workflows</h3><p>{len(wfc)} workflow change(s): {esc(', '.join(sorted(wfc)))}; drawn under "
                    "<a href='#co-workflows'>Workflows</a> below.</p>")
        out += self._co_implements_html(co, ctx)
        return out + "</section>"

    def _co_direct_html(self, ctx: dict) -> str:
        co = ctx["co"]
        prep = ctx["mine"] and ctx["editable"]
        items = cc._items(co)
        rows = []
        for it in items:
            o = it.get("origin") or {}
            old, new = cc._old_new(it)
            op = item_op(it)
            sign, word, cls = SIGN[op]
            if (new or old or {}).get("kind") == "process":  # a workflow from the editor: its changes, one line each
                from ..workflow_model import process_change_words

                what = (f"<span class='co-sign {cls}' aria-hidden='true'>{sign}</span><span class='vh'>{word}: </span>The {esc((new or old).get('name'))} workflow"
                        + "<ul class='co-parts'>" + "".join(f"<li>{esc(w)}</li>" for w in process_change_words(old, new)) + "</ul>")
            else:
                what = (f"<span class='co-sign {cls}' aria-hidden='true'>{sign}</span><span class='vh'>{word}: </span>"
                        + (esc(self._say(new)) if new is not None else f"<del>{esc(self._say(old))}</del>")
                        + (f"<div class='why'>was: {esc(self._say(old))}</div>" if old is not None and new is not None else ""))
            if o.get("kind") == "suggested":
                by = f"suggested by {self._co_who(o.get('by'))}, accepted by {self._co_who(o.get('accepted_by'))}"
            else:
                by = f"{self._co_who(o.get('by'))} <span class='why'>{esc('in the ' + o['editor'] + ' editor' if o.get('editor') else 'in Settings' if co['fields'].get('from_settings') else '')}</span>"
            st = it.get("stated") or {}
            rt = it.get("round_trip") or {}
            prose = ((f"stated in {self._co_frame_line(st.get('sop') or o.get('frame'), (it.get('prose') or {}).get('line'))}"
                      + (" (" + esc(st.get("by")) + ")" if st.get("by") else ""))
                     if st or it.get("prose") else f"not in the prose yet: the next round drafts it into {self._co_frame_line(o.get('frame'), None) or 'its SOP'}")
            if rt.get("ok") is False:
                prose += " <span class='chip warn'>does not read back</span>"
            rm = self._co_form("/co-direct-remove", co, f"rm-{anchor(it['id'])}", f"<input type='hidden' name='item' value='{esc(it['id'])}'>"
                               f"<button class='ghost'>{icon('x')}Remove</button>", cls="inline") if prep else ""
            rows.append([what + (" " + chip("widening: needs both policy owners", "warn") if it.get("widening") else ""), by,
                         f"<span class='why'>{esc(when(o.get('at')))}</span>", prose, rm])
        out = f"<h3>{icon('edit')}Direct changes</h3>"
        out += table(["Change", "Made by", "When", "In the prose", ""], rows,
                     "No direct change. Edits made in Settings, the forms editor or the workflow editor land here.")
        if items:
            out += ("<p class='why'>A direct change is not written into the SOP by the editor it was made in: the review round's qa-manager cog "
                    "writes the prose for it and checks it reads back as the same change.</p>")
        return out

    def _co_rt_html(self, ctx: dict) -> str:
        from .. import recordtypes

        co = ctx["co"]
        prep = ctx["mine"] and ctx["editable"]
        rows = []
        for c in recordtypes.changes_of(co):
            rm = self._co_form("/co-rt-uncarry", co, f"rt-{esc(c.get('id'))}", f"<input type='hidden' name='change' value='{esc(c.get('id'))}'>"
                               f"<button class='ghost'>{icon('x')}Remove</button>", cls="inline") if prep else ""
            rows.append([esc(c.get("summary") or recordtypes.describe(c)), self._co_who(c.get("by")), esc(c.get("reason") or ""),
                         f"<span class='why'>{esc(when(c.get('at')))}</span>", rm])
        out = f"<h3>{icon('grid')}Record types</h3>" + table(["Change", "Carried by", "Why", "When", ""], rows, "No record-type change.")
        if prep:
            out += f"<p class='why'><a href='{href('/record-types', self.user, co=co['number'])}'>{icon('grid')}Change a record type</a> on this change order.</p>"
        return out

    def _co_revisions_html(self, co: dict, ctx: dict) -> str:
        user = self.user
        prep = ctx["mine"] and ctx["editable"]
        rows = []
        for c in eco.carried(self.hub, co):
            dft = c["draft"]
            versions = self.hub.dms.versions(dft["locator"]) if hasattr(self.hub.dms, "versions") else []
            links = []
            if wopi.is_editable(dft) and dft["state"] in ("draft", "open", "returned"):
                links.append(f"<a href='{href('/edit/' + quote(dft['locator'], safe=':'), user)}'>{icon('edit')}Edit</a>")
            if len(versions) > 1:
                links.append(f"<a href='{href('/compare/' + quote(dft['locator'], safe=':'), user, a=1, b=len(versions))}'>compare with the version in force</a>")
            if prep and dft["state"] in ("draft", "open", "returned"):
                links.append(f"<form method='post' action='/co-remove-sop' class='inline'>{hidden_as(user)}{self._once()}"
                             f"<input type='hidden' name='co' value='{esc(co['locator'])}'><input type='hidden' name='sop' value='{esc(c['name'])}'>"
                             f"<button class='ghost'>{icon('x')}Remove</button></form>")
            kind = {"policy": "Policy", "sop": "SOP", "wi": "Work instruction"}.get(dft["fields"].get("doc_type"), "Document")
            authors = cc._version_authors(self.hub, dft)
            rows.append([f"{esc(c['name'])} <span class='why'>{esc(kind)}</span>",
                         f"<a href='{self._doc_page(dft)}'>{esc(dft['number'])}</a> rev {esc(dft['fields'].get('revision') or '')}"
                         + (f" <span class='why'>revises {esc(dft['fields'].get('revises'))}</span>" if dft["fields"].get("revises") else ""),
                         esc(", ".join(authors) or "-"), self._draft_words(dft, co), " ".join(links)])
        out = f"<h3>{icon('file-text')}SOP and policy revisions</h3>" + table(["SOP or policy", "Revision", "Written by", "Where it stands", ""], rows,
                                                                              "No revision yet: add the SOPs and policies this change order changes.")
        if prep:
            carried = {c["name"] for c in eco.carried(self.hub, co)}
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
        return out

    def _co_implements_html(self, co: dict, ctx: dict) -> str:
        user = self.user
        f = co["fields"]
        prep = ctx["mine"] and ctx["editable"]
        crs = {r["number"]: r for r in self._change_requests()}
        rows = []
        for n in f.get("implements") or []:
            r = crs.get(n)
            fl = (r or {}).get("fields", {}).get("flagged") or {}
            flag = (f" <span class='chip warn'>flagged by {esc(fl.get('by'))}</span>" if r and cc.cr_flag_open(self.hub, r) else "")
            rm = (f"<form method='post' action='/co-implements' class='inline'>{hidden_as(user)}{self._once()}<input type='hidden' name='co' value='{esc(co['locator'])}'>"
                  f"<input type='hidden' name='cr' value='{esc(n)}'><input type='hidden' name='action' value='remove'><button class='ghost'>{icon('x')}Remove</button></form>"
                  if prep else "")
            rows.append([self._record_link(r["locator"]) if r else esc(n), (self._state_chip(r["state"]) if r else "<span class='muted'>not found</span>") + flag, rm])
        out = f"<h3>{icon('workflow')}Implements</h3>" + table(["Change request", "State", ""], rows, "No change request named.")
        if prep:
            from .eco_ui import cr_approved

            cands = [r for r in crs.values() if cr_approved(r) and r["number"] not in (f.get("implements") or [])]
            if cands:
                sel = "".join(f"<option value='{esc(r['number'])}'>{esc(r['number'])} {esc((r['fields'].get('title') or '')[:60])}</option>" for r in cands)
                out += (f"<form method='post' action='/co-implements' class='sform row'>{hidden_as(user)}{self._once()}<input type='hidden' name='co' value='{esc(co['locator'])}'>"
                        f"<input type='hidden' name='action' value='add'><label class='f'><span>Add an approved change request</span><select name='cr'>{sel}</select></label>"
                        f"<button class='secondary'>{icon('plus')}Add</button></form>")
        return out

    # ------------------------------------------------------------ 3. review rounds
    def _co_review_html(self, ctx: dict) -> str:
        co = ctx["co"]
        rounds = ctx["ov"]["rounds"]
        out = f"<section class='co-sec' id='co-review' data-live><h2>{icon('users')}Review</h2>"
        if not rounds:
            out += ("<p class='muted'>No review round yet.</p><p class='why'>A round compiles the delta from the prose as it stands (later rounds "
                    "only what changed), drafts the prose for direct changes, and asks the panel the quality-assistant cog suggests from the "
                    "change-control SOP's section on reviewing changes.</p>")
            return out + "</section>"
        rnd = rounds[-1]
        out += self._co_round_html(ctx, rnd, current=True)
        if ctx["reviewing_me"]:
            out += self._co_your_review_html(ctx, rnd)
        earlier = rounds[:-1]
        if earlier:
            out += details(f"Earlier rounds ({len(earlier)})", "".join(self._co_round_html(ctx, r, current=False) for r in reversed(earlier)))
        return out + "</section>"

    def _co_round_html(self, ctx: dict, rnd: dict, current: bool) -> str:
        co = ctx["co"]
        prep = ctx["mine"] and ctx["editable"] and current
        st = rnd.get("status") or ""
        words = {"compiling": ("being compiled", "info"), "reviewing": ("the panel is reviewing", "warn"), "reviewed": ("reviewed", "ok"),
                 "failed": ("stopped", "crit")}.get(st, (st, ""))
        out = (f"<div class='card'><h3 style='margin-top:0'>Review round {esc(rnd['n'])} {chip(words[0], words[1])}"
               + (" " + chip("stale: the baseline changed", "warn") if rnd.get("stale") else "") + "</h3>"
               f"<p class='why'>Requested by {self._co_who(rnd.get('requested_by'))} on {esc(when(rnd.get('at')))}"
               + (f"; compiled as <a href='{href('/proposal/' + rnd['compiled'], self.user)}'>proposal #{esc(rnd.get('compiled_number') or '')}</a>"
                  if rnd.get("compiled") else "") + ".</p>")
        if rnd.get("error"):
            out += f"<div class='card crit' role='alert'>{esc(rnd['error'])}</div>"
        job = self.hub.automation.load(rnd["job"]) if rnd.get("job") else None
        if job:
            from .automation_ui import STEP, _chip

            steps = "".join(f"<li><span class='m {STEP.get(s['status'], ('·', ''))[1]}' aria-hidden='true'>{STEP.get(s['status'], ('·', ''))[0]}</span>"
                            f"<span><span class='vh'>{esc(s['status'])}: </span>{esc(s['name'])}"
                            + (f" <span class='why'>{esc(short(s.get('detail') or '', 200))}</span>" if s.get("detail") else "") + "</span></li>"
                            for s in job.get("steps") or [])
            out += (f"<p>{icon('activity')}<a href='{href('/automation/' + job['id'], self.user)}'>The compile-frame job</a> {_chip(job['status'])}</p>"
                    f"<ol class='co-steps' aria-label='Steps of the compile-frame job'>{steps}</ol>")
        drafted = rnd.get("drafted") or {}
        if drafted:
            out += f"<p class='why'>Prose for direct changes drafted by {esc(drafted.get('by'))}.</p>"
        sug = rnd.get("suggested") or []
        if sug:
            out += ("<h4>Suggested panel</h4><ul>" + "".join(
                f"<li><b>{esc(e['who'])}</b> <span class='chip'>{esc(e.get('kind') or 'person')}</span>"
                + ("" if e.get("member") else " <span class='chip warn'>not a member: shown, not asked</span>")
                + f" <span class='why'>{esc(e.get('reason') or '')}"
                + (f" (suggested by {esc(e.get('suggested_by'))})" if e.get("suggested_by") else "") + "</span></li>" for e in sug) + "</ul>")
        elif st != "compiling":
            out += "<p class='why'>No reviewer suggested: the change-control SOP's section on reviewing changes names none for this delta.</p>"
        results = rnd.get("results") or {}
        rows = []
        for e in rnd.get("panel") or []:
            res = results.get(e["who"]) or {}
            status = res.get("status") or ("not asked" if not e.get("member") else "queued")
            scls = {"done": "ok", "failed": "crit", "queued": "warn"}.get(status, "")
            fs = res.get("findings") or []
            blocking = sum(1 for x in fs if x.get("severity") == "blocking")
            summary = []
            if status in ("done", "failed"):
                summary.append(f"{len(fs)} finding(s) ({blocking} blocking), {len(res.get('questions') or [])} question(s), "
                               f"{len(res.get('suggestions') or [])} suggestion(s)")
            if res.get("error"):
                summary.append(esc(res["error"]))
            txt = (f"<div class='why'>{esc(short(res.get('summary') or '', 300))}</div>" if res.get("summary") else "")
            model = (f"<div class='why'>{'stand-in (no model)' if res.get('stand_in') else esc(res.get('model') or '')}</div>"
                     if res.get("kind") == "cog" and status == "done" else "")
            why = esc(e.get("reason") or "") + (f" <span class='why'>(suggested by {esc(e['suggested_by'])})</span>" if e.get("suggested_by") else "") \
                + (f" <span class='why'>(added by {esc(e['added_by'])})</span>" if e.get("added_by") and not str(e.get("reason") or "").startswith("added by") else "")
            rm = self._co_form("/co-reviewer-remove", co, f"rmr-{e['who']}", f"<input type='hidden' name='who' value='{esc(e['who'])}'>"
                               f"<button class='ghost' aria-label='Take {esc(e['who'])} off the panel'>{icon('x')}Remove</button>", cls="inline") \
                if prep and status != "done" else ""
            rows.append([f"{self._co_who(e['who'])} <span class='chip'>{esc(e.get('kind') or 'person')}</span>", why,
                         chip(status, scls) + (" " + chip("stale", "warn") if res.get("stale") else ""), "; ".join(summary) + txt + model, rm])
        out += "<h4>Panel</h4>" + table(["Reviewer", "Why", "Status", "Results", ""], rows, "Nobody is on the panel of this round.")
        for x in rnd.get("removed") or []:
            out += f"<p class='why'>{esc(x.get('who'))} was taken off the panel by {esc(x.get('by'))} on {esc(when(x.get('at')))}.</p>"
        if current:
            out += self._co_committees_html(ctx) + self._co_compile_committee_html(ctx)
        if prep and st != "compiling":
            on = {e["who"] for e in rnd.get("panel") or []}
            fid = "add-reviewer"
            cands = [(n, f"{n} ({m.get('type') or 'person'})") for n, m in sorted(self._co_members().items(), key=lambda x: (x[1].get("type") != "cog", x[0]))
                     if n not in on]
            if cands:
                val = self._co_cv("/co-reviewer-add", fid, "who")
                opts = "".join(f"<option value='{esc(v)}'{' selected' if v == val else ''}>{esc(t)}</option>" for v, t in cands)
                inner = ("<div class='fields'>" f"<label class='f'><span>Add a reviewer</span><select name='who' required><option value=''>choose…</option>{opts}</select></label>"
                         f"<label class='f'><span>Why (shown to them)</span><input name='reason' value='{esc(self._co_cv('/co-reviewer-add', fid, 'reason'))}'></label></div>"
                         f"<div class='actions'><button class='secondary'>{icon('plus')}Add to the panel</button>"
                         "<span class='why'>They are asked at once: a cog through its queue, a person in their inbox.</span></div>")
                out += self._co_form("/co-reviewer-add", co, fid, inner, cls="sform card", label="Add a reviewer")
        return out + "</div>"

    def _co_committees_html(self, ctx: dict) -> str:
        """Each committee on the panel and where its consensus stands (every member agrees; one objection returns it; a
        member that could not review pauses it for the chair, who may review in its place)."""
        co = ctx["co"]
        cms = ctx["ov"].get("committees") or []
        if not cms:
            return ""
        rnd = ctx["round"] or {}
        res = rnd.get("results") or {}
        out = "<h4>Committees on the panel</h4>"
        for c in cms:
            st = c["status"]
            cls = {"agreed": "ok", "objected": "crit", "paused": "warn", "waiting": "info"}.get(st, "")
            rows = []
            for m in c["members"]:
                r = res.get(m) or {}
                obj = [o for o in c["objections"] if o["member"] == m]
                pause = next((x for x in c["paused"] if x["member"] == m), None)
                if obj:
                    where = chip("objects", "crit") + " " + esc("; ".join(o["text"] or "" for o in obj))
                elif pause:
                    where = chip("late" if pause.get("kind") == "late" else "could not review", "warn") + " " + esc(pause["why"])
                elif r.get("status") == "done":
                    where = chip("agrees", "ok") + (f" <span class='why'>{esc(short(r.get('summary') or '', 200))}</span>" if r.get("intervention") else "")
                else:
                    where = chip("waiting", "info")
                rows.append([self._co_who(m), where])
            out += (f"<div class='card'><p style='margin-top:0'><b>The {esc(c['committee'])} committee</b> {chip(st, cls)} "
                    f"<span class='why'>chair {esc(c.get('chair') or '-')}; decides by consensus: every member agrees, one objection returns it, "
                    "nothing passes by default" + (f"; due {esc(c['due'])}" if c.get("due") else "") + "</span></p>"
                    + table(["Member", "Where it stands"], rows) + f"<p class='why'>{esc(c['reason'])}.</p>")
            if self.user in c["waiting"] and not self._viewing_cog():
                out += f"<p class='co-marker'>{icon('users')} {esc(c['vote'])}</p>"
            for pz in c["paused"] if not self._viewing_cog() else []:
                if self.user not in (pz.get("interveners") or []):
                    continue
                fid = f"intervene-{anchor(c['committee'] + '-' + pz['member'])}"
                act = "/co-intervene-review"
                cv = lambda k, d="": self._co_cv(act, fid, k, d)  # noqa: E731
                choice = cv("action", "agree")
                opts = "".join(f"<option value='{v}'{' selected' if choice == v else ''}>{esc(t)}</option>" for v, t in (
                    ("agree", f"Agree in {pz['member']}'s place"), ("object", f"Object in {pz['member']}'s place"),
                    ("extend", "Extend the due date"), ("return", "Return the change for revision")))
                inner = (f"<input type='hidden' name='committee' value='{esc(c['committee'])}'><input type='hidden' name='member' value='{esc(pz['member'])}'>"
                         f"<p style='margin-top:0'><b>You intervene as {esc(pz.get('who') or 'the chair')}:</b> {esc(pz['member'])} {esc(pz['why'])}. "
                         "Decide in their place (recorded as an intervention with your meaning), extend the due date, or return the change.</p>"
                         f"<label class='f'><span>What you do</span><select name='action'>{opts}</select></label>"
                         f"<label class='f'><span>New due date <i>(to extend)</i></span><input type='date' name='due' value='{esc(cv('due'))}'></label>"
                         f"<label class='f'><span>Meaning <b aria-hidden='true'>*</b> <i>(why you intervene)</i></span>"
                         f"<input name='meaning' required value='{esc(cv('meaning'))}'></label>"
                         f"<div class='actions'><button class='secondary'>{icon('check')}Intervene for {esc(pz['member'])}</button></div>")
                out += self._co_form(act, co, fid, inner, cls="card warn", label=f"Intervene for {pz['member']}")
            out += "</div>"
        return out

    def _co_unavailable_questions(self, ctx: dict) -> list[dict]:
        return [q for q in ctx["ov"]["questions"] if q.get("code") == "member-unavailable"]

    def _co_sysadmins(self) -> list[str]:
        b = self.bundle or {}
        return sorted(p for p, m in (b.get("members") or {}).items() if m.get("type") == "person" and "system-administrator" in (m.get("roles") or []))

    def _co_compile_committee_html(self, ctx: dict) -> str:
        """The compile-frame committee: who reads the prose into the delta, and, when some of its members cannot read
        here (hosted models switched off), what that means and who can change it (the IT SOP: the installation is the
        environment; system administrators change it)."""
        try:
            cmt = self.hub.compile_committee()
        except Exception:
            cmt = None
        if not cmt:
            return ""
        un = cmt.get("unavailable") or {}
        names = ", ".join(n for n, _ in cmt.get("members") or [])
        if not un:
            return f"<p class='why'>{icon('users')} The compile-frame committee ({esc(names)}; chair {esc(cmt.get('chair') or '-')}) read the prose by consensus.</p>"
        qs = [q for q in self._co_unavailable_questions(ctx) if q.get("status", "open") == "open"]
        what = ("; ".join(f"{n}: {w}" for n, w in un.items()))
        return (f"<div class='card warn' role='status' id='co-committee-unavailable'><p style='margin-top:0'>{icon('alert')} <b>The compile-frame committee cannot read here.</b> "
                f"{esc(what)}.</p><p>" + ("Every changed sentence was read exactly by the hub's sentence grammar, so this round did not need it. "
                                          if not qs else f"{len(qs)} question(s) say what it could not read: <a href='#co-unavailable'>see Questions</a>. ")
                + "Prose outside the grammar's sentence shapes waits for the committee, which reads by consensus.</p>"
                + self._co_fix_unavailable_html(ctx) + "</div>")

    def _co_fix_unavailable_html(self, ctx: dict) -> str:
        co = ctx["co"]
        admins = self._co_sysadmins()
        who = esc(", ".join(admins)) if admins else "a system administrator (nobody holds the role in force)"
        reported = [m for m in self.hub.inbox.all() if m.get("record") == co["locator"] and m.get("kind") == "task"
                    and str(m.get("subject") or "").startswith("Incident: the compile-frame committee")]
        out = (f"<p><b>Who can change it:</b> under the IT SOP the installation (the model server, its secrets) is the environment, changed by "
               f"the system administrators ({who}): they switch hosted models on (<code>HUBPROTO_HOSTED_MODELS=on</code> with an "
               "<code>ANTHROPIC_API_KEY</code>) and restart the hub; then request review again. Or the committee, or its members' models, change "
               "through a change order to the cog profiles.</p>")
        if reported:
            last = reported[-1]
            out += f"<p class='why'>Reported to {esc(', '.join(sorted({m['to'] for m in reported})))} on {esc(when(last.get('at')))} by {esc(last.get('from'))}.</p>"
        elif admins and not self._viewing_cog() and (ctx["mine"] or ctx["decider"]):
            out += self._co_form("/co-report-unavailable", co, "report-unavailable", f"<button class='secondary'>{icon('send')}Report it to the system administrators</button>"
                                 "<span class='why'> an incident under the IT SOP: a model unavailable</span>", cls="inline")
        return out

    def _co_item_options(self, ctx: dict) -> list[tuple[str, str]]:
        out = []
        for i, g in enumerate(ctx["groups"], 1):
            out.append((g[0]["id"], f"item {i}: {short(self._co_group_words(g), 90)}"))
        return out

    def _co_your_review_html(self, ctx: dict, rnd: dict) -> str:
        """A person on the panel posts findings (blocking or advisory), questions and suggested changes."""
        co = ctx["co"]
        fid = "review"
        act = "/co-submit-review"
        items = [("", "the change order as a whole")] + self._co_item_options(ctx)
        sops = sorted({c["name"] for c in eco.carried(self.hub, co) if c["name"]} | set(cc.touches(self.hub, co).get("sop") or set()))

        def sel(name, options, value):
            return f"<select name='{esc(name)}'>" + "".join(f"<option value='{esc(v)}'{' selected' if v == value else ''}>{esc(t)}</option>" for v, t in options) + "</select>"

        fs = ""
        for i in range(1, 4):
            fs += (f"<div class='r'><label class='f'><span>Severity <span class='vh'>of finding {i}</span></span>{sel(f'sev{i}', [('advisory', 'advisory'), ('blocking', 'blocking')], self._co_cv(act, fid, f'sev{i}', 'advisory'))}</label>"
                   f"<label class='f'><span>About <span class='vh'>finding {i}</span></span>{sel(f'fabout{i}', items, self._co_cv(act, fid, f'fabout{i}'))}</label>"
                   f"<label class='f'><span>Finding {i}</span><input name='ftext{i}' value='{esc(self._co_cv(act, fid, f'ftext{i}'))}'></label></div>")
        qs = ""
        for i in range(1, 3):
            qs += (f"<div class='r'><span></span>"
                   f"<label class='f'><span>About <span class='vh'>question {i}</span></span>{sel(f'qabout{i}', items, self._co_cv(act, fid, f'qabout{i}'))}</label>"
                   f"<label class='f'><span>Question {i}</span><input name='qtext{i}' value='{esc(self._co_cv(act, fid, f'qtext{i}'))}'></label></div>")
        ss = ""
        for i in range(1, 3):
            ss += (f"<div class='r'><label class='f'><span>SOP <span class='vh'>of suggestion {i}</span></span>{sel(f'ssop{i}', [('', 'choose…')] + [(s, s) for s in sops], self._co_cv(act, fid, f'ssop{i}'))}</label>"
                   f"<label class='f'><span>Section <span class='vh'>of suggestion {i}</span></span><input name='ssec{i}' value='{esc(self._co_cv(act, fid, f'ssec{i}'))}' placeholder='Routing'></label>"
                   f"<label class='f'><span>Suggestion {i}: a sentence it should say</span><input name='stext{i}' value='{esc(self._co_cv(act, fid, f'stext{i}'))}'></label></div>")
        rows = (f"<fieldset class='co-rows'><legend>Findings <span class='why'>(blocking or advisory)</span></legend><div class='co-review-rows'>{fs}</div></fieldset>"
                f"<fieldset class='co-rows'><legend>Questions <span class='why'>(the author answers them in the prose)</span></legend><div class='co-review-rows'>{qs}</div></fieldset>"
                f"<fieldset class='co-rows'><legend>Suggested changes <span class='why'>(a sentence the SOP should say instead or as well)</span></legend><div class='co-review-rows'>{ss}</div></fieldset>")
        inner = (f"<h3 style='margin-top:0' id='co-your-review'>Your review of round {esc(rnd['n'])}</h3>"
                 "<p class='why'>Leave a row empty to skip it. A blocking finding does not stop sending; it becomes a warning the approver "
                 "must accept. A suggested change joins the delta only if the author accepts it.</p>"
                 f"{rows}"
                 f"<label class='f'><span>Summary</span><textarea name='summary' rows='2'>{esc(self._co_cv(act, fid, 'summary'))}</textarea></label>"
                 f"<div class='actions'><button>{icon('send')}Post my review</button></div>")
        return self._co_form(act, co, fid, inner, cls="card", label=f"Your review of round {rnd['n']}")

    # ------------------------------------------------------------ 4. questions, findings, suggestions
    def _co_about(self, ctx: dict, item: str | None = None, frame: str | None = None, line=None) -> str:
        if item:
            for i, g in enumerate(ctx["groups"], 1):
                if any(x["id"] == item for x in g):
                    return f"<a href='#{anchor(g[0]['id'])}'>item {i}</a>: {esc(short(self._co_group_words(g), 120))}"
            return f"<span class='muted'>an item no longer in the delta</span>"
        if frame:
            return self._co_frame_line(frame, line)
        return "<span class='muted'>the change order as a whole</span>"

    def _co_questions_html(self, ctx: dict) -> str:
        co = ctx["co"]
        qs = ctx["ov"]["questions"]
        out = f"<section class='co-sec' id='co-questions'><h2>{icon('help')}Questions</h2>"
        if not qs:
            return out + "<p class='muted'>No question has been asked.</p></section>"
        unav = [q for q in qs if q.get("code") == "member-unavailable"]
        if unav:
            out += self._co_unavailable_card(ctx, unav)
            qs = [q for q in qs if q.get("code") != "member-unavailable"]
            if not qs:
                return out + "</section>"
        out += ("<p class='why'>Answer a question in the prose first (the next compile closes a compiler question that no longer arises); "
                "then mark it answered with the line that says it. An open question becomes a warning the approver must accept.</p><ul class='notes'>")
        for q in qs:
            status = q.get("status", "open")
            raised = q.get("raised_by") or "the compile"
            body = (f"<li id='q-{anchor(q['id'])}'>{icon('help')}<div><p style='margin:0'><b>{esc(q.get('text'))}</b> {chip(status, 'warn' if status == 'open' else 'ok')}</p>"
                    f"<p class='why' style='margin:0'>Asked by {esc(raised)}" + (f" in round {esc(q.get('round'))}" if q.get("round") else "")
                    + f"; about {self._co_about(ctx, q.get('item'), q.get('frame'), q.get('line'))}.</p>")
            if status != "open" and q.get("answer"):
                body += (f"<p style='margin:var(--sp-1) 0 0'>Answer by {esc(q.get('by'))}: “{esc(q.get('answer'))}”"
                         + (f" ({self._co_frame_line(q.get('frame') or next(iter(sorted(cc.touches(self.hub, co).get('sop') or [])), None), q.get('answer_line'))})"
                            if q.get("answer_line") else "") + "</p>")
            if status == "open" and ctx["answerer"]:
                fid = f"answer-{anchor(q['id'])}"
                inner = (f"<input type='hidden' name='qid' value='{esc(q['id'])}'><div class='fields'>"
                         f"<label class='f'><span>Your answer <b aria-hidden='true'>*</b></span><textarea name='answer' rows='2' required>{esc(self._co_cv('/co-answer', fid, 'answer'))}</textarea></label>"
                         f"<label class='f'><span>The line of the prose that says it</span><input name='line' inputmode='numeric' size='6' value='{esc(self._co_cv('/co-answer', fid, 'line'))}'></label></div>"
                         f"<div class='actions'><button class='secondary'>{icon('check')}Mark answered</button></div>")
                body += details("Answer it", self._co_form("/co-answer", co, fid, inner, label=f"Answer: {short(q.get('text') or '', 60)}"),
                                open_=bool(self.err and self.route == "/co-answer" and self.vals.get("form") == fid))
            out += body + "</div></li>"
        return out + "</ul></section>"

    def _co_unavailable_card(self, ctx: dict, unav: list[dict]) -> str:
        """The compile-frame committee's member-unavailable questions as one card: who could not read what, why, who can
        change it, and one answer for them all (each stays a warning the approver accepts)."""
        co = ctx["co"]
        open_ = [q for q in unav if q.get("status", "open") == "open"]
        members = sorted({q.get("member") or "?" for q in unav})
        frames = sorted({q.get("frame") or "?" for q in unav})
        chair = next((q.get("chair") for q in unav if q.get("chair")), "")
        try:
            why = (self.hub.compile_committee() or {}).get("unavailable") or {}
        except Exception:
            why = {}
        out = (f"<div class='card warn' id='co-unavailable'><h3 style='margin-top:0'>{icon('alert')}The compile-frame committee could not read "
               f"{esc(', '.join(frames))} <span class='muted'>{len(unav)} question(s), {len(open_)} open</span></h3>"
               "<p>It reads by consensus, so the prose it could not read is not in the delta until it can: "
               + "; ".join(f"<b>{esc(m)}</b> ({esc(why.get(m) or 'not available')})" for m in members)
               + (f". Its chair is {esc(chair)}." if chair else ".") + "</p>" + self._co_fix_unavailable_html(ctx))
        if open_ and ctx["answerer"] and ctx["editable"] and not self._viewing_cog():
            fid = "answer-unavailable"
            act = "/co-answer-all"
            inner = (f"<label class='f'><span>One answer for the {len(open_)} open question(s) <b aria-hidden='true'>*</b> "
                     "<i>(for example: what was read, and how it was checked)</i></span>"
                     f"<textarea name='answer' rows='2' required>{esc(self._co_cv(act, fid, 'answer'))}</textarea></label>"
                     f"<div class='actions'><button class='secondary'>{icon('check')}Mark them answered</button>"
                     "<span class='why'>The approver still accepts each as a warning; another round with the committee available closes them.</span></div>")
            out += details("Answer them together", self._co_form(act, co, fid, inner, label="Answer the committee's questions"),
                           open_=bool(self.err and self.route == act))
        lis = "".join(f"<li>{esc(q.get('frame') or '')}: {esc(q.get('member') or '')} {chip(q.get('status', 'open'), 'warn' if q.get('status', 'open') == 'open' else 'ok')}"
                      + (f" <span class='why'>answered by {esc(q.get('by'))}: “{esc(q.get('answer'))}”</span>" if q.get("answer") else "") + "</li>" for q in unav)
        return out + details("Each question", f"<ul>{lis}</ul>") + "</div>"

    def _co_findings_html(self, ctx: dict) -> str:
        fs = ctx["ov"]["findings"]
        out = f"<section class='co-sec' id='co-findings'><h2>{icon('flag')}Findings</h2>"
        if not fs:
            return out + "<p class='muted'>No finding is open.</p></section>"
        rows = [[chip(x.get("severity"), "crit" if x.get("severity") == "blocking" else ""), esc(x.get("text")),
                 f"{esc(x.get('who'))} <span class='why'>round {esc(x.get('round'))}</span>",
                 self._co_about(ctx, (x.get("about") or {}).get("item"), (x.get("about") or {}).get("frame"), (x.get("about") or {}).get("line"))
                 + (" " + chip("stale", "warn") if x.get("stale") else "")]
                for x in sorted(fs, key=lambda x: (x.get("severity") != "blocking", x.get("who") or ""))]
        out += table(["Severity", "Finding", "By", "About"], rows)
        out += ("<p class='why'>A finding stands until its reviewer's next review leaves it out. An open blocking finding does not stop sending; "
                "it becomes a warning the approver accepts.</p>")
        return out + "</section>"

    def _co_suggestions_html(self, ctx: dict) -> str:
        co = ctx["co"]
        ss = ctx["ov"]["suggestions"]
        out = f"<section class='co-sec' id='co-suggestions'><h2>{icon('note')}Suggested changes</h2>"
        if not ss:
            return out + "<p class='muted'>No reviewer has suggested a change.</p></section>"
        may = ctx["mine"] and ctx["editable"]
        out += "<ul class='notes'>"
        for s in ss:
            st = s.get("status") or "open"
            ch = s.get("change") or {}
            rule = ch.get("rule")
            what = (f"<p class='why' style='margin:0'>It would {'remove' if ch.get('op') == 'remove' else 'add'}: {esc(self._say(rule))}</p>" if rule else
                    "<p class='why' style='margin:0'>Words only: if you accept it, write it into the prose yourself.</p>")
            where = (f" in {esc(s.get('frame'))}" + (f", section {esc(s.get('section'))}" if s.get("section") else "")) if s.get("frame") else ""
            state = chip(st, "warn" if st == "open" else ("ok" if st == "accepted" else "crit"))
            if st != "open":
                state += f" <span class='why'>by {esc(s.get('decided_by'))}" + (f": {esc(s.get('reason'))}" if s.get("reason") else "") + "</span>"
            body = (f"<li id='s-{anchor(s['id'])}'>{icon('note')}<div><p style='margin:0'><b>“{esc(s.get('text'))}”</b>{where} {state}</p>"
                    f"<p class='why' style='margin:0'>Suggested by {esc(s.get('by'))} in round {esc(s.get('round'))}.</p>{what}")
            if may and st == "open":
                fid = f"sug-{anchor(s['id'])}"
                acc = self._co_form("/co-suggestion", co, fid + "-a", f"<input type='hidden' name='sid' value='{esc(s['id'])}'><input type='hidden' name='action' value='accept'>"
                                    f"<button class='secondary'>{icon('check')}Accept</button>", cls="inline")
                rej = self._co_form("/co-suggestion", co, fid + "-r", f"<input type='hidden' name='sid' value='{esc(s['id'])}'><input type='hidden' name='action' value='reject'>"
                                    f"<label class='f'><span>Why not</span><input name='reason' value='{esc(self._co_cv('/co-suggestion', fid + '-r', 'reason'))}'></label>"
                                    f"<button class='ghost reject'>{icon('x')}Reject</button>", cls="sform row")
                body += f"<div class='actions'>{acc}</div>{details('Reject it', rej)}"
            out += body + "</div></li>"
        return out + "</ul><p class='why'>An accepted suggestion that carries a change becomes a delta item (suggested by the reviewer, accepted by you); request review again to have it written into the prose.</p></section>"

    # ------------------------------------------------------------ 5. the delta
    def _co_group_words(self, g: list[dict]) -> str:
        c = g[0]
        op = item_op(c)
        new = cc._effective(c) if c.get("op") != "remove" else None
        old = c.get("changes_from") or c.get("was") or (c["rule"] if c.get("op") == "remove" else None)
        if (new or old or {}).get("kind") == "process":
            from ..workflow_model import process_change_reason

            return process_change_reason(old, new)
        if op == "change":
            return f"{self._say(old)} → {self._say(new)}"
        if op == "remove":
            return f"no longer: {self._say(old)}"
        return self._say(new)

    def _co_group_html(self, g: list[dict]) -> str:
        c = g[0]
        op = item_op(c)
        new = cc._effective(c) if c.get("op") != "remove" else None
        old = c.get("changes_from") or c.get("was") or (c["rule"] if c.get("op") == "remove" else None)
        if (new or old or {}).get("kind") == "process":  # a workflow: one line per change of meaning (workflow_model), no ids
            from ..workflow_model import process_change_words

            words = process_change_words(old, new)
            name = (new or old).get("name")
            return (f"The <a href='{href('/workflows/' + quote(str(name)), self.user)}'>{esc(name)} workflow</a> "
                    + (f"changes ({len(words)}):" if old is not None and new is not None else "")
                    + "<ul class='co-parts'>" + "".join(f"<li>{esc(w)}</li>" for w in words or ["restated, no change of meaning"]) + "</ul>")
        if op == "change":
            return f"{esc(self._say(new))}<span class='was'>was: <del>{esc(self._say(old))}</del></span>"
        if op == "remove":
            return f"No longer: <del>{esc(self._say(old))}</del>"
        return esc(self._say(new))

    def _co_origin_html(self, c: dict) -> str:
        o = c.get("origin") or {}
        kind = o.get("kind") or "prose"
        bits = []
        if kind == "prose":
            bits.append(f"Read from {self._co_frame_line(o.get('frame'), o.get('line')) or 'the prose'}"
                        + (f", written by {esc(o.get('by'))}" if o.get("by") else "")
                        + ("; read by the " + esc(o["read_by"]) + " cog" if o.get("read_by") else "; read exactly by the hub's sentence grammar (no model)"))
        elif kind == "suggested":
            bits.append(f"Suggested by {esc(o.get('by'))}, accepted by {esc(o.get('accepted_by'))}"
                        + (f" ({esc(when(o.get('at')))})" if o.get("at") else ""))
        else:
            bits.append(f"Made directly by {esc(o.get('by'))}" + (f" in the {esc(o['editor'])} editor" if o.get("editor") else "")
                        + (f" on {esc(when(o.get('at')))}" if o.get("at") else ""))
        if kind != "prose":
            if c.get("unstated"):
                bits.append("not stated in the prose: once approved it is in force while the SOP says otherwise (a divergence)")
            elif o.get("line"):
                bits.append(f"stated in {self._co_frame_line(o.get('frame'), o.get('line'))}")
        return "<p class='why' style='margin:var(--sp-1) 0'>" + "; ".join(bits) + ".</p>"

    def _co_reads_html(self, c: dict) -> str:
        """The model's (or the author's) quote next to the hub's explained sentence."""
        o = c.get("origin") or {}
        quote_ = o.get("quote")
        mine = self._say(cc._effective(c) if c.get("op") != "remove" else c["rule"])
        out = "<div class='co-reads'>"
        if quote_:
            out += (f"<div><h4>The SOP says ({self._co_frame_line(o.get('frame'), o.get('line'))})</h4><q>{esc(quote_)}</q></div>")
        else:
            out += "<div><h4>The SOP says</h4><span class='muted'>nothing yet: no line states it</span></div>"
        out += f"<div><h4>The hub reads it as</h4>{esc(mine)}</div></div>"
        return out

    def _co_delta_html(self, ctx: dict) -> str:
        co, p = ctx["co"], ctx["p"]
        f = co["fields"]
        out = f"<section class='co-sec' id='delta'><h2>{icon('layers')}The delta</h2>"
        out += self._impact_html([c["name"] for c in eco.carried(self.hub, co) if c.get("name")])
        if f.get("staged_errors"):
            out += ("<div class='card crit' role='alert'><p><b>The delta does not compile.</b> Fix these in the revisions:</p><ul>"
                    + "".join(f"<li>{esc(e)}</li>" for e in f["staged_errors"]) + "</ul></div>")
        if not ctx["current"]:
            pv = self._co_preview(ctx)
            if p is not None and p.get("status") == "open" and ctx["groups"]:
                out += ("<div class='card warn' role='status'>What it carries changed since the last round: the items below are that round's. "
                        "Request review again to compile it as it stands.</div>")
            elif pv is None:
                return out + ("<p class='muted'>Nothing compiled yet: the delta is compiled in the change order's review rounds.</p>" if not f.get("staged_errors") else "") + "</section>"
            if pv is not None and not ctx["groups"]:
                if pv.get("errors"):
                    out += ("<div class='card warn' role='status'><p><b>The drafts do not compile as they stand.</b> Fix these first:</p><ul>"
                            + "".join(f"<li>{esc(e)}</li>" for e in pv["errors"]) + "</ul></div>")
                rows = [f"<li><b>{esc(r['verb'])}:</b> {esc(r['text'])}" + (f" <span class='why'>was: {esc(r['was'])}</span>" if r.get("was") else "") + "</li>"
                        for r in self._change_rows(pv)]
                return out + ("<p class='why'>Preview from the drafts as they stand. The delta that counts is compiled in each review round "
                              "(request review), and the change order goes for approval once a round has run on what it carries.</p>"
                              + ("<ul class='staged'>" + "".join(rows) + "</ul>" if rows else "<p class='muted'>No rule changes in the drafts.</p>")) + "</section>"
        gs = ctx["groups"]
        n_w = sum(1 for g in gs if any(cc._widening(x) for x in g))
        states = [group_state(g) for g in gs]
        if p is not None:
            out += (f"<p class='why'>What the configuration becomes when {esc(co['number'])} is released, against the release in force "
                    f"(<a href='{href('/proposal/' + p['id'], self.user)}'>proposal #{esc(p.get('number'))}</a>): {len(gs)} item(s), "
                    f"{states.count('approved')} approved, {states.count('rejected')} rejected, {states.count('pending')} pending"
                    + (f"; {n_w} widening(s) need both policy owners" if n_w else "") + ". Each says where it came from.</p>")
        if not gs:
            out += "<p class='muted'>No configuration item: the prose changes wording only (or nothing).</p>"
        else:
            out += "<ol class='co-delta'>" + "".join(self._co_item_html(ctx, i, g) for i, g in enumerate(gs, 1)) + "</ol>"
        out += self._co_not_decided_html(ctx)
        return out + "</section>"

    def _co_item_html(self, ctx: dict, n: int, g: list[dict]) -> str:
        co = ctx["co"]
        c = g[0]
        op = item_op(c)
        sign, word, cls = SIGN[op]
        st = group_state(g)
        widening = any(cc._widening(x) for x in g)
        stale = any(x.get("stale") for x in g)
        chips = chip(st, {"approved": "ok", "rejected": "crit"}.get(st, "warn"))
        if widening:
            have = sorted({d["by"] for d in c.get("decisions") or [] if d["verdict"] == "approve" and not d.get("stale")})
            chips += " " + chip("widening: needs both policy owners", "warn") + (f" <span class='why'>approved by {esc(', '.join(have))}</span>" if have and st != "approved" else "")
        if stale:
            chips += " " + chip("stale: decide it again", "warn")
        if c.get("unstated"):
            chips += " " + chip("not in the prose", "warn")
        out = (f"<li id='{anchor(c['id'])}' class='st-{st}'><div class='co-item-head'><span class='co-sign {cls}' aria-hidden='true'>{sign}</span>"
               f"<span class='what'><span class='vh'>Item {n}, {word.lower()}: </span><span aria-hidden='true'>{n}. </span>{self._co_group_html(g)}</span>{chips}</div>")
        out += self._co_origin_html(c) + self._co_reads_html(c)
        if c.get("override"):
            ov = c["override"]
            out += (f"<p class='co-marker'><b>Overridden</b> by {esc(ov.get('by'))} ({esc(when(ov.get('at')))}): in force will be “{esc(self._say(ov.get('rule')))}”"
                    f" while the prose says otherwise. “{esc(ov.get('meaning') or '')}”</p>")
        ws = [(x, w) for x in g for w in x.get("warnings") or []]
        if ws:
            out += "<ul class='co-warn' aria-label='Warnings on this item'>" + "".join(
                f"<li>{icon('alert')} <b>{esc(WARNING_WORDS.get(w['code'], w['code']))}:</b> {esc(w['text'])}"
                + (f" <span class='chip ok'>accepted by {esc(w['accepted'].get('by'))}</span> <span class='why'>“{esc(w['accepted'].get('meaning') or '')}”</span>"
                   if w.get("accepted") else "") + "</li>" for x, w in ws) + "</ul>"
        decs = [d for x in g for d in x.get("decisions") or []]
        if decs:
            seen = set()
            lis = []
            for d in decs:
                k = (d["by"], d["verdict"], d.get("meaning"), d.get("stale"))
                if k in seen:
                    continue
                seen.add(k)
                lis.append(f"<li>{esc(d['by'])} {esc(d['verdict'])}d on {esc(when(d.get('at')))}: “{esc(d.get('meaning') or '')}”"
                           + (f" (accepted {len(d.get('accepted_warnings') or [])} warning(s))" if d.get("accepted_warnings") else "")
                           + (" " + chip("stale", "warn") if d.get("stale") else "") + "</li>")
            out += "<ul class='co-decisions' aria-label='Decisions'>" + "".join(lis) + "</ul>"
        if ctx["decider"] and ctx["stage"] == "awaiting-approval" and not self._viewing_cog():
            out += self._co_decide_form(ctx, n, g)
        return out + "</li>"

    def _co_decide_form(self, ctx: dict, n: int, g: list[dict]) -> str:
        co = ctx["co"]
        c = g[0]
        act = "/co-decide"
        fid = f"decide-{anchor(c['id'])}"
        accepted_before = self._co_cmulti(act, fid, "accept")
        open_ws = [w for x in g for w in x.get("warnings") or [] if not w.get("accepted")]
        boxes = "".join(f"<label class='check'><input type='checkbox' name='accept' value='{esc(w.get('ref') or w['code'])}'"
                        f"{' checked' if (w.get('ref') or w['code']) in accepted_before else ''}><span>I accept: {esc(w['text'])}</span></label>" for w in open_ws)
        mine = next((d for d in c.get("decisions") or [] if d["by"] == self.user and not d.get("stale")), None)
        inner = "".join(f"<input type='hidden' name='item' value='{esc(x['id'])}'>" for x in g)
        if mine:
            inner += f"<p class='why'>You {esc(mine['verdict'])}d it: deciding again replaces your decision.</p>"
        if boxes:
            inner += (f"<fieldset class='co-accept'><legend>To approve it, accept each warning</legend>{boxes}</fieldset>")
        inner += ("<div class='row2'>"
                  f"<label class='f'><span>Meaning of your decision <b aria-hidden='true'>*</b> <i>(what you confirm, in your words)</i></span>"
                  f"<input name='meaning' required value='{esc(self._co_cv(act, fid, 'meaning'))}' aria-describedby='{fid}-h'></label>"
                  f"<div class='actions'><button name='verdict' value='approve'>{icon('check')}Approve item {n}</button>"
                  f"<button name='verdict' value='reject' class='reject'>{icon('x')}Reject item {n}</button></div></div>"
                  f"<p class='why' id='{fid}-h'>Approve: it goes into force with the change order. Reject: it does not; if the prose states it, "
                  "the configuration then differs from the prose (a divergence the release records).</p>")
        if c.get("op") != "remove":
            rule = cc._effective(c)
            fields = [(k, v) for k, v in rule.items() if k not in ("kind", "source", "key") and (v is None or isinstance(v, (str, int, float, bool)))]
            if fields:
                ctl = "".join(f"<label class='f'><span>{esc(k.replace('_', ' '))}</span><input name='ov_{esc(k)}' "
                              f"value='{esc(self._co_cv(act, fid, 'ov_' + k, '' if v is None else str(v)))}'></label>" for k, v in fields)
                ov_open = bool(self.err and self.route == act and self.vals.get("form") == fid and self.vals.get("verdict") == "override")
                inner += (f"<details class='co-override'{' open' if ov_open else ''}><summary>Override the value</summary>"
                          f"<p class='why'>Edit what goes into force. The override is a warning you accept with your approval; the configuration "
                          f"may then differ from the prose, which the release records as a divergence with a change request to the SOP's owner.</p>"
                          f"<div class='fields'>{ctl}</div><div class='actions'><button name='verdict' value='override' class='secondary'>"
                          f"{icon('edit')}Approve item {n} with these values</button></div></details>")
        form = self._co_form(act, co, fid, inner, cls="co-decide", label=f"Decide item {n}")
        if mine and not (self.err and self.route == act and self.vals.get("form") == fid):
            return details(f"Change your decision on item {n}", form, cls="co-redecide")
        return form

    def _co_not_decided_html(self, ctx: dict) -> str:
        """Items shown but not decided: wording changes (approved with the change order), rules that only moved in their file,
        record-type changes (approved with the change order)."""
        from .. import recordtypes

        co, p = ctx["co"], ctx["p"]
        lis = []
        for c in ctx["ov"]["delta"]:
            if c.get("op") == "text":
                frame = c.get("frame") or (c.get("rule") or {}).get("frame") or ""
                by = (c.get("origin") or {}).get("by")
                lis.append(f"<li>Wording of {self._co_frame_line(frame, None)} changed" + (f" by {esc(by)}" if by else "")
                           + " <span class='why'>(no rule changes with it; approved with the change order)</span></li>")
        for m in (p or {}).get("moved") or []:
            lis.append(f"<li>{esc(m.get('kind'))} {esc(m.get('name'))} moved from line {esc(m.get('from_line'))} to line {esc(m.get('to_line'))} "
                       "<span class='why'>(no effect: nothing to decide)</span></li>")
        for it in recordtypes.delta_items(self.hub, co):
            lis.append(f"<li>Record type: {esc(it.get('reason'))} <span class='why'>(approved with the change order"
                       + ("; " + esc("; ".join(it.get("errors") or [])) if it.get("errors") else "") + ")</span></li>")
        if not lis:
            return ""
        return "<h3>Shown, not decided item by item</h3><ul>" + "".join(lis) + "</ul>"

    # ------------------------------------------------------------ 6. state map
    def _co_state_map_html(self, ctx: dict) -> str:
        co = ctx["co"]
        rows = state_rows(self.hub, co, ctx["p"], ctx["ov"]["state_questions"], self.bundle or {})
        out = f"<section class='co-sec' id='co-state-map'><h2>{icon('move')}State map</h2>"
        if not rows:
            return out + "<p class='muted'>No item is in a state this change removes.</p></section>"
        may = ctx["mine"] and not eco.finished(co)
        trs = []
        for r in rows:
            n = r["count"]
            rt = r.get("record_type") or ""
            count = f"<a href='{href('/items/' + rt, self.user, state=r['state'])}'>{n}</a>" if rt and n else str(n)
            fid = f"sm-{anchor(r['process'] + '-' + r['state'])}"
            if may and r["options"]:
                val = self._co_cv("/co-state-map", fid, "to", r.get("answer") or "")
                opts = "".join(f"<option value='{esc(v)}'{' selected' if v == val else ''}>{esc(t)}</option>" for v, t in r["options"])
                move = self._co_form("/co-state-map", co, fid, f"<input type='hidden' name='process' value='{esc(r['process'])}'>"
                                     f"<input type='hidden' name='state' value='{esc(r['state'])}'>"
                                     f"<label class='f'><span class='vh'>Move {esc(r['name'])} items to</span><select name='to' required><option value=''>choose…</option>{opts}</select></label>"
                                     f"<button class='secondary'>{icon('check')}Save</button>", cls="sform row")
            else:
                move = esc(dict(r["options"]).get(r.get("answer"), r.get("answer")) or "not answered")
            trs.append([f"{esc(r['process'])}: {esc(r['name'])}" + ("" if r.get("answer") else " " + chip("needs an answer", "warn")), count, move,
                        esc(r.get("consequences") or "")])
        out += table(["Old status", "Items now", "Move to", "Consequences"], trs)
        out += ("<p class='why'>Where items in a removed status go is required before approval. The release moves them without running checks "
                "or actions, withdraws the waiting decisions and records the move on each item.</p>")
        return out + "</section>"

    # ------------------------------------------------------------ 7. each cog
    def _co_cogs_html(self, ctx: dict) -> str:
        co = ctx["co"]
        out = f"<section class='co-sec' id='co-cogs'><h2>{icon('robot')}What this does to each cog</h2>"
        p, delta, note = ctx["p"], ctx["ov"]["delta"], ""
        if p is None or not ctx["current"]:
            pv = self._co_preview(ctx)
            if pv is not None and not pv.get("errors") and pv.get("rules"):
                p, delta, note = pv, pv.get("changes") or [], " From the drafts as they stand (a review round compiles the delta that counts)."
        try:
            effects = cog_effects(self.hub, co, p, delta, self._say)
        except Exception as e:  # never let a derived view stop the page
            log_line(f"cog effects failed for {co['number']}: {type(e).__name__}: {e}")
            return out + "<p class='muted'>Could not work out the effect on cogs for this delta.</p></section>"
        if not effects:
            return out + "<p class='muted'>No cog's roles, context, permissions, routes, approval criteria, duties or steps change.</p></section>"
        out += ("<p class='why'>Derived from the delta, as if every item were approved as it stands (overrides included). A cog is treated like a "
                "person in its position: what it gains here is what an SOP gives it or its roles; a widening needs both policy owners." + note
                + "</p><div class='co-cogs'>")
        names = dict(CATEGORIES)
        for e in effects:
            dl = ""
            for key, _ in CATEGORIES:
                g = e["gained"].get(key) or []
                ls = e["lost"].get(key) or []
                if not g and not ls:
                    continue
                lis = "".join(f"<li><span class='co-gain' aria-hidden='true'>+</span><span class='vh'>gains: </span> {esc(x['text'])}"
                              + (" " + chip("widening", "warn") if x["widening"] else "") + "</li>" for x in g)
                lis += "".join(f"<li><span class='co-loss' aria-hidden='true'>−</span><span class='vh'>loses: </span> <del>{esc(t)}</del></li>" for t in ls)
                dl += f"<dt>{esc(names[key])}</dt><dd><ul>{lis}</ul></dd>"
            head = (f"{self._co_who(e['cog'])}" + (" " + chip("new cog", "info") if e["new"] else "") + (" " + chip("leaves", "crit") if e["gone"] else "")
                    + f" <span class='why'>{esc(', '.join(r.replace('-', ' ') for r in e['roles']))}</span>")
            out += f"<div class='card'><h3 style='margin-top:0'>{head}</h3><dl>{dl}</dl></div>"
        return out + "</div></section>"

    # ------------------------------------------------------------ 8. related
    def _co_related_html(self, ctx: dict) -> str:
        co = ctx["co"]
        bc = ctx["f"].get("baseline_changed") or {}
        out = f"<section class='co-sec' id='co-related'><h2>{icon('git-merge')}Related changes</h2>"
        flagged = [r for r in self._change_requests() if r["number"] in (ctx["f"].get("implements") or []) and cc.cr_flag_open(self.hub, r)]
        if not bc and not flagged:
            return out + "<p class='muted'>Nothing related was released since this change order was last reviewed.</p></section>"
        if bc:
            kind = bc.get("kind")
            out += (f"<div class='card{'' if bc.get('dismissed') else ' warn'}'><p style='margin-top:0'><b>Baseline changed by {esc(bc.get('by'))}</b> "
                    f"({'related by definition: the hub found it' if kind == 'definition' else 'related by meaning: ' + esc(bc.get('cog') or 'a cog') + ' found it'}, "
                    f"{esc(when(bc.get('at')))}).</p><ul>" + "".join(f"<li>{esc(r)}</li>" for r in bc.get("reasons") or []) + "</ul>")
            if bc.get("dismissed"):
                d = bc["dismissed"]
                out += f"<p>Dismissed by {esc(d.get('by'))}: “{esc(d.get('reason'))}”; accepted: “{esc(d.get('meaning'))}”.</p>"
            elif kind == "definition":
                out += "<p class='why'>Found by the hub (the same SOP, process, rule, record type, form or role): it cannot be dismissed. Run another review round.</p>"
            elif self.user == ctx["requester"]:
                fid = "dismiss"
                act = "/co-dismiss-related"
                inner = ("<div class='fields'>"
                         f"<label class='f'><span>Why it is not related <b aria-hidden='true'>*</b></span><textarea name='reason' rows='2' required>{esc(self._co_cv(act, fid, 'reason'))}</textarea></label>"
                         f"<label class='f'><span>I accept: <b aria-hidden='true'>*</b> <i>(the warning recorded with the delta)</i></span><input name='meaning' required value='{esc(self._co_cv(act, fid, 'meaning'))}'></label></div>"
                         f"<div class='actions'><button class='secondary'>{icon('check')}Dismiss the flag</button></div>")
                out += details("Dismiss it (a flag a cog found by meaning)", self._co_form(act, co, fid, inner, label="Dismiss the related-change flag"),
                               open_=bool(self.err and self.route == act))
            else:
                out += f"<p class='why'>{esc(ctx['requester'])} may dismiss a flag found by meaning, with a reason and an accepted warning.</p>"
            out += "</div>"
        for r in flagged:
            fl = r["fields"]["flagged"]
            out += (f"<div class='card warn'><b>{self._record_link(r['locator'], with_summary=False)} is flagged by {esc(fl.get('by'))}</b> "
                    f"({esc(fl.get('cog'))}): {esc(fl.get('reason'))}. No change order implementing it may be approved until "
                    f"{esc(fl.get('approver'))} has reviewed it again" + (f" ({esc(fl['task'])})" if fl.get("task") else "") + ".</div>")
        return out + "</section>"

    # ------------------------------------------------------------ readiness in words
    ITEM_REASONS = ("not decided yet:", "widening:", "decided before the baseline changed", "accept the warning on “")

    def _co_ready_lines(self, co: dict, delta: list[dict], reasons: list[str]) -> list[str]:
        """What still stops the signature, as HTML lines: one per item of the delta (numbered as on the page, linked), then
        the change order's own reasons (readiness, warnings on it as a whole, the state map)."""
        out = []
        base = self._co_href(co)
        owners = set(cc._owners(self.hub))
        for n, g in enumerate(groups(delta), 1):
            c = g[0]
            link = f"<a href='{base}#{anchor(c['id'])}'>item {n}</a>"
            words = esc(short(self._co_group_words(g), 140))
            st = group_state(g)
            if st == "pending":
                if any(x.get("stale") for x in g):
                    out.append(f"{link} was decided before the baseline changed: decide it again ({words})")
                elif any(cc._widening(x) for x in g):
                    have = sorted({d["by"] for d in c.get("decisions") or [] if d["verdict"] == "approve" and not d.get("stale")} & owners)
                    out.append(f"{link} widens authority: both policy owners approve it" + (f" (approved by {esc(', '.join(have))})" if have else "") + f": {words}")
                else:
                    out.append(f"{link} is not decided yet: {words}")
            elif st == "approved":
                for x in g:
                    for w in x.get("warnings") or []:
                        if not w.get("accepted"):
                            out.append(f"{link}: accept the warning “{esc(short(w['text'], 160))}”")
        out += [esc(r) for r in reasons if not r.startswith(self.ITEM_REASONS)]
        return out

    # ------------------------------------------------------------ 9. approval
    def _co_approval_html(self, ctx: dict) -> str:
        co, stage = ctx["co"], ctx["stage"]
        ov = ctx["ov"]
        out = f"<section class='co-sec' id='co-approval'><h2>{icon('check-circle')}Approval</h2>"
        if stage == "released":
            return out + "<p class='status'>Released.</p></section>"
        if stage == "approved":
            return out + "<p class='status'>Approved: being released.</p></section>"
        if stage in cc.EDITABLE:
            if ov["send_reasons"]:
                out += ("<h3>Before it can be sent for approval</h3><ul class='blockers'>" + "".join(f"<li>{esc(b)}</li>" for b in ov["send_reasons"]) + "</ul>")
            else:
                out += ("<p class='ok'>" + icon("check") + "A review round has run on what it carries and its delta compiles: "
                        + (f"{esc(ctx['requester'])} may send it for approval." if not ctx["mine"] else "you may send it for approval.") + "</p>")
                if ctx["mine"]:
                    out += f"<div class='actions'>{self._co_send_form(ctx)}</div>"
            out += ("<p class='why'>Open findings and questions do not stop sending: they become warnings the approver must accept. The quality "
                    "manager signs it; the signature also approves every revision it carries. Whoever contributed to it may not sign it.</p>")
            return out + "</section>"
        # awaiting approval
        reasons = ov["approval_reasons"]
        if reasons:
            lines = self._co_ready_lines(co, ov["delta"], reasons)
            out += "<h3>Before it can be approved</h3><ul class='blockers'>" + "".join(f"<li>{b}</li>" for b in lines) + "</ul>"
        else:
            out += "<p class='ok'>" + icon("check") + "Every item is decided and every warning accepted: it may be signed at its Gate.</p>"
        ws = ov["warnings"]
        unav_ids = {q["id"] for q in self._co_unavailable_questions(ctx)}
        grouped = [w for w in ws if w.get("ref") in unav_ids and not w.get("accepted")]
        if len(grouped) > 1 and ctx["decider"] and not self._viewing_cog():
            fid = "accept-unavailable"
            act = "/co-accept-all"
            inner = ("".join(f"<input type='hidden' name='ref' value='{esc(w['ref'])}'>" for w in grouped)
                     + f"<label class='check'><input type='checkbox' name='confirm' value='1' required> I accept: the compile-frame committee could not read "
                       f"part of the prose ({len(grouped)} warnings, listed below)</label>"
                       f"<label class='f'><span>Meaning <b aria-hidden='true'>*</b></span><input name='meaning' required value='{esc(self._co_cv(act, fid, 'meaning'))}'></label>"
                       f"<button class='secondary'>{icon('check')}Accept the {len(grouped)} warnings</button>")
            out += self._co_form(act, co, fid, inner, cls="sform card warn", label="Accept the committee's warnings together")
        if ws:
            out += "<h3>Warnings on the change order as a whole</h3><ul class='co-warn'>"
            for w in ws:
                line = f"<li>{icon('alert')} <b>{esc(WARNING_WORDS.get(w['code'], w['code']))}:</b> {esc(w['text'])}"
                if w.get("accepted"):
                    line += f" <span class='chip ok'>accepted by {esc(w['accepted'].get('by'))}</span> <span class='why'>“{esc(w['accepted'].get('meaning') or '')}”</span>"
                elif ctx["decider"] and not self._viewing_cog():
                    fid = f"accept-{anchor(w.get('ref') or w['code'])}"
                    act = "/co-accept-warning"
                    inner = (f"<input type='hidden' name='ref' value='{esc(w.get('ref') or '')}'>"
                             f"<label class='check'><input type='checkbox' name='confirm' value='1' required> I accept: {esc(short(w['text'], 200))}</label>"
                             f"<label class='f'><span>Meaning <b aria-hidden='true'>*</b></span><input name='meaning' required value='{esc(self._co_cv(act, fid, 'meaning'))}'></label>"
                             f"<button class='secondary'>{icon('check')}Accept the warning</button>")
                    line += self._co_form(act, co, fid, inner, cls="sform row", label="Accept a warning")
                out += line + "</li>"
            out += "</ul>"
        contributors = ov["contributors"]
        if contributors:
            out += f"<p class='why'>Contributors (who may not sign it): {esc(', '.join(contributors))}.</p>"
        return out + "</section>"

    # ------------------------------------------------------------ divergences (section 6): markers wherever the prose is read
    def _divergences_of(self, frame: str | None = None, digest: str | None = None) -> list[dict]:
        return [d for d in cc.open_divergences(self.hub, digest) if frame is None or d.get("frame") == frame]

    def _co_number_link(self, number: str | None) -> str:
        if not number:
            return "-"
        co = next((r for r in self.hub.kernel.query(type_="change-order") if r["number"] == number), None)
        if co is None or not self._can_read(co["folder"])["allowed"]:
            return esc(number)
        return f"<a href='{self._co_href(co)}'>{esc(number)}</a>"

    def _divergence_marker(self, d: dict, *, with_line: bool = False) -> str:
        """The marker at a line: "In force differs from this line: ... (accepted by bob on CO-2026-0007)"."""
        inf = self._say(d.get("in_force")) if d.get("in_force") else "nothing: the rule this line states is not in force"
        line = (f"{self._co_frame_line(d.get('frame'), d.get('line'))}: “{esc(d.get('prose') or '')}”. " if with_line else "")
        cr = (f" A change request is raised to bring the line in line: {esc(d['change_request'])}." if d.get("change_request") and with_line else "")
        return (f"<div class='co-marker' role='note'>{line}<b>{icon('alert')} In force differs from this line:</b> {esc(inf)} "
                f"(accepted by {esc(d.get('accepted_by') or '-')} on {self._co_number_link(d.get('co'))}"
                + (f": “{esc(d.get('meaning'))}”" if d.get("meaning") else "") + f").{cr}</div>")

    def _divergence_card(self, frame: str) -> str:
        divs = self._divergences_of(frame)
        if not divs:
            return ""
        return (f"<style>{CO_CSS}</style><div class='card warn' id='divergences' role='status'><h3 style='margin-top:0'>{icon('alert')}In force differs from "
                f"{'this line' if len(divs) == 1 else str(len(divs)) + ' lines'} of {esc(frame)}</h3>"
                + "".join(self._divergence_marker(d, with_line=True) for d in divs)
                + "<p class='why'>An approver overrode the prose on a change order. The rule in force is what the hub enforces (and what a cog "
                "reading the line is given beside it) until a change order brings the two back in line.</p></div>")

    def _divergence_line_of(self, d: dict, lines: list[str]) -> int | None:
        """Where a divergence's marker goes in these lines: its own line when that line says what it said, else the line
        that does (the text moved), else nowhere (the card above lists it)."""
        prose = (d.get("prose") or "").strip()
        n = int(d.get("line") or 0)
        if 0 < n <= len(lines) and (not prose or prose in lines[n - 1] or lines[n - 1].strip() in prose and lines[n - 1].strip()):
            return n
        if prose:
            for i, ln in enumerate(lines, 1):
                if prose in ln:
                    return i
        return None

    def _lines_with_markers(self, frame: str, lines: list[str]) -> str:
        """A text's lines in a table, a marker row under each line a divergence names."""
        at: dict[int, list[dict]] = {}
        for d in self._divergences_of(frame):
            n = self._divergence_line_of(d, lines)
            if n:
                at.setdefault(n, []).append(d)
        rows = ""
        for i, line in enumerate(lines, start=1):
            cls = " class='co-div'" if i in at else ""
            rows += f"<tr id='L{i}'{cls}><th scope='row'><a href='#L{i}' aria-label='line {i}'>{i}</a></th><td><pre class='src'>{esc(line) or ' '}</pre></td></tr>"
            for d in at.get(i, []):
                rows += f"<tr class='co-div'><th scope='row'><span class='vh'>marker for line {i}</span></th><td>{self._divergence_marker(d)}</td></tr>"
        return (f"<style>{CO_CSS}</style><div class='tbl'><table><thead><tr><th scope='col'>Line</th><th scope='col'>Text</th></tr></thead><tbody>{rows}</tbody></table></div>")

    # ------------------------------------------------------------ actions
    def _co_post(self) -> dict | None:
        """Common to every change-order action: a release in force, the one-use token, the change order. None when the
        form was sent already (the caller redirects)."""
        self._need_release()
        done = self._once_done()
        if done:
            self._redirect(done)
            return None
        return self._co_get(self._f("co"))

    def _co_done(self, co: dict, msg: str, anchor_: str = "") -> None:
        self._redirect(self._once_finish(self._co_href(co) + "&done=" + quote(msg) + (f"#{anchor_}" if anchor_ else "")))

    @staticmethod
    def _co_refused(e: Exception) -> ShellError:
        s = str(e)
        return ShellError((s[:1].upper() + s[1:]) + ("" if s.endswith(".") else "."))

    def post_co_reviewer_add(self):
        co = self._co_post()
        if co is None:
            return
        who = self._f("who")
        if not who:
            raise ShellError("Choose who to add to the panel.", field="who")
        try:
            cc.add_reviewer(self.hub, co["locator"], who, self.user, reason=self._f("reason"))
        except ValueError as e:
            raise self._co_refused(e) from None
        log_line(f"change order {co['number']} reviewer added {who} by={self.user}")
        self._co_done(co, f"{who} is on the panel and has been asked to review.", "co-review")

    def post_co_reviewer_remove(self):
        co = self._co_post()
        if co is None:
            return
        who = self._f("who")
        try:
            cc.remove_reviewer(self.hub, co["locator"], who, self.user)
        except ValueError as e:
            raise self._co_refused(e) from None
        self._co_done(co, f"{who} is off the panel.", "co-review")

    def post_co_submit_review(self):
        co = self._co_post()
        if co is None:
            return
        findings, questions, suggestions = [], [], []
        for i in range(1, 4):
            text = self._f(f"ftext{i}")
            if text:
                item = self._f(f"fabout{i}")
                findings.append({"severity": self._f(f"sev{i}") or "advisory", "text": text, "about": {"item": item} if item else {}})
        for i in range(1, 3):
            text = self._f(f"qtext{i}")
            if text:
                questions.append({"text": text, "item": self._f(f"qabout{i}") or None})
        for i in range(1, 3):
            text = self._f(f"stext{i}")
            if text:
                sop, sec = self._f(f"ssop{i}"), self._f(f"ssec{i}")
                suggestions.append({"frame": sop, "section": sec, "sentence": text} if sop and sec else {"sentence": text, "frame": sop or None})
        summary = self._f("summary")
        if not (findings or questions or suggestions or summary):
            raise ShellError("Say something: a finding, a question, a suggested change or a summary (\"nothing to add\" is a summary).")
        try:
            cc.submit_review(self.hub, co["locator"], self.user, findings=findings, questions=questions, suggestions=suggestions, summary=summary)
        except ValueError as e:
            raise self._co_refused(e) from None
        log_line(f"change order {co['number']} reviewed by={self.user} findings={len(findings)} questions={len(questions)} suggestions={len(suggestions)}")
        self._co_done(co, f"Your review is posted: {len(findings)} finding(s), {len(questions)} question(s), {len(suggestions)} suggestion(s).", "co-review")

    def post_co_suggestion(self):
        co = self._co_post()
        if co is None:
            return
        sid, action = self._f("sid"), self._f("action")
        try:
            if action == "accept":
                s = cc.accept_suggestion(self.hub, co["locator"], sid, self.user)
                msg = ("Accepted: it is a delta item now (suggested by " + str(s.get("by")) + "); request review again to have it written into the prose."
                       if s.get("change") else "Accepted: write it into the prose, then request review again.")
            elif action == "reject":
                cc.reject_suggestion(self.hub, co["locator"], sid, self.user, reason=self._f("reason"))
                msg = "Rejected; the reviewer will see your reason."
            else:
                raise ShellError("Say accept or reject.")
        except ValueError as e:
            raise self._co_refused(e) from None
        self._co_done(co, msg, "co-suggestions")

    def post_co_answer(self):
        co = self._co_post()
        if co is None:
            return
        raw = self._f("line")
        line = None
        if raw:
            try:
                line = int(raw)
            except ValueError:
                raise ShellError("The line is a number (the line of the SOP that answers it).", field="line") from None
        answer = self._f("answer")
        if not answer:
            raise ShellError("Say what the answer is (and, best, which line of the prose says it).", field="answer")
        try:
            cc.answer_question(self.hub, co["locator"], self._f("qid"), answer, self.user, line=line)
        except ValueError as e:
            raise self._co_refused(e) from None
        self._co_done(co, "The question is marked answered.", "co-questions")

    def _co_override_from_form(self, c: dict) -> dict:
        rule = cc._effective(c)
        out = {}
        for k, v in rule.items():
            if k in ("kind", "source", "key") or not (v is None or isinstance(v, (str, int, float, bool))):
                continue
            name = f"ov_{k}"
            if name not in self.form:
                continue
            raw = self._f(name)
            if isinstance(v, bool):
                new = raw.lower() in ("true", "yes", "1")
            elif isinstance(v, int):
                try:
                    new = int(raw.replace(",", ""))
                except ValueError:
                    raise ShellError(f"{k.replace('_', ' ')} is a whole number.", field=name) from None
            elif isinstance(v, float):
                try:
                    new = float(raw.replace(",", ""))
                except ValueError:
                    raise ShellError(f"{k.replace('_', ' ')} is a number.", field=name) from None
            elif v is None:
                if not raw:
                    continue
                try:
                    new = float(raw.replace(",", ""))
                except ValueError:
                    new = raw
            else:
                new = raw
            if new != v:
                out[k] = new
        if not out:
            raise ShellError("Change a value to override it, or approve the item as it stands.")
        return out

    def post_co_decide(self):
        co = self._co_post()
        if co is None:
            return
        ids = self._multi("item")
        verdict = self._f("verdict")
        meaning = self._f("meaning")
        accept = self._multi("accept")
        if verdict not in ("approve", "reject", "override"):
            raise ShellError("Approve, reject or override the item.")
        if not meaning:
            raise ShellError("A decision needs its meaning: say in a few words what you are confirming.", field="meaning")
        items = {c["id"]: c for c in cc.delta(self.hub, co["locator"])}
        group = [items[i] for i in ids if i in items]
        if not group:
            raise ShellError("That item is no longer in the delta: the page has changed, look again.")
        if verdict != "reject":  # every warning ticked before anything is recorded
            missing = [w for c in group for w in c.get("warnings") or [] if not w.get("accepted") and not cc._accepts(accept, w)
                       and not (verdict == "override" and c is group[0] and w.get("code") == "override")]
            if missing:
                raise ShellError("Tick I accept for each warning to approve it: " + "; ".join(w["text"] for w in missing[:3]) + ".")
        override = self._co_override_from_form(group[0]) if verdict == "override" else None
        try:
            for i, c in enumerate(group):
                cc.decide_item(self.hub, co["locator"], c["id"], "reject" if verdict == "reject" else "approve", self.user, meaning,
                               accept_warnings=accept, override=override if i == 0 else None)
        except ValueError as e:
            raise self._co_refused(e) from None
        log_line(f"change order {co['number']} item {ids[0]} {verdict} by={self.user}")
        words = {"approve": "Approved", "reject": "Rejected", "override": "Approved with your override"}[verdict]
        done = next((c for c in cc.delta(self.hub, co["locator"]) if c["id"] == group[0]["id"]), group[0])
        said = self._say(cc._effective(done)) if done.get("op") != "remove" else "no longer: " + self._say(done["rule"])
        self._co_done(co, f"{words}: {short(said, 140)}", anchor(group[0]["id"]))

    def post_co_accept_warning(self):
        co = self._co_post()
        if co is None:
            return
        if self._f("confirm") != "1":
            raise ShellError("Tick I accept to accept the warning.", field="confirm")
        meaning = self._f("meaning")
        if not meaning:
            raise ShellError("Say what you accept, in a few words.", field="meaning")
        try:
            cc.accept_warning(self.hub, co["locator"], self._f("ref"), self.user, meaning)
        except ValueError as e:
            raise self._co_refused(e) from None
        self._co_done(co, "The warning is accepted.", "co-approval")

    def post_co_state_map(self):
        co = self._co_post()
        if co is None:
            return
        to = self._f("to")
        if not to:
            raise ShellError("Choose where the items go.", field="to")
        if not self._co_mine(co):
            raise ShellError("Only its requester or a document controller says where the items go.", 403)
        try:
            cc.set_state_map(self.hub, co["locator"], self._f("process"), {self._f("state"): to}, self.user)
        except ValueError as e:
            raise self._co_refused(e) from None
        self._co_done(co, f"Items in {self._f('state')} go to {to} when it is released.", "co-state-map")

    def post_co_dismiss_related(self):
        co = self._co_post()
        if co is None:
            return
        try:
            cc.dismiss_related(self.hub, co["locator"], self.user, self._f("reason"), self._f("meaning"))
        except ValueError as e:
            raise self._co_refused(e) from None
        self._co_done(co, "The flag is dismissed; your accepted warning is on the delta.", "co-related")

    def post_co_direct_remove(self):
        co = self._co_post()
        if co is None:
            return
        try:
            cc.remove_direct_change(self.hub, co["locator"], self._f("item"), self.user)
        except ValueError as e:
            raise self._co_refused(e) from None
        self._co_done(co, "The direct change is off the change order (prose already drafted for it stays in the revision: edit it there).", "co-carries")

    def post_co_intervene_review(self):
        co = self._co_post()
        if co is None:
            return
        member = self._f("member")
        meaning = self._f("meaning")
        choice = self._f("action") or "agree"
        if not meaning:
            raise ShellError("An intervention needs its meaning: say why you intervene.", field="meaning")
        action = {"agree": "decide", "object": "decide"}.get(choice, choice)
        try:
            cc.intervene_review(self.hub, co["locator"], self._f("committee"), member, self.user, meaning, action=action,
                                verdict="object" if choice == "object" else "agree", due=self._f("due") or None)
        except ValueError as e:
            raise self._co_refused(e) from None
        log_line(f"change order {co['number']} committee {self._f('committee')} intervention ({choice}) for {member} by={self.user}")
        done = {"agree": f"Recorded: you agreed in {member}'s place.", "object": f"Recorded: you objected in {member}'s place.",
                "extend": f"Recorded: {member}'s vote is now due {self._f('due')}.", "return": "Recorded: the change goes back for revision."}
        self._co_done(co, done.get(choice, "Recorded."), "co-review")

    def post_co_report_unavailable(self):
        co = self._co_post()
        if co is None:
            return
        admins = self._co_sysadmins()
        if not admins:
            raise ShellError("Nobody holds the system-administrator role in force: give someone the role through a change order to the organization model.")
        try:
            un = (self.hub.compile_committee() or {}).get("unavailable") or {}
        except Exception:
            un = {}
        if not un:
            raise ShellError("Every member of the compile-frame committee can read now: request review again.")
        body = (f"{self.user} reports an incident under the IT SOP (a model unavailable): the compile-frame committee cannot read the prose of "
                f"{co['number']}: " + "; ".join(f"{n}: {w}" for n, w in un.items()) + ". Switch hosted models on in the environment "
                "(HUBPROTO_HOSTED_MODELS=on with ANTHROPIC_API_KEY) and restart the hub, or say why not on the change order.")
        for a in admins:
            self.hub.inbox.notify(a, kind="task", subject=f"Incident: the compile-frame committee cannot read ({co['number']})", body=body, record=co["locator"])
        log_line(f"change order {co['number']} committee unavailable reported to {','.join(admins)} by={self.user}")
        self._co_done(co, f"Reported to {', '.join(admins)}.", "co-questions")

    def post_co_answer_all(self):
        co = self._co_post()
        if co is None:
            return
        answer = self._f("answer")
        if not answer:
            raise ShellError("Say what the answer is.", field="answer")
        qs = [q for q in cc.questions(self.hub, co["locator"]) if q.get("code") == "member-unavailable" and q.get("status", "open") == "open"]
        if not qs:
            raise ShellError("No question of the compile-frame committee is open.")
        try:
            for q in qs:
                cc.answer_question(self.hub, co["locator"], q["id"], answer, self.user)
        except ValueError as e:
            raise self._co_refused(e) from None
        self._co_done(co, f"{len(qs)} question(s) marked answered.", "co-questions")

    def post_co_accept_all(self):
        co = self._co_post()
        if co is None:
            return
        if self._f("confirm") != "1":
            raise ShellError("Tick I accept to accept the warnings.", field="confirm")
        meaning = self._f("meaning")
        if not meaning:
            raise ShellError("Say what you accept, in a few words.", field="meaning")
        refs = self._multi("ref")
        try:
            for ref in refs:
                cc.accept_warning(self.hub, co["locator"], ref, self.user, meaning)
        except ValueError as e:
            raise self._co_refused(e) from None
        self._co_done(co, f"{len(refs)} warning(s) accepted.", "co-approval")

    def post_co_rt_uncarry(self):
        from .. import recordtypes

        co = self._co_post()
        if co is None:
            return
        try:
            recordtypes.uncarry(self.hub, co["locator"], self._f("change"), by=self.user)
        except ValueError as e:
            raise self._co_refused(e) from None
        self._co_done(co, "The record-type change is off the change order.", "co-carries")

