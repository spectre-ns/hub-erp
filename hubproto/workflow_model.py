"""Workflow graphs as data (change control v2, section 10): pure functions over the section 10.1 shape.

A process's workflow is one graph::

    {"process", "record_type",
     "statuses": [{"id", "name", "category": "todo"|"in_progress"|"done", "outcome" (done only), "sla_days"?}],
     "transitions": [{"id", "type": "initial"|"directed"|"global", "from": [...] (directed), "except": [...] (global;
                      missing means the done statuses), "to", "name", "gate", "validators", "actions", "fields"}],
     "source": {"frame", "lines"}}

Gates: ``{"kind": "person", "role", "not": ["author" | <transition id>]}``, ``{"kind": "cog", "cog", "fallback": <role>}``,
``{"kind": "auto", "when": "actions-succeed" | <condition> | "not <condition>"}`` (conditions: the closed vocabulary of
``hubproto.rules.CONDITION_VOCABULARY``). Validators, a closed list: ``{"fields_set": [...]}``, ``{"form_condition":
<condition as in form layouts>}``, ``{"status_visited": <status id>}``, ``{"linked": <one of LINKED>}``. Actions, in order:
a code step id, ``"notify:<role>"`` or ``"set:<field>=<value>"``.

Layout is presentation, not policy: ``{<status id>: [x, y], "label:<transition id>": [dx, dy]}`` (integers on an 8 px
grid; label offsets move a transition's label from where it would sit).

What is here (workstream 8 owns it; workstream 7 imports it):

* ``graph_of(bundle, process, types)``: the graph of a process in a release. A bundle that already holds the 10.1 shape
  (workstream 7's compile) is returned as is; today's linear workflow shape is converted without changing its meaning.
* ``validate(graph)`` (shape) and ``checks(graph, ...)`` (section 10.3: errors and warnings, each naming an element).
* ``diff(old, new, ...)``: the change as delta items in words, keyed by element id (section 10.4).
* ``tidy(graph)``: a deterministic layered layout. ``apply_op(doc, op)``: every editor operation (``OPS``).
* The geometry the server-rendered drawing and the editor's script share: box size, nearest-side ports, quadratic
  curves bent apart for opposite transitions, loop arcs, "any status" markers, start points.
"""

from __future__ import annotations

import copy
import re
from collections import deque

from .rules import CONDITION_VOCABULARY, sha256_of

CATEGORIES = ("todo", "in_progress", "done")
CATEGORY_WORDS = {"todo": "to do", "in_progress": "in progress", "done": "done"}
OUTCOMES = ("completed", "rejected", "cancelled", "superseded")
TYPES = ("initial", "directed", "global")
GATE_KINDS = ("person", "cog", "auto")
GATE_RANK = {"auto": 0, "cog": 1, "person": 2}  # a gate weakens down this order (a widening)
CONDITIONS = tuple(sorted(set(CONDITION_VOCABULARY.values())))
CONDITION_WORDS = {}
for _phrase, _key in CONDITION_VOCABULARY.items():
    CONDITION_WORDS.setdefault(_key, _phrase)
AUTO_WHEN = ("actions-succeed",) + CONDITIONS
VALIDATORS = ("fields_set", "form_condition", "status_visited", "linked")
LINKED = {"no_open_ncr": "no open nonconformance is linked to it", "requirements_accepted": "every linked requirement is accepted",
          "bom_resolved": "its bill of materials is resolved"}
# Roles a record names rather than the org model (who started it, who owns it ...) and the hub itself: always "held".
FIELD_ROLES = ("assignee", "owner", "author", "requester", "auditor", "chair", "evaluator", "reporter")
PSEUDO_ROLES = FIELD_ROLES + ("system",)
SYSTEM_STATUSES = ("failed",)  # the hub moves an item here when an action fails; Retry takes it back
MAX_STATUSES = 25
OPS = ("add_status", "rename_status", "recategorise_status", "set_sla", "delete_status", "add_transition", "rename_transition",
       "reroute_transition", "delete_transition", "set_gate", "set_validators", "set_actions", "set_fields", "set_global",
       "set_except", "move", "move_label", "tidy", "batch")

# --- geometry (shared with the editor's script, hubproto.shell.workflow_ui) ---------------------------------------------
GRID = 8
W, H = 184, 64  # a status box
X0, Y0 = 120, 112  # where tidy puts the first column and row (room for start points and "any status" markers)
COL, ROW = 376, 152  # tidy's column and row pitch (a label fits between two columns)
PAIR_GAP = 80  # control-point spread between transitions joining the same two statuses
LOOP_H, LOOP_STEP = 44, 28
GLOBAL_DY, GLOBAL_PITCH = 56, 176
START_DX = 64

_SLUG = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    return _SLUG.sub("-", str(text or "").strip().lower()).strip("-") or "x"


def snap(v) -> int:
    return int(round(float(v) / GRID)) * GRID


def _r(v: float) -> float:
    return round(v * 10) / 10


def _n(v: float) -> str:
    v = _r(v)
    return str(int(v)) if v == int(v) else f"{v:.1f}"


# ---------------------------------------------------------------- lookups
def status_map(g: dict) -> dict:
    return {s["id"]: s for s in g.get("statuses") or []}


def transition_map(g: dict) -> dict:
    return {t["id"]: t for t in g.get("transitions") or []}


def done_ids(g: dict) -> list[str]:
    return [s["id"] for s in g.get("statuses") or [] if s.get("category") == "done"]


def except_of(g: dict, t: dict) -> list[str]:
    """The statuses a global transition does not leave from: its ``except``, else the done statuses."""
    return list(t["except"]) if t.get("except") is not None else done_ids(g)


def sources(g: dict, t: dict) -> list[str]:
    """The statuses a transition leaves from (none for the initial transition)."""
    kind = t.get("type") or "directed"
    if kind == "initial":
        return []
    if kind == "global":
        ex = set(except_of(g, t))
        return [s["id"] for s in g.get("statuses") or [] if s["id"] not in ex and s["id"] != t.get("to")]
    return [x for x in t.get("from") or []]


def edges(g: dict, skip: str | None = None) -> list[tuple[str, dict, str]]:
    """(from, transition, to) for every way an item moves between statuses."""
    out = []
    for t in g.get("transitions") or []:
        if t["id"] == skip:
            continue
        for s in sources(g, t):
            out.append((s, t, t.get("to")))
    return out


def initial_targets(g: dict) -> list[str]:
    return [t.get("to") for t in g.get("transitions") or [] if t.get("type") == "initial"]


def completed(g: dict) -> set[str]:
    return {s["id"] for s in g.get("statuses") or [] if s.get("category") == "done" and (s.get("outcome") or "completed") == "completed"}


def name_of(g: dict, sid: str | None) -> str:
    s = status_map(g).get(sid)
    return (s or {}).get("name") or str(sid or "?")


def gated(t: dict) -> bool:
    return ((t.get("gate") or {}).get("kind")) in ("person", "cog")


def label_key(tid: str) -> str:
    return f"label:{tid}"


def positions(layout: dict | None) -> dict:
    return {k: v for k, v in (layout or {}).items() if not str(k).startswith("label:")}


def meaning(g: dict) -> dict:
    """The graph without what does not change how items move (the source citation)."""
    return {k: v for k, v in g.items() if k not in ("source",)}


def digest(g: dict) -> str:
    return sha256_of(meaning(g))


# ---------------------------------------------------------------- words
def role_words(role: str | None) -> str:
    r = str(role or "?")
    if r == "system":
        return "the hub"
    if r in FIELD_ROLES:
        return f"the item's {r}"
    return "the " + r.replace("-", " ")


def condition_words(cond: str | None) -> str:
    c = str(cond or "")
    neg = c.startswith("not ")
    key = c[4:] if neg else c
    words = CONDITION_WORDS.get(key, key.replace("_", " "))
    return f"not ({words})" if neg else words


def form_condition_words(cond) -> str:
    if not isinstance(cond, dict):
        return "a condition"
    if "all" in cond:
        return " and ".join(form_condition_words(c) for c in cond["all"])
    if "any" in cond:
        return "(" + " or ".join(form_condition_words(c) for c in cond["any"]) + ")"
    if "not" in cond:
        return "not " + form_condition_words(cond["not"])
    name = str(cond.get("field") or "?").replace("_", " ")
    op, v = cond.get("op"), cond.get("value")
    if op == "set":
        return f"{name} is filled in"
    if op == "empty":
        return f"{name} is empty"
    ops = {"==": "is", "!=": "is not", "in": "is one of", "not in": "is none of", ">": "is over", "<": "is under", ">=": "is at least",
           "<=": "is at most"}
    vals = ", ".join(map(str, v)) if isinstance(v, list) else str(v)
    return f"{name} {ops.get(op, op)} {vals}"


def gate_words(g: dict, gate: dict | None, *, initial: bool = False) -> str:
    gate = gate or {}
    kind = gate.get("kind")
    if kind == "person":
        if initial:
            return "the hub starts it" if gate.get("role") == "system" else f"{role_words(gate.get('role'))} starts it"
        out = f"{role_words(gate.get('role'))} decides" if gate.get("role") != "system" else "the hub does it"
        nots = []
        for n in gate.get("not") or []:
            nots.append("not the author" if n == "author" else f"not whoever took “{(transition_map(g).get(n) or {}).get('name') or n}”")
        return out + (", " + ", ".join(nots) if nots else "")
    if kind == "cog":
        return (f"the {gate.get('cog') or '?'} cog decides within its approval criteria, otherwise "
                f"{role_words(gate.get('fallback'))}")
    if kind == "auto":
        w = gate.get("when") or "actions-succeed"
        return "automatically, once its actions succeed" if w == "actions-succeed" else f"automatically when {condition_words(w)}"
    return "nobody is named to decide"


