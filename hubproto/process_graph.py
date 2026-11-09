"""Processes as graphs (change control v2, section 10.1): the shape the release carries at ``workflows[<process>]``.

A process is a graph of **statuses** and **transitions**. One shape serves both ways an SOP writes a process:

* numbered steps (the linear shorthand) compile here with no change of meaning (``from_linear``): each checkpoint is a
  status ``awaiting-<checkpoint>``; the cog steps before the first checkpoint are the actions of an automatic transition
  out of ``open``; the steps after a checkpoint are the actions of its approval; a rejection is a transition (to
  ``rejected``, to ``returned`` with a Resubmit, or back to an earlier checkpoint); Cancel is a global transition; a
  failed action moves the item to the system ``failed`` status, which has a Retry loop;
* the graph form (``Statuses:`` and ``From X to Y ...`` sentences, read by ``hubproto.extract``) compiles with
  ``from_statements``.

The graph is policy: ids are stable (a rename keeps the id), and everything the runner does follows from it
(``hubproto.runner``). The keys ``object``, ``states``, ``checkpoints``, ``terminal``, ``reject_to`` and ``sla`` are a
derived view kept for the readers of the earlier state-machine shape (the shell's status bar and kanban columns, the
CLI, routing's SLA lookup); ``graph_only`` drops them. ``legacy_transitions`` gives the earlier transition list.

Pure functions, standard library only.
"""

from __future__ import annotations

import re

CATEGORIES = ("todo", "in_progress", "done")
OUTCOMES = ("completed", "rejected", "cancelled", "superseded")
GATE_KINDS = ("person", "cog", "committee", "auto")
DECIDED_BY = ("person", "cog", "committee")  # gates that wait for a decision (a committee: every member agrees)
TRANSITION_TYPES = ("initial", "directed", "global")
# Roles a gate may name that are fields of the item (who is named on it), not roles of the org model.
FIELD_ROLES = ("assignee", "owner", "author", "requester", "auditor", "chair", "evaluator", "reporter")
# The derived view of the earlier shape, kept beside the graph for its readers.
LEGACY_KEYS = ("object", "states", "checkpoints", "terminal", "reject_to", "sla")
SYSTEM_FAILED = {"id": "failed", "name": "Failed", "category": "in_progress", "system": True}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower()).strip("-")


def _cancelled(done_ids: list[str]) -> dict:
    return {"id": "cancel", "type": "global", "except": list(done_ids), "to": "cancelled", "name": "Cancel",
            "gate": {"kind": "person", "role": "owner", "also": "approvers"}, "validators": [], "actions": [], "fields": []}


def _retry() -> dict:
    # A loop on the system status: the runner resumes the transition that failed, from the action that failed.
    return {"id": "retry", "type": "directed", "from": ["failed"], "to": "failed", "name": "Retry", "system": True,
            "gate": {"kind": "person", "role": "owner", "also": "approvers"}, "validators": [], "actions": [], "fields": []}


