"""Change orders (ECOs): the way a policy or SOP changes once the SOPs are documents.

A change request (CR) asks for a change and says why; it is approved on its own. A change order (CO) carries the
work: a draft revision of every SOP or policy it changes, and the change requests it implements. Each time a carried
draft changes, the hub stages the Frame those drafts compile into (a proposal marked with the change order), so the
change order always shows the rule changes next to the text changes. A compile error, or a change order staged
against a release that is no longer in force, blocks it; a staged Frame that widens anyone's authority also needs both
policy owners on its widening changes. When the change order is approved, it is released: the staged Frame comes into
force and the carried revisions become effective together, each making its predecessor obsolete.
"""

from __future__ import annotations

import hashlib

from . import authz, sopdocs

CARRIES = "carries"
IMPLEMENTS = "implements"
OPEN_STATES = ("open", "draft", "returned", "failed")


def _co(hub, ref: str) -> dict:
    rec = hub.kernel.get(ref) if str(ref).startswith("rec:") else next(
        (r for r in hub.kernel.query(type_="change-order") if r["number"] == ref), None)
    if rec is None or rec["type"] != "change-order":
        raise ValueError(f"{ref} is not a change order")
    return rec


def carried(hub, co: dict) -> list[dict]:
    """The SOP drafts a change order carries: [{"name", "draft", "in_force"}]."""
    out = []
    for link in hub.kernel.links_of(co["locator"]):
        if link["rel"] != CARRIES or link["direction"] != "out":
            continue
        d = hub.kernel.get(link["target"])
        if d is not None and d["state"] == "withdrawn":
            continue
        if d is None:
            continue
        name = d["fields"].get("sop_name")
        out.append({"name": name, "draft": d, "in_force": sopdocs.in_force(hub.kernel, name)})
    return sorted(out, key=lambda x: x["name"] or "")


def drafts_of(hub, co: dict) -> dict[str, str]:
    return {c["name"]: c["draft"]["locator"] for c in carried(hub, co) if c["name"]}


def finished(co: dict) -> bool:
    return co["state"] not in OPEN_STATES and not co["state"].startswith("awaiting")


def may_change_sop(bundle: dict, rec: dict, by: str) -> str:
    """Empty when ``by`` may revise this SOP or policy: its owner or author, a document controller, or (a policy) a
    policy owner."""
    f = rec["fields"]
    roles = authz.roles_of(bundle, by)
    if by in (f.get("owner"), f.get("author"), rec.get("created_by")) or "document-controller" in roles:
        return ""
    if f.get("doc_type") == "policy" and "policy-owner" in roles:
        return ""
    return f"only {f.get('owner') or 'its owner'}, its author, a document controller" + (" or a policy owner" if f.get("doc_type") == "policy" else "") \
        + f" revises {rec['number']}"


def _next_revision(rev: str) -> str:
    rev = (rev or "A").strip()
    if rev.isalpha() and len(rev) == 1:
        return chr(ord(rev.upper()) + 1) if rev.upper() != "Z" else "AA"
    return rev + "-1"


def add_sop(hub, co_ref: str, name: str, *, by: str) -> dict:
    """Start a draft revision of an SOP or policy on this change order (or return the one it already carries)."""
    co = _co(hub, co_ref)
    if finished(co) or (co.get("state") or "").startswith("awaiting-approve"):
        raise ValueError(f"{co['number']} is {co['state']}: add SOPs while it is being prepared, or send it back to the start")
    bundle = hub.releases.bundle(hub.releases.current()["digest"])
    if by not in (co["fields"].get("requested_by"), co.get("created_by")) and "document-controller" not in authz.roles_of(bundle, by):
        raise ValueError(f"{co['number']} is {co['fields'].get('requested_by') or co.get('created_by')}'s change order: they or a document controller add SOPs to it")
    for c in carried(hub, co):
        if c["name"] == name:
            return c["draft"]
    current = sopdocs.in_force(hub.kernel, name)
    if current is None:
        raise ValueError(f"there is no SOP or policy {name!r} in force")
    why = may_change_sop(bundle, current, by)
    if why:
        raise ValueError(why)
    for d in sopdocs.of_lineage(hub.kernel, name):
        if d["state"] in ("draft", "open"):
            other = next((lk["target"] for lk in hub.kernel.links_of(d["locator"]) if lk["rel"] == CARRIES and lk["direction"] == "in"), None)
            o = hub.kernel.get(other) if other else None
            raise ValueError(f"{current['number']} is already being revised ({d['number']}" + (f", on {o['number']})" if o else ")"))
    content, v = hub.dms.content(current["locator"])
    f = current["fields"]
    keep = {k: f.get(k) for k in ("lineage", "sop_name", "frame_meta", "frame_header", "description", "owner", "review_date") if f.get(k) is not None}
    draft = hub.dms.create(title=f.get("title") or name, doc_class="controlled", folder=current["folder"], by=by, content=content,
                           filename=v.get("filename") or f"{name}.docx", mime=v.get("mime") or "application/octet-stream",
                           doc_type=f.get("doc_type") or "sop",
                           fields={**keep, "revision": _next_revision(f.get("revision")), "revises": current["number"], "author": by,
                                   "change_order": co["number"]})
    hub.kernel.link(co["locator"], CARRIES, draft["locator"], by=by)
    stage(hub, co["locator"])
    return hub.kernel.get(draft["locator"])


