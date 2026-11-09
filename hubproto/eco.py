"""Change orders (ECOs): the way a policy or SOP changes once the SOPs are documents.

A change request (CR) asks for a change and says why; it is approved on its own. A change order (CO) carries the
work: a draft revision of every SOP or policy it changes, and the change requests it implements. This module keeps
what a change order carries (its drafts), stages the Frame they compile into (a proposal marked with the change
order), and releases it: the staged Frame comes into force and the carried revisions become effective together, each
making its predecessor obsolete.

The loop around it (change control v2: review rounds, the delta and its decisions, approval readiness, the release
steps) is hubproto.changecontrol: the Frame is compiled in a change order's review rounds from the drafts as they
stand, no longer when document control approves each revision, and the change order's approval approves the
revisions it carries. ``frame_reasons`` and ``check_approval`` here answer with its readiness checks.
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


def drafts_of(hub, co: dict) -> dict[str, str | None]:
    """{SOP name: draft locator} of what the change order carries; a retirement it carries is the name with None (the
    Frame compiles as absent)."""
    out = {c["name"]: c["draft"]["locator"] for c in carried(hub, co) if c["name"]}
    for name in retirements(co):
        out[name] = None
    return out


def retirements(co: dict) -> dict[str, dict]:
    """{SOP name: {"document", "locator", "by", "reason", "at"}}: the documents this change order retires."""
    return dict(co["fields"].get("retires") or {})


def _may_carry(bundle: dict, co: dict, by: str, what: str) -> str:
    """Empty when ``by`` may change what the change order carries: its requester, a document controller or a policy owner
    (who then contributes to it)."""
    roles = authz.roles_of(bundle, by)
    if by in (co["fields"].get("requested_by"), co.get("created_by")) or {"document-controller", "policy-owner"} & set(roles):
        return ""
    return (f"{co['number']} is {co['fields'].get('requested_by') or co.get('created_by')}'s change order: they, a document controller or a "
            f"policy owner {what}")


def frame_path_problem(hub, name: str, rel: str) -> str:
    """Why a Frame named ``name`` may not live at ``rel`` under frames/ (empty when it may): a relative path inside
    frames/, ending in .frame.md, not another Frame's file, and not a second file for a Frame that has one."""
    from pathlib import PurePosixPath

    p = PurePosixPath(str(rel or ""))
    if not rel or p.is_absolute() or ".." in p.parts or any(part.startswith(".") for part in p.parts) or not p.name.endswith(".frame.md"):
        return f"{rel!r} is not a place for a Frame: a relative path under frames/ ending in .frame.md"
    for other in hub.frames.frames.values():
        if str(other.relpath) == str(p) and other.name != name:
            return f"frames/{p} is the Frame {other.name}"
        if other.name == name and str(other.relpath) != str(p):
            return f"the Frame {name} is at frames/{other.relpath}, not frames/{p}: a second file would give two Frames one name"
    return ""