# ------------------------------------------------------------------------------------------- linear shorthand
def from_linear(process: dict, slas: dict, record_type: str) -> dict:
    """The graph of a process written as numbered steps, with no change of meaning."""
    steps = process.get("steps") or []
    checkpoints = [s for s in steps if s["kind"] == "checkpoint"]
    cp_ids = [c["id"] for c in checkpoints]
    terminal = process.get("terminal") or "closed"
    reject_to = process.get("reject_to")
    statuses = [{"id": "open", "name": "Open", "category": "todo"}]
    for cp in checkpoints:
        st = {"id": f"awaiting-{cp['id']}", "name": f"Awaiting {cp['title'][:1].lower()}{cp['title'][1:]}", "category": "in_progress"}
        if slas.get(cp["id"]):
            st["sla_days"] = slas[cp["id"]]
        statuses.append(st)
    statuses.append({"id": terminal, "name": terminal.replace("-", " ").capitalize(), "category": "done", "outcome": "completed"})
    if reject_to and reject_to in cp_ids:
        on_reject = f"awaiting-{reject_to}"
    elif reject_to:
        on_reject = "returned"
        statuses.append({"id": "returned", "name": "Returned", "category": "todo"})
    else:
        on_reject = "rejected"
        statuses.append({"id": "rejected", "name": "Rejected", "category": "done", "outcome": "rejected"})
    ids = {s["id"] for s in statuses}
    if "failed" not in ids:
        statuses.append(dict(SYSTEM_FAILED))
    if "cancelled" not in ids:
        statuses.append({"id": "cancelled", "name": "Cancelled", "category": "done", "outcome": "cancelled"})
    lines = {"create": steps[0]["line"]} if steps and steps[0].get("line") else {}
    transitions = [{"id": "create", "type": "initial", "to": "open", "name": steps[0]["title"] if steps else "Create",
                    "gate": {"kind": "person", "role": steps[0]["role"] if steps else "system"},
                    "validators": [], "actions": [], "fields": []}]
    # Walk the steps: cog steps gather as actions; a checkpoint closes the transition into its waiting status.
    current, actions, gate_cp = "open", [], None
    groups: list[tuple[str, str, list[str], dict | None]] = []  # (from, to, actions, the checkpoint that decides it)
    for s in steps:
        if s["kind"] == "cog":
            actions.append(s["id"])
            continue
        if gate_cp is not None and not actions:
            actions.append(f"reach-{s['id']}")  # two checkpoints in a row: a no-op step carries the second Gate
        groups.append((current, f"awaiting-{s['id']}", actions, gate_cp))
        current, actions, gate_cp = f"awaiting-{s['id']}", [], s
    groups.append((current, terminal, actions, gate_cp))
    for src, dst, acts, cp in groups:
        if cp is None:
            transitions.append({"id": f"auto-{dst}", "type": "directed", "from": [src], "to": dst, "name": "Automatically",
                                "gate": {"kind": "auto", "when": "actions-succeed"}, "validators": [], "actions": list(acts), "fields": []})
            continue
        gate = {"kind": "person", "role": cp["role"]}
        transitions.append({"id": cp["id"], "type": "directed", "from": [src], "to": dst, "name": cp["title"], "gate": gate,
                            "validators": [], "actions": list(acts), "fields": []})
        transitions.append({"id": f"reject-{cp['id']}", "type": "directed", "from": [src], "to": on_reject, "name": "Reject",
                            "gate": dict(gate), "validators": [], "actions": [], "fields": []})
        if cp.get("line"):
            lines[cp["id"]] = lines[f"reject-{cp['id']}"] = cp["line"]
    if on_reject == "returned":
        # Rework: the owner resubmits and the process runs again from the step the rejection returns to.
        owner = next((t for t in transitions if reject_to in (t.get("actions") or [])), None) or {"to": "open", "actions": [reject_to]}
        resumed = owner["actions"][owner["actions"].index(reject_to):]
        # The rework loop: the owner resubmits and the steps from the one the rejection returns to run again.
        transitions.append({"id": "resubmit", "type": "directed", "from": ["returned"], "to": owner["to"], "name": "Resubmit",
                            "gate": {"kind": "person", "role": "owner"}, "validators": [], "actions": list(resumed), "fields": []})
    done = [s["id"] for s in statuses if s["category"] == "done"]
    transitions.append(_cancelled(done))
    transitions.append(_retry())
    graph = {"process": process["name"], "record_type": record_type, "statuses": statuses, "transitions": transitions,
             "source": {"frame": (process.get("source") or {}).get("frame", ""), "lines": lines}, "form": "steps"}
    return {**graph, **_legacy_view(graph, process, record_type, slas)}