def gate_badge(gate: dict | None) -> tuple[str, str]:
    """(icon kind, short words) for a transition's badge on the drawing."""
    gate = gate or {}
    kind = gate.get("kind")
    if kind == "person":
        r = gate.get("role")
        who = "hub" if r == "system" else str(r or "?").replace("-", " ")
        return "person", who + (" · not author" if "author" in (gate.get("not") or []) else "")
    if kind == "cog":
        return "cog", f"{gate.get('cog') or '?'} cog"
    if kind == "auto":
        w = gate.get("when") or "actions-succeed"
        return "auto", "auto" if w == "actions-succeed" else "auto: " + condition_words(w)
    return "person", "nobody"


def validator_words(g: dict, v: dict) -> str:
    if not isinstance(v, dict):
        return "a check"
    if "fields_set" in v:
        names = [str(x).replace("_", " ") for x in v.get("fields_set") or []]
        if not names:
            return "nothing to check"
        return (", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]) + (" are" if len(names) > 1 else " is") + " filled in"
    if "form_condition" in v:
        return form_condition_words(v["form_condition"])
    if "status_visited" in v:
        return f"it has been in “{name_of(g, v['status_visited'])}”"
    if "linked" in v:
        return LINKED.get(v["linked"], str(v["linked"]).replace("_", " "))
    return "a check"


def action_words(a: str) -> str:
    a = str(a)
    if a.startswith("notify:"):
        return "tells " + role_words(a[7:])
    if a.startswith("set:"):
        f, _, val = a[4:].partition("=")
        return f"sets {f.replace('_', ' ')} to {val}"
    return a.replace("-", " ")


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}{'es' if word.endswith('s') else 's'}"


def route_words(g: dict, t: dict) -> str:
    kind = t.get("type") or "directed"
    to = f"“{name_of(g, t.get('to'))}”"
    if kind == "initial":
        return f"at the start, into {to}"
    if kind == "global":
        if t.get("except") is None:
            ex = " except done ones"
        elif t["except"]:
            ex = " except " + ", ".join(f"“{name_of(g, x)}”" for x in t["except"])
        else:
            ex = ""
        return f"from any status{ex} to {to}"
    frm = t.get("from") or []
    if len(frm) == 1 and frm[0] == t.get("to"):
        return f"from {to} back to itself"
    return "from " + ", ".join(f"“{name_of(g, x)}”" for x in frm) + f" to {to}"


def sentence(g: dict, t: dict) -> str:
    """A transition's every rule as one sentence, in the shape of the section 10.2 grammar."""
    rw = route_words(g, t)
    parts = [f"{rw[0].upper()}{rw[1:]} (“{t.get('name') or t['id']}”): {gate_words(g, t.get('gate'), initial=t.get('type') == 'initial')}"]
    vs = [validator_words(g, v) for v in t.get("validators") or []]
    if vs:
        parts.append("checks that " + "; and that ".join(vs))
    if t.get("fields"):
        parts.append("asks for " + ", ".join(str(f).replace("_", " ") for f in t["fields"]))
    if t.get("actions"):
        parts.append("after the move: " + ", ".join(action_words(a) for a in t["actions"]))
    return "; ".join(parts) + "."


def status_words(s: dict) -> str:
    cat = CATEGORY_WORDS.get(s.get("category"), str(s.get("category")))
    if s.get("category") == "done":
        cat += f", {s.get('outcome') or 'completed'}"
    return cat + (f", due within {s['sla_days']} days" if s.get("sla_days") else "")


# ---------------------------------------------------------------- today's workflows in the 10.1 shape
def _title(text: str) -> str:
    t = str(text or "").replace("-", " ").strip()
    return t[:1].upper() + t[1:]


def graph_of(bundle: dict, process: str, types: dict | None = None) -> dict:
    """The graph of a process in a release bundle (section 10.1).

    When the bundle already holds the graph shape (workstream 7 compiles processes into it) it is returned as it is.
    Otherwise today's linear workflow (``hubproto.release.render_workflow``) is converted without changing its meaning:
    the automatic transitions become auto-gated transitions that run their steps; each checkpoint becomes a person gate
    (a cog gate, falling back to the checkpoint's role, when the routing sends it to a cog with approval criteria for the
    process; ``not author`` when a segregation-of-duties rule covers the object), with the steps after it as its actions,
    the fields the type collects at a decision, and the fields the next state requires as a validator; the rejection
    exit, the returned, failed and cancelled exits become ordinary statuses; Cancel is a global transition and Retry a
    loop on the system ``failed`` status."""
    wf = (bundle.get("workflows") or {}).get(process)
    if wf is None:
        raise KeyError(f"no workflow {process!r} in the release")
    if "statuses" in wf:
        return copy.deepcopy(wf)
    proc = (bundle.get("processes") or {}).get(process) or {}
    td = (types or {}).get(wf.get("record_type")) or {}
    system_fields = set(td.get("system_fields") or [])
    required = td.get("required_by_state") or {}
    decision_fields = list(td.get("decision_fields") or [])
    titles = {s["id"]: s.get("title") or s["id"] for s in proc.get("steps") or []}
    steps = proc.get("steps") or []
    cps = {c["id"]: c for c in wf.get("checkpoints") or []}
    terminal = wf.get("terminal") or "closed"

    statuses = []
    for st in wf.get("states") or []:
        if st == "open":
            statuses.append({"id": st, "name": "Open", "category": "todo"})
        elif st.startswith("awaiting-") and st[9:] in cps:
            cp = cps[st[9:]]
            entry = {"id": st, "name": f"Awaiting: {cp.get('title') or cp['id']}", "category": "in_progress"}
            if cp.get("sla_days"):
                entry["sla_days"] = cp["sla_days"]
            statuses.append(entry)
        elif st == terminal:
            statuses.append({"id": st, "name": _title(st), "category": "done", "outcome": "completed"})
        elif st == "rejected":
            statuses.append({"id": st, "name": "Rejected", "category": "done", "outcome": "rejected"})
        elif st == "returned":
            statuses.append({"id": st, "name": "Returned", "category": "todo"})
        else:
            statuses.append({"id": st, "name": _title(st), "category": "in_progress"})
    have = {s["id"] for s in statuses}
    if "failed" not in have:
        statuses.append({"id": "failed", "name": "Failed", "category": "in_progress"})
    if "cancelled" not in have:
        statuses.append({"id": "cancelled", "name": "Cancelled", "category": "done", "outcome": "cancelled"})

    def validators_into(state: str) -> list[dict]:
        need = [f for f in required.get(state) or [] if f not in system_fields]
        return [{"fields_set": need}] if need else []

    def gate_for(cp: dict) -> dict:
        role = cp.get("role")
        cogs = [str(r.get("assignee"))[4:] for r in bundle.get("routing") or []
                if r.get("process") == process and r.get("step") == cp["id"] and str(r.get("assignee", "")).startswith("cog:")]
        criteria = {str(c.get("approver"))[4:] for c in bundle.get("approval_criteria") or [] if c.get("process") == process}
        cog = next((c for c in cogs if c in criteria), None)
        gate = {"kind": "cog", "cog": cog, "fallback": role} if cog else {"kind": "person", "role": role}
        if any(s.get("object") == wf.get("object") for s in bundle.get("sod") or []) and gate["kind"] == "person":
            gate["not"] = ["author"]
        return gate

    first_role = steps[0].get("role") if steps else "staff"
    transitions = [{"id": "create", "type": "initial", "to": "open", "name": "Create",
                    "gate": {"kind": "person", "role": first_role or "staff"}, "validators": [], "actions": [], "fields": []}]
    autos = [t for t in wf.get("transitions") or [] if t.get("kind") == "automatic"]
    human = [t for t in wf.get("transitions") or [] if t.get("kind") == "human"]
    human_pairs = {(t["from"], t["to"]) for t in human}
    for t in autos:
        if (t["from"], t["to"]) in human_pairs:
            continue  # the steps that run after a decision: the decision's actions (below)
        acts = [s for s in t.get("steps") or [] if not str(s).startswith("reach-")]
        name = titles.get(acts[0], _title(acts[0])) if acts else "Continue"
        transitions.append({"id": f"auto-{t['to']}", "type": "directed", "from": [t["from"]], "to": t["to"], "name": name,
                            "gate": {"kind": "auto", "when": "actions-succeed"}, "validators": validators_into(t["to"]),
                            "actions": acts, "fields": []})
    for t in human:
        cp = cps.get(t.get("step")) or {"id": t.get("step"), "title": t.get("title"), "role": "staff"}
        after = next((a for a in autos if a["from"] == t["from"] and a["to"] == t["to"]), {})
        acts = [s for s in after.get("steps") or [] if not str(s).startswith("reach-")]
        gate = gate_for(cp)
        transitions.append({"id": cp["id"], "type": "directed", "from": [t["from"]], "to": t["to"], "name": cp.get("title") or cp["id"],
                            "gate": gate, "validators": validators_into(t["to"]), "actions": acts, "fields": decision_fields})
        rej = t.get("on_reject")
        if rej:
            back = rej != "rejected"
            transitions.append({"id": f"reject-{cp['id']}", "type": "directed", "from": [t["from"]], "to": rej,
                                "name": "Return" if back else "Reject", "gate": copy.deepcopy(gate), "validators": [], "actions": [],
                                "fields": []})
    if "returned" in {s["id"] for s in statuses}:
        transitions.append({"id": "resubmit", "type": "directed", "from": ["returned"], "to": "open", "name": "Resubmit",
                            "gate": {"kind": "person", "role": "owner"}, "validators": [], "actions": [], "fields": []})
    transitions.append({"id": "retry", "type": "directed", "from": ["failed"], "to": "failed", "name": "Retry",
                        "gate": {"kind": "person", "role": "owner"}, "validators": [], "actions": [], "fields": []})
    transitions.append({"id": "cancel", "type": "global", "to": "cancelled", "name": "Cancel",
                        "gate": {"kind": "person", "role": "owner"}, "validators": [], "actions": [], "fields": []})
    lines = sorted(s.get("line") for s in steps if isinstance(s.get("line"), int))
    src = proc.get("source") or {}
    return {"process": process, "record_type": wf.get("record_type"), "statuses": statuses, "transitions": transitions,
            "source": {"frame": src.get("frame"), "lines": [src.get("line") or (lines[0] if lines else None), lines[-1] if lines else None]}}