def add_new_sop(hub, co_ref: str, name: str, text: str, *, by: str, author: str | None = None, relpath: str | None = None) -> dict:
    """A new SOP, work instruction, policy, cog profile or persona document carried by the change order: ``text`` is its
    whole Frame text (front matter and body, ``name`` its Frame name). It is a draft at revision A until the change
    order's release makes it effective, like any revision. ``by`` asks (the requester, a document controller or a policy
    owner);
    ``author`` writes it (default ``by``). ``relpath`` is where its Frame file goes under frames/ (default
    ``<name>.frame.md``). Returns the draft (or the one the change order already carries for that name)."""
    from .frames import load_frame_text

    co = _co(hub, co_ref)
    if finished(co) or (co.get("state") or "").startswith("awaiting"):
        raise ValueError(f"{co['number']} is {co['state']}: change what it carries while it is being prepared or revised")
    bundle = hub.releases.bundle(hub.releases.current()["digest"])
    why = _may_carry(bundle, co, by, "add documents to it")
    if why:
        raise ValueError(why)
    for c in carried(hub, co):
        if c["name"] == name:
            return c["draft"]
    if sopdocs.of_lineage(hub.kernel, name):
        raise ValueError(f"{name} already has a document: revise it on the change order (add_sop)")
    from pathlib import Path

    rel = relpath or f"{name}.frame.md"
    why = frame_path_problem(hub, name, rel)
    if why:
        raise ValueError(why)
    frame = load_frame_text(text, Path(hub.root) / "frames" / rel)
    if frame.name != name:
        raise ValueError(f"the text is the Frame {frame.name!r}, not {name!r}")
    doc_type, folder = sopdocs.kind_of(frame)
    writer = author or by
    head = "\n".join(text.splitlines()[: max(0, frame.body_start - 1)])
    tail = max(1, len(text) - len(text.rstrip("\n")))
    draft = hub.dms.create(title=sopdocs.title_of(frame), doc_class="controlled", folder=folder, by=writer, content=sopdocs.to_docx(frame.body),
                           filename=f"{name}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                           doc_type=doc_type,
                           fields={"lineage": sopdocs.LINEAGE + name, "sop_name": name, "frame_meta": dict(frame.frontmatter), "frame_header": head,
                                   "frame_tail": tail, "revision": "A", "owner": frame.maintainer or writer, "author": writer,
                                   "description": frame.description, "change_order": co["number"], "frame_relpath": rel, "new_document": True,
                                   **({"started_by": by} if writer != by else {})})
    hub.kernel.link(co["locator"], CARRIES, draft["locator"], by=by)
    return hub.kernel.get(draft["locator"])


def retire_sop(hub, co_ref: str, name: str, *, by: str, reason: str) -> dict:
    """The change order retires the document in force for ``name``: the delta compiles without that Frame (a delta item
    says so), and at release the document becomes obsolete and its Frame leaves the release (its file is moved aside
    to frames/.retired/). The requester, a document controller or a policy owner, with a reason. Returns the change order."""
    from .release import utcnow

    co = _co(hub, co_ref)
    if finished(co) or (co.get("state") or "").startswith("awaiting"):
        raise ValueError(f"{co['number']} is {co['state']}: change what it carries while it is being prepared or revised")
    bundle = hub.releases.bundle(hub.releases.current()["digest"])
    why = _may_carry(bundle, co, by, "retire documents on it")
    if why:
        raise ValueError(why)
    if not (reason or "").strip():
        raise ValueError("say why the document is retired")
    if any(c["name"] == name for c in carried(hub, co)):
        raise ValueError(f"{co['number']} carries a revision of {name}: take it off first, then retire it")
    d = sopdocs.in_force(hub.kernel, name)
    if d is None:
        raise ValueError(f"there is no SOP or policy {name!r} in force to retire")
    r = retirements(co)
    if name not in r:
        r[name] = {"document": d["number"], "locator": d["locator"], "by": by, "reason": reason.strip(), "at": utcnow()}
        hub.kernel.set_fields(co["locator"], {"retires": r}, by=by)
    return hub.kernel.get(co["locator"])


def unretire_sop(hub, co_ref: str, name: str, *, by: str) -> dict:
    """Take a retirement off the change order."""
    co = _co(hub, co_ref)
    r = retirements(co)
    if name not in r:
        raise ValueError(f"{co['number']} does not retire {name}")
    r.pop(name)
    return hub.kernel.set_fields(co["locator"], {"retires": r}, by=by)


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