# ------------------------------------------------------------------------------------------- graph statements
def from_statements(process: dict, slas: dict, record_type: str) -> dict:
    """The graph of a process written in the graph form (``Statuses:`` and ``From X to Y ...`` sentences)."""
    statuses = [{k: v for k, v in s.items() if k not in ("line", "text")} for s in process.get("statuses") or []]
    transitions = []
    lines: dict[str, int] = {}
    for t in process.get("transitions") or []:
        tt = {k: v for k, v in t.items() if k not in ("line", "text", "source")}
        tt.setdefault("validators", [])
        tt.setdefault("actions", [])
        tt.setdefault("fields", [])
        tt["actions"] = [action_step_id(process, t, a) if isinstance(a, str) or "swarm" in a else a for a in tt["actions"]]
        transitions.append(tt)
        if t.get("line"):
            lines[t["id"]] = t["line"]
    # SLA sentences name a decision (a gated transition): the status it waits at carries the due date.
    for cp, days in (slas or {}).items():
        t = next((x for x in transitions if x["id"] == cp and x.get("type") == "directed"), None)
        for sid in (t or {}).get("from") or []:
            st = next((s for s in statuses if s["id"] == sid), None)
            if st is not None and not st.get("sla_days"):
                st["sla_days"] = days
    if not any(s["id"] == "failed" for s in statuses):
        statuses.append(dict(SYSTEM_FAILED))
    if not any(t["id"] == "retry" for t in transitions):
        transitions.append(_retry())
    graph = {"process": process["name"], "record_type": record_type, "statuses": statuses, "transitions": transitions,
             "source": {"frame": (process.get("source") or {}).get("frame", ""), "lines": lines}, "form": "graph"}
    return {**graph, **_legacy_view(graph, process, record_type, slas)}


def is_graph(process: dict) -> bool:
    return bool(process.get("statuses"))


def build(process: dict, slas: dict, record_type: str) -> dict:
    return from_statements(process, slas, record_type) if is_graph(process) else from_linear(process, slas, record_type)


def action_step_id(process: dict, transition: dict, cog) -> str:
    """The step id of a cog action in a graph process: the cog id, or the cog id and the transition id when the cog
    runs in more than one transition (step ids are unique in a process). A swarm ({"swarm": [cogs]}) is named by its
    cogs."""
    if isinstance(cog, dict):
        cog = "swarm-" + "-".join(cog.get("swarm") or [])
    uses = sum(1 for t in process.get("transitions") or [] for a in t.get("actions") or []
               if (a if isinstance(a, str) else "swarm-" + "-".join(a.get("swarm") or []) if isinstance(a, dict) and "swarm" in a else None) == cog)
    return cog if uses <= 1 else f"{cog}-{transition['id']}"


def is_cog_action(a) -> bool:
    return isinstance(a, str) or (isinstance(a, dict) and "swarm" in a)


def action_steps(process: dict) -> list[tuple[str, object, dict]]:
    """(step id, cog id or {"swarm": [cogs]}, transition) for every cog action of a graph process, in the order written."""
    out, seen = [], set()
    for t in process.get("transitions") or []:
        for a in t.get("actions") or []:
            if is_cog_action(a):
                sid = action_step_id(process, t, a)
                if sid not in seen:
                    seen.add(sid)
                    out.append((sid, a, t))
    return out


def gated_checkpoints(process: dict) -> list[dict]:
    """The decisions of a graph process as checkpoints ({id, title, role, kind}), for routing and SLA sentences."""
    out = []
    for t in process.get("transitions") or []:
        g = t.get("gate") or {}
        if t.get("type") == "directed" and g.get("kind") in DECIDED_BY:
            out.append({"id": t["id"], "title": t.get("name") or t["id"], "role": g.get("role") or g.get("fallback") or "system",
                        "kind": "checkpoint", "line": t.get("line", 0)})
    return out