# ---------------------------------------------------------------- shape
def _problem(level: str, code: str, text: str, element: str | None = None, id_: str | None = None, **extra) -> dict:
    return {"level": level, "code": code, "element": element, "id": id_, "text": text, **extra}


def validate(g: dict) -> list[dict]:
    """What is wrong with the graph's shape: ids, references, categories, gates, validators. Each an error naming the
    element ({"level", "code", "element", "id", "text"})."""
    out = []
    if not isinstance(g, dict) or not isinstance(g.get("statuses"), list) or not isinstance(g.get("transitions"), list):
        return [_problem("error", "shape", "a workflow has a list of statuses and a list of transitions")]
    sids = set()
    for s in g["statuses"]:
        sid = s.get("id") if isinstance(s, dict) else None
        if not sid or not isinstance(sid, str):
            out.append(_problem("error", "shape", "a status needs an id"))
            continue
        if sid in sids:
            out.append(_problem("error", "duplicate", f"two statuses have the id {sid}", "status", sid))
        sids.add(sid)
        if not str(s.get("name") or "").strip():
            out.append(_problem("error", "name", f"status {sid} needs a name", "status", sid))
        if s.get("category") not in CATEGORIES:
            out.append(_problem("error", "category", f"“{s.get('name') or sid}” needs a category: to do, in progress or done", "status", sid))
        if s.get("outcome") is not None and s.get("category") != "done":
            out.append(_problem("error", "outcome", f"“{s.get('name') or sid}” has an outcome but is not done", "status", sid))
        if s.get("category") == "done" and s.get("outcome") not in (None,) + OUTCOMES:
            out.append(_problem("error", "outcome", f"“{s.get('name') or sid}”: the outcome is one of {', '.join(OUTCOMES)}", "status", sid))
        if s.get("sla_days") is not None and (not isinstance(s["sla_days"], int) or s["sla_days"] < 1):
            out.append(_problem("error", "sla", f"“{s.get('name') or sid}”: the due date is a whole number of days", "status", sid))
    names = {}
    for s in g["statuses"]:
        n = str(s.get("name") or "").strip().lower()
        if n and n in names:
            out.append(_problem("error", "duplicate", f"two statuses are called “{s.get('name')}”", "status", s.get("id")))
        names[n] = s.get("id")
    tids = set()
    tmap = {t.get("id"): t for t in g["transitions"] if isinstance(t, dict)}
    for t in g["transitions"]:
        tid = t.get("id") if isinstance(t, dict) else None
        if not tid or not isinstance(tid, str):
            out.append(_problem("error", "shape", "a transition needs an id"))
            continue
        if tid in tids or tid in sids:
            out.append(_problem("error", "duplicate", f"the id {tid} is used twice", "transition", tid))
        tids.add(tid)
        label = f"“{t.get('name') or tid}”"
        if not str(t.get("name") or "").strip():
            out.append(_problem("error", "name", f"transition {tid} needs a name", "transition", tid))
        kind = t.get("type") or "directed"
        if kind not in TYPES:
            out.append(_problem("error", "type", f"{label}: the type is initial, directed or global", "transition", tid))
        if t.get("to") not in sids:
            out.append(_problem("error", "reference", f"{label} goes to a status that is not in the workflow ({t.get('to')})", "transition", tid))
        if kind == "directed":
            frm = t.get("from")
            if not isinstance(frm, list) or not frm:
                out.append(_problem("error", "reference", f"{label} needs at least one status it leaves from", "transition", tid))
            else:
                for x in frm:
                    if x not in sids:
                        out.append(_problem("error", "reference", f"{label} leaves from a status that is not in the workflow ({x})", "transition", tid))
        if kind == "initial" and t.get("from"):
            out.append(_problem("error", "type", f"{label} is the start: it leaves from no status", "transition", tid))
        if kind == "global":
            for x in t.get("except") or []:
                if x not in sids:
                    out.append(_problem("error", "reference", f"{label} names a status that is not in the workflow ({x})", "transition", tid))
        gate = t.get("gate")
        if not isinstance(gate, dict) or gate.get("kind") not in GATE_KINDS:
            out.append(_problem("error", "gate", f"{label}: say who decides (a person's role, a cog, or automatically)", "transition", tid))
        else:
            if gate["kind"] == "person" and not gate.get("role"):
                out.append(_problem("error", "gate", f"{label}: name the role that decides", "transition", tid))
            if gate["kind"] == "cog" and (not gate.get("cog") or not gate.get("fallback")):
                out.append(_problem("error", "gate", f"{label}: name the cog and the role it falls back to", "transition", tid))
            if gate["kind"] == "auto":
                w = str(gate.get("when") or "actions-succeed")
                if (w[4:] if w.startswith("not ") else w) not in AUTO_WHEN or w == "not actions-succeed":
                    out.append(_problem("error", "gate", f"{label}: “{w}” is not a condition the hub can test", "transition", tid))
            for n in gate.get("not") or []:
                if n != "author" and n not in tmap:
                    out.append(_problem("error", "gate", f"{label}: segregation of duties names an unknown transition ({n})", "transition", tid))
        for v in t.get("validators") or []:
            keys = [k for k in (v if isinstance(v, dict) else {}) if k in VALIDATORS]
            if len(keys) != 1:
                out.append(_problem("error", "validator", f"{label}: a check is one of {', '.join(VALIDATORS)}", "transition", tid))
            elif keys[0] == "status_visited" and v["status_visited"] not in sids:
                out.append(_problem("error", "validator", f"{label}: checks a visit to a status that is not in the workflow", "transition", tid))
            elif keys[0] == "linked" and v["linked"] not in LINKED:
                out.append(_problem("error", "validator", f"{label}: “{v['linked']}” is not a linked-record check the hub knows", "transition", tid))
        for key in ("actions", "fields"):
            if not isinstance(t.get(key, []), list):
                out.append(_problem("error", "shape", f"{label}: {key} are a list", "transition", tid))
    return out


# ---------------------------------------------------------------- checks (10.3)
def reachable(g: dict) -> set[str]:
    seen = set(x for x in initial_targets(g) if x)
    todo = deque(seen)
    out_of: dict[str, list[str]] = {}
    for a, _, b in edges(g):
        out_of.setdefault(a, []).append(b)
    while todo:
        s = todo.popleft()
        for b in out_of.get(s, []):
            if b not in seen:
                seen.add(b)
                todo.append(b)
    return seen


def path_avoiding(g: dict, avoid: str | None = None, *, ungated: bool = False) -> list[str] | None:
    """The shortest way (status, transition, status, ...) from the start to a completed status that does not take the
    transition ``avoid`` (or, with ``ungated``, takes no person or cog gate at all); None when there is none."""
    goal = completed(g)
    starts = [x for x in initial_targets(g) if x]
    prev: dict[str, tuple[str, str] | None] = {s: None for s in starts}
    todo = deque(starts)
    out_of: dict[str, list[tuple[dict, str]]] = {}
    for a, t, b in edges(g, skip=avoid):
        if ungated and gated(t):
            continue
        out_of.setdefault(a, []).append((t, b))
    while todo:
        s = todo.popleft()
        if s in goal:
            path = [s]
            while prev[s] is not None:
                p, tid = prev[s]
                path[:0] = [p, tid]
                s = p
            return path
        for t, b in out_of.get(s, []):
            if b not in prev:
                prev[b] = (s, t["id"])
                todo.append(b)
    return None


def path_words(g: dict, path: list[str]) -> str:
    tm, sm = transition_map(g), status_map(g)
    return " → ".join((sm.get(x) or {}).get("name") or (tm.get(x) or {}).get("name") or x for x in path)