def remove_sop(hub, co_ref: str, name: str, *, by: str) -> None:
    co = _co(hub, co_ref)
    for c in carried(hub, co):
        if c["name"] == name:
            if c["draft"]["state"] not in ("draft", "open"):
                raise ValueError(f"{c['draft']['number']} is {c['draft']['state']}")
            hub.kernel.transition(c["draft"]["locator"], "withdrawn", by=by, reason=f"taken off {co['number']}")
            hub.kernel.unlink(co["locator"], CARRIES, c["draft"]["locator"], by=by)
            stage(hub, co["locator"])
            return
    raise ValueError(f"{co['number']} does not carry {name}")


def signature(hub, co: dict) -> str:
    """What the staged Frame was compiled from: the release in force and each carried draft's content."""
    meta = hub.releases.current()
    parts = [meta["digest"] if meta else ""] + [f"{c['name']}={c['draft']['fields'].get('content_sha') or ''}" for c in carried(hub, co)]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]


def stage(hub, co_ref: str) -> dict:
    """Compile the Frame this change order's drafts make, as its staged proposal (replacing the one staged before)."""
    from .compile import CompileError, blocking_findings

    co = _co(hub, co_ref)
    drafts = drafts_of(hub, co)
    old = co["fields"].get("staged_proposal")
    fields: dict = {"staged_sig": signature(hub, co), "staged_base": (hub.releases.current() or {}).get("digest"), "staged_errors": []}
    if not drafts:
        fields.update({"staged_proposal": None, "staged_number": None, "staged_changes": 0, "staged_widenings": 0})
    else:
        try:
            p = hub.compile(sop_drafts=drafts, save=True)
            p["eco"] = co["number"]
            p["source"] = "eco"
            hub.proposals.save(p)
            fields.update({"staged_proposal": p["id"], "staged_number": p.get("number"), "staged_changes": len(p["changes"]),
                           "staged_widenings": sum(1 for c in p["changes"] if c.get("widening")),
                           "staged_errors": [f"{f['detail']} ({f['frame']} line {f['line']})" for f in blocking_findings(p)]})
        except CompileError as e:
            fields.update({"staged_proposal": None, "staged_number": None, "staged_errors": [str(e)]})
    if old and old != fields.get("staged_proposal"):
        try:
            prev = hub.proposals.load(old)
            if prev.get("status") == "open":
                prev["status"], prev["superseded_by"] = "superseded", fields.get("staged_proposal")
                hub.proposals.save(prev)
        except Exception:
            pass
    hub.kernel.set_fields(co["locator"], fields, by="hub")
    return hub.kernel.get(co["locator"])["fields"]