# ------------------------------------------------------------------------------------------- the derived view
def _legacy_view(graph: dict, process: dict, record_type: str, slas: dict) -> dict:
    """The earlier state-machine keys, derived from the graph."""
    sts = graph["statuses"]
    terminal = next((s["id"] for s in sts if s["category"] == "done" and s.get("outcome", "completed") == "completed"), None)
    if graph.get("form") == "steps":
        states = ["open"] + [s["id"] for s in sts if s["id"].startswith("awaiting-")] + [process.get("terminal") or "closed"]
        states += [s for s in ("returned", "rejected") if any(x["id"] == s for x in sts)]
        checkpoints = [{"id": c["id"], "title": c["title"], "role": c["role"], "state": f"awaiting-{c['id']}",
                        "sla_days": slas.get(c["id"])} for c in process["steps"] if c["kind"] == "checkpoint"]
        reject_to = process.get("reject_to")
        terminal = process.get("terminal") or "closed"
    else:
        states = [s["id"] for s in sts if not s.get("system")]
        checkpoints = []
        for s in sts:
            prim = primary_option(graph, s["id"])
            if prim is not None:
                g = prim.get("gate") or {}
                checkpoints.append({"id": prim["id"], "title": prim.get("name") or prim["id"], "role": g.get("role") or g.get("fallback") or "",
                                    "state": s["id"], "sla_days": s.get("sla_days")})
        reject_to = None
        terminal = process.get("terminal") if process.get("terminal") in {s["id"] for s in sts} else (terminal or "closed")
    return {"object": process.get("object", ""), "states": states, "checkpoints": checkpoints, "terminal": terminal,
            "reject_to": reject_to, "sla": {c["id"]: c["sla_days"] for c in checkpoints if c.get("sla_days")}}


def graph_only(wf: dict) -> dict:
    """The 10.1 graph without the derived keys of the earlier shape."""
    return {k: v for k, v in wf.items() if k not in LEGACY_KEYS}


def is_graph_shape(wf: dict | None) -> bool:
    return bool(wf) and "statuses" in wf


def legacy_transitions(wf: dict | None) -> list[dict]:
    """The transitions in the earlier shape ({from, to, kind: automatic|human, steps | step, title, on_reject, sla_days}),
    for readers written against it. A workflow already in that shape is returned as it is."""
    wf = wf or {}
    ts = wf.get("transitions") or []
    if not is_graph_shape(wf):
        return list(ts)
    out, human = [], []
    for s in wf["statuses"]:
        for t in transitions_out(wf, s["id"], include_global=False):
            g = (t.get("gate") or {}).get("kind")
            if g == "auto":
                out.append({"from": s["id"], "to": t["to"], "kind": "automatic", "steps": [a for a in t.get("actions") or [] if isinstance(a, str)]})
            elif g in DECIDED_BY and option_verdict(wf, t) == "approve" and not t.get("system"):
                rej = rejection_option(wf, s["id"])
                human.append({"from": s["id"], "to": t["to"], "kind": "human", "step": t["id"], "title": t.get("name") or t["id"],
                              "on_reject": rej["to"] if rej else None, "sla_days": s.get("sla_days")})
    if wf.get("form") == "steps":
        # The earlier shape listed an automatic transition for the steps after each checkpoint as well.
        for t in wf["transitions"]:
            if t.get("type") == "directed" and (t.get("gate") or {}).get("kind") == "person" and t.get("actions") \
                    and not t.get("system") and option_verdict(wf, t) == "approve":
                out.append({"from": t["from"][0], "to": t["to"], "kind": "automatic", "steps": list(t["actions"])})
        order = {s: i for i, s in enumerate(x["id"] for x in wf["statuses"])}
        out.sort(key=lambda x: order.get(x["from"], 0))
    return out + human


# ------------------------------------------------------------------------------------------- reading a graph
def status_of(wf: dict, sid: str | None) -> dict | None:
    return next((s for s in (wf or {}).get("statuses") or [] if s["id"] == sid), None)


def transition(wf: dict, tid: str | None) -> dict | None:
    return next((t for t in (wf or {}).get("transitions") or [] if t["id"] == tid), None)


def done_ids(wf: dict) -> list[str]:
    return [s["id"] for s in (wf or {}).get("statuses") or [] if s.get("category") == "done"]


def initial(wf: dict) -> dict | None:
    return next((t for t in (wf or {}).get("transitions") or [] if t.get("type") == "initial"), None)


def global_applies(wf: dict, t: dict, sid: str) -> bool:
    """A global transition applies from every status except those it names (default: the done statuses)."""
    exc = t.get("except")
    exc = done_ids(wf) if exc is None else [x for e in exc for x in (done_ids(wf) if e == "done" else [e])]
    return sid not in exc and sid != t.get("to") and not (status_of(wf, sid) or {}).get("system")