def mandatory_gates(g: dict) -> dict[str, bool]:
    """Each person or cog gate (not the start) and whether every way from the start to a completed status crosses it."""
    out = {}
    if not completed(g) or not initial_targets(g):
        return out
    for t in g.get("transitions") or []:
        if t.get("type") == "initial" or not gated(t):
            continue
        out[t["id"]] = path_avoiding(g, t["id"]) is None
    return out


def _auto_conflicts(g: dict) -> list[dict]:
    out = []
    by_source: dict[str, list[dict]] = {}
    gated_out: set[str] = set()
    for a, t, b in edges(g):
        if (t.get("gate") or {}).get("kind") == "auto":
            by_source.setdefault(a, []).append(t)
        elif gated(t) and a != b and (t.get("type") or "directed") != "global":
            gated_out.add(a)  # someone decides the way on (a global Cancel is not a way on)
    sm = status_map(g)
    for sid, ts in by_source.items():
        name = (sm.get(sid) or {}).get("name") or sid
        whens = [str((t.get("gate") or {}).get("when") or "actions-succeed") for t in ts]
        uncond = [t for t, w in zip(ts, whens) if w == "actions-succeed"]
        conds = [w for w in whens if w != "actions-succeed"]
        names = ", ".join(f"“{t.get('name') or t['id']}”" for t in ts)
        if len(uncond) > 1 or (uncond and conds) or len(set(conds)) < len(conds):
            out.append(_problem("error", "auto-overlap", f"automatic transitions out of “{name}” overlap: {names} could all fire",
                                "status", sid, transitions=[t["id"] for t in ts]))
            continue
        if not conds:
            continue
        pairs = [c for c in conds if ("not " + c) in conds]
        if pairs and len(conds) > 2:
            out.append(_problem("error", "auto-overlap", f"automatic transitions out of “{name}” overlap: {names}", "status", sid,
                                transitions=[t["id"] for t in ts]))
        elif not pairs and sid not in gated_out:
            out.append(_problem("error", "auto-gap", f"items can be stuck in “{name}”: when none of {', '.join(condition_words(c) for c in conds)} "
                                f"holds, no transition leaves it", "status", sid, transitions=[t["id"] for t in ts]))
    return out


def checks(g: dict, *, bundle: dict | None = None, old: dict | None = None, items: dict | None = None,
           state_map: dict | None = None) -> list[dict]:
    """Section 10.3: errors and warnings, each naming the element it is about (``element`` "status" or "transition" and
    its ``id``; none for the graph as a whole). ``bundle`` (the release in force) checks roles and cogs; ``old`` (the
    graph in force) finds widenings, including a mandatory gate bypassed (with the ``path``); ``items`` ({status id:
    count}) and ``state_map`` ({old status id: new status id}) check that items in a removed status have a place to go."""
    out = validate(g)
    if any(p["code"] == "shape" for p in out):
        return out
    sm, tm = status_map(g), transition_map(g)
    inits = [t for t in g["transitions"] if t.get("type") == "initial"]
    if not inits:
        out.append(_problem("error", "no-initial", "there is no start: add the transition that creates an item"))
    elif len(inits) > 1:
        for t in inits[1:]:
            out.append(_problem("error", "two-initial", f"“{t.get('name')}” is a second start: an item is created one way", "transition", t["id"]))
    reach = reachable(g)
    for s in g["statuses"]:
        if inits and s["id"] not in reach and s["id"] not in SYSTEM_STATUSES:
            out.append(_problem("error", "unreachable", f"no transition leads to “{s['name']}”", "status", s["id"]))
    leaves = {a for a, t, b in edges(g) if a != b and (t.get("type") or "directed") != "global"}
    for s in g["statuses"]:
        if s.get("category") != "done" and s["id"] not in SYSTEM_STATUSES and s["id"] not in leaves:
            out.append(_problem("error", "dead-end", f"“{s['name']}” is not done but no transition leaves it", "status", s["id"]))
    out += _auto_conflicts(g)
    if bundle:
        held = set(PSEUDO_ROLES)
        cogs = set()
        for m, info in (bundle.get("members") or {}).items():
            held |= set(info.get("roles") or [])
            if info.get("type") == "cog":
                cogs.add(m)
        criteria = {(str(c.get("approver"))[4:], c.get("process")) for c in bundle.get("approval_criteria") or []}
        for t in g["transitions"]:
            gate = t.get("gate") or {}
            for role in ([gate.get("role")] if gate.get("kind") == "person" else [gate.get("fallback")] if gate.get("kind") == "cog" else []):
                if role and role not in held:
                    out.append(_problem("error", "role", f"“{t['name']}”: nobody holds the role {role.replace('-', ' ')}", "transition", t["id"]))
            if gate.get("kind") == "cog":
                if gate.get("cog") not in cogs:
                    out.append(_problem("error", "cog", f"“{t['name']}”: there is no cog called {gate.get('cog')}", "transition", t["id"]))
                elif (gate.get("cog"), g.get("process")) not in criteria:
                    out.append(_problem("error", "cog-criteria", f"“{t['name']}”: the {gate.get('cog')} cog has no approval criteria for "
                                        f"{g.get('process')}, so it could never decide", "transition", t["id"]))
    if len(g["statuses"]) > MAX_STATUSES:
        out.append(_problem("warning", "size", f"{len(g['statuses'])} statuses: more than about {MAX_STATUSES} is hard to follow"))
    if old:
        out += _widenings(old, g)
        osm = status_map(old)
        for sid, n in sorted((items or {}).items()):
            if not n or sid in sm or sid not in osm:
                continue
            target = (state_map or {}).get(sid)
            if target and target in sm:
                out.append(_problem("warning", "items-move", f"{plural(n, 'item')} {'is' if n == 1 else 'are'} in “{osm[sid]['name']}”, "
                                    f"which this removes: {'it moves' if n == 1 else 'they move'} to “{sm[target]['name']}”", "status", sid, count=n))
            else:
                out.append(_problem("error", "state-map", f"{plural(n, 'item')} {'is' if n == 1 else 'are'} in “{osm[sid]['name']}”, which this "
                                    f"removes: say where {'it goes' if n == 1 else 'they go'}", "status", sid, count=n))
    return out


def _gate_rank(t: dict) -> int:
    return GATE_RANK.get((t.get("gate") or {}).get("kind"), 0)


def _widenings(old: dict, new: dict) -> list[dict]:
    out = []
    otm, ntm = transition_map(old), transition_map(new)
    for tid, t in ntm.items():
        o = otm.get(tid)
        name = f"“{t.get('name') or tid}”"
        if o is None:
            if (t.get("type") or "directed") == "global":
                out.append(_problem("warning", "widening", f"{name} lets items move from any status: a widening", "transition", tid, widening=True))
            continue
        if _gate_rank(t) < _gate_rank(o):
            out.append(_problem("warning", "widening", f"{name}: {gate_words(new, t.get('gate'))}, where {gate_words(old, o.get('gate'))}: "
                                "a weaker gate, a widening", "transition", tid, widening=True))
        lost = [n for n in (o.get("gate") or {}).get("not") or [] if n not in ((t.get("gate") or {}).get("not") or [])]
        if lost:
            out.append(_problem("warning", "widening", f"{name}: segregation of duties removed ({', '.join(lost)}): a widening", "transition", tid, widening=True))
        gone = [v for v in o.get("validators") or [] if v not in (t.get("validators") or [])]
        if gone:
            out.append(_problem("warning", "widening", f"{name} no longer checks that {'; '.join(validator_words(old, v) for v in gone)}: a widening",
                                "transition", tid, widening=True))
        if (t.get("type") or "directed") == "global" and (o.get("type") or "directed") != "global":
            out.append(_problem("warning", "widening", f"{name} now leaves from any status: a widening", "transition", tid, widening=True))
    for tid, was in mandatory_gates(old).items():
        if not was:
            continue
        o, t = otm[tid], ntm.get(tid)
        # Gone, or no longer a person or a cog: any way through that crosses no gate at all; else a way around it.
        path = path_avoiding(new, ungated=True) if t is None or not gated(t) else path_avoiding(new, tid)
        if path:
            out.append(_problem("warning", "bypass", f"{path_words(new, path)} avoids “{o.get('name') or tid}” ({gate_words(old, o.get('gate'))}"
                                f"{', removed' if t is None else ''}): a mandatory gate bypassed, a widening", "transition", tid, path=path,
                                widening=True))
    return out


# ---------------------------------------------------------------- the change in words (10.4)
def _same(a, b) -> bool:
    return sha256_of(a) == sha256_of(b)