def status(hub, co_ref: str) -> dict:
    """Where a change order stands: what it carries, what it implements, the staged Frame and what blocks it."""
    co = _co(hub, co_ref)
    if not finished(co) and co["fields"].get("staged_sig") != signature(hub, co):
        stage(hub, co["locator"])  # a carried draft changed, or another release came into force: stage again
        co = hub.kernel.get(co["locator"])
    f = co["fields"]
    reasons = list(f.get("staged_errors") or [])
    proposal = None
    if f.get("staged_proposal"):
        from .compile import change_state

        proposal = hub.proposals.load(f["staged_proposal"])
        for c in proposal["changes"]:
            # A wording change is the carried revision itself, approved through document control: the change order
            # covers it. A rule that widens anyone's authority also needs both policy owners.
            if c.get("widening") and c.get("op") != "text" and change_state(c) != "approved":
                have = sorted({d["by"] for d in c["decisions"] if d["verdict"] == "approve"})
                reasons.append(f"widening: {c['reason']} needs both policy owners" + (f" (approved by {', '.join(have)})" if have else ""))
            if change_state(c) == "rejected":
                reasons.append(f"rejected by a policy owner: {c['reason']}")
    implements = []
    for n in f.get("implements") or []:
        cr = next((r for r in hub.kernel.query(type_="change-request") if r["number"] == n), None)
        implements.append(cr)
        if cr is None:
            reasons.append(f"there is no change request {n}")
        elif cr["state"] in ("open", "draft", "returned", "failed") or cr["state"].startswith("awaiting-approve"):
            reasons.append(f"{n} is not approved yet ({cr['state']})")
    return {"carried": carried(hub, co), "implements": [x for x in implements if x], "proposal": proposal, "ready": not reasons,
            "reasons": reasons, "co": co}


def check_approval(hub, co_ref: str) -> list[str]:
    """Why the change order may not be approved yet (empty when it may): it carries something, every revision it
    carries is approved (held for it), and its staged Frame compiles with its widenings approved by both owners."""
    st = status(hub, co_ref)
    if not st["carried"]:
        return ["it carries no SOP or policy revision"]
    held = [f"{c['draft']['number']} ({c['name']}) is {c['draft']['state']}; it must be approved first"
            for c in st["carried"] if not (c["draft"]["state"] == "approved" and c["draft"]["fields"].get("held_for") == st["co"]["number"])]
    return held + st["reasons"]


def release(hub, co_ref: str, *, by: str) -> dict:
    """The change order is released: its staged Frame comes into force and its revisions become effective together."""
    from .compile import change_state

    st = status(hub, co_ref)
    blocked = check_approval(hub, co_ref)
    if blocked:
        raise ValueError("the change order cannot be released: " + "; ".join(blocked))
    co, proposal = st["co"], st["proposal"]
    for c in proposal["changes"]:
        if (not c.get("widening") or c.get("op") == "text") and change_state(c) == "pending":  # covered by the change order's approval
            for who in ([f"change-order:{co['number']}"] + ([f"document-control:{co['number']}"] if c.get("widening") else [])):
                c["decisions"].append({"by": who, "verdict": "approve", "at": __import__("hubproto.release", fromlist=["utcnow"]).utcnow(),
                                       "meaning": f"approved with {co['number']} (the revision was approved through document control)", "change_digest": ""})
    hub.proposals.save(proposal)
    meta = hub.release(proposal["id"], f"change-order:{co['number']}")
    texts = hub.releases.frame_texts(meta["digest"]) if hasattr(hub.releases, "frame_texts") else {}
    bundle = hub.releases.bundle(meta["digest"])
    from .cogs import assign_training

    for c in st["carried"]:
        d = hub.kernel.get(c["draft"]["locator"])
        f = d["fields"]
        text = texts.get(c["name"]) or ""
        header = text.split("\n---", 1)[0] + "\n---" if text.startswith("---") else f.get("frame_header")
        hub.kernel.set_fields(d["locator"], {"frame_header": header, "released_in": meta["digest"], "held_for": None}, by=by)
        grant = {"id": f.get("approved_grant"), "run_id": f.get("approved_run")}
        hub.kernel.publish_document(d["number"], run_id=f.get("approved_run") or "", release=meta["digest"], grant=grant,
                                    approved_by=f.get("approved_by") or "", by=by)
        hub.dms.release_revision(d["locator"], by=by, release=meta["digest"])
        hub.kernel.transition(d["locator"], "effective", by=by, release=meta["digest"], reason=f"released with {co['number']}")
        try:
            assign_training(hub.runner, bundle, hub.kernel, hub.kernel.get(d["locator"]), f.get("training_roles") or [],
                            owner=f.get("approved_by") or by, parent_run=f.get("approved_run") or "", by=by, release=meta["digest"])
        except Exception as e:  # the release stands; the training is assigned by hand
            hub.inbox.notify(co["fields"].get("requested_by") or by, kind="failure", subject=f"Training for {d['number']} was not assigned",
                             body=str(e), record=d["locator"])
    hub.kernel.set_fields(co["locator"], {"released_release": meta["digest"]}, by=by)
    return meta
