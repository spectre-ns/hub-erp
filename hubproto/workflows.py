"""Workflow graphs in the hub: the graph and layout in force, editor drafts with an operation log, and carrying a draft on
a change order (change control v2, sections 10.4 to 10.6). The graph itself is pure data (hubproto.workflow_model).

* **In force.** A process's graph comes from the release in force (``workflow_model.graph_of``). Its layout (where each
  status sits; presentation, not policy) is a versioned configuration object in ``state/workflows/layouts.json``, carried
  on a change order like a form layout; until one is released it is ``tidy``'s.
* **Drafts.** One per person and process (``state/workflows/drafts/<person>.json``), based on the release in force when
  it was started: the base graph and layout, the operation log, a cursor (undo and redo move it) and the document the
  log makes (``{"graph", "layout", "state_map"}``). Every edit is saved at once.
* **Change orders.** ``carry`` puts a snapshot of a draft on a change order in its ``workflow_changes`` field, and its
  state map in the change order's ``state_map`` field (section 8)::

      workflow_changes: {<process>: {"graph": <10.1 graph>, "layout": {...}, "state_map": {old: new},
                                     "base": {"release", "graph_digest", "layout_version"},
                                     "items": {<status id>: count when carried}, "delta": [<delta item>],
                                     "layout_only": bool, "by", "at", "origin": {"kind": "direct", "by", "at"}}}
      state_map: {<process>: {<old status id>: <new status id>}}

  The change-order core (workstream 1) calls, as it calls ``hubproto.formlayout`` and ``hubproto.recordtypes``:
  ``carried_reasons(hub, co)`` (why it cannot be released), ``delta_items(hub, co)`` (each change as a section 8 delta
  item, origin ``direct``) and ``activate(hub, co, digest)`` (release step: the layouts come into force; only for an
  approved change order). A change of meaning comes into force through the release that states it: the prose of the SOP
  that owns the process, drafted from the direct items and compiled into the graph (workstreams 2 and 7).
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
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        tmp.replace(p)

    def layouts(self) -> dict:
        return self._read(self.root / "layouts.json", {})

    def set_layout(self, process: str, entry: dict) -> None:
        data = self.layouts()
        data[process] = entry
        self._write(self.root / "layouts.json", data)
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


def carry(hub, co_ref: str, user: str, process: str) -> dict:
    """Put a snapshot of the person's draft of a process's workflow on a change order (carried again after a later
    edit). Refused while the draft has errors, is based on a workflow no longer in force, or the change order is past
    preparing."""
    from . import eco

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
    an = analysis(hub, process, doc, base, by=user)
    errors = [c["text"] for c in an["checks"] if c["level"] == "error"]
    if errors:
        raise ValueError("the draft has problems: " + "; ".join(errors[:3]))
    if not an["delta"]:
        raise ValueError("the draft changes nothing")
    at = utcnow()
    for item in an["delta"]:
        item["origin"].update({"by": user, "at": at})
    entry = {"graph": copy.deepcopy(doc["graph"]), "layout": copy.deepcopy(doc["layout"]), "state_map": dict(doc.get("state_map") or {}),
             "base": {"release": base.get("release"), "graph_digest": wm.digest(base["graph"]), "layout_version": int(base.get("layout_version") or 0)},
             "items": an["items"], "delta": an["delta"], "layout_only": wm.layout_only(an["delta"]), "by": user, "at": at,
             "origin": {"kind": "direct", "by": user, "at": at}}
    carried = changes_of(co)
    carried[process] = entry
    smap = dict(co["fields"].get("state_map") or {})
    removed = {k: v for k, v in entry["state_map"].items() if k not in wm.status_map(entry["graph"])}
    if removed:
        smap[process] = removed
    else:
        smap.pop(process, None)
    hub.kernel.set_fields(co["locator"], {"workflow_changes": carried, "state_map": smap}, by=user)
    return entry


def uncarry(hub, co_ref: str, process: str, *, by: str) -> None:
    from . import eco

    co = eco._co(hub, co_ref)
    if eco.finished(co) or co["state"].startswith("awaiting"):
        raise ValueError(f"{co['number']} is {co['state'].replace('-', ' ')}: change what it carries while it is being prepared")
    why = _may_carry(hub, co, by)
    if why and by not in (co["fields"].get("requested_by"), co.get("created_by")):
        raise ValueError(why)
    carried = changes_of(co)
    if process not in carried:
        raise ValueError(f"{co['number']} carries no change to the {process} workflow")
    carried.pop(process)
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
    """Why the workflow changes a change order carries cannot be released as they stand (empty when they can): checks
    with errors, a workflow or layout released since they were carried, items in a removed status with nowhere to go,
    and a change of meaning that no carried revision of the SOP owning the process can state."""
    from . import eco

    out = []
    carried = changes_of(co)
    if not carried:
        return out
    _, bundle = bundle_in_force(hub)
    sops = {c["name"] for c in eco.carried(hub, co)}
    for process, c in sorted(carried.items()):
        label = f"{process} workflow"
        if _base_changed(hub, process, c):
            out.append(f"{label}: another change order changed it since this one carried it; carry the draft again")
            continue
        base = graph_in_force(hub, process, bundle)
        problems = wm.checks(c["graph"], bundle=bundle, old=base, items=items_by_status(hub, base), state_map=c.get("state_map"))
        out += [f"{label}: {p['text']}" for p in problems if p["level"] == "error"]
        frame = (c["graph"].get("source") or {}).get("frame") or (base.get("source") or {}).get("frame")
        if not c.get("layout_only") and frame and frame not in sops:
            n = len([i for i in c.get("delta") or [] if not i.get("no_effect")])
            out.append(f"{label}: {n} change(s) of how items move come into force only through {frame}, the SOP that states the process: "
                       f"carry its revision so the change is written there")
    return out


def delta_items(hub, co: dict) -> list[dict]:
    """Each workflow change the change order carries as a section 8 delta item (origin direct), marked stale when the
    workflow or its layout changed since it was carried."""
    out = []
    for process, c in sorted(changes_of(co).items()):
        st = _base_changed(hub, process, c)
        for item in c.get("delta") or []:
            out.append({**copy.deepcopy(item), "stale": st})
    return out


def state_maps(co: dict) -> dict:
    """{process: {old status: new status}} the change order carries (the release moves the items, workstream 7)."""
    return {p: dict(m) for p, m in (co["fields"].get("state_map") or {}).items()}


def activate(hub, co: dict, digest: str) -> list[str]:
    """The release step: each carried layout comes into force as the next version of that process's layout. Safe to
    repeat; refuses a change order that is not approved (hubproto.closure). A carried change of meaning is not applied
    here: the release states it (workstream 7 compiles it from the SOP); this says whether the release in force now has
    the graph that was carried. Returns what it did."""
    carried = changes_of(co)
    if not carried:
        return []
    s = store(hub)
    if any((s.layouts().get(p) or {}).get("released_with") != co["number"] for p in carried):
        from .closure import check_co_approved

        check_co_approved(co, "its workflow changes")
    done = []
    _, bundle = bundle_in_force(hub)
    for process, c in sorted(carried.items()):
        now = s.layouts().get(process) or {}
        if now.get("released_with") == co["number"]:
            done.append(f"{process} layout v{now['version']}")
            continue
        entry = {"version": int(now.get("version") or 0) + 1, "layout": c["layout"], "released_with": co["number"], "release": digest,
                 "by": c.get("by"), "at": utcnow(), "changes": [i["text"] for i in c.get("delta") or []]}
        s.set_layout(process, entry)
        done.append(f"{process} layout v{entry['version']}")
        if not c.get("layout_only"):
            try:
                live = wm.graph_of(bundle, process, hub.kernel.types)
            except KeyError:
                live = None
            same = live is not None and wm.digest(live) == wm.digest(c["graph"])
            done.append(f"{process} workflow " + ("in force as carried" if same else "as the release states it (compare with what was carried)"))
    return done