def transitions_out(wf: dict, sid: str, include_global: bool = True) -> list[dict]:
    out = []
    for t in (wf or {}).get("transitions") or []:
        if t.get("type") == "directed" and sid in (t.get("from") or []):
            out.append(t)
        elif include_global and t.get("type") == "global" and global_applies(wf, t, sid):
            out.append(t)
    return out


def is_gated(t: dict) -> bool:
    return (t.get("gate") or {}).get("kind") in DECIDED_BY


def is_auto(t: dict) -> bool:
    return (t.get("gate") or {}).get("kind") == "auto"


def options(wf: dict, sid: str) -> list[dict]:
    """The waiting decision at a status: the gated directed transitions out of it, in the order the process states
    them (globals such as Cancel are offered on the item, not at the Gate, and system loops are the hub's)."""
    return [t for t in transitions_out(wf, sid, include_global=False) if is_gated(t) and not t.get("system")]


def primary_option(wf: dict, sid: str) -> dict | None:
    """The option an approval takes: the first that moves the item forward (else the first option)."""
    opts = options(wf, sid)
    return next((t for t in opts if option_verdict(wf, t) == "approve"), opts[0] if opts else None)


def rejection_option(wf: dict, sid: str) -> dict | None:
    return next((t for t in options(wf, sid) if option_verdict(wf, t) == "reject"), None)


def depths(wf: dict) -> dict[str, int]:
    """Shortest number of moves from the start to each status (breadth first over directed transitions)."""
    init = initial(wf)
    if init is None:
        return {}
    out = {init["to"]: 0}
    frontier = [init["to"]]
    while frontier:
        nxt = []
        for sid in frontier:
            for t in transitions_out(wf, sid, include_global=False):
                if t["to"] not in out:
                    out[t["to"]] = out[sid] + 1
                    nxt.append(t["to"])
        frontier = nxt
    return out


def option_verdict(wf: dict, t: dict) -> str:
    """What choosing a transition means for the signature: 'reject' when it ends the item as rejected, cancelled or
    superseded, returns it for rework, loops, or goes back towards the start; otherwise 'approve'."""
    target = status_of(wf, t.get("to")) or {}
    if target.get("category") == "done":
        return "approve" if target.get("outcome", "completed") == "completed" else "reject"
    if t.get("to") == "returned":
        return "reject"
    srcs = t.get("from") or []
    if t.get("to") in srcs:
        return "reject"
    d = depths(wf)
    if srcs and t.get("to") in d and all(s in d for s in srcs) and d[t["to"]] < min(d[s] for s in srcs):
        return "reject"
    return "approve"


# ------------------------------------------------------------------------------------------- writing a graph
CATEGORY_WORDS = {"todo": "to do", "in_progress": "in progress", "done": "done"}


def _role_words(role: str) -> str:
    return "the " + role.replace("-", " ")


def _names(items: list[str]) -> str:
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def gate_words(gate: dict, wf: dict | None = None) -> str:
    g = gate or {}
    if g.get("kind") == "auto":
        cond = g.get("when")
        return "automatically" + (f" when {condition_phrase(cond)}" if cond and cond != "actions-succeed" else "")
    if g.get("kind") == "cog":
        return f"the {g.get('cog', '').replace('-', ' ')} cog decides, else {_role_words(g.get('fallback') or 'quality-manager')}"
    if g.get("kind") == "committee":
        return f"the {g.get('committee', '')} committee decides"
    words = _role_words(g.get("role") or "system") + (" or an approver" if g.get("also") == "approvers" else "")
    nots = []
    for n in g.get("not") or []:
        t = transition(wf or {}, n)
        nots.append(f'whoever decided "{t.get("name") or n}"' if t else _role_words(n))
    return words + (" decides, not " + _names(nots) if nots else "")


def condition_phrase(key: str) -> str:
    from .rules import CONDITION_VOCABULARY

    return next((p for p, k in CONDITION_VOCABULARY.items() if k == key), key.replace("_", " "))