def add_sop(hub, co_ref: str, name: str, *, by: str, author: str | None = None) -> dict:
    """Start a draft revision of an SOP or policy on this change order (or return the one it already carries). ``by``
    asks for it (and must be allowed to); ``author`` is whose draft it is when someone else writes it (the qa-manager cog
    drafting the prose for a direct change): its first version and its author field are theirs."""
    co = _co(hub, co_ref)
    if finished(co) or (co.get("state") or "").startswith("awaiting-approve"):
        raise ValueError(f"{co['number']} is {co['state']}: add SOPs while it is being prepared, or send it back to the start")
    bundle = hub.releases.bundle(hub.releases.current()["digest"])
    why = _may_carry(bundle, co, by, "add SOPs to it")
    if why:
        raise ValueError(why)
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
        if d["state"] in ("draft", "open", "held"):
            other = next((lk["target"] for lk in hub.kernel.links_of(d["locator"]) if lk["rel"] == CARRIES and lk["direction"] == "in"), None)
            o = hub.kernel.get(other) if other else None
            raise ValueError(f"{current['number']} is already being revised ({d['number']}" + (f", on {o['number']})" if o else ")"))
    content, v = hub.dms.content(current["locator"])
    f = current["fields"]
    keep = {k: f.get(k) for k in ("lineage", "sop_name", "frame_meta", "frame_header", "description", "owner", "review_date") if f.get(k) is not None}
    bom = {x["name"]: x for x in (hub.releases.current() or {}).get("frames") or []}
    keep["frame_relpath"] = sopdocs._relpath(hub, name, current, bom)  # the revision's Frame stays where the one in force is
    writer = author or by
    draft = hub.dms.create(title=f.get("title") or name, doc_class="controlled", folder=current["folder"], by=writer, content=content,
                           filename=v.get("filename") or f"{name}.docx", mime=v.get("mime") or "application/octet-stream",
                           doc_type=f.get("doc_type") or "sop",
                           fields={**keep, "revision": _next_revision(f.get("revision")), "revises": current["number"], "author": writer,
                                   "change_order": co["number"], **({"started_by": by} if writer != by else {})})
    hub.kernel.link(co["locator"], CARRIES, draft["locator"], by=by)
    return hub.kernel.get(draft["locator"])


def remove_sop(hub, co_ref: str, name: str, *, by: str) -> None:
    """Take a revision off the change order (it is withdrawn): its requester or a document controller, while it is being
    prepared or revised (once sent for approval what it carries is fixed)."""
    from . import changecontrol

    co = changecontrol.sync(hub, co_ref)
    why = changecontrol.may_edit(hub, co, by)
    if why:
        raise ValueError(why)
    if changecontrol.stage_of(co) not in changecontrol.EDITABLE:
        raise ValueError(f"{co['number']} is {changecontrol.stage_of(co).replace('-', ' ')}: what it carries is fixed now")
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


class carried_record_types:
    """While the change order's delta is compiled, the record types it carries are the ones in force (so a CO can carry a
    new record type together with the process that uses it): the record types in force with its record-type changes
    applied (hubproto.recordtypes.check). Restored afterwards; the release activates them for good."""

    def __init__(self, hub, co: dict):
        self.hub, self.co, self.saved = hub, co, None

    def __enter__(self):
        changes = list(self.co["fields"].get("record_type_changes") or [])
        if not changes:
            return self
        try:
            from . import recordtypes
        except ImportError:
            return self
        k = self.hub.kernel
        types, _, _ = recordtypes.check(self.hub, changes, k.types)
        self.saved = (k.types_hook, k._types)
        k.types_hook, k._types = None, types
        return self

    def __exit__(self, *exc):
        if self.saved is not None:
            self.hub.kernel.types_hook, self.hub.kernel._types = self.saved
        return False


