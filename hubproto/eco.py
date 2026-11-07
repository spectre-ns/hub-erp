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
    if "policy-owner" in roles:  # the policy owners own the rules every SOP states
        return ""
    return f"only {f.get('owner') or 'its owner'}, its author, a document controller or a policy owner revises {rec['number']}"


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
    return hub.kernel.get(draft["locator"])


def remove_sop(hub, co_ref: str, name: str, *, by: str) -> None:
    co = _co(hub, co_ref)
    for c in carried(hub, co):
        if c["name"] == name:
            if c["draft"]["state"] not in ("draft", "open"):
                raise ValueError(f"{c['draft']['number']} is {c['draft']['state']}")
            hub.kernel.transition(c["draft"]["locator"], "withdrawn", by=by, reason=f"taken off {co['number']}")
            hub.kernel.unlink(co["locator"], CARRIES, c["draft"]["locator"], by=by)
            return
    raise ValueError(f"{co['number']} does not carry {name}")


def approved_of(hub, co: dict) -> dict[str, dict]:
    """The carried revisions approved through document control and held for this change order: {SOP name: draft}."""
    return {c["name"]: c["draft"] for c in carried(hub, co)
            if c["draft"]["state"] == "approved" and c["draft"]["fields"].get("held_for") == co["number"]}


def compiled_from(hub, co: dict) -> dict[str, str]:
    """What a compile of this change order now would be made of: the approved revisions and their content."""
    return {n: str(d["fields"].get("content_sha") or "") for n, d in sorted(approved_of(hub, co).items())}