def diff(old: dict, new: dict, *, old_layout: dict | None = None, new_layout: dict | None = None, state_map: dict | None = None,
         items: dict | None = None, by: str | None = None, at: str | None = None) -> list[dict]:
    """The change from ``old`` to ``new`` as delta items keyed by element id (section 10.4): a status or transition added,
    removed, renamed, recategorised or rerouted; a gate, check, action or field changed. A removed status is one item with
    the transitions it takes with it as ``children``. A layout change is one item that changes nothing about how items
    move. Each item has the section 8 keys (``origin`` direct, ``warnings``, ``stale``) and ``key`` (the element it is
    about: ``status:<id>``, ``transition:<id>`` or ``layout``)."""
    process = new.get("process") or old.get("process")
    origin = {"kind": "direct", "by": by, "frame": None, "line": None, "quote": None, "read_by": None, "at": at}
    warns = {(w.get("id"), w.get("code")): w for w in _widenings(old, new)}
    out: list[dict] = []

    def item(element: str, eid: str | None, change: str, text: str, op: str = "change", children=None, widening=False, no_effect=False) -> dict:
        key = f"{element}:{eid}" if eid else element
        d = {"id": f"workflow:{process}:{key}" + (f":{change}" if op == "change" and element != "layout" else ""), "key": key,
             "kind": "workflow", "process": process, "element": element, "element_id": eid, "op": op, "change": change,
             "reason": text, "text": text, "widening": widening, "no_effect": no_effect, "children": children or [],
             "origin": dict(origin), "warnings": [], "stale": False}
        out.append(d)
        return d

    osm, nsm = status_map(old), status_map(new)
    otm, ntm = transition_map(old), transition_map(new)
    taken: set[str] = set()
    for sid, s in osm.items():
        if sid in nsm:
            continue
        kids = []
        for tid, t in otm.items():
            touches = t.get("to") == sid or sid in (t.get("from") or [])
            if not touches:
                continue
            if tid not in ntm:
                kids.append({"key": f"transition:{tid}", "element": "transition", "element_id": tid, "change": "removed",
                             "text": f"Transition “{t.get('name') or tid}” ({route_words(old, t)}) removed with it"})
                taken.add(tid)
            elif sid in (t.get("from") or []) and sid not in (ntm[tid].get("from") or []):
                kids.append({"key": f"transition:{tid}", "element": "transition", "element_id": tid, "change": "rerouted",
                             "text": f"“{t.get('name') or tid}” no longer leaves from it"})
        n = (items or {}).get(sid) or 0
        target = (state_map or {}).get(sid)
        where = ""
        if n:
            where = f"; {plural(n, 'item')} in it move{'s' if n == 1 else ''} to “{name_of(new, target)}”" if target in nsm else f"; {plural(n, 'item')} in it, with nowhere to go yet"
        item("status", sid, "removed", f"Status “{s['name']}” ({status_words(s)}) removed{where}", op="remove", children=kids)
    for sid, s in nsm.items():
        o = osm.get(sid)
        if o is None:
            item("status", sid, "added", f"Status “{s['name']}” added ({status_words(s)})", op="add")
            continue
        if o.get("name") != s.get("name"):
            item("status", sid, "renamed", f"Status “{o['name']}” renamed “{s['name']}” (the same status: no item moves)")
        if o.get("category") != s.get("category") or (o.get("outcome") or None) != (s.get("outcome") or None):
            item("status", sid, "recategorised", f"Status “{s['name']}” is now {status_words({**s, 'sla_days': None})}, was "
                 f"{status_words({**o, 'sla_days': None})}")
        if o.get("sla_days") != s.get("sla_days"):
            item("status", sid, "due", f"Status “{s['name']}”: " + (f"due within {s['sla_days']} days" if s.get("sla_days") else "no due date")
                 + (f" (was {o['sla_days']} days)" if o.get("sla_days") else " (had none)"))
    for tid, t in otm.items():
        if tid in ntm or tid in taken:
            continue
        item("transition", tid, "removed", f"Transition “{t.get('name') or tid}” ({route_words(old, t)}) removed", op="remove")
    for tid, t in ntm.items():
        o = otm.get(tid)
        name = t.get("name") or tid
        w = lambda code: (tid, code) in warns
        if o is None:
            widen = (t.get("type") or "directed") == "global"
            item("transition", tid, "added", f"Transition “{name}” added: {route_words(new, t)}, {gate_words(new, t.get('gate'))}", op="add",
                 widening=widen)
            continue
        if (o.get("name") or tid) != name:
            item("transition", tid, "renamed", f"Transition “{o.get('name') or tid}” renamed “{name}”")
        if (o.get("type") or "directed") != (t.get("type") or "directed") or sorted(o.get("from") or []) != sorted(t.get("from") or []) \
                or o.get("to") != t.get("to") or (o.get("except") != t.get("except") and (t.get("type") == "global")):
            item("transition", tid, "rerouted", f"“{name}” now goes {route_words(new, t)} (was {route_words(old, o)})",
                 widening=(t.get("type") == "global" and o.get("type") != "global"))
        if not _same(o.get("gate") or {}, t.get("gate") or {}):
            item("transition", tid, "gate", f"“{name}”: {gate_words(new, t.get('gate'))}, instead of {gate_words(old, o.get('gate'))}",
                 widening=w("widening") and (_gate_rank(t) < _gate_rank(o) or bool(
                     [n for n in (o.get("gate") or {}).get("not") or [] if n not in ((t.get("gate") or {}).get("not") or [])])))
        if not _same(o.get("validators") or [], t.get("validators") or []):
            now = "; ".join(validator_words(new, v) for v in t.get("validators") or []) or "nothing"
            was = "; ".join(validator_words(old, v) for v in o.get("validators") or []) or "nothing"
            gone = [v for v in o.get("validators") or [] if v not in (t.get("validators") or [])]
            item("transition", tid, "validators", f"“{name}” checks {now} (was {was})", widening=bool(gone))
        if list(o.get("actions") or []) != list(t.get("actions") or []):
            now = ", ".join(action_words(a) for a in t.get("actions") or []) or "nothing"
            was = ", ".join(action_words(a) for a in o.get("actions") or []) or "nothing"
            item("transition", tid, "actions", f"“{name}” then {now} (was {was})" if t.get("actions") else f"“{name}” runs nothing after it (was {was})")
        if list(o.get("fields") or []) != list(t.get("fields") or []):
            now = ", ".join(str(f).replace("_", " ") for f in t.get("fields") or []) or "nothing"
            was = ", ".join(str(f).replace("_", " ") for f in o.get("fields") or []) or "nothing"
            item("transition", tid, "fields", f"“{name}” asks for {now} (was {was})")
    for wn in warns.values():  # the items that open a way around a mandatory gate are widenings too
        if wn.get("code") == "bypass":
            for d in out:
                if d["element"] == "transition" and d["element_id"] in (wn.get("path") or []):
                    d["widening"] = True
    if old_layout is not None and new_layout is not None and not _same(_layout_of(new, old_layout), _layout_of(new, new_layout)):
        item("layout", None, "layout", "Layout changed: no effect on how items move", no_effect=True)
    return out


def _layout_of(g: dict, layout: dict) -> dict:
    keep = set(status_map(g)) | {label_key(t) for t in transition_map(g)}
    return {k: list(v) for k, v in sorted((layout or {}).items()) if k in keep}


def layout_only(items: list[dict]) -> bool:
    return bool(items) and all(i.get("no_effect") for i in items)


# ---------------------------------------------------------------- tidy
def tidy(g: dict) -> dict:
    """A deterministic layered layout: columns by the longest path from the start (loops and backward transitions do not
    count), done statuses in the last column, the system ``failed`` status last of all; rows ordered by where the statuses
    they come from sit; every position an integer on the 8 px grid."""
    order = [s["id"] for s in g.get("statuses") or []]
    idx = {sid: i for i, sid in enumerate(order)}
    sm = status_map(g)
    succ: dict[str, list[str]] = {sid: [] for sid in order}
    for t in g.get("transitions") or []:
        if (t.get("type") or "directed") != "directed" or t.get("to") not in succ:
            continue
        for a in t.get("from") or []:
            if a in succ and a != t["to"] and t["to"] not in succ[a]:
                succ[a].append(t["to"])
    starts = [x for x in initial_targets(g) if x in succ] or order[:1]
    # Depth-first from the start: an edge back to a status on the current path is a backward transition (ignored).
    forward: dict[str, list[str]] = {sid: [] for sid in order}
    state: dict[str, int] = {}
    for root in starts + order:
        if root in state:
            continue
        stack = [(root, iter(succ[root]))]
        state[root] = 1
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
                continue
            if state.get(nxt) == 1:
                continue  # backward
            forward[node].append(nxt)
            if nxt not in state:
                state[nxt] = 1
                stack.append((nxt, iter(succ[nxt])))
    indeg = {sid: 0 for sid in order}
    for a in order:
        for b in forward[a]:
            indeg[b] += 1
    layer = {sid: 0 for sid in order}
    ready = sorted((sid for sid in order if indeg[sid] == 0), key=lambda s: idx[s])
    while ready:
        a = ready.pop(0)
        for b in forward[a]:
            layer[b] = max(layer[b], layer[a] + 1)
            indeg[b] -= 1
            if indeg[b] == 0:
                ready.append(b)
                ready.sort(key=lambda s: idx[s])
    last_side = [sid for sid in order if sm[sid].get("category") != "done" and sid not in SYSTEM_STATUSES]
    top = max((layer[s] for s in last_side), default=-1)
    for sid in order:
        if sm[sid].get("category") == "done" or sid in SYSTEM_STATUSES:
            layer[sid] = top + 1
    cols: dict[int, list[str]] = {}
    for sid in order:
        cols.setdefault(layer[sid], []).append(sid)
    preds: dict[str, list[str]] = {sid: [] for sid in order}
    for a in order:
        for b in succ[a]:
            preds[b].append(a)

    # Rows: each status as level as it can be with the statuses it comes from (the main path runs straight), the
    # completed outcome first, the rest below; at least a row apart in a column.
    ys: dict[str, int] = {}
    out = {}
    for c in sorted(cols):
        def want(sid: str):
            ps = [ys[p] for p in preds[sid] if p in ys and layer[p] < layer[sid]]
            return sum(ps) / len(ps) if ps else None

        def rank(sid: str) -> tuple:
            w = want(sid)
            done_rank = ({"completed": 0}.get(sm[sid].get("outcome") or "completed", 1) if sm[sid].get("category") == "done" else 0)
            return (1 if sid in SYSTEM_STATUSES else 0, done_rank, 1 if w is None else 0, w or 0, idx[sid])

        last = None
        for sid in sorted(cols[c], key=rank):
            w = want(sid)
            y = snap(w) if w is not None else (Y0 if last is None else last + ROW)
            if last is not None and y < last + ROW:
                y = last + ROW
            y = max(y, Y0)
            ys[sid] = y
            last = y
            out[sid] = [snap(X0 + c * COL), snap(y)]
    return {sid: out[sid] for sid in order}


