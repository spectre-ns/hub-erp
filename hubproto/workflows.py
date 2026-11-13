"""Workflow graphs in the hub: the graph and layout in force, editor drafts with an operation log, and carrying a draft on
a change order (change control v2, sections 10.4 to 10.6). The graph itself is pure data (hubproto.workflow_model).

* **In force.** A process's graph comes from the release in force (``workflow_model.graph_of``). Its layout (where each
  status sits; presentation, not policy) is a versioned configuration object in ``state/workflows/layouts.json``, carried
  on a change order like a form layout; until one is released it is ``tidy``'s.
* **Drafts.** One per person and process (``state/workflows/drafts/<person>.json``), based on the release in force when
  it was started: the base graph and layout, the operation log, a cursor (undo and redo move it) and the document the
  log makes (``{"graph", "layout", "state_map"}``). Every edit is saved at once.
* **Change orders.** ``carry`` puts a draft on a change order in three parts:

  - what changes how items move: **one direct change** of the process rule (``changecontrol.add_direct_change``, the
    graph form of the SOP's process section, ``process_rule``). The review round drafts its prose into the SOP
    (``process_graph.to_sentences`` writes sentences that compile back to the same graph) and checks the round trip;
  - where items in removed statuses go: the change order's ``state_map`` (``changecontrol.set_state_map``), which the
    release applies;
  - the layout (presentation) and a snapshot for the visual diff, in the change order's ``workflow_changes`` field::

      workflow_changes: {<process>: {"graph": <10.1 graph>, "layout": {...}, "state_map": {old: new},
                                     "base": {"release", "graph_digest", "layout_version"}, "direct_id": <direct change id>,
                                     "items": {<status id>: count when carried}, "delta": [<delta item in words>],
                                     "layout_only": bool, "by", "at", "origin": {"kind": "direct", "by", "at"}}}

  The change-order core calls ``carried_reasons(hub, co)`` with its readiness checks, and the release automation
  ``activate(hub, co, digest)`` with the form layouts (the layouts come into force; only for an approved change order).
  ``delta_items(hub, co)`` gives the change in words, element by element (section 8 keys).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from . import workflow_model as wm
from .release import utcnow

EDIT_ROLES = ("document-controller", "policy-owner")
UNDO_KEEP = 500  # operations kept in a draft's log


class WorkflowStore:
    """state/workflows/: layouts.json (the layout in force per process, with its version), history/<process>.jsonl,
    drafts/<person>.json ({process: draft})."""

    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "drafts").mkdir(parents=True, exist_ok=True)
        (self.root / "history").mkdir(parents=True, exist_ok=True)

    def _read(self, p: Path, default):
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
        except ValueError:
            return default

    def _write(self, p: Path, data) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        tmp.replace(p)

    def layouts(self) -> dict:
        return self._read(self.root / "layouts.json", {})

    def set_layout(self, process: str, entry: dict) -> None:
        data = self.layouts()
        data[process] = entry
        self._write(self.root / "layouts.json", data)
        (self.root / "history").mkdir(parents=True, exist_ok=True)
        with (self.root / "history" / f"{process}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def history(self, process: str) -> list[dict]:
        p = self.root / "history" / f"{process}.jsonl"
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def drafts(self, user: str) -> dict:
        return self._read(self.root / "drafts" / f"{user}.json", {})

    def save_draft(self, user: str, process: str, draft: dict) -> None:
        data = self.drafts(user)
        data[process] = {**draft, "updated_at": utcnow()}
        self._write(self.root / "drafts" / f"{user}.json", data)

    def discard_draft(self, user: str, process: str) -> None:
        data = self.drafts(user)
        data.pop(process, None)
        self._write(self.root / "drafts" / f"{user}.json", data)


def store(hub) -> WorkflowStore:
    s = getattr(hub, "_workflow_store", None)
    if s is None:
        s = WorkflowStore(Path(hub.root) / "state" / "workflows")
        hub._workflow_store = s
    return s


# ---------------------------------------------------------------- in force
def bundle_in_force(hub) -> tuple[dict | None, dict]:
    meta = hub.releases.current()
    return meta, (hub.releases.bundle(meta["digest"]) if meta else {})


def processes(bundle: dict) -> list[str]:
    return sorted((bundle or {}).get("workflows") or {})


def graph_in_force(hub, process: str, bundle: dict | None = None) -> dict:
    if bundle is None:
        bundle = bundle_in_force(hub)[1]
    if process not in ((bundle or {}).get("workflows") or {}):
        raise KeyError(f"there is no process {process!r} in the release in force")
    return wm.graph_of(bundle, process, hub.kernel.types)


def layout_entry(hub, process: str) -> dict | None:
    return store(hub).layouts().get(process)


def layout_in_force(hub, process: str, graph: dict) -> dict:
    """The released layout fitted to the graph (a status it has no place for gets tidy's or a free one), else tidy's."""
    entry = layout_entry(hub, process)
    return wm.complete_layout(graph, (entry or {}).get("layout"))


def items_by_status(hub, graph: dict) -> dict[str, int]:
    """How many items of the process's record type are in each status now (counts only: no item is shown)."""
    rt = graph.get("record_type")
    out: dict[str, int] = {}
    if not rt or rt not in hub.kernel.types:
        return out
    for r in hub.kernel.query(type_=rt):
        out[r["state"]] = out.get(r["state"], 0) + 1
    return out


def may_edit(bundle: dict, user: str) -> bool:
    from . import authz

    return bool(set(EDIT_ROLES) & set(authz.roles_of(bundle or {}, user)))


# ---------------------------------------------------------------- drafts
def _base(hub, process: str) -> dict:
    meta, bundle = bundle_in_force(hub)
    g = graph_in_force(hub, process, bundle)
    entry = layout_entry(hub, process) or {}
    return {"release": (meta or {}).get("digest"), "graph": g, "layout": layout_in_force(hub, process, g),
            "layout_version": int(entry.get("version") or 0)}


def _replay(base: dict, log: list[dict]) -> dict:
    doc = {"graph": copy.deepcopy(base["graph"]), "layout": copy.deepcopy(base["layout"]), "state_map": {}}
    for op in log:
        doc = wm.apply_op(doc, op)
    return doc


def draft_of(hub, user: str, process: str) -> dict | None:
    return store(hub).drafts(user).get(process)


def working(hub, user: str, process: str) -> tuple[dict, dict, dict | None]:
    """(the document the person is working on, the base it started from, their draft or None). Without a draft the
    document is the workflow in force."""
    d = draft_of(hub, user, process)
    if d:
        return copy.deepcopy(d["doc"]), d["base"], d
    base = _base(hub, process)
    return {"graph": copy.deepcopy(base["graph"]), "layout": copy.deepcopy(base["layout"]), "state_map": {}}, base, None


def stale(hub, draft: dict | None, process: str) -> bool:
    """The draft was started from a graph or layout that is no longer in force (rebasing is for later: start again)."""
    if not draft:
        return False
    base = draft["base"]
    try:
        now = graph_in_force(hub, process)
    except KeyError:
        return True
    return wm.digest(now) != wm.digest(base["graph"]) or int((layout_entry(hub, process) or {}).get("version") or 0) != int(base.get("layout_version") or 0)


def edit(hub, user: str, process: str, op: dict, *, coalesce: bool = False) -> dict:
    """Apply one operation to the person's draft (started from the release in force when they have none) and save it.
    ``undo`` and ``redo`` move along the log. ``coalesce``: a move of the same status as the last operation replaces it
    (nudging with the arrow keys is one step to undo). Returns {"doc", "op", "said", "can_undo", "can_redo"}; raises
    ValueError in words."""
    s = store(hub)
    d = draft_of(hub, user, process)
    kind = op.get("op")
    if kind in ("undo", "redo"):
        if not d:
            raise ValueError("there is nothing to " + kind)
        cur = int(d.get("cursor", len(d["log"])))
        if kind == "undo":
            if cur == 0:
                raise ValueError("there is nothing to undo")
            said = "Undid: " + d["log"][cur - 1].get("said", "the last change")
            cur -= 1
        else:
            if cur >= len(d["log"]):
                raise ValueError("there is nothing to redo")
            said = "Redid: " + d["log"][cur].get("said", "the change")
            cur += 1
        d["cursor"] = cur
        d["doc"] = _replay(d["base"], [_strip(o) for o in d["log"][:cur]])
        s.save_draft(user, process, d)
        return {"doc": d["doc"], "op": op, "said": said, "can_undo": cur > 0, "can_redo": cur < len(d["log"])}
    if not d:
        base = _base(hub, process)
        d = {"process": process, "by": user, "base": base, "log": [], "cursor": 0,
             "doc": {"graph": copy.deepcopy(base["graph"]), "layout": copy.deepcopy(base["layout"]), "state_map": {}}}
    doc = d["doc"]
    prepared = wm.prepare_op(doc, op)
    said = wm.op_words(doc, prepared)
    new = wm.apply_op(doc, prepared)
    cur = int(d.get("cursor", len(d["log"])))
    log = d["log"][:cur]
    entry = {**prepared, "said": said, "logged_at": utcnow()}
    if coalesce and kind == "move" and log and log[-1].get("op") == "move" and log[-1].get("id") == prepared.get("id"):
        log[-1] = entry
    elif coalesce and kind == "move_label" and log and log[-1].get("op") == "move_label" and log[-1].get("id") == prepared.get("id"):
        log[-1] = entry
    else:
        log.append(entry)
    if len(log) > UNDO_KEEP:  # fold the oldest operations into the base
        fold = log[: len(log) - UNDO_KEEP]
        d["base"] = {**d["base"], **{k: v for k, v in _replay(d["base"], [_strip(o) for o in fold]).items() if k in ("graph", "layout")}}
        log = log[len(fold):]
    d["log"], d["cursor"], d["doc"] = log, len(log), new
    s.save_draft(user, process, d)
    return {"doc": new, "op": prepared, "said": said, "can_undo": True, "can_redo": False}


def _strip(op: dict) -> dict:
    return {k: v for k, v in op.items() if k not in ("said", "logged_at")}


def discard(hub, user: str, process: str) -> None:
    store(hub).discard_draft(user, process)


def can_undo_redo(draft: dict | None) -> tuple[bool, bool]:
    if not draft:
        return False, False
    cur = int(draft.get("cursor", len(draft["log"])))
    return cur > 0, cur < len(draft["log"])


def analysis(hub, process: str, doc: dict, base: dict, *, by: str | None = None, bundle: dict | None = None) -> dict:
    """The checks (section 10.3) and the change in words (10.4) of a document against the workflow it was based on."""
    if bundle is None:
        bundle = bundle_in_force(hub)[1]
    items = items_by_status(hub, base["graph"])
    checks = wm.checks(doc["graph"], bundle=bundle, old=base["graph"], items=items, state_map=doc.get("state_map"))
    delta = wm.diff(base["graph"], doc["graph"], old_layout=base["layout"], new_layout=doc["layout"], state_map=doc.get("state_map"),
                    items=items, by=by, at=None)
    return {"checks": checks, "delta": delta, "items": items}


# ---------------------------------------------------------------- change orders
def changes_of(co: dict) -> dict:
    return dict(co["fields"].get("workflow_changes") or {})


def _may_carry(hub, co: dict, by: str) -> str:
    from . import authz

    _, bundle = bundle_in_force(hub)
    if not may_edit(bundle, by):
        return "only document controllers and policy owners change workflows"
    if by in (co["fields"].get("requested_by"), co.get("created_by")) or "document-controller" in authz.roles_of(bundle, by):
        return ""
    return (f"{co['number']} is {co['fields'].get('requested_by') or co.get('created_by')}'s change order: they or a document "
            "controller change what it carries")


def step_cogs(proc: dict) -> dict[str, str]:
    """{step id: cog id} of a process in force: the actions of its graph are step ids, the prose names cogs."""
    if proc.get("statuses"):
        from .process_graph import action_steps

        return {step: cog for step, cog, _ in action_steps(proc)}
    # A step naming several cogs is a swarm: the graph prose states it as "the <cog> with the <cog>".
    return {s["id"]: ({"swarm": list(s["swarm"])} if s.get("swarm") else s["cog"]) for s in proc.get("steps") or [] if s.get("cog")}


def process_rule(bundle: dict, process: str, graph: dict) -> dict:
    """The process rule (the graph form of the SOP's process section) that states ``graph``: what a direct change of a
    workflow is (``changecontrol.add_direct_change``). The hub's own status and loop (``failed``, Retry) are left out (the
    compile puts them back); actions name cogs, as the prose does; the no-op carriers of two checkpoints in a row go."""
    old = dict((bundle.get("processes") or {}).get(process) or {})
    cogs = step_cogs(old)
    statuses = [{k: v for k, v in st.items() if k != "system"} for st in graph.get("statuses") or [] if not wm.is_system(st)]
    transitions = []
    for t in graph.get("transitions") or []:
        if wm.is_system(t):
            continue
        tt = {k: v for k, v in t.items() if k not in ("system", "line", "source")}
        tt["actions"] = [a if isinstance(a, dict) else cogs.get(a, a) for a in t.get("actions") or []
                         if isinstance(a, dict) or not str(a).startswith("reach-")]
        transitions.append(tt)
    done = [st["id"] for st in statuses if st.get("category") == "done" and (st.get("outcome") or "completed") == "completed"]
    rule = {"kind": "process", "name": process, "folder": old.get("folder"), "object": old.get("object"),
            "record_type": graph.get("record_type") or old.get("record_type"), "steps": [], "terminal": done[0] if done else None,
            "reject_to": None, "statuses": statuses, "transitions": transitions}
    if old.get("files_by_type"):
        rule["files_by_type"] = dict(old["files_by_type"])
    if old.get("links_produced"):  # traceability the editor does not show: a workflow change keeps it
        rule["links_produced"] = True
    return rule


def prose_of(bundle: dict, process: str, graph: dict) -> list[str]:
    """The graph as the sentences of the SOP's process section (``process_graph.to_sentences``): they compile back to the
    same graph, so this is what the review round writes for a carried workflow change."""
    from .process_graph import to_sentences

    return to_sentences(process_rule(bundle, process, graph))


def _rule_in_force(hub, process: str) -> dict | None:
    return next((r for r in hub.settings_rules() if r.get("kind") == "process" and r.get("name") == process), None)


def carry(hub, co_ref: str, user: str, process: str) -> dict:
    """Put the person's draft of a process's workflow on a change order (carried again after a later edit replaces it).

    What changes how items move becomes **one direct change** of the process rule on the change order
    (``changecontrol.add_direct_change``: the review round drafts its prose into the SOP and checks it reads back to the
    same graph); the answers given at delete time go to the change order's state map (``changecontrol.set_state_map``);
    the layout and a snapshot for the visual diff go in ``workflow_changes[<process>]``. Refused while the draft has
    errors, is based on a workflow no longer in force, or the change order is past preparing."""
    from . import changecontrol, eco

    co = eco._co(hub, co_ref)
    if eco.finished(co) or co["state"].startswith("awaiting"):
        raise ValueError(f"{co['number']} is {co['state'].replace('-', ' ')}: change what it carries while it is being prepared")
    why = _may_carry(hub, co, user)
    if why:
        raise ValueError(why)
    d = draft_of(hub, user, process)
    if not d:
        raise ValueError(f"you have no draft of the {process} workflow")
    if stale(hub, d, process):
        raise ValueError(f"the {process} workflow in force changed since your draft was started: discard it and start again")
    doc, base = d["doc"], d["base"]
    _, bundle = bundle_in_force(hub)
    an = analysis(hub, process, doc, base, by=user, bundle=bundle)
    errors = [c["text"] for c in an["checks"] if c["level"] == "error"]
    if errors:
        raise ValueError("the draft has problems: " + "; ".join(errors[:3]))
    if not an["delta"]:
        raise ValueError("the draft changes nothing")
    at = utcnow()
    for item in an["delta"]:
        item["origin"].update({"by": user, "at": at})
    layout_only = wm.layout_only(an["delta"])
    old = changes_of(co).get(process) or {}
    if old.get("direct_id"):  # carried before: the new snapshot replaces the old one
        try:
            changecontrol.remove_direct_change(hub, co["locator"], old["direct_id"], user)
        except ValueError:
            pass
    direct_id = None
    if not layout_only:
        was = _rule_in_force(hub, process)
        rule = process_rule(bundle, process, doc["graph"])
        items = changecontrol.add_direct_change(hub, co["locator"], {"op": "change" if was else "add", "rule": rule, **({"was": was} if was else {})},
                                                user, origin={"editor": "workflow", "process": process})
        direct_id = next((i["id"] for i in reversed(items) if (i.get("origin") or {}).get("process") == process), None)
        # A rename keeps the id: the item carries {id the new names give: id kept} (process_graph.id_map), which the
        # drafter writes beside the prose (the revision's workflow_ids), so the compile reads the same ids back.
        from .process_graph import id_map

        ids = id_map(rule)
        if direct_id and ids:
            fresh = changecontrol.sync(hub, co["locator"])
            its = [dict(i, workflow_ids=ids) if i.get("id") == direct_id else i for i in changecontrol._items(fresh)]
            hub.kernel.set_fields(co["locator"], {"direct_changes": its}, by=user)
    # The state map says where the items of the statuses the draft removes go: only those (a status deleted and then put
    # back by undo is not removed), and the answers carried before for this process are replaced, not merged.
    removed = {k: v for k, v in (doc.get("state_map") or {}).items() if k not in wm.status_map(doc["graph"]) and k in wm.status_map(base["graph"]) and v}
    cur = eco._co(hub, co["locator"])
    smap = dict(cur["fields"].get("state_map") or {})
    if process in smap:
        smap.pop(process)
        hub.kernel.set_fields(co["locator"], {"state_map": smap}, by=user)
    if removed:
        changecontrol.set_state_map(hub, co["locator"], process, removed, user)
    entry = {"graph": copy.deepcopy(doc["graph"]), "layout": copy.deepcopy(doc["layout"]), "state_map": removed,
             "base": {"release": base.get("release"), "graph_digest": wm.digest(base["graph"]), "layout_version": int(base.get("layout_version") or 0)},
             "items": an["items"], "delta": an["delta"], "layout_only": layout_only, "direct_id": direct_id, "by": user, "at": at,
             "origin": {"kind": "direct", "by": user, "at": at}}
    carried = changes_of(eco._co(hub, co["locator"]))
    carried[process] = entry
    hub.kernel.set_fields(co["locator"], {"workflow_changes": carried}, by=user)
    return entry


def uncarry(hub, co_ref: str, process: str, *, by: str) -> None:
    """Take a process's workflow change off a change order: its direct change, its state map and its layout."""
    from . import changecontrol, eco

    co = eco._co(hub, co_ref)
    if eco.finished(co) or co["state"].startswith("awaiting"):
        raise ValueError(f"{co['number']} is {co['state'].replace('-', ' ')}: change what it carries while it is being prepared")
    carried = changes_of(co)
    if process not in carried:
        raise ValueError(f"{co['number']} carries no change to the {process} workflow")
    c = carried.pop(process)
    if c.get("direct_id"):
        try:
            changecontrol.remove_direct_change(hub, co["locator"], c["direct_id"], by)
        except ValueError:
            pass
    co = eco._co(hub, co["locator"])
    smap = dict(co["fields"].get("state_map") or {})
    smap.pop(process, None)
    hub.kernel.set_fields(co["locator"], {"workflow_changes": carried, "state_map": smap}, by=by)


def _base_changed(hub, process: str, c: dict) -> bool:
    try:
        now = graph_in_force(hub, process)
    except KeyError:
        return True
    return (wm.digest(now) != (c.get("base") or {}).get("graph_digest")
            or int((layout_entry(hub, process) or {}).get("version") or 0) != int((c.get("base") or {}).get("layout_version") or 0))


def carried_reasons(hub, co: dict) -> list[str]:
    """Why the workflow changes a change order carries cannot be released as they stand (empty when they can): a
    workflow or layout released since they were carried, checks with errors (against the release in force), a direct
    change taken off the change order since. (The review round states each change of meaning in the SOP and checks it
    reads back: that is the change-order core's.)"""
    from . import changecontrol

    out = []
    carried = changes_of(co)
    if not carried:
        return out
    _, bundle = bundle_in_force(hub)
    ids = {i.get("id") for i in changecontrol._items(co)}
    for process, c in sorted(carried.items()):
        label = f"{process} workflow"
        if _base_changed(hub, process, c):
            out.append(f"{label}: another change order changed it since this one carried it; carry the draft again")
            continue
        base = graph_in_force(hub, process, bundle)
        problems = wm.checks(c["graph"], bundle=bundle, old=base, items=items_by_status(hub, base), state_map=c.get("state_map"))
        out += [f"{label}: {p['text']}" for p in problems if p["level"] == "error"]
        if c.get("direct_id") and c["direct_id"] not in ids:
            out.append(f"{label}: its change of how items move is no longer on the change order; carry the draft again")
    return out


def delta_items(hub, co: dict) -> list[dict]:
    """Each workflow change the change order carries as section 8 delta items in words (origin direct), keyed by element
    id, marked stale when the workflow or its layout changed since it was carried. The changes of meaning are one direct
    change of the process on the change order (``direct_id``); these words say what that change does, element by element."""
    out = []
    for process, c in sorted(changes_of(co).items()):
        st = _base_changed(hub, process, c)
        for item in c.get("delta") or []:
            out.append({**copy.deepcopy(item), "stale": st, "direct_id": c.get("direct_id")})
    return out


def _mine(hub, co: dict, process: str) -> dict | None:
    """The version of the process's layout this change order brought into force (in force now, or in its history)."""
    from .closure import released_with_co

    s = store(hub)
    return released_with_co(co, [*s.history(process), s.layouts().get(process)])


def later_changes(hub, co: dict) -> list[str]:
    """The workflow layouts this change order carries and has not brought into force whose version in force is no longer
    the one it carried them against (another change order released one since), in words."""
    from .closure import now_in_force

    out = []
    for process, c in sorted(changes_of(co).items()):
        now = layout_entry(hub, process) or {}
        if _mine(hub, co, process) is None and int(now.get("version") or 0) != int((c.get("base") or {}).get("layout_version") or 0):
            out.append(now_in_force(f"the {process} workflow layout", now))
    return out


def activate(hub, co: dict, digest: str) -> list[str]:
    """The release step (with the form layouts): each carried layout comes into force as the next version of that
    process's layout. Safe to repeat: a layout this change order already brought into force (found in the history, as a
    later change order may have released another version since) counts as done only when it was made from what is
    signed (``closure.check_in_force_as_signed``); a layout carried against a version no longer in force is refused, as
    it would undo the later one. Refuses a change order that is not approved (hubproto.closure). The graph itself comes
    into force with the release (its direct change, compiled from the SOP); this says whether the release now has the
    graph that was carried. Returns what it did."""
    carried = changes_of(co)
    if not carried:
        return []
    from .closure import check_co_approved, check_in_force_as_signed, later_change_refusal, part_digest
    from .rules import sha256_of

    s = store(hub)
    mine = {process: _mine(hub, co, process) for process in carried}
    if any(e is None for e in mine.values()):
        check_co_approved(co, "its workflow changes")
    signed_part = co["fields"].get("workflow_changes") or {}
    for process, c in sorted(carried.items()):  # a layout already in force with it counts only when made from what is signed (#53)
        if mine[process] is not None:
            check_in_force_as_signed(co, "workflows", signed_part, mine[process], f"the {process} workflow layout v{mine[process]['version']}",
                                     same_content=sha256_of(mine[process].get("layout")) == sha256_of(c.get("layout")))
    stale = later_changes(hub, co)
    if stale:
        raise ValueError(later_change_refusal(co, stale))
    done = []
    _, bundle = bundle_in_force(hub)
    for process, c in sorted(carried.items()):
        now = s.layouts().get(process) or {}
        if mine[process] is not None:
            later = (f" (released with {co['number']}; v{now.get('version')} is in force now, released with {now.get('released_with')})"
                     if now != mine[process] else "")
            done.append(f"{process} layout v{mine[process]['version']}{later}")
            continue
        entry = {"version": int(now.get("version") or 0) + 1, "layout": c["layout"], "released_with": co["number"], "release": digest,
                 "by": c.get("by"), "at": utcnow(), "changes": [i["text"] for i in c.get("delta") or []], "signed_digest": part_digest(signed_part)}
        s.set_layout(process, entry)
        done.append(f"{process} layout v{entry['version']}")
        if not c.get("layout_only"):
            try:
                live = wm.graph_of(bundle, process, hub.kernel.types)
            except KeyError:
                live = None
            same = live is not None and not [i for i in wm.diff(live, c["graph"]) if not i.get("no_effect")]
            done.append(f"{process} workflow " + ("in force as carried" if same else "as the release states it (compare with what was carried)"))
    return done