def stage(hub, co_ref: str, drafts: dict[str, str] | None = None) -> dict:
    """Compile the Frame the change order's approved revisions make (or the given ``drafts``: {SOP name: locator}),
    against the release in force, and stage it on the change order as its proposal (replacing the one staged before).
    The compile-frame automation does this when a revision is approved."""
    from .compile import CompileError, blocking_findings

    co = _co(hub, co_ref)
    if drafts is None:
        drafts = {n: d["locator"] for n, d in approved_of(hub, co).items()}
    old = co["fields"].get("staged_proposal")
    shas = {n: str((hub.kernel.get(loc) or {}).get("fields", {}).get("content_sha") or "") for n, loc in sorted(drafts.items())}
    fields: dict = {"staged_from": shas, "staged_base": (hub.releases.current() or {}).get("digest"), "staged_errors": [],
                    "staged_at": __import__("hubproto.release", fromlist=["utcnow"]).utcnow()}
    if not drafts:
        fields.update({"staged_proposal": None, "staged_number": None, "staged_changes": 0, "staged_widenings": 0})
    else:
        try:
            p = hub.compile(sop_drafts=drafts, save=True)
            p["eco"] = co["number"]
            p["source"] = "eco"
            hub.proposals.save(p)
            fields.update({"staged_proposal": p["id"], "staged_number": p.get("number"), "staged_changes": len(p["changes"]),
                           "staged_widenings": sum(1 for c in p["changes"] if c.get("widening") and c.get("op") != "text"),
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


def preview(hub, co_ref: str) -> dict | None:
    """What the Frame would become with every carried draft as it stands now (approved or not): compiled but not
    saved or staged, for the people writing the revisions. The compiled Frame that counts is the one the compile-frame
    automation stages from the approved revisions. Returns a proposal-shaped dict, or None when nothing is carried."""
    from .compile import CompileError, blocking_findings

    co = _co(hub, co_ref)
    drafts = drafts_of(hub, co)
    if not drafts:
        return None
    try:
        p = hub.compile(sop_drafts=drafts, save=False)
    except CompileError as e:
        return {"changes": [], "errors": [str(e)], "preview": True}
    p["errors"] = [f"{f['detail']} ({f['frame']} line {f['line']})" for f in blocking_findings(p)]
    p["preview"] = True
    return p


def frame_reasons(hub, co: dict) -> list[str]:
    """Why the compiled Frame is not ready (empty when it is): every carried revision approved, compiled from exactly
    those approved revisions against the release in force, with no problem that stops it."""
    reasons = []
    carried_now = carried(hub, co)
    if not carried_now:
        return ["it carries no SOP or policy revision"]
    for c in carried_now:
        d = c["draft"]
        if not (d["state"] == "approved" and d["fields"].get("held_for") == co["number"]):
            reasons.append(f"{d['number']} ({c['name']}) is {d['state'].replace('-', ' ')}: it must be approved through document control first")
    f = co["fields"]
    running = [j for j in (hub.automation.of(co["locator"]) if getattr(hub, "automation", None) else [])
               if j["automation"] == "compile-frame" and j["status"] in ("queued", "running")]
    if not reasons:
        if running:
            reasons.append("the Frame is being compiled (see the automation job)")
        elif not f.get("staged_proposal") and not f.get("staged_errors"):
            reasons.append("the Frame has not been compiled yet")
        elif (f.get("staged_from") or {}) != compiled_from(hub, co):
            reasons.append("the compiled Frame is not made of the approved revisions: compile it again")
        elif f.get("staged_base") != (hub.releases.current() or {}).get("digest"):
            reasons.append("the Frame was compiled against a release no longer in force: compile it again")
    reasons += list(f.get("staged_errors") or [])
    return reasons


def status(hub, co_ref: str) -> dict:
    """Where a change order stands: what it carries, what it implements, the compiled Frame and what blocks it."""
    co = _co(hub, co_ref)
    f = co["fields"]
    reasons = frame_reasons(hub, co) if not finished(co) else []
    proposal = None
    if f.get("staged_proposal"):
        from .compile import change_state

        proposal = hub.proposals.load(f["staged_proposal"])
        if not finished(co):
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
        if finished(co):
            continue
        if cr is None:
            reasons.append(f"there is no change request {n}")
        elif cr["state"] in ("open", "draft", "returned", "failed") or cr["state"].startswith("awaiting-approve"):
            reasons.append(f"{n} is not approved yet ({cr['state']})")
    return {"carried": carried(hub, co), "implements": [x for x in implements if x], "proposal": proposal, "ready": not reasons,
            "reasons": reasons, "co": co}


def check_approval(hub, co_ref: str) -> list[str]:
    """Why the change order may not be approved yet (empty when it may): every revision it carries is approved, the
    Frame compiled from them is ready, its widenings are signed by both policy owners and what it implements is approved."""
    return status(hub, co_ref)["reasons"]


def _utc():
    return __import__("hubproto.release", fromlist=["utcnow"]).utcnow()


def activate_frame(hub, co_ref: str) -> dict:
    """The compiled Frame comes into force (step 2 of the release automation). Its wording changes and narrowing
    changes are covered by the change order's approval; widenings carry both policy owners' signatures already."""
    from .compile import change_state

    co = _co(hub, co_ref)
    if co["fields"].get("released_release"):
        return hub.releases.get(co["fields"]["released_release"]) or {"digest": co["fields"]["released_release"]}
    proposal = hub.proposals.load(co["fields"]["staged_proposal"])
    for c in proposal["changes"]:
        if (not c.get("widening") or c.get("op") == "text") and change_state(c) == "pending":
            for who in [f"change-order:{co['number']}"] + ([f"document-control:{co['number']}"] if c.get("widening") else []):
                c["decisions"].append({"by": who, "verdict": "approve", "at": _utc(), "change_digest": "",
                                       "meaning": f"approved with {co['number']} (the revision was approved through document control)"})
    hub.proposals.save(proposal)
    meta = hub.release(proposal["id"], f"change-order:{co['number']}")
    hub.kernel.set_fields(co["locator"], {"released_release": meta["digest"]}, by="hub")
    return meta


def publish_revisions(hub, co_ref: str, digest: str) -> list[str]:
    """Every carried revision becomes effective, replacing the one it revises (step 3). Safe to repeat."""
    co = _co(hub, co_ref)
    texts = hub.releases.frame_texts(digest) if hasattr(hub.releases, "frame_texts") else {}
    done = []
    for c in carried(hub, co):
        d = hub.kernel.get(c["draft"]["locator"])
        if d["state"] == "effective":
            done.append(d["number"])
            continue
        f = d["fields"]
        text = texts.get(c["name"]) or ""
        header = text.split("\n---", 1)[0] + "\n---" if text.startswith("---") else f.get("frame_header")
        hub.kernel.set_fields(d["locator"], {"frame_header": header, "released_in": digest, "held_for": None}, by="hub")
        grant = {"id": f.get("approved_grant"), "run_id": f.get("approved_run")}
        hub.kernel.publish_document(d["number"], run_id=f.get("approved_run") or "", release=digest, grant=grant,
                                    approved_by=f.get("approved_by") or "", by="hub")
        hub.dms.release_revision(d["locator"], by="hub", release=digest)
        hub.kernel.transition(d["locator"], "effective", by="hub", release=digest, reason=f"released with {co['number']}")
        done.append(d["number"])
    return done


def assign_revision_training(hub, co_ref: str, digest: str) -> tuple[int, list[str]]:
    """Training for each new revision (step 4): (tasks assigned, problems)."""
    from .cogs import assign_training

    co = _co(hub, co_ref)
    bundle = hub.releases.bundle(digest)
    n, problems = 0, []
    for c in carried(hub, co):
        d = hub.kernel.get(c["draft"]["locator"])
        f = d["fields"]
        if f.get("training_assigned_for") == digest:
            continue
        try:
            got = assign_training(hub.runner, bundle, hub.kernel, d, f.get("training_roles") or [], owner=f.get("approved_by") or "hub",
                                  parent_run=f.get("approved_run") or "", by="hub", release=digest)
            n += len(got)
            hub.kernel.set_fields(d["locator"], {"training_assigned_for": digest}, by="hub")
        except Exception as e:
            problems.append(f"{d['number']}: {e}")
    return n, problems


def release(hub, co_ref: str, *, by: str) -> dict:
    """Release an approved change order at once (the release automation does the same, step by step)."""
    blocked = check_approval(hub, co_ref)
    if blocked:
        raise ValueError("the change order cannot be released: " + "; ".join(blocked))
    meta = activate_frame(hub, co_ref)
    publish_revisions(hub, co_ref, meta["digest"])
    assign_revision_training(hub, co_ref, meta["digest"])
    co = _co(hub, co_ref)
    if co["state"] != "released":
        hub.kernel.transition(co["locator"], "released", by=by, reason="released: its Frame and revisions are in force")
    return meta