def complete_layout(g: dict, layout: dict | None) -> dict:
    """A layout for every status of the graph: the given positions, tidy's for any status without one; label offsets
    kept only for transitions that exist."""
    lay = {k: [int(v[0]), int(v[1])] for k, v in (layout or {}).items() if isinstance(v, (list, tuple)) and len(v) == 2}
    keep = set(status_map(g)) | {label_key(t) for t in transition_map(g)}
    lay = {k: v for k, v in lay.items() if k in keep}
    missing = [s["id"] for s in g.get("statuses") or [] if s["id"] not in lay]
    if missing:
        if len(missing) == len(g.get("statuses") or []):
            return {**tidy(g), **{k: v for k, v in lay.items() if k.startswith("label:")}}
        for sid in missing:
            lay[sid] = free_spot(lay)
    return lay


def free_spot(layout: dict, near: list | tuple | None = None) -> list[int]:
    """Where a new status overlaps none: the free place closest to ``near`` (the middle of what the editor shows), else
    the first free place on tidy's grid."""
    boxes = list(positions(layout).values())
    if near:
        cands = [(snap(near[0] - W / 2 + i * (W + 32)), snap(near[1] - H / 2 + j * (H + 48))) for i in range(-4, 5) for j in range(-4, 5)]
        cands.sort(key=lambda p: ((p[0] + W / 2 - near[0]) ** 2 + (p[1] + H / 2 - near[1]) ** 2, p))
        for x, y in cands:
            if all(abs(x - bx) >= W + 2 * GRID or abs(y - by) >= H + 2 * GRID for bx, by in boxes):
                return [x, y]
    for r in range(0, 40):
        for c in range(0, 12):
            x, y = X0 + c * COL, Y0 + r * ROW
            if all(abs(x - bx) >= W + GRID or abs(y - by) >= H + GRID for bx, by in boxes):
                return [snap(x), snap(y)]
    return [X0, Y0]


# ---------------------------------------------------------------- operations
DEFAULT_GATE = {"kind": "person", "role": "owner"}


def _doc(doc: dict) -> tuple[dict, bool]:
    if "graph" in doc:
        d = copy.deepcopy(doc)
        d.setdefault("layout", {})
        d.setdefault("state_map", {})
        return d, True
    return {"graph": copy.deepcopy(doc), "layout": {}, "state_map": {}}, False


def _unique(base: str, taken: set[str]) -> str:
    out, n = base, 2
    while out in taken:
        out = f"{base}-{n}"
        n += 1
    return out


def _ids(g: dict) -> set[str]:
    return set(status_map(g)) | set(transition_map(g))


def _status(g: dict, sid) -> dict:
    s = status_map(g).get(sid)
    if s is None:
        raise ValueError(f"there is no status {sid!r}")
    return s


def _transition(g: dict, tid) -> dict:
    t = transition_map(g).get(tid)
    if t is None:
        raise ValueError(f"there is no transition {tid!r}")
    return t


def _name(text, what: str) -> str:
    n = " ".join(str(text or "").split())
    if not n:
        raise ValueError(f"give the {what} a name")
    if len(n) > 60:
        raise ValueError(f"keep the {what}'s name under 60 characters")
    return n


def prepare_op(doc: dict, op: dict) -> dict:
    """The operation as it is logged: new elements get their ids (from their names, unique) and a new status its
    place, positions are snapped to the grid. Replaying the log gives the same graph."""
    d, _ = _doc(doc)
    g = d["graph"]
    op = copy.deepcopy(op)
    kind = op.get("op")
    if kind == "batch":
        ops, cur = [], d
        for o in op.get("ops") or []:
            p = prepare_op(cur, o)
            ops.append(p)
            cur = apply_op(cur, p)
        op["ops"] = ops
        return op
    if kind == "add_status":
        op["name"] = _name(op.get("name"), "status")
        if not op.get("id"):
            op["id"] = _unique(slug(op["name"]), _ids(g))
        if not op.get("at"):
            near = op.pop("near", None)
            ok = isinstance(near, (list, tuple)) and len(near) == 2 and all(isinstance(v, (int, float)) for v in near)
            op["at"] = free_spot(complete_layout(g, d["layout"]), near if ok else None)
        if op.get("global") and not op.get("transition_id"):
            op["transition_id"] = _unique("to-" + op["id"], _ids(g) | {op["id"]})
    elif kind == "add_transition":
        op["name"] = _name(op.get("name") or name_of(g, op.get("to")), "transition")
        if not op.get("id"):
            op["id"] = _unique(slug(op["name"]), _ids(g))
    elif kind == "move" and op.get("at"):
        op["at"] = [snap(op["at"][0]), snap(op["at"][1])]
    elif kind == "move_label" and op.get("offset"):
        op["offset"] = [snap(op["offset"][0]), snap(op["offset"][1])]
    return op