def stage(hub, co_ref: str, drafts: dict[str, str] | None = None, extractor=None) -> dict:
    """Compile the Frame the change order's approved revisions make (or the given ``drafts``: {SOP name: locator}),
    against the release in force, and stage it on the change order as its proposal (replacing the one staged before).
    The compile-frame automation does this when a revision is approved."""
    from .compile import CompileError, blocking_findings

    co = _co(hub, co_ref)
    if drafts is None:
        drafts = {**{n: d["locator"] for n, d in approved_of(hub, co).items()}, **{n: None for n in retirements(co)}}
    old = co["fields"].get("staged_proposal")
    shas = {n: (str((hub.kernel.get(loc) or {}).get("fields", {}).get("content_sha") or "") if loc else "retired") for n, loc in sorted(drafts.items())}
    fields: dict = {"staged_from": shas, "staged_base": (hub.releases.current() or {}).get("digest"), "staged_errors": [],
                    "staged_at": __import__("hubproto.release", fromlist=["utcnow"]).utcnow()}
    if not drafts:
        fields.update({"staged_proposal": None, "staged_number": None, "staged_changes": 0, "staged_widenings": 0})
    else:
        try:
            with carried_record_types(hub, co):  # a CO may carry a new record type and the process that uses it
                p = hub.compile(sop_drafts=drafts, save=True, extractor=extractor)
            p["eco"] = co["number"]
            p["source"] = "eco"
            if old and old != p["id"]:  # the questions of the compile staged before (on any release) carry, or close
                from .compile import carry_questions

                try:
                    p["questions"] = carry_questions(hub.proposals.load(old).get("questions") or [], p.get("questions") or [])
                except Exception:
                    pass
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
    # A page never calls a model: a changed sentence the grammar cannot read and the compile-frame committee has not read
    # yet is not an error of the draft, it is what the committee reads in the next review round.
    p["unread"] = [{"frame": n["frame"], "line": n["line"], "text": n["text"]} for n in p.get("notes") or [] if n.get("severity") == "unread"]
    p["preview"] = True
    return p


def frame_reasons(hub, co: dict) -> list[str]:
    """Why the change order may not go for approval yet (empty when it may): hubproto.changecontrol.send_reasons (a
    review round has run on what it carries now, its delta compiles against the release in force, nothing flagged)."""
    from . import changecontrol

    return changecontrol.send_reasons(hub, co)


def status(hub, co_ref: str) -> dict:
    """Where a change order stands: what it carries, what it implements, the staged Frame and what blocks it (sending
    it for approval, or, once it waits at its Gate, signing it)."""
    from . import changecontrol

    co = changecontrol.sync(hub, co_ref)
    f = co["fields"]
    proposal = None
    if f.get("staged_proposal"):
        try:
            proposal = hub.proposals.load(f["staged_proposal"])
        except Exception:
            proposal = None
    if finished(co):
        reasons = []
    elif changecontrol.stage_of(co) == "awaiting-approval":
        reasons = changecontrol.approval_reasons(hub, co)
    else:
        reasons = changecontrol.send_reasons(hub, co)
    crs = {r["number"]: r for r in hub.kernel.query(type_="change-request")}
    implements = [crs[n] for n in f.get("implements") or [] if n in crs]
    return {"carried": carried(hub, co), "implements": implements, "proposal": proposal, "ready": not reasons, "reasons": reasons, "co": co,
            "stage": changecontrol.stage_of(co)}


def check_approval(hub, co_ref: str) -> list[str]:
    """Why the change order may not be approved yet (empty when it may): hubproto.changecontrol.approval_reasons
    (every item of its delta decided, every warning accepted, the delta current, nothing stale or flagged, the state
    map answered, every change request it implements approved and not flagged)."""
    from . import changecontrol

    return changecontrol.approval_reasons(hub, co_ref)


def _utc():
    return __import__("hubproto.release", fromlist=["utcnow"]).utcnow()


def activate_frame(hub, co_ref: str) -> dict:
    """The compiled Frame comes into force (step 2 of the release automation). Its wording changes and narrowing
    changes are covered by the change order's approval; widenings carry both policy owners' signatures already."""
    from .compile import change_state

    co = _co(hub, co_ref)
    if co["fields"].get("released_release"):
        return hub.releases.get(co["fields"]["released_release"]) or {"digest": co["fields"]["released_release"]}
    if not co["fields"].get("staged_proposal") and not carried(hub, co) and not retirements(co):
        return hub.releases.current()  # it changes forms only: the Frame in force stays
    proposal = hub.proposals.load(co["fields"]["staged_proposal"])
    for c in proposal["changes"]:
        if (not c.get("widening") or c.get("op") == "text") and change_state(c) == "pending":
            signer = (co["fields"].get("signed") or {}).get("by") or "its approver"
            c["decisions"].append({"by": f"change-order:{co['number']}", "verdict": "approve", "at": _utc(), "change_digest": "",
                                   "meaning": f"covered by the signature of {co['number']} ({signer}): the change order's approval approves "
                                              "the revisions it carries"})
    hub.proposals.save(proposal)
    meta = hub.release(proposal["id"], f"change-order:{co['number']}")
    hub.kernel.set_fields(co["locator"], {"released_release": meta["digest"]}, by="hub")
    return meta