def action_words(a, cogs: dict | None = None) -> str:
    if isinstance(a, str) and isinstance((cogs or {}).get(a), dict):
        a = cogs[a]
    if isinstance(a, dict) and "swarm" in a:
        return " with ".join("the " + c.replace("-", " ") for c in a["swarm"])
    if isinstance(a, dict):
        if "notify" in a:
            kind, _, who = str(a["notify"]).partition(":")
            return "notify " + _role_words(who)
        if "set" in a:
            (f, v), = a["set"].items()
            return f"set {f.replace('_', ' ')} to {v}"
        return str(a)
    cog = (cogs or {}).get(a, a)
    return "the " + cog.replace("-", " ")


def to_sentences(wf: dict, cogs: dict | None = None) -> list[str]:
    """The graph written as the sentences ``hubproto.extract`` reads back to the same graph (statuses, transitions and
    their ids): what a direct change of a workflow says in the SOP. ``cogs`` maps a step id to its cog id."""
    sts = [s for s in wf.get("statuses") or [] if not s.get("system")]
    names = {s["id"]: s["name"] for s in wf.get("statuses") or []}

    def st_item(s):
        attrs = [CATEGORY_WORDS[s["category"]]]
        if s["category"] == "done" and s.get("outcome"):
            attrs.append(s["outcome"])
        if s.get("sla_days"):
            attrs.append(f"due within {s['sla_days']} days")
        ident = "" if slug(s["name"]) == s["id"] else f" [{s['id']}]"
        return f"{s['name']}{ident} ({', '.join(attrs)})"
    lines = ["Statuses: " + ", ".join(st_item(s) for s in sts) + "."]

    def tail(t):
        parts = [gate_words(t.get("gate") or {}, wf)]
        acts = [a for a in t.get("actions") or []]
        if (t.get("gate") or {}).get("kind") == "auto" and acts:
            parts[0] += ", after " + _names([action_words(a, cogs) for a in acts])
            acts = []
        if t.get("fields"):
            parts.append("needs " + _names([f.replace("_", " ") for f in t["fields"]]))
        extra = [v for v in t.get("validators") or [] if "fields_set" not in v]
        if extra:
            parts.append("only if " + _names([validator_words(v, wf) for v in extra]))
        if acts:
            verb = "" if any(isinstance(a, dict) and "swarm" not in a for a in acts) else (" runs" if len(acts) == 1 else " run")
            parts.append("then " + _names([action_words(a, cogs) for a in acts]) + verb)
        return "; ".join(parts) + "."

    def label(t):
        if (t.get("gate") or {}).get("kind") == "auto" and t.get("name") in (None, "", "Automatically") and t["id"] == default_id(t):
            return ""
        ident = "" if slug(t.get("name") or "") == t["id"] else f" [{t['id']}]"
        return f' ("{t.get("name") or t["id"]}"{ident})'
    for t in wf.get("transitions") or []:
        if t.get("system"):
            continue
        if t.get("type") == "initial":
            lines.append(f"Starts in {names.get(t['to'], t['to'])}{label(t)}: {tail(t)}")
        elif t.get("type") == "global":
            exc = t.get("except")
            if exc is None or sorted(exc) == sorted(done_ids(wf)) or exc == ["done"]:
                frm = "any status except done"
            elif not exc:
                frm = "any status"
            else:
                frm = "any status except " + _names([names.get(e, e) for e in exc])
            lines.append(f"From {frm} to {names.get(t['to'], t['to'])}{label(t)}: {tail(t)}")
        else:
            frm = _names([names.get(f, f) for f in t.get("from") or []])
            lines.append(f"From {frm} to {names.get(t['to'], t['to'])}{label(t)}: {tail(t)}")
    return lines


def default_id(t: dict) -> str:
    frm = (t.get("from") or ["any"])[0] if t.get("type") != "global" else "any"
    return f"{frm}-to-{t['to']}" if t.get("type") != "initial" else "start"


def validator_words(v: dict, wf: dict | None = None) -> str:
    if "status_visited" in v:
        return f"{(status_of(wf or {}, v['status_visited']) or {}).get('name', v['status_visited'])} was visited"
    if "condition" in v:
        return condition_phrase(v["condition"])
    if "fields_set" in v:
        return _names([f.replace("_", " ") for f in v["fields_set"]]) + " set"
    return str(v)