def apply_op(doc: dict, op: dict) -> dict:
    """One editor operation on a document ``{"graph", "layout", "state_map"}`` (or on a bare graph, giving a graph).
    Pure: the input is not changed. Raises ValueError, in words, for an operation that cannot apply. The operations
    (``OPS``):

    add_status {name, category, outcome?, sla_days?, at?, global?}   rename_status {id, name}
    recategorise_status {id, category, outcome?}    set_sla {id, days}    delete_status {id, move_to?}
    add_transition {name, from: [ids] | "any", to, type?, gate?, except?, validators?, actions?, fields?}
    rename_transition {id, name}    reroute_transition {id, from?, to?}    delete_transition {id}
    set_gate {id, gate}   set_validators {id, validators}   set_actions {id, actions}   set_fields {id, fields}
    set_global {id, global, from?, except?}   set_except {id, except (None: the done statuses)}
    move {id, at: [x, y]}   move_label {id, offset: [dx, dy] | None}   tidy {}   batch {ops}
    """
    d, wrapped = _doc(doc)
    g, lay, smap = d["graph"], d["layout"], d["state_map"]
    g.setdefault("statuses", [])
    g.setdefault("transitions", [])
    kind = op.get("op")
    if kind not in OPS:
        raise ValueError(f"unknown operation {kind!r}")
    if kind == "batch":
        for o in op.get("ops") or []:
            d = apply_op(d, o)
        return d if wrapped else d["graph"]
    if kind == "add_status":
        op = prepare_op(d, op) if not op.get("id") or not op.get("at") else op
        if op["id"] in _ids(g):
            raise ValueError(f"the id {op['id']} is taken")
        cat = op.get("category") or "in_progress"
        if cat not in CATEGORIES:
            raise ValueError("the category is to do, in progress or done")
        s = {"id": op["id"], "name": _name(op.get("name"), "status"), "category": cat}
        if cat == "done":
            s["outcome"] = op.get("outcome") or "completed"
        if op.get("sla_days"):
            s["sla_days"] = int(op["sla_days"])
        g["statuses"].append(s)
        lay[op["id"]] = [snap(op["at"][0]), snap(op["at"][1])]
        smap.pop(op["id"], None)
        if op.get("global"):
            tid = op.get("transition_id") or _unique("to-" + op["id"], _ids(g))
            g["transitions"].append({"id": tid, "type": "global", "to": op["id"], "name": s["name"], "gate": copy.deepcopy(DEFAULT_GATE),
                                     "validators": [], "actions": [], "fields": []})
    elif kind == "rename_status":
        s = _status(g, op.get("id"))
        name = _name(op.get("name"), "status")
        if any(x["id"] != s["id"] and x.get("name", "").lower() == name.lower() for x in g["statuses"]):
            raise ValueError(f"another status is called “{name}”")
        s["name"] = name
    elif kind == "recategorise_status":
        s = _status(g, op.get("id"))
        cat = op.get("category")
        if cat not in CATEGORIES:
            raise ValueError("the category is to do, in progress or done")
        s["category"] = cat
        if cat == "done":
            out = op.get("outcome") or s.get("outcome") or "completed"
            if out not in OUTCOMES:
                raise ValueError(f"the outcome is one of {', '.join(OUTCOMES)}")
            s["outcome"] = out
        else:
            s.pop("outcome", None)
    elif kind == "set_sla":
        s = _status(g, op.get("id"))
        days = op.get("days")
        if days in (None, "", 0):
            s.pop("sla_days", None)
        else:
            try:
                n = int(days)
            except (TypeError, ValueError):
                raise ValueError("the due date is a whole number of days") from None
            if n < 1 or n > 365:
                raise ValueError("the due date is between 1 and 365 days")
            s["sla_days"] = n
    elif kind == "delete_status":
        sid = op.get("id")
        _status(g, sid)
        g["statuses"] = [s for s in g["statuses"] if s["id"] != sid]
        keep = []
        for t in g["transitions"]:
            if t.get("to") == sid:
                continue
            if sid in (t.get("from") or []):
                t["from"] = [x for x in t["from"] if x != sid]
                if not t["from"] and (t.get("type") or "directed") == "directed":
                    continue
            if t.get("except"):
                t["except"] = [x for x in t["except"] if x != sid]
            keep.append(t)
        gone = {t["id"] for t in g["transitions"]} - {t["id"] for t in keep}
        g["transitions"] = keep
        for t in keep:
            gate = t.get("gate") or {}
            if gate.get("not"):
                gate["not"] = [n for n in gate["not"] if n not in gone]
            t["validators"] = [v for v in t.get("validators") or [] if v.get("status_visited") != sid]
        lay.pop(sid, None)
        for tid in gone:
            lay.pop(label_key(tid), None)
        if op.get("move_to"):
            if op["move_to"] == sid or op["move_to"] not in status_map(g):
                raise ValueError("move the items to a status that stays")
            smap[sid] = op["move_to"]
    elif kind == "add_transition":
        op = prepare_op(d, op) if not op.get("id") else op
        if op["id"] in _ids(g):
            raise ValueError(f"the id {op['id']} is taken")
        to = op.get("to")
        _status(g, to)
        ttype = op.get("type") or ("global" if op.get("from") == "any" else "directed")
        t = {"id": op["id"], "type": ttype, "to": to, "name": _name(op.get("name"), "transition"),
             "gate": copy.deepcopy(op.get("gate") or DEFAULT_GATE), "validators": copy.deepcopy(op.get("validators") or []),
             "actions": list(op.get("actions") or []), "fields": list(op.get("fields") or [])}
        if ttype == "directed":
            frm = op.get("from") or []
            if isinstance(frm, str):
                frm = [frm]
            if not frm:
                raise ValueError("choose at least one status it leaves from, or Any status")
            for x in frm:
                _status(g, x)
            t["from"] = list(dict.fromkeys(frm))
        elif ttype == "global":
            if op.get("except") is not None:
                for x in op["except"]:
                    _status(g, x)
                t["except"] = list(op["except"])
        elif ttype == "initial":
            if any(x.get("type") == "initial" for x in g["transitions"]):
                raise ValueError("there is a start already: change where it leads instead")
        else:
            raise ValueError("a transition is directed, global or the start")
        g["transitions"].append(t)
    elif kind == "rename_transition":
        _transition(g, op.get("id"))["name"] = _name(op.get("name"), "transition")
    elif kind == "reroute_transition":
        t = _transition(g, op.get("id"))
        if op.get("to") is not None:
            _status(g, op["to"])
            t["to"] = op["to"]
        if op.get("from") is not None:
            if (t.get("type") or "directed") != "directed":
                raise ValueError(f"“{t['name']}” leaves from {'no status' if t.get('type') == 'initial' else 'any status'}: make it directed first")
            frm = [op["from"]] if isinstance(op["from"], str) else list(op["from"])
            if not frm:
                raise ValueError("choose at least one status it leaves from")
            for x in frm:
                _status(g, x)
            t["from"] = list(dict.fromkeys(frm))
    elif kind == "delete_transition":
        tid = op.get("id")
        _transition(g, tid)
        g["transitions"] = [t for t in g["transitions"] if t["id"] != tid]
        for t in g["transitions"]:
            gate = t.get("gate") or {}
            if tid in (gate.get("not") or []):
                gate["not"] = [n for n in gate["not"] if n != tid]
        lay.pop(label_key(tid), None)
    elif kind == "set_gate":
        t = _transition(g, op.get("id"))
        gate = op.get("gate")
        if not isinstance(gate, dict) or gate.get("kind") not in GATE_KINDS:
            raise ValueError("a gate is a person's role, a cog or automatic")
        clean = {"kind": gate["kind"]}
        if gate["kind"] == "person":
            clean["role"] = gate.get("role")
            if gate.get("not"):
                clean["not"] = list(dict.fromkeys(gate["not"]))
        elif gate["kind"] == "cog":
            clean["cog"], clean["fallback"] = gate.get("cog"), gate.get("fallback")
        else:
            clean["when"] = gate.get("when") or "actions-succeed"
        t["gate"] = clean
    elif kind == "set_validators":
        t = _transition(g, op.get("id"))
        vs = op.get("validators") or []
        if not isinstance(vs, list) or not all(isinstance(v, dict) for v in vs):
            raise ValueError("checks are a list")
        t["validators"] = copy.deepcopy(vs)
    elif kind == "set_actions":
        t = _transition(g, op.get("id"))
        t["actions"] = [str(a).strip() for a in op.get("actions") or [] if str(a).strip()]
    elif kind == "set_fields":
        t = _transition(g, op.get("id"))
        t["fields"] = list(dict.fromkeys(str(f).strip() for f in op.get("fields") or [] if str(f).strip()))
    elif kind == "set_global":
        t = _transition(g, op.get("id"))
        if t.get("type") == "initial":
            raise ValueError("the start leaves from no status")
        if op.get("global"):
            t["type"] = "global"
            t.pop("from", None)
            if op.get("except") is not None:
                t["except"] = list(op["except"])
            else:
                t.pop("except", None)
        else:
            frm = op.get("from") or []
            frm = [frm] if isinstance(frm, str) else list(frm)
            if not frm:
                raise ValueError("choose the statuses it leaves from")
            for x in frm:
                _status(g, x)
            t["type"] = "directed"
            t["from"] = list(dict.fromkeys(frm))
            t.pop("except", None)
    elif kind == "set_except":
        t = _transition(g, op.get("id"))
        if t.get("type") != "global":
            raise ValueError(f"“{t['name']}” is not from any status")
        if op.get("except") is None:
            t.pop("except", None)
        else:
            for x in op["except"]:
                _status(g, x)
            t["except"] = list(dict.fromkeys(op["except"]))
    elif kind == "move":
        _status(g, op.get("id"))
        at = op.get("at") or [0, 0]
        lay[op["id"]] = [snap(at[0]), snap(at[1])]
    elif kind == "move_label":
        _transition(g, op.get("id"))
        if op.get("offset") in (None, [0, 0]):
            lay.pop(label_key(op["id"]), None)
        else:
            lay[label_key(op["id"])] = [snap(op["offset"][0]), snap(op["offset"][1])]
    elif kind == "tidy":
        d["layout"] = tidy(g)
    return d if wrapped else d["graph"]


def op_words(doc: dict, op: dict) -> str:
    """What an operation did, for the live region: 'Added status “Review”'."""
    d, _ = _doc(doc)
    g = d["graph"]
    k = op.get("op")
    sname = lambda: name_of(g, op.get("id"))
    tname = lambda: (transition_map(g).get(op.get("id")) or {}).get("name") or op.get("id")
    if k == "batch":
        words, cur = [], d
        for o in op.get("ops") or []:
            words.append(op_words(cur, o))
            try:
                cur = apply_op(cur, o)
            except ValueError:
                break
        return op.get("said") or ("; ".join(words) if words else "No change")
    return {
        "add_status": lambda: f"Added status “{op.get('name')}”",
        "rename_status": lambda: f"Renamed “{sname()}” to “{op.get('name')}”",
        "recategorise_status": lambda: f"“{sname()}” is now {CATEGORY_WORDS.get(op.get('category'), op.get('category'))}",
        "set_sla": lambda: f"“{sname()}”: " + (f"due within {op.get('days')} days" if op.get("days") else "no due date"),
        "delete_status": lambda: f"Deleted status “{sname()}”" + (f"; its items move to “{name_of(g, op.get('move_to'))}”" if op.get("move_to") else ""),
        "add_transition": lambda: f"Added transition “{op.get('name')}” to “{name_of(g, op.get('to'))}”",
        "rename_transition": lambda: f"Renamed “{tname()}” to “{op.get('name')}”",
        "reroute_transition": lambda: f"Rerouted “{tname()}”",
        "delete_transition": lambda: f"Deleted transition “{tname()}”",
        "set_gate": lambda: f"“{tname()}”: {gate_words(g, op.get('gate'))}",
        "set_validators": lambda: f"“{tname()}”: checks changed",
        "set_actions": lambda: f"“{tname()}”: actions changed",
        "set_fields": lambda: f"“{tname()}”: fields changed",
        "set_global": lambda: f"“{tname()}” " + ("now leaves from any status" if op.get("global") else "now leaves from chosen statuses"),
        "set_except": lambda: f"“{tname()}”: exceptions changed",
        "move": lambda: f"Moved “{sname()}” to {op.get('at', [0, 0])[0]}, {op.get('at', [0, 0])[1]}",
        "move_label": lambda: f"Moved the label of “{tname()}”" if op.get("offset") else f"Put the label of “{tname()}” back",
        "tidy": lambda: "Tidied the layout",
    }.get(k, lambda: "Done")()