def release_problems(hub, co_ref: str) -> list[str]:
    """What would stop the release part-way, checked before anything comes into force (empty when nothing would): each
    Frame the release writes goes to a sound place that is not another Frame's file and not a second file for its name,
    no two go to one place, each carried revision may still be published, and each document it retires is in force."""
    co = _co(hub, co_ref)
    out = []
    try:
        disk = hub.frames
    except Exception as e:  # the Frames on disk do not load as they are: writing more would not help
        return [f"the Frames on disk do not load: {e}"]
    retired = set(retirements(co))
    pid = co["fields"].get("staged_proposal")
    p = hub.proposals.load(pid) if pid else None
    places: dict[str, str] = {}
    for name, o in sorted(((p or {}).get("overrides") or {}).items()):
        rel = str(o.get("path") or (disk.get(name).relpath if name in disk.frames else f"{name}.frame.md"))
        why = frame_path_problem(hub, name, rel)
        if why:
            out.append(f"{name}: {why}")
        if rel in places:
            out.append(f"{name} and {places[rel]} would both be written to frames/{rel}")
        places[rel] = name
    for name in sorted(retired):
        r = retirements(co)[name]
        d = hub.kernel.get(r["locator"])
        if d is None or d["state"] != "effective":
            out.append(f"{r.get('document')} ({name}) is {(d or {}).get('state') or 'gone'}, not in force: it cannot be retired")
    for c in carried(hub, co):
        d = c["draft"]
        if d["state"] in ("withdrawn", "obsolete", "rejected"):
            out.append(f"{d['number']} ({c['name']}) is {d['state']}: it cannot be published")
        cur = c["in_force"]
        if d["fields"].get("revises") and (cur is None or cur["number"] != d["fields"]["revises"]):
            out.append(f"{d['number']} revises {d['fields']['revises']}, which is no longer the {c['name']} in force")
    return out


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
    for name, r in retirements(co).items():  # the documents it retires become obsolete; their Frame file is moved aside
        old = hub.kernel.get(r["locator"])
        if old and old["state"] == "effective":
            hub.kernel.transition(old["locator"], "obsolete", by="hub", release=digest, reason=f"retired with {co['number']}: {r.get('reason')}")
            done.append(old["number"])
        frame = hub.frames.frames.get(name)
        if frame is not None and frame.path.exists():
            aside = hub.frames.root / ".retired" / frame.relpath
            aside.parent.mkdir(parents=True, exist_ok=True)
            frame.path.replace(aside)
            hub._frames = None
            hub._released_cache = {}
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
    """Release an approved change order now: the release automation's own job (``release-change-order``), worked at
    once, so there is one release path. Raises ValueError with the job's reason when it stops."""
    from . import automation

    co = _co(hub, co_ref)
    if co["state"] == "released" and co["fields"].get("released_release"):
        return hub.releases.get(co["fields"]["released_release"])
    if co["state"] != "approved":
        raise ValueError(f"{co['number']} is {co['state']}: it is released once its approver signs it")
    blocked = check_approval(hub, co_ref)
    if blocked:
        raise ValueError("the change order cannot be released: " + "; ".join(blocked))
    job = automation.start(hub, "release-change-order", subject=co["locator"], trigger=f"{by} released {co['number']}", by=by)
    if job["status"] in ("queued", "running"):
        job = automation.run(hub, job["id"])
    if job["status"] != "success":
        raise ValueError(f"the release of {co['number']} stopped: {job.get('outcome')}")
    return hub.releases.get(_co(hub, co_ref)["fields"]["released_release"])