def default_move_to(g: dict, sid: str) -> str | None:
    """Where the items in a status go when it is deleted, unless someone says otherwise: for a done status another done
    status with the same outcome (else the first done one), else the status the work came from (the source of the first
    transition into it), else the start."""
    sm = status_map(g)
    s = sm.get(sid) or {}
    others = [x for x in g.get("statuses") or [] if x["id"] != sid]
    if s.get("category") == "done":
        same = [x["id"] for x in others if x.get("category") == "done" and (x.get("outcome") or "completed") == (s.get("outcome") or "completed")]
        anydone = [x["id"] for x in others if x.get("category") == "done"]
        if same or anydone:
            return (same or anydone)[0]
    for t in g.get("transitions") or []:
        if t.get("to") == sid and (t.get("type") or "directed") == "directed":
            for a in t.get("from") or []:
                if a != sid and a in sm:
                    return a
    starts = [x for x in initial_targets(g) if x != sid and x in sm]
    return starts[0] if starts else (others[0]["id"] if others else None)


# ---------------------------------------------------------------- geometry shared with the editor's script
def box(layout: dict, sid: str) -> tuple[float, float, float, float]:
    x, y = (layout or {}).get(sid) or (X0, Y0)
    return (float(x), float(y), float(W), float(H))


def port(b: tuple, toward: tuple) -> tuple[float, float]:
    """The middle of the side of box ``b`` that faces the point ``toward``."""
    x, y, w, h = b
    cx, cy = x + w / 2, y + h / 2
    dx, dy = toward[0] - cx, toward[1] - cy
    if abs(dx) * h >= abs(dy) * w:
        return (x + w, cy) if dx > 0 else (x, cy)
    return (cx, y + h) if dy > 0 else (cx, y)


def _hits(points: list[tuple], boxes: list[tuple]) -> int:
    n = 0
    for x, y, w, h in boxes:
        if any(x - GRID < px < x + w + GRID and y - GRID < py < y + h + GRID for px, py in points):
            n += 1
    return n


def _samples(geo_d: str) -> list[tuple]:
    nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", geo_d)]
    (x1, y1, cx, cy, x2, y2) = nums[:6]
    return [((1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t * t * x2, (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t * t * y2)
            for t in (i / 16 for i in range(2, 15))]


def bends(g: dict, layout: dict | None = None) -> dict[str, float]:
    """How far each transition between two statuses bows from the straight line, so opposite and parallel transitions
    get separate curves: transitions joining the same pair are spread PAIR_GAP apart around the middle. With a layout,
    a lone transition whose straight line would cross another status bows round it (the side that crosses fewer)."""
    groups: dict[tuple, list[tuple[str, bool]]] = {}
    for t in g.get("transitions") or []:
        frm = t.get("from") or []
        if (t.get("type") or "directed") != "directed" or len(frm) != 1 or frm[0] == t.get("to"):
            continue
        a, b = frm[0], t.get("to")
        key = (a, b) if a <= b else (b, a)
        groups.setdefault(key, []).append((t["id"], a > b))
    out = {}
    for key, members in groups.items():
        n = len(members)
        for i, (tid, reverse) in enumerate(sorted(members)):
            off = (i - (n - 1) / 2) * PAIR_GAP
            out[tid] = -off if reverse else off
    if layout:
        tm = transition_map(g)
        for key, members in groups.items():
            if len(members) != 1:
                continue
            tid = members[0][0]
            a, b = tm[tid]["from"][0], tm[tid]["to"]
            if a not in layout or b not in layout:
                continue
            others = [box(layout, s) for s in positions(layout) if s not in (a, b) and s in status_map(g)]
            ba, bb = box(layout, a), box(layout, b)
            if not _hits(_samples(edge_geometry(ba, bb, 0.0)["d"]), others):
                continue
            best = min(((_hits(_samples(edge_geometry(ba, bb, c)["d"]), others), abs(c), -c) for c in (96.0, -96.0, 176.0, -176.0)))
            out[tid] = -best[2]
    return out


def curve(p1: tuple, p2: tuple, bend: float) -> dict:
    """A quadratic curve from p1 to p2 bowed ``bend`` to the left of its direction; ends moved apart along the normal
    so two opposite curves do not share their end points. Returns {"d", "label": point at t = 0.5}."""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    ln = (dx * dx + dy * dy) ** 0.5 or 1.0
    nx, ny = -dy / ln, dx / ln
    if bend:
        s = 8 if bend > 0 else -8
        p1 = (p1[0] + nx * s, p1[1] + ny * s)
        p2 = (p2[0] + nx * s, p2[1] + ny * s)
    mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
    c = (mx + nx * bend, my + ny * bend)
    label = ((p1[0] + 2 * c[0] + p2[0]) / 4, (p1[1] + 2 * c[1] + p2[1]) / 4)
    return {"d": f"M{_n(p1[0])},{_n(p1[1])} Q{_n(c[0])},{_n(c[1])} {_n(p2[0])},{_n(p2[1])}", "label": label}


def edge_geometry(a: tuple, b: tuple, bend: float) -> dict:
    ca, cb = (a[0] + a[2] / 2, a[1] + a[3] / 2), (b[0] + b[2] / 2, b[1] + b[3] / 2)
    return curve(port(a, cb), port(b, ca), bend)


def loop_geometry(b: tuple, k: int) -> dict:
    """A loop: an arc below the box (the k-th loop on it a little lower)."""
    x, y, w, h = b
    cx, base = x + w / 2, y + h
    hh = LOOP_H + LOOP_STEP * k
    x1, x2 = cx - 18, cx + 18
    d = f"M{_n(x1)},{_n(base)} C{_n(x1 - 22)},{_n(base + hh)} {_n(x2 + 22)},{_n(base + hh)} {_n(x2)},{_n(base)}"
    return {"d": d, "label": (cx, base + hh * 0.75 + 16)}


def global_label(b: tuple, k: int, n: int) -> tuple[float, float]:
    x, y, w, h = b
    return (x + w / 2 + (k - (n - 1) / 2) * GLOBAL_PITCH, y - GLOBAL_DY)


def stem(label: tuple, b: tuple) -> dict:
    """A straight line from a label (an 'any status' marker, a shared transition's junction) into box ``b``."""
    p2 = port(b, label)
    return {"d": f"M{_n(label[0])},{_n(label[1])} L{_n(p2[0])},{_n(p2[1])}"}


def start_point(b: tuple) -> tuple[float, float]:
    x, y, w, h = b
    return (x - START_DX, y + h / 2)


def junction(srcs: list[tuple], b: tuple) -> tuple[float, float]:
    """Where a transition from several statuses joins before its target: halfway from their middle to the target."""
    sx = sum(s[0] + s[2] / 2 for s in srcs) / len(srcs)
    sy = sum(s[1] + s[3] / 2 for s in srcs) / len(srcs)
    tx, ty = b[0] + b[2] / 2, b[1] + b[3] / 2
    return ((sx + tx) / 2, (sy + ty) / 2)


def transition_geometry(g: dict, layout: dict, t: dict, *, bend: float = 0.0, k: int = 0, n: int = 1) -> dict:
    """The drawing of one transition: {"kind", "parts": [(part name, path d, arrow?)], "label": (x, y)} with the label
    offset applied. Kinds: initial (a start point), global (an 'any status' marker above the target), loop, edge (one
    source) and shared (several sources joining at the label)."""
    lay = layout or {}
    off = lay.get(label_key(t["id"])) or [0, 0]
    dst = box(lay, t.get("to"))
    kind = t.get("type") or "directed"
    if kind == "initial":
        sp = start_point(dst)
        lbl = (sp[0], sp[1] - 30)
        return {"kind": "initial", "start": sp, "parts": [("line", f"M{_n(sp[0])},{_n(sp[1])} L{_n(dst[0])},{_n(sp[1])}", True)],
                "label": (lbl[0] + off[0], lbl[1] + off[1])}
    if kind == "global":
        base = global_label(dst, k, n)
        lbl = (base[0] + off[0], base[1] + off[1])
        return {"kind": "global", "parts": [("line", stem((lbl[0], lbl[1] + 18), dst)["d"], True)], "label": lbl}
    frm = [x for x in t.get("from") or [] if x in lay]
    loops = [x for x in frm if x == t.get("to")]
    others = [x for x in frm if x != t.get("to")]
    if loops and not others:
        geo = loop_geometry(dst, k)
        return {"kind": "loop", "parts": [("line", geo["d"], True)], "label": (geo["label"][0] + off[0], geo["label"][1] + off[1])}
    if len(others) == 1 and not loops:
        geo = edge_geometry(box(lay, others[0]), dst, bend)
        return {"kind": "edge", "parts": [("line", geo["d"], True)], "label": (geo["label"][0] + off[0], geo["label"][1] + off[1])}
    srcs = [box(lay, x) for x in others] or [dst]
    j = junction(srcs, dst)
    lbl = (j[0] + off[0], j[1] + off[1])
    parts = []
    for x, sb in zip(others, srcs):
        p1 = port(sb, lbl)
        parts.append((f"arm:{x}", f"M{_n(p1[0])},{_n(p1[1])} L{_n(lbl[0])},{_n(lbl[1])}", False))
    parts.append(("line", stem(lbl, dst)["d"], True))
    if loops:
        parts.append(("loop", loop_geometry(dst, k)["d"], True))
    return {"kind": "shared", "parts": parts, "label": lbl}
