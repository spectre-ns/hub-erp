"""Change control v2: the change-order loop (docs/design/change-control.md, sections 3, 6 and 8).

A change order (CO) carries prose revisions of SOPs and policies, **direct changes** (edits made in Settings and the
editors, kept on the CO as delta items, not written into the prose by Settings) and data-model changes (form layouts,
record-type changes). What is reviewed and approved is the **configuration delta**: every change the CO would make
against the release in force, each item with where it came from (``origin``), the ``warnings`` an approver must accept
and whether its decisions are ``stale``.

The CO's ``stage`` field tracks the loop (its record state follows the change-order process)::

    preparing -> in-review -> revising -> (in-review ...) -> awaiting-approval -> approved -> released

* **Request review** starts a round (the ``compile-frame`` Op, so a page never waits on a model): compile the
  delta from the carried drafts *as they stand* (later rounds recompile only what changed), draft the prose for
  direct changes not yet stated (``hubproto.prose_drafting`` when it exists, else the deterministic sentence writer
  of ``hubproto.sop_settings``) and check the round trip, suggest the panel (the quality-assistant cog reads the
  change-control SOP's "Review of changes" section), and queue the panel's reviews. Cogs review through the cog queue
  (task kind ``co-review``); people are told and answer with :func:`submit_review`. A reviewer in round n>1 is given
  only what changed since its last review, plus its earlier findings.
* **Revise**: answer questions, accept or reject suggestions (an accepted one becomes a delta item with origin
  ``suggested``), request review again.
* **Send for approval** (the requester) once :func:`send_reasons` is empty: the change-order run reaches the
  quality manager's Gate.
* **Decide the delta**: approvers (policy owners and people who may approve change orders) approve or reject each
  item with a meaning, accepting every warning it carries; a widening needs both policy owners; an override (an
  edited value) is a warning the approver accepts and may leave a **divergence** between configuration and prose.
* **Approve**: the Gate may be signed only when :func:`approval_reasons` is empty. Signing it also approves every
  revision the CO carries (recorded on each, :func:`approve_revisions`). Segregation of duties: the CO approver is not
  a contributor (the change-control SOP's sentence "The contributor and the approver of a change-order must be
  different people." keeps them off the Gate, from the CO's ``contributor`` field; the release re-checks it).
* **Release** (``release-change-order`` automation): the decided delta is applied (:func:`apply_delta`), divergences
  are recorded in the release meta (:func:`record_release`) with a change request to the SOP owner for each new one,
  every open CO is compiled again, and related COs and change requests are sent back (:func:`find_related`).

Everything below takes the hub and a change order (its number or locator) and raises ValueError with a sentence a
person can act on when a step is refused. The API the shell builds its pages on:

Reading
    stage_of(co)                                        the stage of a CO record
    sync(hub, co)                                       the CO, after noticing a rejected approval (back to revising)
    overview(hub, co)                                   everything a CO page shows, in one dict
    delta(hub, co)                                      the delta items (origin, warnings with acceptances, state, stale)
    co_warnings(hub, co)                                warnings on the CO as a whole (kept divergences, general findings)
    questions(hub, co)                                  every question: compiler, round trip, rebase, reviewers
    findings(hub, co)                                   the open findings: each reviewer's latest review
    suggestions(hub, co)                                every suggestion made in any round, with its status
    suggest_panel(hub, co)                              the panel the quality-assistant cog suggests, with reasons
    committee_reviews(hub, co)                          each committee on the panel: agreed, objected, waiting or paused
    send_reasons(hub, co) / approval_reasons(hub, co)   why it may not be sent / signed yet (empty: it may)
    contributors(hub, co)                               who wrote or made what it carries (none of them may approve it)
    touches(hub, co)                                    what it changes: SOPs, processes, rule slots, record types, forms, roles
    state_questions(hub, co)                            states the delta removes that items are in, and the answer

The loop
    request_review(hub, co, by, panel=None)             start a round; ``panel`` (names or {"who", "reason"}) replaces the suggestion
    add_reviewer(hub, co, who, by, reason="") / remove_reviewer(hub, co, who, by)
    submit_review(hub, co, who, findings=(), questions=(), suggestions=(), summary="")
    intervene_review(hub, co, committee, member, by, meaning)  the chair reviews in place of a member that could not
    cog_review(hub, co, n, cog)                         a cog's review of round n (the co-review queue task)
    accept_suggestion(hub, co, sid, by) / reject_suggestion(hub, co, sid, by, reason="")
    answer_question(hub, co, qid, answer, by, line=None)
    add_direct_change(hub, co, rule_or_change, by) / remove_direct_change(hub, co, item_id, by)
    rules_to_change_order(hub, user, new_rules, reason="")   Settings: direct changes on the person's settings CO
    set_state_map(hub, co, process, mapping, by)
    send_for_approval(hub, co, by)
    decide_item(hub, co, item_id, verdict, by, meaning, accept_warnings=(), override=None)
    accept_warning(hub, co, ref, by, meaning)           a warning on the CO as a whole
    dismiss_related(hub, co, by, reason, meaning)       a "related by meaning" flag, with an accepted warning
    clear_cr_flag(hub, cr, by, note)                    the approver of a flagged change request has looked again

Release (hubproto.automation calls these)
    on_approved, approve_revisions, apply_delta, record_release, raise_divergence_requests, find_related,
    open_change_orders, keep_divergences (the compiler keeps an open divergence's rule in force)
"""

from __future__ import annotations

import copy
import json
import re
import uuid

from . import authz, eco
from .release import utcnow
from .rules import rule_key, sha256_of, validate_rule

STAGES = ("preparing", "in-review", "revising", "awaiting-approval", "approved", "released")
EDITABLE = ("preparing", "in-review", "revising")
CHANGE_CONTROL_SOP = "change-control-sop"
REVIEW_SECTION = "review of changes"
PANEL_COG = "quality-assistant"
SOD_SENTENCE = "The contributor and the approver of a change-order must be different people."
MACHINES = ("hub", "system", "seed", "")
CR_OPEN = ("open", "draft", "returned", "failed")

# Seams for tests and for other workstreams (no model in tests): a callable that stands in for a cog's reading.
REVIEWER = None  # (hub, co, cog, brief) -> text in the review format (see REVIEW_FORMAT)
MEANING = None  # (hub, cog, released_co, candidates) -> [(number, reason)]: related by meaning
PANEL = None  # (hub, co, section_text, touched) -> [{"who", "reason"}]: the quality-assistant's suggestion


# ---------------------------------------------------------------- basics
def _co(hub, ref) -> dict:
    return eco._co(hub, ref["locator"] if isinstance(ref, dict) else ref)


def _bundle(hub) -> dict:
    meta = hub.releases.current()
    return hub.releases.bundle(meta["digest"]) if meta else {"members": {}, "roles": [], "cogs": {}}


def _digest(hub) -> str | None:
    return (hub.releases.current() or {}).get("digest")


def requester(co: dict) -> str:
    return co["fields"].get("requested_by") or co.get("created_by") or ""


def _set(hub, co: dict, by: str = "hub", **fields) -> dict:
    return hub.kernel.set_fields(co["locator"], fields, by=by)


def _proposal(hub, co: dict) -> dict | None:
    pid = co["fields"].get("staged_proposal")
    if not pid:
        return None
    try:
        return hub.proposals.load(pid)
    except Exception:
        return None


def _owners(hub) -> list[str]:
    b = _bundle(hub)
    return sorted(p for p, m in (b.get("members") or {}).items() if m.get("type") == "person" and "policy-owner" in authz.roles_of(b, p))


def _person(hub, who: str) -> bool:
    return ((_bundle(hub).get("members") or {}).get(who) or {}).get("type") == "person"


def _sentence(rule: dict | None) -> str:
    if not rule:
        return ""
    from .settings import sentence

    try:
        return sentence({k: v for k, v in rule.items() if k != "source"}, None).replace("\n", " / ")
    except Exception:
        return (rule.get("source") or {}).get("text") or json.dumps({k: v for k, v in rule.items() if k != "source"}, default=str)


def _slot(rule: dict) -> tuple:
    from .sop_settings import slot

    return slot(rule)


def _slot_key(rule: dict) -> str:
    return json.dumps(_slot(rule), default=str)


def _may_edit(hub, co: dict, by: str) -> str:
    """Empty when ``by`` may change the change order: its requester, who prepared it, or a document controller."""
    if by and (by in (requester(co), co.get("created_by")) or "document-controller" in authz.roles_of(_bundle(hub), by)):
        return ""
    return f"{co['number']} is {requester(co)}'s change order: they or a document controller change it"


def _may_decide(hub, co: dict, by: str) -> str:
    """Empty when ``by`` may decide delta items: a person who is a policy owner or may approve change orders."""
    b = _bundle(hub)
    if not _person(hub, by):
        return f"{by} is not a person: people decide the items of a delta (cogs review and suggest)"
    if "policy-owner" in authz.roles_of(b, by) or authz.check(b, by, "approve", "change-order", co["folder"])["allowed"]:
        return ""
    return f"{by} is neither a policy owner nor an approver of change orders: only they decide the items of a delta"


# ---------------------------------------------------------------- stage
def stage_of(co: dict) -> str:
    """Where the change order stands in the loop (its ``stage`` field; the record state wins once it is approved or
    released)."""
    st = co["state"]
    if st == "released":
        return "released"
    if st == "approved":
        return "approved"
    s = co["fields"].get("stage")
    if s in STAGES:
        return s
    if st.startswith("awaiting"):
        return "awaiting-approval"
    return "in-review" if co["fields"].get("rounds") else "preparing"


def sync(hub, co_ref) -> dict:
    """The change order, after noticing what happened to it outside this module: an approval Gate that was rejected
    sends it back to revising (the record is open again, the rejection kept in ``rejected``)."""
    co = _co(hub, co_ref)
    if co["state"] == "rejected":
        dec = next((d for d in reversed(co.get("decisions") or []) if d.get("verdict") == "reject"), {})
        hub.kernel.transition(co["locator"], "open", by="hub", reason="its approval was rejected: back to revising")
        co = _set(hub, co, stage="revising", rejected={"by": dec.get("by"), "meaning": dec.get("meaning"), "at": dec.get("at")})
    return co


def on_approved(hub, co_ref) -> dict:
    """The CO Gate was signed (hubproto.automation calls this before the release starts)."""
    co = _co(hub, co_ref)
    return _set(hub, co, stage="approved")


# ---------------------------------------------------------------- rounds
def rounds(co: dict) -> list[dict]:
    return copy.deepcopy(list(co["fields"].get("rounds") or []))


def current_round(co: dict) -> dict | None:
    rs = rounds(co)
    return rs[-1] if rs else None


def _update_round(hub, co_ref, n: int, fn, by: str = "hub", **more) -> dict:
    co = _co(hub, co_ref)
    rs = rounds(co)
    for r in rs:
        if r["n"] == n:
            fn(r)
    return _set(hub, co, by=by, rounds=rs, **more)


# ---------------------------------------------------------------- direct changes (delta items made by people)
# A direct change is kept on the change order (``direct_changes``) in the shape hubproto.prose_drafting reads: {"id",
# "op": "add"|"remove"|"change", "rule", "was"?, "origin": {"kind": "direct"|"suggested", "by", "at", "frame", ...},
# "warnings", "stale"}, plus "reason" and "widening" (this module) and "stated"/"round_trip" (written by the drafter).
def _items(co: dict) -> list[dict]:
    return copy.deepcopy(list(co["fields"].get("direct_changes") or []))


def _strip(rule: dict | None) -> dict:
    return {k: v for k, v in (rule or {}).items() if k not in ("source", "key")}


def _direct_item(rule: dict, *, by: str, op: str = "add", was: dict | None = None, at: str | None = None) -> dict:
    try:
        from .prose_drafting import direct_item
    except ImportError:  # the same shape, without the drafting module
        ident = sha256_of({"op": op, "rule": _strip(rule), "was": _strip(was) if was else None})[:12]
        item = {"id": f"direct:{ident}", "op": op, "rule": _strip(rule), "warnings": [], "stale": False,
                "origin": {"kind": "direct", "by": by, "frame": None, "line": None, "quote": None, "read_by": None, "at": at or utcnow()}}
        if was is not None:
            item["was"] = _strip(was)
        return item
    return direct_item(rule, by=by, op=op, was=was, at=at)


def _old_new(it: dict) -> tuple[dict | None, dict | None]:
    """(the rule an item takes out or changes, the rule it puts in)."""
    op = it.get("op") or "add"
    was = it.get("was") or it.get("changes_from")
    if op == "remove":
        return (was or it.get("rule")), None
    return was, it.get("rule")


def _same(a: dict | None, b: dict | None) -> bool:
    if not a or not b:
        return False
    try:
        from .prose_drafting import same_rule
    except ImportError:
        return rule_key(_strip(a)) == rule_key(_strip(b))
    return same_rule(a, b)


def _with(rules: dict[str, dict], old: dict | None, new: dict | None) -> None:
    if old is not None:
        rules.pop(rule_key(old), None)
    if new is not None:
        rules[rule_key(new)] = new


def _apply(rules: list[dict], items: list[dict]) -> list[dict]:
    """``rules`` with the items applied (each takes out what it changes or removes and puts in its rule)."""
    out = {rule_key(r): r for r in rules}
    for it in items:
        _with(out, *_old_new(it))
    return list(out.values())


def _item_slot(it: dict) -> tuple:
    old, new = _old_new(it)
    return _slot(new or old)


def _item_reason(it: dict) -> str:
    old, new = _old_new(it)
    if old is not None and new is not None:
        return f"change “{_sentence(old)}” to “{_sentence(new)}”"
    return f"add “{_sentence(new)}”" if new is not None else f"remove “{_sentence(old)}”"


def _item_widening(base: list[dict], it: dict) -> bool:
    from .guards import drift_guard

    return any(c.get("widening") for c in drift_guard(base, _apply(base, [it])))


def _sig(items: list[dict]) -> str:
    return sha256_of(sorted(it["id"] for it in items))[:16]


def _rebased(hub, co: dict) -> tuple[list[dict], list[dict]]:
    """The CO's direct and accepted items against the release in force: unchanged when it is the release they were
    made against; otherwise each is kept (a change re-pointed at what now holds its slot) unless the release already
    does it or no longer has what it removes: then it becomes a question."""
    from .compile import question

    items = _items(co)
    digest = _digest(hub)
    base_at = co["fields"].get("direct_base")
    questions = list(co["fields"].get("rebase_questions") or [])
    if not items or not base_at or base_at == digest:
        return items, questions
    base = hub.settings_rules()
    keys = {rule_key(r) for r in base}
    out = []
    for it in items:
        old, new = _old_new(it)
        done = (new is not None and rule_key(new) in keys and (old is None or rule_key(old) not in keys)) or \
               (new is None and old is not None and rule_key(old) not in keys)
        if done:
            questions.append(question((it.get("origin") or {}).get("frame") or "", None,
                                      f"{it.get('reason') or _item_reason(it)}: the release in force already does this (or no longer has what it "
                                      "changes), so it is no longer part of the delta. Is anything still to change here?",
                                      raised_by="hub", code="rebase", item=None))
            continue
        if it.get("op") == "change" and old is not None and rule_key(old) not in keys:
            now = next((r for r in base if _slot(r) == _slot(new)), None)
            it = {**it, "was": _strip(now)} if now else {**{k: v for k, v in it.items() if k != "was"}, "op": "add"}
        it["widening"] = _item_widening(base, it)
        out.append(it)
    return out, questions


def add_direct_change(hub, co_ref, rule_or_change, by: str, *, origin: dict | None = None) -> list[dict]:
    """A direct change on the change order: an edit made in Settings or an editor, kept as a delta item with origin
    ``direct`` (who, when, what). It is not written into the prose here: the next review round drafts the prose.

    ``rule_or_change`` is a rule (it changes the rule in force that holds the same slot, or is added), a change
    ``{"op": "add"|"remove"|"change", "rule": {...}, "was"|"changes_from": {...}}``, or a list of these. A later edit
    of the same slot replaces the earlier one; one that puts back the rule in force drops it. ``origin`` adds to the
    item's origin (an accepted suggestion passes ``{"kind": "suggested", "by": <suggester>, "accepted_by": by}``).
    Returns the change order's direct changes."""
    from .settings import stamp
    from .sop_settings import home_frame

    co = sync(hub, co_ref)
    why = _may_edit(hub, co, by)
    if why:
        raise ValueError(why)
    st = stage_of(co)
    if st not in EDITABLE:
        raise ValueError(f"{co['number']} is {st.replace('-', ' ')}: its changes are fixed now")
    base = hub.settings_rules()
    items, questions = _rebased(hub, co)
    processes = _bundle(hub).get("processes") or {}
    now = utcnow()
    for ch in rule_or_change if isinstance(rule_or_change, list) else [rule_or_change]:
        if "op" in ch and "rule" in ch:
            op, rule, was = ch["op"], dict(ch["rule"]), ch.get("was") or ch.get("changes_from")
            if op == "add" and was:
                op = "change"
            if op not in ("add", "remove", "change"):
                raise ValueError("a direct change adds, removes or changes a rule")
        else:
            rule = dict(ch)
            was = next((r for r in base if _slot(r) == _slot(rule)), None)
            op = "change" if was else "add"
        if op != "remove":
            if not rule.get("source"):
                rule = stamp(rule, by, processes)
            problems = validate_rule(rule)
            if problems:
                raise ValueError("; ".join(problems))
        item = _direct_item(rule if op != "remove" else (was or rule), by=by, op=op, was=was if op == "change" else None, at=now)
        prior = next((x for x in items if _item_slot(x) == _item_slot(item)), None)
        if prior is not None:
            items.remove(prior)
            p_old, _ = _old_new(prior)
            if op in ("add", "change") and p_old is not None and not _same(p_old, (_old_new(item))[0]):
                item = _direct_item(rule, by=by, op="change", was=p_old, at=now)
        old, new = _old_new(item)
        if old is not None and new is not None and _same(old, new):
            continue  # it puts back the rule in force: nothing to change
        if (new is not None and old is None and any(_same(new, r) for r in base)) or \
                (new is None and not any(_same(old, r) for r in base)):
            continue  # the release in force already says so
        live = next((r for r in base if old is not None and _same(r, old)), None)  # the SOP that states the rule in force
        frame = ((live or {}).get("source") or {}).get("frame")
        item["origin"] = {**item["origin"], "frame": frame if frame not in (None, "", "settings") else home_frame(new or old, base), **(origin or {})}
        item["reason"], item["widening"] = _item_reason(item), _item_widening(base, item)
        items.append(item)
    _set(hub, co, by=by, direct_changes=items, direct_base=_digest(hub), rebase_questions=questions)
    _set(hub, _co(hub, co["locator"]), contributor=contributors(hub, co["locator"]))
    return items


def remove_direct_change(hub, co_ref, item_id: str, by: str) -> list[dict]:
    """Take a direct change off the change order (the prose already drafted for it stays in the revision: edit it)."""
    co = sync(hub, co_ref)
    why = _may_edit(hub, co, by)
    if why:
        raise ValueError(why)
    if stage_of(co) not in EDITABLE:
        raise ValueError(f"{co['number']} is {stage_of(co).replace('-', ' ')}: its changes are fixed now")
    items = _items(co)
    if not any(x["id"] == item_id for x in items):
        raise ValueError(f"{co['number']} has no direct change {item_id}")
    rest = [x for x in items if x["id"] != item_id]
    _set(hub, co, by=by, direct_changes=rest)
    return rest


def rules_to_change_order(hub, user: str, new_rules: list[dict], reason: str = "") -> dict:
    """Settings as direct changes (change control v2): the difference between the rules in force and ``new_rules`` is
    recorded on the person's open settings change order (one is started when they have none). Settings no longer
    writes sentences into the SOPs: the review round drafts the prose (the qa-manager cog). Returns {"change_order",
    "locator", "report"}; each report line says what was recorded."""
    from .guards import drift_guard

    base = hub.settings_rules()
    changes = drift_guard(base, new_rules)
    if not changes:
        raise ValueError("that changes nothing")
    co = next((r for r in hub.kernel.query(type_="change-order") if r["state"] in eco.OPEN_STATES and r["fields"].get("requested_by") == user
               and r["fields"].get("from_settings") and stage_of(r) in EDITABLE), None)
    if co is None:
        co = hub.kernel.create("change-order", "Quality/Changes", {"title": f"Policy changes by {user}", "description": reason or "Changes to the policy.",
                                                                   "requested_by": user, "from_settings": True, "stage": "preparing"}, by=user)
    before = {it["id"] for it in _items(co)}
    wanted = [{"op": "change" if c.get("changes_from") else c["op"], "rule": c["rule"], **({"was": c["changes_from"]} if c.get("changes_from") else {})}
              for c in changes if not (c["op"] == "remove" and c.get("changed_to"))]
    items = add_direct_change(hub, co, wanted, by=user)
    report = []
    for it in items:
        if it["id"] in before:
            continue
        old, new = _old_new(it)
        report.append({"sop": (it.get("origin") or {}).get("frame"), "draft": None, "ok": True, "item": it["id"],
                       "detail": "recorded on the change order as a direct change; its review round writes it into the SOP",
                       "says": _sentence(new) if new is not None else "(removed)", "was": _sentence(old) if old is not None else None})
    return {"change_order": co["number"], "locator": co["locator"], "report": report}


# ---------------------------------------------------------------- who contributed
def _version_authors(hub, draft: dict) -> list[str]:
    try:
        versions = hub.dms.versions(draft["locator"])
    except Exception:
        versions = []
    who = [v.get("by") for v in versions] + [draft["fields"].get("author")]
    return [w for w in dict.fromkeys(who) if w and w not in MACHINES]


def contributors(hub, co_ref) -> list[str]:
    """Everyone who wrote a revision the change order carries or made a direct, suggested (who accepted it), form or
    record-type change on it. None of them may sign its approval (the change-control SOP's segregation sentence)."""
    co = _co(hub, co_ref)
    out = []
    for c in eco.carried(hub, co):
        out += _version_authors(hub, c["draft"])
    for it in _items(co):
        o = it.get("origin") or {}
        out += [o.get("by"), o.get("accepted_by")] if o.get("kind") == "suggested" else [o.get("by")]
    for kind in ("form_changes", "record_type_changes", "workflow_changes"):
        carried = co["fields"].get(kind) or {}
        out += [(v or {}).get("by") for v in (carried.values() if isinstance(carried, dict) else carried)]
    return sorted({w for w in out if w and w not in MACHINES})


# ---------------------------------------------------------------- compiling the delta
def _shas(hub, drafts: dict[str, str]) -> dict[str, str]:
    return {n: str((hub.kernel.get(loc) or {}).get("fields", {}).get("content_sha") or "") for n, loc in sorted(drafts.items())}


def _synthetic(hub, co: dict, prev: dict | None) -> dict:
    """The proposal of a change order that carries no prose revision: the release in force as it stands, which the
    delta's direct items change when they are approved."""
    from .compile import _reviewers

    meta = hub.releases.current()
    rules = hub.releases.rules(meta["digest"])
    existing = hub.proposals.list()
    p = {"id": uuid.uuid4().hex[:12], "created_at": utcnow(), "extractor": "none", "scopes": [], "frames": meta.get("frames") or [],
         "config": hub.kernel.config_digests, "record_types": hub.kernel.types, "clauses": meta.get("clauses") or {}, "rules": rules,
         "notes": [], "findings": [{"guard": "divergence", "severity": "warning", "rule_key": f"divergence:{d['id']}", "frame": d.get("frame") or "",
                                    "line": int(d.get("line") or 0), "detail": _kept_words(d)} for d in open_divergences(hub)],
         "changes": [], "base": meta["digest"], "reviewers": sorted(_reviewers(rules)), "status": "open", "release": None, "overrides": {},
         "eco": co["number"], "co": co["number"], "source": "eco", "proposed_by": requester(co),
         "number": 1 + max(len(existing), max((int(x.get("number") or 0) for x in existing), default=0))}
    hub.proposals.save(p)
    if prev and prev.get("status") == "open" and prev["id"] != p["id"]:
        prev["status"], prev["superseded_by"] = "superseded", p["id"]
        hub.proposals.save(prev)
    _set(hub, co, staged_proposal=p["id"], staged_number=p["number"], staged_from={}, staged_base=meta["digest"], staged_errors=[],
         staged_at=utcnow())
    return p


def compile_delta(hub, co_ref, *, extractor=None, n: int | None = None) -> dict:
    """Compile the change order's delta against the release in force and stage it (the staged proposal): the carried
    drafts as they stand (nothing is recompiled when neither they, the direct changes nor the release changed since
    the last compile), then its items annotated: each with ``origin``, ``warnings`` and ``stale``, direct changes the
    prose does not state yet added as items of their own (with a round-trip warning), decisions carried from the last
    compile for every item that is the same. Returns {"proposal", "errors", "recompiled"}."""
    co = _co(hub, co_ref)
    items, rebase_qs = _rebased(hub, co)
    if items != _items(co) or rebase_qs != list(co["fields"].get("rebase_questions") or []) or \
            (items and co["fields"].get("direct_base") != _digest(hub)):
        co = _set(hub, co, direct_changes=items, direct_base=_digest(hub), rebase_questions=rebase_qs)
    prev = _proposal(hub, co)
    drafts = eco.drafts_of(hub, co)
    f = co["fields"]
    same = (prev is not None and prev.get("status") == "open" and f.get("staged_base") == _digest(hub)
            and (f.get("staged_from") or {}) == _shas(hub, drafts) and not f.get("staged_errors")
            and (bool(drafts) or prev.get("extractor") == "none"))
    recompiled = not same
    if same:
        p = prev
    elif drafts:
        fields = eco.stage(hub, co["locator"], drafts=drafts, extractor=extractor)
        if not fields.get("staged_proposal"):
            return {"proposal": None, "errors": list(fields.get("staged_errors") or []), "recompiled": True}
        p = hub.proposals.load(fields["staged_proposal"])
    elif items:
        p = _synthetic(hub, co, prev)
    else:
        eco.stage(hub, co["locator"], drafts={})
        return {"proposal": None, "errors": [], "recompiled": prev is not None}
    p = _annotate(hub, _co(hub, co["locator"]), p, prev, n)
    co = _co(hub, co["locator"])
    live = [c for c in p["changes"] if c.get("op") != "text"]
    from .compile import blocking_findings

    _set(hub, co, staged_sig=_sig(_items(co)), staged_changes=len(live), staged_widenings=sum(1 for c in live if _widening(c)),
         staged_errors=[f"{x['detail']} ({x['frame']} line {x['line']})" for x in blocking_findings(p)],
         contributor=contributors(hub, co))
    return {"proposal": p, "errors": [], "recompiled": recompiled}


def _draft_authors(hub, co: dict) -> dict[str, list[str]]:
    return {c["name"]: _version_authors(hub, c["draft"]) for c in eco.carried(hub, co) if c["name"]}


def _annotate(hub, co: dict, p: dict, prev: dict | None, n: int | None) -> dict:
    """The staged proposal as the delta: each compiled change with its origin (a direct change the prose now states keeps
    its maker: origin ``direct`` or ``suggested``, with the SOP line that states it), every direct change the prose does
    not state (yet) as an item of its own (``unstated``), decisions and overrides carried from the last compile for an
    item that is the same, questions carried (a rebase question for each item a release made moot)."""
    old = {c["id"]: c for c in ((prev or p).get("changes") or [])}
    p["changes"] = [c for c in p.get("changes") or [] if not c.get("unstated")]
    items = _items(co)
    base = hub.settings_rules()
    authors = _draft_authors(hub, co)
    covered: dict[str, set] = {}
    for c in p["changes"]:
        if c.get("op") == "text":
            c.setdefault("origin", {"kind": "prose", "frame": c.get("frame"), "line": None, "quote": None, "read_by": None,
                                    "at": p.get("created_at")})
            c["origin"]["by"] = c["origin"].get("by") or ", ".join(authors.get(c.get("frame")) or []) or None
        else:
            src = (c.get("rule") or {}).get("source") or {}
            where = {"frame": src.get("frame"), "line": src.get("line"), "quote": src.get("text"), "read_by": src.get("read_by") or None}
            it = next((x for x in items if (c.get("op") == "add" and _same(_old_new(x)[1], c["rule"]))
                       or (c.get("op") == "remove" and _same(_old_new(x)[0], c["rule"]))), None)
            if it is not None:
                c["origin"] = {**(it.get("origin") or {}), **where}
                c["direct"] = it["id"]
                covered.setdefault(it["id"], set()).add(c["op"])
            else:
                o = c.get("origin") or {}
                c["origin"] = {"kind": "prose", **o, **where, "by": o.get("by") or ", ".join(authors.get(where["frame"]) or []) or None,
                               "at": o.get("at") or p.get("created_at")}
            prior = old.get(c["id"])
            c["decisions"] = [dict(d) for d in (prior or {}).get("decisions") or []]
            if prior and prior.get("override"):
                c["override"] = prior["override"]
        c.setdefault("warnings", [])
        c.setdefault("stale", False)
    for it in items:
        o, nw = _old_new(it)
        need = ({"add"} if nw is not None else set()) | ({"remove"} if o is not None and not _same(o, nw) else set())
        stated = need <= covered.get(it["id"], set())
        it["prose"] = next(({k: c["origin"].get(k) for k in ("frame", "line", "quote")} for c in p["changes"] if c.get("direct") == it["id"]), None) \
            if stated else None
        if stated:
            continue
        prior = old.get(it["id"]) or {}
        c = {"id": it["id"], "op": it.get("op") or "add", "rule": it["rule"], "widening": _item_widening(base, it),
             "reason": it.get("reason") or _item_reason(it), "origin": it.get("origin"), "unstated": True, "direct": it["id"],
             "decisions": [dict(d) for d in prior.get("decisions") or []], "warnings": [], "stale": False}
        if o is not None and nw is not None:
            c["was"] = o
        if prior.get("override"):
            c["override"] = prior["override"]
        p["changes"].append(c)
    _set(hub, co, direct_changes=items)
    qs = {q["id"]: q for q in p.get("questions") or []}
    for q in co["fields"].get("rebase_questions") or []:
        qs.setdefault(q["id"], dict(q))
    p["questions"] = list(qs.values())
    p["eco"] = p["co"] = co["number"]
    p["source"] = "eco"
    if n:
        p["round"] = n
    p["acceptances"] = {**((prev or {}).get("acceptances") or {}), **(p.get("acceptances") or {})}
    _refresh(hub, co, p)
    hub.proposals.save(p)
    return p


# ---------------------------------------------------------------- warnings
def _wkey(scope: str, w: dict) -> str:
    return f"{scope}|{w['code']}|{w.get('ref') or ''}"


def _widening(c: dict) -> bool:
    return bool((c.get("override") or {}).get("widening", c.get("widening"))) if c.get("override") else bool(c.get("widening"))


def _effective(c: dict) -> dict:
    return (c.get("override") or {}).get("rule") or c["rule"]


def _latest_results(co: dict) -> dict[str, tuple[int, dict]]:
    """Each reviewer's latest finished review: {who: (round, result)}."""
    out: dict[str, tuple[int, dict]] = {}
    for r in rounds(co):
        for who, res in (r.get("results") or {}).items():
            if res.get("status") == "done":
                out[who] = (r["n"], res)
    return out


def findings(hub, co_ref) -> list[dict]:
    """The open findings: every finding of each reviewer's latest review (an earlier finding it does not repeat is
    resolved). Each: {"id", "who", "round", "severity": "blocking"|"advisory", "text", "about", "stale"}."""
    co = _co(hub, co_ref)
    out = []
    for who, (n, res) in _latest_results(co).items():
        for f in res.get("findings") or []:
            out.append({**f, "who": who, "round": n, "stale": bool(res.get("stale"))})
    return out


def questions(hub, co_ref) -> list[dict]:
    """Every question on the change order: the compiler's, the round-trip and rebase questions (in the staged
    proposal) and the reviewers' (their latest review)."""
    co = _co(hub, co_ref)
    p = _proposal(hub, co)
    out = [dict(q) for q in (p or {}).get("questions") or []]
    for who, (n, res) in _latest_results(co).items():
        for q in res.get("questions") or []:
            out.append({**q, "raised_by": who, "round": n})
    return out


def suggestions(hub, co_ref) -> list[dict]:
    """Every suggestion made in any round: {"id", "by", "round", "text", "change": {"op", "rule"} | None, "status"}."""
    co = _co(hub, co_ref)
    out = []
    for r in rounds(co):
        for who, res in (r.get("results") or {}).items():
            for s in res.get("suggestions") or []:
                out.append({**s, "by": s.get("by") or who, "round": r["n"]})
    return out


def _warnings(hub, co: dict, p: dict) -> tuple[dict[str, list[dict]], list[dict]]:
    items: dict[str, list[dict]] = {c["id"]: [] for c in p.get("changes") or []}
    where = {((c.get("origin") or {}).get("frame"), (c.get("origin") or {}).get("line")): c["id"] for c in p.get("changes") or []
             if (c.get("origin") or {}).get("line")}
    general: list[dict] = []

    def attach(about: dict | None, w: dict) -> None:
        about = about or {}
        if about.get("item") in items:
            items[about["item"]].append(w)
        elif (about.get("frame"), about.get("line")) in where:
            items[where[(about.get("frame"), about.get("line"))]].append(w)
        else:
            general.append(w)

    div_slots = {d.get("slot"): d for d in open_divergences(hub)}
    direct = {it["id"]: it for it in _items(co)}
    qtext = {q["id"]: q for q in p.get("questions") or []}
    for c in p.get("changes") or []:
        if c.get("op") == "text":
            continue
        items[c["id"]] += [dict(w) for w in c.get("read_warnings") or []]  # the compiler's (a reading that drops a "not")
        rt = (direct.get(c.get("direct") or "") or {}).get("round_trip") or {}
        if c.get("unstated") or rt.get("ok") is False:
            q = qtext.get(rt.get("question") or "") or {}
            items[c["id"]].append({"code": "round-trip", "ref": rt.get("question") or f"rt:{c['id']}",
                                   "text": (q.get("text") or "the prose does not state this direct change")
                                   + ("; once approved it is in force while the SOP says otherwise" if c.get("unstated") else "")})
        if c.get("override"):
            o = c["override"]
            items[c["id"]].append({"code": "override", "ref": f"ov:{c['id']}",
                                   "text": f"overridden by {o.get('by')}: in force will be “{_sentence(o['rule'])}” while the prose says "
                                           f"“{(c.get('origin') or {}).get('quote') or _sentence(c['rule'])}”"})
        d = div_slots.get(_slot_key(_effective(c)))
        if d:
            items[c["id"]].append({"code": "divergence", "ref": d["id"], "text": _kept_words(d)})
    for f in p.get("findings") or []:
        if f.get("guard") == "divergence":
            general.append({"code": "divergence", "ref": str(f.get("rule_key") or "").split(":", 1)[-1], "text": f["detail"]})
    for q in questions(hub, co):
        if q.get("status", "open") != "open" or "round-trip" in (q.get("kind"), q.get("code")):
            continue  # a round-trip question is the item's round-trip warning
        attach({"item": q.get("item"), "frame": q.get("frame"), "line": q.get("line")}, {"code": "question", "ref": q["id"], "text": q["text"]})
    for f in findings(hub, co):
        if f.get("severity") == "blocking":
            attach(f.get("about"), {"code": "finding", "ref": f["id"], "text": f"{f['who']}: {f['text']}"})
    bc = co["fields"].get("baseline_changed") or {}
    if bc.get("dismissed"):
        general.append({"code": "related", "ref": f"related:{bc.get('by')}", "text": f"related by meaning to {bc.get('by')}: {'; '.join(bc.get('reasons') or [])} "
                        f"(dismissed by {bc['dismissed'].get('by')}: {bc['dismissed'].get('reason')})"})
    return items, general


def _refresh(hub, co: dict, p: dict) -> dict:
    acc = p.get("acceptances") or {}
    per_item, general = _warnings(hub, co, p)
    for c in p.get("changes") or []:
        ws = per_item.get(c["id"], [])
        for w in ws:
            w["accepted"] = acc.get(_wkey(c["id"], w))
        c["warnings"] = ws
        live = [d for d in c.get("decisions") or [] if not d.get("stale")]
        c["stale"] = bool(c.get("decisions")) and not live
    for w in general:
        w["accepted"] = acc.get(_wkey("co", w))
    p["warnings"] = general
    return p


def _refresh_saved(hub, co_ref) -> None:
    co = _co(hub, co_ref)
    p = _proposal(hub, co)
    if p is not None and p.get("status") == "open":
        _refresh(hub, co, p)
        hub.proposals.save(p)


def item_state(c: dict, owners) -> str:
    """approved, rejected or pending: a reject decides it; a widening needs two policy owners, anything else one
    approver. Stale decisions (made before the baseline changed) do not count."""
    live = [d for d in c.get("decisions") or [] if not d.get("stale")]
    if any(d["verdict"] == "reject" for d in live):
        return "rejected"
    approvers = {d["by"] for d in live if d["verdict"] == "approve"}
    if _widening(c):
        return "approved" if len(approvers & set(owners)) >= 2 else "pending"
    return "approved" if approvers else "pending"


def delta(hub, co_ref) -> list[dict]:
    """The delta items of the staged compile, each: {"id", "op", "rule", "override"?, "widening", "reason", "origin",
    "warnings": [{"code", "text", "ref", "accepted"}], "decisions", "state", "stale", "unstated"?}. Wording changes
    (op "text") are listed too: the CO's approval approves them."""
    co = _co(hub, co_ref)
    p = _proposal(hub, co)
    if p is None:
        return []
    _refresh(hub, co, p)
    owners = p.get("reviewers") or _owners(hub)
    return [{**c, "state": "covered" if c.get("op") == "text" else item_state(c, owners)} for c in p.get("changes") or []]


def co_warnings(hub, co_ref) -> list[dict]:
    co = _co(hub, co_ref)
    p = _proposal(hub, co)
    return _refresh(hub, co, p)["warnings"] if p else []


# ---------------------------------------------------------------- what a change order touches
def _roles_named(rule: dict) -> set[str]:
    k = rule.get("kind")
    if k == "role":
        return {rule.get("name")}
    if k == "member":
        return set(rule.get("roles") or [])
    if k == "permission" and str(rule.get("subject") or "").startswith("role:"):
        return {rule["subject"][5:]}
    return set()


def touches(hub, co_ref, p: dict | None = None) -> dict[str, set]:
    """What the change order changes: {"sop", "process", "slot", "record_type", "form", "role", "kind"} (sets)."""
    co = _co(hub, co_ref)
    f = co["fields"]
    p = p if p is not None else _proposal(hub, co)
    out: dict[str, set] = {k: set() for k in ("sop", "process", "slot", "record_type", "form", "role", "kind")}
    out["sop"] |= {c["name"] for c in eco.carried(hub, co) if c["name"]}
    changes = list((p or {}).get("changes") or []) + list((p or {}).get("applied_items") or [])
    if p is None:
        changes = _items(co)
    for c in changes:
        if c.get("op") == "text":
            out["sop"].add(c["frame"])
            out["kind"].add("text")
            continue
        for r in [_effective(c)] + [c[k] for k in ("changes_from", "was") if c.get(k)]:
            out["kind"].add(r.get("kind"))
            frame = (c.get("origin") or {}).get("frame") or (r.get("source") or {}).get("frame")
            if frame and frame != "settings":
                out["sop"].add(frame)
            proc = r.get("process") or (r.get("name") if r.get("kind") == "process" else None)
            if proc:
                out["process"].add(proc)
            out["slot"].add(_slot_key(r))
            out["role"] |= _roles_named(r)
    out["form"] = set(f.get("form_changes") or {})
    out["process"] |= set(f.get("workflow_changes") or {})  # a workflow carried from the editor (hubproto.workflows)
    rtc = f.get("record_type_changes") or []
    out["record_type"] = {x.get("type") for x in (rtc if isinstance(rtc, list) else [{"type": k} for k in rtc])}
    for k in out:
        out[k].discard(None)
        out[k].discard("")
    return out


def _quality(hub, t: dict[str, set]) -> list[str]:
    """The quality processes (and quality SOPs) a change touches."""
    from . import sopdocs

    b = _bundle(hub)
    out = []
    for proc in sorted(t["process"]):
        folder = ((b.get("processes") or {}).get(proc) or {}).get("folder") or ((b.get("ops") or {}).get(proc) or {}).get("folder") or ""
        if folder.startswith("Quality"):
            out.append(f"the {proc} process")
    for name in sorted(t["sop"]):
        d = sopdocs.in_force(hub.kernel, name)
        if d and str(d.get("folder") or "").startswith("Quality"):
            out.append(name)
    return out


# ---------------------------------------------------------------- the panel
_REVIEW_RE = re.compile(r"^an? change (?:that touches|that changes|to|of) (?P<what>.+?) is reviewed by (?P<who>.+?)\.?$", re.I)
TERM_KINDS = {"permission": {"permission"}, "routing": {"routing"}, "route": {"routing"}, "due date": {"sla"}, "deadline": {"sla"},
              "threshold": {"threshold"}, "limit": {"threshold", "approval_criteria"}, "approval criteria": {"approval_criteria"},
              "approval criterion": {"approval_criteria"}, "segregation of duties": {"sod"}, "role": {"role", "member"},
              "member": {"member"}, "people": {"member"}, "folder": {"folder"}, "process": {"process"}, "workflow": {"process"},
              "cog": {"cog"}, "cog profile": {"cog"}, "persona": {"persona"}, "scope": {"scope"}, "wording": {"text"}, "text": {"text"}}


def review_section(hub) -> str:
    """The change-control SOP's section on reviewing changes, as released (empty when it has none)."""
    try:
        text = hub.released_frames().get(CHANGE_CONTROL_SOP).text
    except Exception:
        return ""
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip().lower() == REVIEW_SECTION
            continue
        if inside:
            out.append(line)
    return "\n".join(out).strip()


def _sentences(section: str) -> list[str]:
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", section) if p.strip()]
    lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
    out = []
    for s in [*lines, *(x for p in paras for x in re.split(r"(?<=\.)\s+", p))]:
        if _REVIEW_RE.match(s) and s not in out:
            out.append(s)
    return out


def _term_hit(hub, term: str, t: dict[str, set]) -> str:
    w = re.sub(r"^(?:a|an|the|any)\s+", "", term.strip().lower()).strip(" .")
    if w in ("record type", "record types", "data model", "field", "fields"):
        return f"record types: {', '.join(sorted(t['record_type']))}" if t["record_type"] else ""
    if w in ("form", "forms", "form layout", "form layouts"):
        return f"forms: {', '.join(sorted(t['form']))}" if t["form"] else ""
    if "quality process" in w:
        q = _quality(hub, t)
        return f"a quality process: {', '.join(q)}" if q else ""
    if w in ("change", "thing", "anything", "everything"):
        return "every change"
    kinds = TERM_KINDS.get(w) or TERM_KINDS.get(w[:-1] if w.endswith("s") else w) or set()
    hit = kinds & t["kind"]
    if not hit:
        return ""
    if hit & {"role", "member"} and t["role"]:
        return f"{w}: {', '.join(sorted(t['role']))}"
    return w


def _who(hub, phrase: str) -> list[dict]:
    from .extract import singular_role, slug
    from .groups import committee, committee_named

    b = _bundle(hub)
    members = b.get("members") or {}
    out = []
    for part in re.split(r",\s*|\s+and\s+|\s+or\s+", phrase):
        w = re.sub(r"^(?:the|a|an)\s+", "", part.strip().lower()).strip(" .")
        if re.search(r"\bcommittee$", w):
            # A committee reviews by consensus: every member of it is on the panel (groups and committees).
            name = committee_named(b, w)
            c = committee(b, name) if name else None
            if c:
                out += [{"who": m, "kind": (members.get(m) or {}).get("type") or "person", "member": True, "committee": name}
                        for m in c["members"]]
            else:
                out.append({"who": slug(w), "kind": "committee", "member": False})
            continue
        is_cog = bool(re.search(r"\bcogs?$", w))
        w = re.sub(r"\s+cogs?$", "", w)
        if not w:
            continue
        name = slug(w)
        if name in members:
            out.append({"who": name, "kind": members[name].get("type") or "person", "member": True})
        elif singular_role(w) in (b.get("roles") or []):
            role = singular_role(w)
            out += [{"who": p, "kind": m.get("type") or "person", "member": True, "role": role} for p, m in sorted(members.items())
                    if role in (m.get("roles") or [])]
        else:
            out.append({"who": name, "kind": "cog" if is_cog else "person", "member": False})
    return out


def suggest_panel(hub, co_ref) -> list[dict]:
    """The panel the quality-assistant cog suggests for the next round: it reads the change-control SOP's "Review of
    changes" section and the delta. Without a model (its stand-in) the section's sentences ("A change that touches
    permissions, record types or forms is reviewed by the it-admin cog.") are applied literally to what the delta
    touches. Each entry: {"who", "kind": "cog"|"person", "reason", "suggested_by", "member"}; a name that is not a
    member of the organization is suggested (and shown) but cannot review."""
    co = _co(hub, co_ref)
    t = touches(hub, co)
    section = review_section(hub)
    b = _bundle(hub)
    members = b.get("members") or {}
    by = PANEL_COG if PANEL_COG in members else "hub"
    picked: dict[str, dict] = {}

    def put(entry: dict, reason: str) -> None:
        cur = picked.get(entry["who"])
        if cur:
            cur["reason"] += f"; {reason}"
            if entry.get("committee"):
                cur["committee"] = entry["committee"]
        else:
            picked[entry["who"]] = {"who": entry["who"], "kind": entry["kind"], "reason": reason, "suggested_by": by,
                                    "member": bool(entry.get("member")), **({"committee": entry["committee"]} if entry.get("committee") else {})}

    if PANEL is not None:
        for e in PANEL(hub, co, section, t) or []:
            for x in _who(hub, e["who"]):
                put(x, e.get("reason") or "")
        return list(picked.values())
    if by == PANEL_COG and section:
        read = _panel_by_model(hub, co, section, t)
        if read:
            for e in read:
                for x in _who(hub, e["who"]):
                    put(x, e["reason"])
            return list(picked.values())
    for s in _sentences(section):
        m = _REVIEW_RE.match(s)
        hits = [h for h in (_term_hit(hub, term, t) for term in re.split(r",\s*|\s+or\s+|\s+and\s+", m.group("what"))) if h]
        if not hits:
            continue
        for x in _who(hub, m.group("who")):
            put(x, f"{s} (this change touches {'; '.join(hits)})")
    return list(picked.values())


def _panel_by_model(hub, co: dict, section: str, t: dict) -> list[dict]:
    prof = (_bundle(hub).get("cogs") or {}).get(PANEL_COG) or {}
    user = (f"SECTION OF THE CHANGE-CONTROL SOP ON REVIEWING CHANGES:\n{section}\n\nTHE DELTA OF {co['number']} TOUCHES:\n"
            + "\n".join(f"- {k}: {', '.join(sorted(v))}" for k, v in t.items() if v)
            + "\n\nWho should review this change? One line per reviewer: '- <member name>: <reason, citing the section>'. Nothing else.")
    try:
        out = hub.model.complete(model=prof.get("model") or "", system=prof.get("prompt") or "You are the quality assistant.", user=user,
                                 by=PANEL_COG, max_tokens=500)
    except Exception:
        return []
    if out.get("stand_in"):
        return []
    found = []
    for line in (out.get("text") or "").splitlines():
        m = re.match(r"^\s*[-*]\s*([a-z][\w -]*?)\s*:\s*(.+)$", line.strip(), re.I)
        if m:
            found.append({"who": m.group(1), "reason": m.group(2).strip()})
    return found


def _panel_entry(hub, raw, by: str, suggested: list[dict]) -> list[dict]:
    who = raw.get("who") if isinstance(raw, dict) else str(raw)
    reason = raw.get("reason") if isinstance(raw, dict) else ""
    same = next((s for s in suggested if s["who"] == who), None)
    if same and not reason:
        return [dict(same)]
    return [{**x, "reason": reason or f"added by {by}", "suggested_by": same["suggested_by"] if same else None,
             **({} if same else {"added_by": by})} for x in _who(hub, who)]


# ---------------------------------------------------------------- a round
def request_review(hub, co_ref, by: str, panel=None) -> dict:
    """Start a review round: the compile-frame Op (hubproto.automation, queued in the hub-automation lane, every step
    recorded) compiles the delta from the drafts as they stand, drafts the prose for direct changes, checks the
    guards, stages it, suggests the panel (or takes ``panel``: member names or {"who", "reason"}, which replaces the
    suggestion; the suggestion is still recorded) and queues the panel's reviews. The requester or a document
    controller asks. A new round clears a "baseline changed" flag. Returns the round (``job``: the Op's job id)."""
    co = sync(hub, co_ref)
    why = _may_edit(hub, co, by)
    if why:
        raise ValueError(why)
    st = stage_of(co)
    if st not in EDITABLE:
        raise ValueError(f"{co['number']} is {st.replace('-', ' ')}: review rounds are over")
    f = co["fields"]
    if not (eco.carried(hub, co) or _items(co) or f.get("form_changes") or f.get("record_type_changes") or f.get("workflow_changes")):
        raise ValueError(f"{co['number']} changes nothing yet: carry an SOP or policy revision, make a direct change or carry a form first")
    last = current_round(co)
    if last and last.get("status") == "compiling":
        jobs = [j for j in hub.automation.of(co["locator"]) if j["automation"] == "compile-frame" and j["status"] in ("queued", "running")]
        if jobs:
            return last
    rs = rounds(co)
    n = len(rs) + 1
    rs.append({"n": n, "requested_by": by, "at": utcnow(), "status": "compiling", "compiled": None, "panel": [], "suggested": [],
               "results": {}, "requested_panel": list(panel) if panel is not None else None,
               "baseline": f.get("baseline_changed") or None})
    _set(hub, co, by=by, rounds=rs, stage="in-review", baseline_changed=None, rejected=None)
    from . import automation

    job = automation.start(hub, "compile-frame", subject=co["locator"], trigger=f"{by} requested review (round {n})", by=by,
                           acting_on=f"round {n} of {co['number']}")
    co = _co(hub, co["locator"])
    if not (current_round(co) or {}).get("job"):  # queued, not worked yet: the round is that job's
        co = _update_round(hub, co, n, lambda r: r.update({"job": job["id"]}))
    return current_round(co)


def _round_failed(hub, co: dict, n: int, why: str) -> None:
    _update_round(hub, co, n, lambda r: r.update({"status": "failed", "error": why}), stage="revising")


def round_compile(hub, co_ref, n: int, *, extractor=None) -> str:
    """Round step 1: compile the delta from the drafts as they stand."""
    co = _co(hub, co_ref)
    out = compile_delta(hub, co, extractor=extractor, n=n)
    if out["proposal"] is None and out["errors"]:
        _round_failed(hub, co, n, "the drafts do not compile: " + "; ".join(out["errors"][:3]))
        raise ValueError("the drafts do not compile: " + "; ".join(out["errors"][:3]))
    p = out["proposal"]
    _update_round(hub, co, n, lambda r: r.update({"compiled": (p or {}).get("id"), "compiled_number": (p or {}).get("number")}))
    if p is None:
        return "nothing to compile: it carries forms or record types only"
    live = [c for c in p["changes"] if c.get("op") != "text"]
    return (f"compiled as proposal #{p.get('number')}: {len(live)} item(s), {sum(1 for c in live if _widening(c))} widening(s)"
            + ("" if out["recompiled"] else " (nothing changed since the last compile: not compiled again)"))


def round_draft(hub, co_ref, n: int, by: str, *, extractor=None) -> str:
    """Round step 2: the prose for each direct change not stated yet, by the qa-manager cog
    (hubproto.prose_drafting.draft_direct_changes, which also checks the round trip of every stated item and returns a
    round-trip question for each that does not read back); without that module, the deterministic sentence writer
    (hubproto.sop_settings). What changed is compiled again; the round-trip questions go on the staged proposal."""
    from .compile import carry_questions

    co = _co(hub, co_ref)
    items = [it for it in _items(co) if not it.get("stale")]
    if not items:
        return "no direct change to write up"
    todo = [it for it in items if not it.get("stated")]
    try:
        from . import prose_drafting  # workstream 2: the qa-manager cog drafts the prose
    except ImportError:
        prose_drafting = None
    try:
        if prose_drafting is not None and hasattr(prose_drafting, "draft_direct_changes"):
            qs = prose_drafting.draft_direct_changes(hub, co["locator"], by, extractor=extractor) or []
            who = "the qa-manager cog"
        else:
            qs = _draft_fallback(hub, co, todo)
            who = "the hub (sentence grammar)"
        out = compile_delta(hub, co, extractor=extractor, n=n)
    except ValueError as e:
        _round_failed(hub, co, n, str(e))
        raise
    if out["proposal"] is None and out["errors"]:
        why = "the drafts do not compile after the prose was written: " + "; ".join(out["errors"][:3])
        _round_failed(hub, co, n, why)
        raise ValueError(why)
    p = out["proposal"]
    if p is not None:
        co = _co(hub, co["locator"])
        p["questions"] = carry_questions(p.get("questions") or [], qs, codes=("round-trip",))
        _refresh(hub, co, p)
        hub.proposals.save(p)
    _update_round(hub, co, n, lambda r: r.update({"drafted": {"by": who, "questions": [q.get("id") for q in qs]}}))
    left = [c for c in (p or {}).get("changes") or [] if c.get("unstated")]
    return (f"{who} wrote the prose for {len(todo)} direct change(s)" if todo else f"{len(items)} direct change(s) already stated") + \
        (f"; {len(left)} not stated as it stands ({len(qs)} round-trip question(s))" if left or qs else "; each reads back as itself")


def _draft_fallback(hub, co: dict, todo: list[dict]) -> list[dict]:
    """The prose for direct changes written by the sentence grammar into the carried revisions, in their makers' names
    (used only when hubproto.prose_drafting is missing). Returns no question: an item that is not stated after the
    compile is shown with a round-trip warning."""
    from . import sop_settings

    base = hub.settings_rules()
    processes = _bundle(hub).get("processes") or {}
    items = _items(co)
    for it in todo:
        maker = (it.get("origin") or {}).get("accepted_by") or (it.get("origin") or {}).get("by") or requester(co)
        planned = sop_settings.plan(base, _apply(base, [it]), processes)
        try:
            rep = sop_settings.apply_plan(hub, co["locator"], planned, by=maker)
        except ValueError:
            continue
        if rep and all(r.get("ok") for r in rep):
            for x in items:
                if x["id"] == it["id"]:
                    x["stated"] = {"sop": rep[0].get("sop"), "draft": rep[0].get("draft"), "by": maker, "how": "sentence", "at": utcnow()}
    _set(hub, co, direct_changes=items)
    return []


def round_check(hub, co_ref, n: int) -> str:
    """Round step 3: the guards (a compile error stops the round) and the round trip."""
    co = _co(hub, co_ref)
    errors = co["fields"].get("staged_errors") or []
    if errors:
        _round_failed(hub, co, n, "the compiled delta has problems that stop it: " + "; ".join(errors[:3]))
        raise ValueError("the compiled delta has problems that stop it: " + "; ".join(errors[:3]))
    left = [it for it in _items(co) if not it.get("stated")]
    w = int(co["fields"].get("staged_widenings") or 0)
    # What this round reviews: the revisions and the direct changes as they are now (send_reasons compares with it).
    seen = {"drafts": _shas(hub, eco.drafts_of(hub, co)), "items": _sig(_items(co))}
    _update_round(hub, co, n, lambda r: r.update({"reviewed_from": seen}))
    return ("no guard stops it" + (f"; {w} widening(s) need both policy owners" if w else "")
            + (f"; {len(left)} direct change(s) the prose does not state (round-trip questions)" if left else ""))


def round_panel(hub, co_ref, n: int) -> str:
    """Round step 4: the suggested panel, or the one the requester gave."""
    co = _co(hub, co_ref)
    suggested = suggest_panel(hub, co)
    rnd = next(r for r in rounds(co) if r["n"] == n)
    if rnd.get("requested_panel") is not None:
        panel = []
        for raw in rnd["requested_panel"]:
            for e in _panel_entry(hub, raw, rnd["requested_by"], suggested):
                if e["who"] not in {x["who"] for x in panel}:
                    panel.append(e)
    else:
        panel = [dict(s) for s in suggested]
    _update_round(hub, co, n, lambda r: r.update({"suggested": suggested, "panel": panel}))
    named = [f"{e['who']}" + ("" if e["member"] else " (not a member: shown, not asked)") for e in panel]
    return ("panel: " + ", ".join(named)) if named else "no reviewer suggested: the section on reviewing changes names none for this delta"


def round_queue(hub, co_ref, n: int, by: str) -> str:
    """Round step 5: each member of the panel is asked: a cog through its queue, a person in their inbox."""
    co = _co(hub, co_ref)
    rnd = next(r for r in rounds(co) if r["n"] == n)
    asked = [e for e in rnd.get("panel") or [] if e.get("member")]
    now = utcnow()
    _update_round(hub, co, n, lambda r: r.update({"status": "reviewing" if asked else "reviewed",
                                                  "results": {**(r.get("results") or {}),
                                                              **{e["who"]: {"status": "queued", "kind": e["kind"], "at": now} for e in asked}}}),
                  **({} if asked else {"stage": "revising"}))
    for e in asked:
        _ask(hub, co, n, e, by)
    co = _co(hub, co["locator"])
    rnd = next(r for r in rounds(co) if r["n"] == n)
    done = [w for w, res in (rnd.get("results") or {}).items() if res.get("status") in ("done", "failed")]
    return (f"asked {', '.join(e['who'] for e in asked)}" + (f"; {len(done)} reviewed already" if done else "")) if asked else "nobody to ask"


def _ask(hub, co: dict, n: int, e: dict, by: str) -> None:
    if e["kind"] == "cog":
        hub.queue.submit(e["who"], "co-review", by=by, title=f"Review round {n} of {co['number']}", record=co["locator"],
                         payload={"co": co["locator"], "round": n}, key=f"co-review:{co['locator']}:{n}:{e['who']}")
    else:
        hub.inbox.notify(e["who"], kind="review", subject=f"Review {co['number']} (round {n}): {co['fields'].get('title') or ''}",
                         body=f"{by} asks you to review the configuration delta of {co['number']}. Reason: {e.get('reason') or 'added to the panel'}. "
                              "Post findings (blocking or advisory), questions and suggested changes on the change order.", record=co["locator"])


def round_tell(hub, co_ref, n: int) -> str:
    co = _co(hub, co_ref)
    rnd = next(r for r in rounds(co) if r["n"] == n)
    who = requester(co)
    if who:
        hub.inbox.notify(who, kind="automation", subject=f"Round {n} of {co['number']} is under way",
                         body=f"The delta is compiled; panel: {', '.join(e['who'] for e in rnd.get('panel') or []) or 'nobody'}. "
                              "Answer questions in the prose, accept or reject suggestions, then request review again or send it for approval.",
                         record=co["locator"])
    return f"told {who}" if who else "nobody to tell"


def add_reviewer(hub, co_ref, who: str, by: str, reason: str = "") -> dict:
    """Add a reviewer (a person or a cog) to the current round's panel; they are asked at once."""
    co = sync(hub, co_ref)
    why = _may_edit(hub, co, by)
    if why:
        raise ValueError(why)
    rnd = current_round(co)
    if rnd is None or rnd.get("status") == "compiling" or stage_of(co) not in EDITABLE:
        raise ValueError(f"{co['number']} has no review round under way to add a reviewer to")
    entries = _panel_entry(hub, {"who": who, "reason": reason}, by, rnd.get("suggested") or [])
    if not entries or not all(e["member"] for e in entries):
        raise ValueError(f"{who} is not a member of the organization: only members review")
    new = [e for e in entries if e["who"] not in {x["who"] for x in rnd.get("panel") or []}]
    if not new:
        return rnd
    now = utcnow()
    _update_round(hub, co, rnd["n"], lambda r: r.update({"panel": (r.get("panel") or []) + new, "status": "reviewing",
                                                         "results": {**(r.get("results") or {}),
                                                                     **{e["who"]: {"status": "queued", "kind": e["kind"], "at": now} for e in new}}}),
                  stage="in-review")
    for e in new:
        _ask(hub, _co(hub, co["locator"]), rnd["n"], e, by)
    return current_round(_co(hub, co["locator"]))


def remove_reviewer(hub, co_ref, who: str, by: str) -> dict:
    """Take a reviewer off the current round's panel; a review it has not started is withdrawn from its queue (a
    review already done stays on the record)."""
    co = sync(hub, co_ref)
    why = _may_edit(hub, co, by)
    if why:
        raise ValueError(why)
    rnd = current_round(co)
    if rnd is None or who not in {e["who"] for e in rnd.get("panel") or []}:
        raise ValueError(f"{who} is not on the panel of {co['number']}")
    for t in hub.queue.open_tasks(cog=who, record=co["locator"]):
        if t["kind"] == "co-review" and t["status"] == "queued":
            hub.queue.cancel(t["id"], by, f"taken off the panel of {co['number']}")

    def fn(r):
        r["panel"] = [e for e in r.get("panel") or [] if e["who"] != who]
        r["removed"] = (r.get("removed") or []) + [{"who": who, "by": by, "at": utcnow()}]
        res = r.get("results") or {}
        if res.get(who, {}).get("status") != "done":
            res.pop(who, None)
        r["results"] = res
        _maybe_reviewed(r)
    co = _update_round(hub, co, rnd["n"], fn)
    if (current_round(co) or {}).get("status") == "reviewed" and stage_of(co) == "in-review":
        _set(hub, co, stage="revising")
    return current_round(_co(hub, co["locator"]))


def _maybe_reviewed(r: dict) -> None:
    panel = {e["who"] for e in r.get("panel") or [] if e.get("member")}
    res = r.get("results") or {}
    if r.get("status") == "reviewing" and all(res.get(w, {}).get("status") in ("done", "failed") for w in panel):
        r["status"] = "reviewed"


def _record_result(hub, co_ref, n: int, who: str, result: dict) -> dict:
    co = _co(hub, co_ref)

    def fn(r):
        r.setdefault("results", {})[who] = result
        _maybe_reviewed(r)
    co = _update_round(hub, co, n, fn)
    rnd = next(r for r in rounds(co) if r["n"] == n)
    if rnd.get("status") == "reviewed" and n == len(rounds(co)) and stage_of(co) == "in-review":
        co = _set(hub, co, stage="revising")
    _refresh_saved(hub, co)
    entry = next((e for e in rnd.get("panel") or [] if e["who"] == who and e.get("committee")), None)
    if entry is not None and result.get("status") == "failed":
        from .groups import committee

        chair = (committee(_bundle(hub), entry["committee"]) or {}).get("chair")
        if chair:  # the committee cannot agree without this member: its chair intervenes
            hub.inbox.notify(chair, kind="intervention", subject=f"Intervene: {co['number']} ({entry['committee']} committee): {who} could not review",
                             body=(f"{result.get('error') or 'Its review did not run'}. The {entry['committee']} committee reviews by consensus, so "
                                   f"{co['number']} cannot be sent for approval until you review in {who}'s place, or the change is revised."),
                             record=co["locator"])
    blocking = sum(1 for f in result.get("findings") or [] if f.get("severity") == "blocking")
    if requester(co) and requester(co) != who:
        hub.inbox.notify(requester(co), kind="review", subject=f"{who} reviewed {co['number']} (round {n})",
                         body=(f"{result.get('status')}: " + (result.get("error") or f"{len(result.get('findings') or [])} finding(s) ({blocking} blocking), "
                               f"{len(result.get('questions') or [])} question(s), {len(result.get('suggestions') or [])} suggestion(s).")),
                         record=co["locator"])
    return co


# ---------------------------------------------------------------- reviews
REVIEW_FORMAT = """Answer with one line per point, nothing else:
- [blocking] <item id, or general>: <a finding that must be resolved before approval>
- [advisory] <item id, or general>: <a finding worth knowing>
- [question] <item id, or general>: <a question the author should answer in the prose>
- [suggest] <SOP name> | <section heading> | <a sentence the SOP should say instead or as well>"""
_LINE = re.compile(r"^\s*[-*]\s*\[(blocking|advisory|question|suggest)\]\s*(.*)$", re.I)


def _snapshot(p: dict | None) -> dict[str, str]:
    return {c["id"]: rule_key(_effective(c)) if c.get("op") != "text" else str((c.get("rule") or {}).get("sha256") or "")
            for c in (p or {}).get("changes") or []}


def brief(hub, co_ref, n: int, who: str) -> dict:
    """What a reviewer is given in round n: the delta (in round n>1, only what changed since its last review: the
    delta of the delta), plus its earlier findings. {"items", "gone", "earlier", "since", "text"}."""
    co = _co(hub, co_ref)
    p = _proposal(hub, co)
    now = _snapshot(p)
    last = None
    for r in rounds(co):
        res = (r.get("results") or {}).get(who) or {}
        if r["n"] < n and res.get("status") == "done":
            last = (r["n"], res)
    if last:
        seen = last[1].get("reviewed") or {}
        ids = [i for i, k in now.items() if seen.get(i) != k]
        gone = [i for i in seen if i not in now]
    else:
        ids, gone = list(now), []
    by_id = {c["id"]: c for c in (p or {}).get("changes") or []}
    items = [by_id[i] for i in ids]
    earlier = list(last[1].get("findings") or []) if last else []
    f = co["fields"]
    lines = [f"Change order {co['number']}: {f.get('title') or ''}. {f.get('description') or ''}".strip(),
             f"Review round {n}. You review the configuration delta: what this change order would change against the release in force."]
    if last:
        lines.append(f"Since your review in round {last[0]}, only these items are new or changed:")
    for c in items:
        o = c.get("origin") or {}
        where = f"; {o.get('frame')} line {o.get('line')}: “{o.get('quote')}”" if o.get("line") else (f"; {o.get('frame')}" if o.get("frame") else "")
        lines.append(f"- {c['id']} [{c.get('op')}] {c.get('reason')} (origin: {o.get('kind')} by {o.get('by') or '-'}{where})")
    if not items:
        lines.append("- (no item changed)")
    for g in gone:
        lines.append(f"- {g}: no longer in the delta")
    if earlier:
        lines.append(f"Your findings in round {last[0]} (say again any that still stand; one you do not repeat is resolved):")
        lines += [f"- [{x.get('severity')}] {(x.get('about') or {}).get('item') or 'general'}: {x.get('text')}" for x in earlier]
    for e in f.get("staged_errors") or []:
        lines.append(f"The compile reports: {e}")
    return {"items": items, "gone": gone, "earlier": earlier, "since": last[0] if last else None, "text": "\n".join(lines)}


def _parse_review(hub, co: dict, text: str, prefix: str, ids: set[str]) -> tuple[list, list, list]:
    fs, qs, ss = [], [], []
    for line in (text or "").splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        kind, body = m.group(1).lower(), m.group(2).strip()
        if kind == "suggest":
            parts = [x.strip() for x in body.split("|")]
            raw = {"frame": parts[0], "section": parts[1], "sentence": parts[2]} if len(parts) >= 3 else {"sentence": body}
            ss.append(_suggestion(hub, co, raw, f"{prefix}S{len(ss) + 1}"))
            continue
        m2 = re.match(r"^(\S+):\s+(.*)$", body)  # "<item id>: text" (ids hold colons) or "general: text"
        target, rest = (m2.group(1), m2.group(2)) if m2 else ("", body)
        about = {"item": target} if target in ids else {}
        txt = rest.strip() if (target in ids or target.lower() == "general") else body
        if kind == "question":
            qs.append({"id": f"{prefix}Q{len(qs) + 1}", "text": txt, "item": about.get("item"), "status": "open", "answer": None, "by": None})
        else:
            fs.append({"id": f"{prefix}F{len(fs) + 1}", "severity": kind, "text": txt, "about": about})
    return fs, qs, ss


def cog_review(hub, co_ref, n: int, cog: str) -> dict:
    """A cog's review of round n (the co-review task in its queue runs this): it is given the brief (the delta, or
    the delta of the delta, and its earlier findings) and answers in REVIEW_FORMAT with its model. Its stand-in (no
    model, tests) says deterministically what the hub can see: a compile error is blocking, a widening advisory. The
    result lands in rounds[n].results[cog]."""
    from .model import ModelUnavailable

    co = _co(hub, co_ref)
    rnd = next((r for r in rounds(co) if r["n"] == n), None)
    if rnd is None or cog not in {e["who"] for e in rnd.get("panel") or []}:
        return {"skipped": f"{cog} is not on the panel of round {n} of {co['number']}"}
    if ((rnd.get("results") or {}).get(cog) or {}).get("status") == "done":
        return rnd["results"][cog]
    b = brief(hub, co, n, cog)
    ids = {c["id"] for c in b["items"]}
    prefix = f"R{n}.{cog}."
    prof = (_bundle(hub).get("cogs") or {}).get(cog) or {}
    model, stand_in, text = prof.get("model") or "stand-in", True, ""
    if REVIEWER is not None:
        text, stand_in, model = REVIEWER(hub, co, cog, b) or "", False, "test"
    else:
        try:
            out = hub.model.complete(model=prof.get("model") or "", system=_review_system(hub, co, cog, prof), user=b["text"] + "\n\n" + REVIEW_FORMAT,
                                     by=cog, max_tokens=1200)
            text, stand_in, model = out.get("text") or "", bool(out.get("stand_in")), out.get("model") or model
        except ModelUnavailable as e:
            result = {"status": "failed", "kind": "cog", "at": utcnow(), "error": f"{cog} could not review: its model is not available ({e})",
                      "findings": [], "questions": [], "suggestions": []}
            _record_result(hub, co, n, cog, result)
            return result
    if stand_in:
        fs = [{"id": f"{prefix}F{i}", "severity": "blocking", "text": f"the delta does not compile: {e}", "about": {}}
              for i, e in enumerate(co["fields"].get("staged_errors") or [], 1)]
        for c in b["items"]:
            if c.get("op") != "text" and _widening(c):
                fs.append({"id": f"{prefix}F{len(fs) + 1}", "severity": "advisory", "about": {"item": c["id"]},
                           "text": f"{c.get('reason')}: it widens authority, so both policy owners must approve it"})
        qs, ss = [], []
        summary = f"{len(b['items'])} item(s) looked at by {cog}'s stand-in (no model): " + ("; ".join(x["text"] for x in fs) or "nothing to say")
    else:
        fs, qs, ss = _parse_review(hub, co, text, prefix, ids)
        summary = "\n".join(ln for ln in text.splitlines() if not _LINE.match(ln)).strip()[:2000]
    for s in ss:
        s["by"] = cog
    result = {"status": "done", "kind": "cog", "at": utcnow(), "findings": fs, "questions": qs, "suggestions": ss, "summary": summary,
              "model": model, "stand_in": stand_in, "reviewed": _snapshot(_proposal(hub, co)), "since": b["since"]}
    _record_result(hub, co, n, cog, result)
    return result


def _review_system(hub, co: dict, cog: str, prof: dict) -> str:
    from .cogs import RunContext, cog_context

    meta = hub.releases.current() or {}
    try:
        ctx = RunContext(run_id=f"co-review-{co['number']}", release=meta.get("digest") or "", bundle=_bundle(hub), owner=cog, kernel=hub.kernel,
                         runner=hub.runner, inbox=hub.inbox, record=co["locator"], dms=hub.dms, frames=lambda: hub.released_frames(),
                         model=hub.model, extras={"cog_profile": prof, "process": "change-order"})
        return cog_context(ctx, co, task="review")
    except Exception:
        return prof.get("prompt") or f"You are {cog}, a reviewer of configuration changes."


def submit_review(hub, co_ref, who: str, findings=(), questions=(), suggestions=(), summary: str = "") -> dict:
    """A person's review of the current round (they were asked in their inbox). ``findings``: text or {"severity":
    "blocking"|"advisory", "text", "about": {"item"} or {"frame", "line"}}; ``questions``: text or {"text", "item"};
    ``suggestions``: a delta item {"op", "rule"}, a rule, {"frame", "section", "sentence"} or a sentence. Returns the
    result as recorded in rounds[n].results[who]."""
    co = sync(hub, co_ref)
    rnd = current_round(co)
    if rnd is None or who not in {e["who"] for e in rnd.get("panel") or []}:
        raise ValueError(f"{who} is not on the panel of the current round of {co['number']}")
    n = rnd["n"]
    prefix = f"R{n}.{who}."
    fs = []
    for i, x in enumerate(findings or [], 1):
        x = {"text": x} if isinstance(x, str) else dict(x)
        sev = {"error": "blocking", "warning": "advisory"}.get(x.get("severity"), x.get("severity") or "advisory")
        if sev not in ("blocking", "advisory"):
            raise ValueError("a finding is blocking or advisory")
        fs.append({"id": f"{prefix}F{i}", "severity": sev, "text": x.get("text") or "", "about": x.get("about") or {}})
    qs = []
    for i, x in enumerate(questions or [], 1):
        x = {"text": x} if isinstance(x, str) else dict(x)
        qs.append({"id": f"{prefix}Q{i}", "text": x.get("text") or "", "item": x.get("item"), "frame": x.get("frame"), "line": x.get("line"),
                   "status": "open", "answer": None, "by": None})
    ss = [{**_suggestion(hub, co, x, f"{prefix}S{i}"), "by": who} for i, x in enumerate(suggestions or [], 1)]
    result = {"status": "done", "kind": "person", "at": utcnow(), "findings": fs, "questions": qs, "suggestions": ss, "summary": summary,
              "reviewed": _snapshot(_proposal(hub, co))}
    _record_result(hub, co, n, who, result)
    return result


def _rule_from_sentence(hub, frame: str, section: str, sentence: str) -> dict | None:
    from pathlib import Path

    from .extract import ConventionExtractor
    from .frames import load_frame_text

    try:
        base = hub.released_frames().get(frame).text
        head = base.split("\n---", 1)[0] + "\n---\n" if base.startswith("---") else ""
        fr = load_frame_text(f"{head}# x\n\n## {section.strip().lstrip('#').strip()}\n{sentence.strip()}\n", Path(f"{frame}.frame.md"))
        rules = [r for r in ConventionExtractor().extract(fr).rules if r.get("kind") not in ("process", "cog", "scope", "persona", "committee")]
    except Exception:
        return None
    if len(rules) != 1:
        return None
    r = rules[0]
    r["source"] = {"frame": frame, "version": "", "line": 0, "text": sentence.strip()}
    return r


def _suggestion(hub, co: dict, raw, sid: str) -> dict:
    if isinstance(raw, str):
        raw = {"sentence": raw}
    raw = dict(raw)
    change, text = None, raw.get("text") or raw.get("sentence") or ""
    if raw.get("rule") and raw.get("op") in ("add", "remove"):
        change = {"op": raw["op"], "rule": raw["rule"], **({"changes_from": raw["changes_from"]} if raw.get("changes_from") else {})}
        text = text or raw.get("reason") or _sentence(raw["rule"])
    elif raw.get("kind"):
        change = {"rule": raw}
        text = text or _sentence(raw)
    elif raw.get("sentence") and raw.get("frame") and raw.get("section"):
        r = _rule_from_sentence(hub, raw["frame"], raw["section"], raw["sentence"])
        if r is not None:
            change = {"rule": r}
    return {"id": sid, "text": text, "frame": raw.get("frame"), "section": raw.get("section"), "change": change, "status": "open"}


def _find_suggestion(co: dict, sid: str) -> tuple[int, str, dict]:
    for r in rounds(co):
        for who, res in (r.get("results") or {}).items():
            for s in res.get("suggestions") or []:
                if s["id"] == sid:
                    return r["n"], who, s
    raise ValueError(f"{co['number']} has no suggestion {sid}")


def _set_suggestion(hub, co: dict, n: int, who: str, sid: str, **upd) -> None:
    def fn(r):
        for s in r["results"][who].get("suggestions") or []:
            if s["id"] == sid:
                s.update(upd)
    _update_round(hub, co, n, fn)


def accept_suggestion(hub, co_ref, sid: str, by: str) -> dict:
    """The author accepts a reviewer's suggestion: one that carries a change becomes a delta item with origin
    ``suggested`` (who suggested it, who accepted it); one that is only words is recorded as accepted, for the
    author to write into the prose. Returns the suggestion."""
    co = sync(hub, co_ref)
    why = _may_edit(hub, co, by)
    if why:
        raise ValueError(why)
    if stage_of(co) not in EDITABLE:
        raise ValueError(f"{co['number']} is {stage_of(co).replace('-', ' ')}: suggestions are settled")
    n, who, s = _find_suggestion(co, sid)
    if s.get("status") != "open":
        raise ValueError(f"suggestion {sid} is {s.get('status')} already")
    now = utcnow()
    if s.get("change"):
        ch = s["change"]
        add_direct_change(hub, co, ch if "op" in ch else ch["rule"], by,
                          origin={"kind": "suggested", "by": s.get("by") or who, "accepted_by": by, "at": now, "suggestion": sid})
    _set_suggestion(hub, _co(hub, co["locator"]), n, who, sid, status="accepted", decided_by=by, decided_at=now)
    return _find_suggestion(_co(hub, co["locator"]), sid)[2]


def reject_suggestion(hub, co_ref, sid: str, by: str, reason: str = "") -> dict:
    co = sync(hub, co_ref)
    why = _may_edit(hub, co, by)
    if why:
        raise ValueError(why)
    n, who, s = _find_suggestion(co, sid)
    if s.get("status") != "open":
        raise ValueError(f"suggestion {sid} is {s.get('status')} already")
    _set_suggestion(hub, co, n, who, sid, status="rejected", decided_by=by, decided_at=utcnow(), reason=reason)
    return _find_suggestion(_co(hub, co["locator"]), sid)[2]


def answer_question(hub, co_ref, qid: str, answer: str, by: str, line: int | None = None) -> dict:
    """Mark a question answered, with the answer (and, best, the line of the prose that answers it). The next compile
    also closes a compiler question that no longer arises. The requester, a document controller or a contributor
    answers."""
    co = sync(hub, co_ref)
    if _may_edit(hub, co, by) and by not in contributors(hub, co):
        raise ValueError(f"only {requester(co)}, a document controller or a contributor answers questions on {co['number']}")
    if not (answer or "").strip():
        raise ValueError("say what the answer is (and, best, which line of the prose says it)")
    upd = {"status": "answered", "answer": answer.strip(), "by": by, "at": utcnow(), "answer_line": line}
    p = _proposal(hub, co)
    for q in (p or {}).get("questions") or []:
        if q["id"] == qid:
            q.update(upd)
            _refresh(hub, co, p)
            hub.proposals.save(p)
            return q
    for r in rounds(co):
        for who, res in (r.get("results") or {}).items():
            for q in res.get("questions") or []:
                if q["id"] == qid:
                    def fn(rr, who=who):
                        for x in rr["results"][who].get("questions") or []:
                            if x["id"] == qid:
                                x.update(upd)
                    _update_round(hub, co, r["n"], fn)
                    _refresh_saved(hub, co)
                    return {**q, **upd}
    raise ValueError(f"{co['number']} has no question {qid}")


# ---------------------------------------------------------------- state maps
def _states(proc: dict | None) -> set[str]:
    return {f"awaiting-{s['id']}" for s in (proc or {}).get("steps") or [] if s.get("kind") == "checkpoint"}


def state_questions(hub, co_ref) -> list[dict]:
    """Where the delta removes a state that items are in (spec 5.2): [{"process", "state", "count", "record_type",
    "answer"}]. Questions of kind "state-map" a compiler left in the proposal are honoured too (missing keys: empty)."""
    co = _co(hub, co_ref)
    p = _proposal(hub, co)
    if p is None:
        return []
    b = _bundle(hub)
    smap = co["fields"].get("state_map") or {}
    old = {r["name"]: r for r in hub.settings_rules() if r.get("kind") == "process"}
    new = {r["name"]: r for r in p.get("rules") or [] if r.get("kind") == "process"}
    out, seen = [], set()
    for c in p.get("changes") or []:
        r = (c.get("rule") or {})
        if c.get("op") == "text" or r.get("kind") != "process" or r.get("name") in seen or r.get("name") not in old:
            continue
        name = r["name"]
        seen.add(name)
        rtype = ((b.get("workflows") or {}).get(name) or {}).get("record_type") or old[name].get("record_type") or old[name].get("object")
        for s in sorted(_states(old[name]) - _states(new.get(name))):
            count = len(hub.kernel.query(type_=rtype, state=s)) if rtype else 0
            if count:
                out.append({"process": name, "state": s, "count": count, "record_type": rtype, "answer": (smap.get(name) or {}).get(s)})
    for q in p.get("questions") or []:
        if "state-map" in (q.get("kind"), q.get("code")) and q.get("process") and q.get("state") and (q["process"], q["state"]) not in {(x["process"], x["state"]) for x in out}:
            out.append({"process": q.get("process"), "state": q.get("state"), "count": q.get("count") or 0, "record_type": q.get("record_type"),
                        "answer": (smap.get(q["process"]) or {}).get(q["state"])})
    return out


def set_state_map(hub, co_ref, process: str, mapping: dict, by: str) -> dict:
    """Where items in a state the delta removes go: {old_state: new_state} for ``process`` (merged into state_map)."""
    co = sync(hub, co_ref)
    why = _may_edit(hub, co, by)
    if why:
        raise ValueError(why)
    smap = dict(co["fields"].get("state_map") or {})
    smap[process] = {**(smap.get(process) or {}), **{k: v for k, v in (mapping or {}).items() if v}}
    return _set(hub, co, by=by, state_map=smap)["fields"]["state_map"]


# ---------------------------------------------------------------- readiness
def _approved_cr(cr: dict) -> bool:
    return cr["state"] not in CR_OPEN and not cr["state"].startswith("awaiting-approve") and cr["state"] not in ("rejected", "cancelled")


def cr_flag_open(hub, cr: dict) -> bool:
    fl = cr["fields"].get("flagged") or {}
    if not fl or fl.get("cleared"):
        return False
    if fl.get("task"):
        t = next((r for r in hub.kernel.query(type_="task") if r["number"] == fl["task"]), None)
        if t is not None and t["state"] in ("done", "closed"):
            return False
    return True


def _cr_reasons(hub, co: dict) -> list[str]:
    out = []
    crs = {r["number"]: r for r in hub.kernel.query(type_="change-request")}
    for n in co["fields"].get("implements") or []:
        cr = crs.get(n)
        if cr is None:
            out.append(f"there is no change request {n}")
        elif not _approved_cr(cr):
            out.append(f"{n} is not approved yet ({cr['state']})")
        elif cr_flag_open(hub, cr):
            fl = cr["fields"]["flagged"]
            out.append(f"{n} is flagged by {fl.get('by')}: {fl.get('reason')}" + (f" (until {fl['task']} is done)" if fl.get("task") else ""))
    return out


def send_reasons(hub, co_ref) -> list[str]:
    """Why the change order may not be sent for approval yet (empty when it may): it changes something; a review
    round has run on exactly what it carries now (the drafts and the direct changes); its delta is compiled against
    the release in force and compiles; no "baseline changed" flag waits for another round; every change request it
    implements is approved and not flagged; its forms are valid. Unresolved findings and questions do not stop it:
    they become warnings at approval."""
    from . import formlayout

    co = _co(hub, co_ref)
    if eco.finished(co):
        return []
    f = co["fields"]
    carried = eco.carried(hub, co)
    items = _items(co)
    if not (carried or items or f.get("form_changes") or f.get("record_type_changes") or f.get("workflow_changes")):
        return ["it changes nothing yet: carry an SOP or policy revision, make a direct change or carry a form"]
    reasons = []
    last = current_round(co)
    if last is None:
        reasons.append("no review round has run yet: request review")
    elif last.get("status") == "compiling":
        reasons.append(f"round {last['n']} is still being compiled (see the automation job)")
    elif last.get("status") == "failed":
        reasons.append(f"round {last['n']} stopped ({last.get('error')}): fix that, then request review again")
    else:
        rec = last.get("reviewed_from") or {}
        drafts = eco.drafts_of(hub, co)
        if rec.get("drafts") is not None and rec["drafts"] != _shas(hub, drafts):
            reasons.append(f"the revisions changed since round {last['n']}: request review again")
        if rec.get("items") is not None and rec["items"] != _sig(items):
            reasons.append(f"the direct changes changed since round {last['n']}: request review again")
        p = _proposal(hub, co)
        if (carried or items) and (p is None or p.get("status") != "open"):
            reasons.append("the delta is not compiled (or its compile was replaced): request review again")
        elif p is not None and (f.get("staged_base") != _digest(hub) or (f.get("staged_from") or {}) != _shas(hub, drafts)):
            reasons.append("the delta was compiled against a release no longer in force: it is being compiled again (or request review again)")
    for d in carried:
        run = hub.runner.run_for_record(d["draft"]["locator"])
        if run and run["status"] in ("submitted", "running", "waiting"):
            reasons.append(f"{d['draft']['number']} is in a {run['process']} run: withdraw it; the change order's approval approves it")
    bc = f.get("baseline_changed") or {}
    if bc and not bc.get("dismissed"):
        reasons.append(f"the baseline changed: {bc.get('by')} was released ({'; '.join(bc.get('reasons') or [])}): run another review round")
    reasons += list(f.get("staged_errors") or [])
    reasons += formlayout.carried_reasons(hub, co)
    try:
        from . import recordtypes  # workstream 4: record-type changes carried on change orders
    except ImportError:
        recordtypes = None
    if recordtypes is not None:
        reasons += recordtypes.carried_reasons(hub, co)
    from . import workflows  # workstream 8: workflow layouts and graph snapshots carried from the editor

    reasons += workflows.carried_reasons(hub, co)
    reasons += _cr_reasons(hub, co)
    reasons += [c["reason"] for c in committee_reviews(hub, co) if c["status"] != "agreed"]
    return reasons


# ---------------------------------------------------------------- committees on the panel (groups and committees)
def committee_reviews(hub, co_ref) -> list[dict]:
    """Each committee on the current round's panel, by consensus (change control 11): ``agreed`` when every member has
    reviewed and none raised a blocking finding (an objection); ``objected`` when one did (it returns for revision: the
    author revises and requests review again); ``waiting`` while a member has not reviewed; ``paused`` when a member
    could not review (its model is not available) or the chair is asked to intervene. A committee on the panel must
    agree before the change order is sent for approval: nothing passes by default. Each: {"committee", "chair",
    "members", "status", "objections": [{"member", "text"}], "waiting", "paused", "reason"}."""
    from .groups import committee

    co = _co(hub, co_ref)
    rnd = current_round(co)
    if rnd is None or rnd.get("status") in ("compiling", "failed"):
        return []
    b = _bundle(hub)
    names = list(dict.fromkeys(e["committee"] for e in rnd.get("panel") or [] if e.get("committee")))
    out = []
    for name in names:
        c = committee(b, name) or {"members": [], "chair": ""}
        members = [e["who"] for e in rnd.get("panel") or [] if e.get("committee") == name]
        res = rnd.get("results") or {}
        objections = [{"member": m, "text": f.get("text")} for m in members for f in (res.get(m) or {}).get("findings") or []
                      if f.get("severity") == "blocking" and (res.get(m) or {}).get("status") == "done"]
        paused = [{"member": m, "why": (res.get(m) or {}).get("error") or "its review did not run"} for m in members
                  if (res.get(m) or {}).get("status") == "failed"]
        waiting = [m for m in members if (res.get(m) or {}).get("status") not in ("done", "failed")]
        chair = c.get("chair") or ""
        if objections:
            status = "objected"
            reason = (f"the {name} committee does not agree: " + "; ".join(f"{o['member']} objects: {o['text']}" for o in objections)
                      + " (revise and request review again)")
        elif paused:
            status = "paused"
            reason = (f"the {name} committee cannot agree: " + "; ".join(f"{p['member']}: {p['why']}" for p in paused)
                      + f"; its chair{(', ' + chair + ',') if chair else ''} intervenes (decide in the member's place, or return it)")
        elif waiting:
            status = "waiting"
            reason = f"the {name} committee has not agreed yet: waiting for {', '.join(waiting)}"
        else:
            status, reason = "agreed", f"the {name} committee agrees ({', '.join(members)})"
        out.append({"committee": name, "chair": chair, "members": members, "status": status, "objections": objections,
                    "waiting": waiting, "paused": paused, "reason": reason})
    return out


def intervene_review(hub, co_ref, committee_name: str, member: str, by: str, meaning: str) -> dict:
    """The chair of a committee on the panel reviews in the place of a member that could not (its model is not
    available): the member's result is recorded as done, with no finding, as an intervention with its meaning. To return
    the change order instead, the chair raises a blocking finding through their own review or the requester revises."""
    from .groups import committee

    co = sync(hub, co_ref)
    rnd = current_round(co)
    c = committee(_bundle(hub), committee_name)
    if rnd is None or c is None:
        raise ValueError(f"the {committee_name} committee is not on the panel of {co['number']}")
    if by != c.get("chair"):
        raise ValueError(f"only the chair of the {committee_name} committee ({c.get('chair') or 'none'}) intervenes")
    if not (meaning or "").strip():
        raise ValueError("an intervention needs its meaning: say why you review in the member's place")
    entry = next((e for e in rnd.get("panel") or [] if e["who"] == member and e.get("committee") == committee_name), None)
    if entry is None:
        raise ValueError(f"{member} is not on the panel for the {committee_name} committee")
    if ((rnd.get("results") or {}).get(member) or {}).get("status") == "done":
        raise ValueError(f"{member} has reviewed already")
    result = {"status": "done", "kind": entry["kind"], "at": utcnow(), "findings": [], "questions": [], "suggestions": [],
              "summary": f"reviewed by {by}, chair of the {committee_name} committee, in {member}'s place: {meaning.strip()}",
              "intervention": {"by": by, "meaning": meaning.strip(), "for": member, "at": utcnow(),
                               "was": ((rnd.get("results") or {}).get(member) or {}).get("error")},
              "reviewed": _snapshot(_proposal(hub, co))}
    _record_result(hub, co, rnd["n"], member, result)
    return result


def approval_reasons(hub, co_ref) -> list[str]:
    """Why the CO Gate may not be signed yet (empty when it may): everything send_reasons checks, every delta item
    decided (a widening by both policy owners), every warning accepted (on the items approved and on the CO as a
    whole), no decision left stale, and a state map answer for every state the delta removes that items are in."""
    co = _co(hub, co_ref)
    if eco.finished(co) and co["state"] != "approved":
        return []
    reasons = send_reasons(hub, co)
    p = _proposal(hub, co)
    if p is not None:
        _refresh(hub, co, p)
        owners = p.get("reviewers") or _owners(hub)
        for c in p.get("changes") or []:
            if c.get("op") == "text":
                continue
            st = item_state(c, owners)
            if st == "pending":
                if c.get("stale"):
                    reasons.append(f"decided before the baseline changed, decide it again: {c.get('reason')}")
                elif _widening(c):
                    have = sorted({d["by"] for d in c.get("decisions") or [] if d["verdict"] == "approve" and not d.get("stale")})
                    reasons.append(f"widening: {c.get('reason')} needs both policy owners" + (f" (approved by {', '.join(have)})" if have else ""))
                else:
                    reasons.append(f"not decided yet: {c.get('reason')}")
            elif st == "approved":
                for w in c.get("warnings") or []:
                    if not w.get("accepted"):
                        reasons.append(f"accept the warning on “{c.get('reason')}”: [{w['code']}] {w['text']}")
        for w in p.get("warnings") or []:
            if not w.get("accepted"):
                reasons.append(f"accept the warning: [{w['code']}] {w['text']}")
    for q in state_questions(hub, co):
        if not q.get("answer"):
            reasons.append(f"{q['count']} {q['record_type']} item(s) are in {q['state']}, which this change removes from {q['process']}: "
                           "say where they go (state map)")
    return reasons


# ---------------------------------------------------------------- sending, deciding, approving
def send_for_approval(hub, co_ref, by: str) -> dict:
    """The requester sends the change order for approval: it must be ready (send_reasons); its contributors are
    recorded (they may not sign it) and the change-order process takes it to the quality manager's Gate. Returns
    the run's state."""
    from .runner import RunError

    co = sync(hub, co_ref)
    if by not in (requester(co), co.get("created_by")) and "document-controller" not in authz.roles_of(_bundle(hub), by):
        raise ValueError(f"only {requester(co)} (its requester) or a document controller sends {co['number']} for approval")
    run = hub.runner.run_for_record(co["locator"])
    if run and run["status"] in ("submitted", "running", "waiting"):
        raise ValueError(f"{co['number']} is already in the change-order process ({run['status']})")
    st = stage_of(co)
    if st == "preparing":
        raise ValueError(f"{co['number']} has not been reviewed: request review first")
    if st not in ("in-review", "revising"):
        raise ValueError(f"{co['number']} is {st.replace('-', ' ')}")
    reasons = send_reasons(hub, co)
    if reasons:
        raise ValueError("it cannot be sent for approval yet: " + "; ".join(reasons))
    _set(hub, co, by=by, stage="awaiting-approval", contributor=contributors(hub, co), rejected=None)
    try:
        run_id = hub.runner.submit("change-order", {"record": {}}, owner=by, record=co["locator"])
    except RunError as e:
        _set(hub, _co(hub, co["locator"]), stage=st)
        raise ValueError(str(e)) from None
    state = hub.runner.load(run_id)
    if state["status"] not in ("waiting", "completed", "running", "submitted"):
        why = next((e.get("reason") for e in reversed(hub.runner.track(run_id)) if e.get("reason")), state["status"])
        _set(hub, _co(hub, co["locator"]), stage=st)
        raise ValueError(f"the change-order process stopped: {why}")
    return state


def _accepts(accept, w: dict) -> bool:
    accept = set(accept or ())
    return bool(accept & {"*", "all", w.get("ref"), w.get("code"), w.get("text")})


def decide_item(hub, co_ref, item_id: str, verdict: str, by: str, meaning: str, accept_warnings=(), override: dict | None = None) -> dict:
    """An approver's decision on one delta item: approve or reject, with a meaning. To approve, every warning the item
    carries must be accepted (``accept_warnings``: refs, codes, or "all"); the acceptance is recorded with the
    decision. ``override`` edits the item's value (a rule, or fields to change in it): it is approved with the item,
    is a warning the approver accepts, and may leave a divergence between configuration and prose. A widening needs
    both policy owners (two decisions). Returns the item."""
    from .guards import consistency_guard, drift_guard

    co = sync(hub, co_ref)
    if eco.finished(co):
        raise ValueError(f"{co['number']} is {co['state']}")
    why = _may_decide(hub, co, by)
    if why:
        raise ValueError(why)
    if verdict not in ("approve", "reject"):
        raise ValueError("the verdict is approve or reject")
    if not (meaning or "").strip():
        raise ValueError("a decision needs its meaning: say in a few words what you are confirming")
    p = _proposal(hub, co)
    if p is None or p.get("status") != "open":
        raise ValueError(f"{co['number']} has no compiled delta to decide: request review first")
    c = next((x for x in p.get("changes") or [] if x["id"] == item_id), None)
    if c is None:
        raise ValueError(f"{co['number']} has no item {item_id}")
    if c.get("op") == "text":
        raise ValueError("a wording change is the revision itself: the change order's approval approves it")
    now = utcnow()
    acc = dict(p.get("acceptances") or {})
    if override is not None:
        if verdict != "approve":
            raise ValueError("an override is approved with the item: approve it")
        new = dict(override) if override.get("kind") else {**c["rule"], **override}
        new = {k: v for k, v in new.items() if k != "source"}
        new["source"] = {**((c["rule"].get("source")) or {}), "override": {"by": by, "at": now, "co": co["number"]}}
        problems = validate_rule(new)
        if problems:
            raise ValueError("; ".join(problems))
        if rule_key(new) == rule_key(c["rule"]):
            raise ValueError("that override is the item as it stands")
        base = hub.settings_rules()
        target = _apply(base, [{"op": "add", "rule": new, **({"changes_from": c["changes_from"]} if c.get("changes_from") else {})}]
                        + ([{"op": "remove", "rule": c["rule"]}] if c.get("op") == "remove" else []))
        errs = [x for x in consistency_guard(target, hub.registry.ids(), hub.kernel.types) if x.severity == "error" and x.rule_key == rule_key(new)]
        if errs:
            raise ValueError("the override does not hold: " + "; ".join(x.detail for x in errs[:3]))
        widen = any(d.get("widening") for d in drift_guard(base, target) if d["op"] == "add" and rule_key(d["rule"]) == rule_key(new))
        c["override"] = {"rule": new, "by": by, "at": now, "meaning": meaning, "widening": widen}
        c["decisions"] = [d for d in c.get("decisions") or [] if d["by"] == by]
        acc[_wkey(item_id, {"code": "override", "ref": f"ov:{item_id}"})] = {"by": by, "meaning": meaning, "at": now}
        p["acceptances"] = acc
    _refresh(hub, co, p)
    taken = []
    if verdict == "approve":
        missing = [w for w in c["warnings"] if not w.get("accepted") and not _accepts(accept_warnings, w)]
        if missing:
            raise ValueError("accept each warning to approve it (“I accept: …”): " + "; ".join(f"[{w['code']}] {w['text']}" for w in missing))
        for w in c["warnings"]:
            if not w.get("accepted"):
                acc[_wkey(item_id, w)] = {"by": by, "meaning": meaning, "at": now}
            taken.append(w.get("ref"))
    c["decisions"] = [d for d in c.get("decisions") or [] if d["by"] != by] + [
        {"by": by, "verdict": verdict, "at": now, "meaning": meaning, "change_digest": rule_key(_effective(c)), "accepted_warnings": taken}]
    p["acceptances"] = acc
    _refresh(hub, co, p)
    hub.proposals.save(p)
    return {**c, "state": item_state(c, p.get("reviewers") or _owners(hub))}


def accept_warning(hub, co_ref, ref: str, by: str, meaning: str) -> dict:
    """Accept a warning on the change order as a whole (a kept divergence, a finding or question about no single
    item): an approver ticks "I accept: ..."; recorded with who, when and the meaning."""
    co = sync(hub, co_ref)
    why = _may_decide(hub, co, by)
    if why:
        raise ValueError(why)
    if not (meaning or "").strip():
        raise ValueError("say what you accept, in a few words")
    p = _proposal(hub, co)
    if p is None:
        raise ValueError(f"{co['number']} has no compiled delta")
    _refresh(hub, co, p)
    w = next((x for x in p.get("warnings") or [] if x.get("ref") == ref), None)
    if w is None:
        raise ValueError(f"{co['number']} has no warning {ref}")
    p.setdefault("acceptances", {})[_wkey("co", w)] = {"by": by, "meaning": meaning, "at": utcnow()}
    _refresh(hub, co, p)
    hub.proposals.save(p)
    return next(x for x in p["warnings"] if x.get("ref") == ref)


def approve_revisions(hub, co_ref) -> list[str]:
    """Signing the CO approves every revision it carries (document control's approval moves into this step): the
    approver's signature is recorded on each revision as its document-control approval, which makes it approved and
    held for the change order, so publish_revisions can make it effective. Refuses (segregation of duties) when the
    approver contributed to the change order or wrote the revision."""
    co = _co(hub, co_ref)
    run = hub.runner.run_for_record(co["locator"])
    state = hub.runner.load(run["id"]) if run else None
    decision = next((d for d in reversed(co.get("decisions") or []) if d.get("verdict") == "approve" and (not run or d.get("run_id") == run["id"])), None)
    if state is None or decision is None:
        raise ValueError(f"{co['number']} has no approval to record on its revisions")
    approver = decision["by"]
    if approver in contributors(hub, co):
        raise ValueError(f"segregation of duties: {approver} contributed to {co['number']} and may not approve it "
                         f"({CHANGE_CONTROL_SOP}: “{SOD_SENTENCE}”)")
    grant = state.get("grant") or {}
    done = []
    for c in eco.carried(hub, co):
        d = hub.kernel.get(c["draft"]["locator"])
        f = d["fields"]
        if d["state"] == "effective" or (d["state"] == "approved" and f.get("held_for") == co["number"] and f.get("approved_run")):
            done.append(d["number"])
            continue
        if approver in _version_authors(hub, d):
            raise ValueError(f"segregation of duties: {approver} wrote {d['number']} and may not approve it (document-control-sop)")
        sig = {k: decision.get(k) for k in ("by", "by_type", "verdict", "meaning", "at", "payload_digest", "signature", "run_id", "step", "release_digest")}
        hub.kernel.append("record.decision", d["locator"], {**sig, "approves": d["number"], "via": co["number"],
                                                             "meaning": f"approved with {co['number']}: {decision.get('meaning') or ''}".strip()},
                          run_id=state["id"], release=state["release"], by=approver)
        hub.kernel.set_fields(d["locator"], {"held_for": co["number"], "approved_by": approver, "approved_run": state["id"], "approved_grant": grant.get("id"),
                                             "approved_release": state["release"], "approved_with": co["number"]},
                              by="hub", run_id=state["id"], release=state["release"])
        if d["state"] != "approved":
            hub.kernel.transition(d["locator"], "approved", by="hub", run_id=state["id"], release=state["release"],
                                  reason=f"approved with {co['number']} by {approver}")
        done.append(d["number"])
    return done


# ---------------------------------------------------------------- release: the decided delta, divergences
def apply_delta(hub, co_ref) -> dict:
    """Before the release: the decided delta goes into the staged proposal's rules. Stale decisions are set aside; an
    approved override replaces its rule; an approved direct change the prose does not state is applied; such items
    leave the proposal's changes (``applied_items``) so the compiler's own release logic sees only prose changes. What
    the prose states is kept as ``prose_rules`` (divergences are measured against it). Safe to repeat."""
    co = _co(hub, co_ref)
    p = _proposal(hub, co)
    if p is None or p.get("applied") or p.get("status") != "open":
        return {"overrides": 0, "direct": 0}
    owners = p.get("reviewers") or _owners(hub)
    p["prose_rules"] = copy.deepcopy(p["rules"])
    rules = {rule_key(r): r for r in p["rules"]}
    applied, keep, n_over, n_direct = [], [], 0, 0
    for c in p.get("changes") or []:
        if c.get("op") == "text":
            keep.append(c)
            continue
        c["stale_decisions"] = [d for d in c.get("decisions") or [] if d.get("stale")]
        c["decisions"] = [d for d in c.get("decisions") or [] if not d.get("stale")]
        st = item_state(c, owners)
        if c.get("unstated"):
            if st == "approved":
                old, new = _old_new(c)
                _with(rules, old, (c.get("override") or {}).get("rule") or new)
                n_direct += 1
            applied.append({**c, "state": st})
            continue
        if c.get("override") and st == "approved":
            rules.pop(rule_key(c["rule"]), None)
            rules[rule_key(c["override"]["rule"])] = c["override"]["rule"]
            n_over += 1
        keep.append(c)
    p["rules"] = list(rules.values())
    p["changes"] = keep
    p["applied_items"] = applied
    p["applied"] = utcnow()
    hub.proposals.save(p)
    return {"overrides": n_over, "direct": n_direct}


def open_divergences(hub, digest: str | None = None) -> list[dict]:
    """The divergences open in a release (the release in force by default)."""
    try:
        meta = hub.releases.get(digest) if digest else hub.releases.current()
    except Exception:
        meta = None
    return [d for d in (meta or {}).get("divergences") or [] if d.get("status", "open") == "open"]


def _kept_words(d: dict) -> str:
    where = f"{d.get('frame')} line {d.get('line')}" if d.get("line") else (d.get("frame") or "the SOP")
    return (f"divergence {d['id']}: in force is “{_sentence(d.get('in_force')) or 'nothing'}” while {where} says "
            f"“{d.get('prose') or 'nothing'}” (accepted by {d.get('accepted_by') or '-'} on {d.get('co') or '-'}); it stays until a change order resolves it")


def keep_divergences(rules: list[dict], base: dict | None):
    """For the compiler (hubproto.compile): an open divergence keeps its rule in force for its slot while the prose
    still says what it said, with a warning naming the divergence; prose that now says something else (or the rule
    in force) resolves it. Returns (rules, [Finding])."""
    divs = [d for d in (base or {}).get("divergences") or [] if d.get("status", "open") == "open"]
    if not divs:
        return rules, []
    from .guards import Finding

    out = list(rules)
    found = []
    for d in divs:
        prose, in_force = d.get("prose_rule"), d.get("in_force")
        keys = {rule_key(r) for r in out}
        slot = d.get("slot")
        in_slot = [r for r in out if _slot_key(r) == slot]
        if prose is not None and rule_key(prose) in keys:
            out = [r for r in out if rule_key(r) != rule_key(prose)] + ([in_force] if in_force else [])
        elif prose is None and in_force is not None and not in_slot:
            out.append(in_force)
        else:
            continue  # the prose changed (or states what is in force): the change order resolves it
        found.append(Finding("divergence", "warning", _kept_words(d), rule_key=f"divergence:{d['id']}", frame=d.get("frame") or "",
                             line=int(d.get("line") or 0)))
    return out, found


def _annotate_release(hub, digest: str, **extra) -> dict:
    rs = hub.releases
    index = rs._index()
    meta = None
    for m in index:
        if m["digest"] == digest:
            m.update(extra)
            meta = m
    rs.index_path.write_text(json.dumps(index, indent=1))
    path = rs.root / digest / "release.json"
    if path.exists() and meta is not None:
        path.write_text(json.dumps(meta, indent=1))
    return meta or {}


def record_release(hub, co_ref, digest: str) -> dict:
    """After the release: the release meta records which change order released it and its divergences: the open
    ones of the release before that this compile kept, plus a new one for every slot where the configuration now
    differs from what the prose states (an override, a rejected prose change, an approved direct change the prose does
    not state). A divergence: {id, frame, line, prose, prose_rule, in_force, slot, co, accepted_by, meaning, at,
    status}. Returns {"new", "kept", "resolved"} (ids)."""
    co = _co(hub, co_ref)
    meta = hub.releases.get(digest)
    if meta.get("change_order") == co["number"]:
        return {"new": [d["id"] for d in meta.get("divergences") or [] if d.get("co") == co["number"]], "kept": [], "resolved": meta.get("divergences_resolved") or []}
    p = _proposal(hub, co)
    parent = meta.get("parent")
    if p is None or meta.get("proposal_id") != p["id"]:
        return {"new": [], "kept": [], "resolved": []}  # no new release (forms only): nothing to record
    before = open_divergences(hub, parent) if parent else []
    kept_ids = {str(f.get("rule_key") or "").split(":", 1)[-1] for f in p.get("findings") or [] if f.get("guard") == "divergence"}
    kept = [d for d in before if d["id"] in kept_ids]
    resolved = [d["id"] for d in before if d["id"] not in kept_ids]
    final = hub.releases.rules(digest)
    prose = p.get("prose_rules") or p.get("rules") or []
    fin, pro = {}, {}
    for r in final:
        fin.setdefault(_slot_key(r), {})[rule_key(r)] = r
    for r in prose:
        pro.setdefault(_slot_key(r), {})[rule_key(r)] = r
    kept_slots = {d.get("slot") for d in kept}
    items = list(p.get("changes") or []) + list(p.get("applied_items") or [])
    new = []
    for slot in sorted(set(fin) | set(pro)):
        if set(fin.get(slot, {})) == set(pro.get(slot, {})) or slot in kept_slots:
            continue
        pr = next(iter(pro.get(slot, {}).values()), None)
        inf = next(iter(fin.get(slot, {}).values()), None)
        cause = next((c for c in items if c.get("op") != "text" and any(_slot_key(r) == slot for r in (c["rule"], _effective(c)))), None)
        dec = next((d for d in reversed((cause or {}).get("decisions") or []) if not d.get("stale")), {})
        src = ((pr or {}).get("source") or {}) if pr else ((inf or {}).get("source") or {})
        frame = src.get("frame") if src.get("frame") not in (None, "", "settings") else ((cause or {}).get("origin") or {}).get("frame")
        new.append({"id": f"{co['number']}/D{len(new) + 1}", "frame": frame, "line": src.get("line") if pr else None,
                    "prose": (pr or {}).get("source", {}).get("text") if pr else "", "prose_rule": pr, "in_force": inf, "slot": slot,
                    "co": co["number"], "accepted_by": ((cause or {}).get("override") or {}).get("by") or dec.get("by"),
                    "meaning": ((cause or {}).get("override") or {}).get("meaning") or dec.get("meaning"), "at": utcnow(), "status": "open",
                    "item": (cause or {}).get("id")})
    _annotate_release(hub, digest, change_order=co["number"], divergences=kept + new, divergences_resolved=resolved)
    return {"new": [d["id"] for d in new], "kept": [d["id"] for d in kept], "resolved": resolved}


def raise_divergence_requests(hub, co_ref, digest: str) -> list[str]:
    """A change request for each new divergence, to the SOP's owner: "bring <sop> line n in line with what is in
    force". Safe to repeat. Returns the change requests' numbers."""
    from . import sopdocs

    co = _co(hub, co_ref)
    meta = hub.releases.get(digest)
    divs = list(meta.get("divergences") or [])
    out = []
    for d in divs:
        if d.get("co") != co["number"]:
            continue
        if d.get("change_request"):
            out.append(d["change_request"])
            continue
        doc = sopdocs.in_force(hub.kernel, d.get("frame") or "") if d.get("frame") else None
        owner = (doc or {}).get("fields", {}).get("owner")
        if not owner:
            try:
                owner = hub.released_frames().get(d.get("frame")).maintainer
            except Exception:
                owner = None
        if not owner or owner not in (_bundle(hub).get("members") or {}):
            owner = requester(co)
        where = f"{d.get('frame')} line {d['line']}" if d.get("line") else (d.get("frame") or "the SOP")
        cr = hub.kernel.create("change-request", "Quality/Changes", {
            "title": f"Bring {where} in line with what is in force",
            "description": f"{co['number']} released a configuration that differs from the prose ({d['id']}): in force is "
                           f"“{_sentence(d.get('in_force')) or 'nothing'}”, the SOP says “{d.get('prose') or 'nothing'}”. "
                           f"Accepted by {d.get('accepted_by') or '-'}: {d.get('meaning') or ''}",
            "impact": f"{d.get('frame') or 'the SOP'}: its text, so that it states the rule in force",
            "affected_documents": [d.get("frame")] if d.get("frame") else [], "requested_by": owner, "divergence": d["id"]}, by="hub")
        d["change_request"] = cr["number"]
        out.append(cr["number"])
        hub.inbox.notify(owner, kind="task", subject=f"{cr['number']}: bring {where} in line with what is in force",
                         body=f"{co['number']} was released with a divergence ({d['id']}). Raise the change through a change order.",
                         record=cr["locator"])
    if out:
        _annotate_release(hub, digest, divergences=divs)
    return out


# ---------------------------------------------------------------- release: every open CO, related changes
def open_change_orders(hub, exclude: str | None = None) -> list[dict]:
    """Every change order not yet finished (preparing, in review, revising or waiting at its Gate)."""
    return [r for r in hub.kernel.query(type_="change-order") if r["locator"] != exclude and not eco.finished(r)]


def _overlap(a: dict, b: dict) -> list[str]:
    out = []
    for key, words in (("sop", "both change {x}"), ("process", "both change the {x} process"), ("record_type", "both change the {x} record type"),
                       ("form", "both change the {x} form"), ("role", "both change the {x} role")):
        for x in sorted(a[key] & b[key]):
            out.append(words.format(x=x))
    for s in sorted(a["slot"] & b["slot"]):
        try:
            v = json.loads(s)
            out.append(f"both change the same {v[0]} rule ({', '.join(str(x) for x in v[1:] if x not in (None, 'None'))})")
        except Exception:
            out.append("both change the same rule")
    return list(dict.fromkeys(out))


def _approved_uncarried_crs(hub) -> list[dict]:
    carried = {n for co in open_change_orders(hub) for n in co["fields"].get("implements") or []}
    return [cr for cr in hub.kernel.query(type_="change-request") if _approved_cr(cr) and cr["state"] != "closed" and cr["number"] not in carried]


def _meaning(hub, cog: str, released: dict, candidates: list[dict]) -> list[tuple[str, str]]:
    if not candidates:
        return []
    if MEANING is not None:
        return list(MEANING(hub, cog, released, candidates) or [])
    prof = (_bundle(hub).get("cogs") or {}).get(cog) or {}
    lines = [f"{released['number']} was just released: {released['fields'].get('title') or ''}. {released['fields'].get('description') or ''}",
             "Which of these open change orders and approved change requests does it affect in your area? One line each: "
             "'- <number>: <reason>'. Nothing else; none is a fine answer."]
    for c in candidates:
        lines.append(f"- {c['number']} ({c['type']}): {c['fields'].get('title') or ''}. {(c['fields'].get('description') or '')[:300]}")
    try:
        out = hub.model.complete(model=prof.get("model") or "", system=prof.get("prompt") or f"You are {cog}.", user="\n".join(lines), by=cog,
                                 max_tokens=500)
    except Exception:
        return []
    if out.get("stand_in"):
        return []  # the stand-in finds no relation by meaning
    numbers = {c["number"] for c in candidates}
    found = []
    for line in (out.get("text") or "").splitlines():
        m = re.match(r"^\s*[-*]\s*([A-Z]+-[\w-]+)\s*:\s*(.+)$", line)
        if m and m.group(1) in numbers:
            found.append((m.group(1), m.group(2).strip()))
    return found


def _flag_co(hub, other: dict, released: dict, kind: str, reasons: list[str], cog: str | None = None) -> None:
    f = other["fields"]
    cur = f.get("baseline_changed") or {}
    if cur and not cur.get("dismissed") and cur.get("by") == released["number"]:
        kind = "definition" if "definition" in (kind, cur.get("kind")) else kind
        reasons = list(dict.fromkeys(list(cur.get("reasons") or []) + reasons))
    entry = {"by": released["number"], "kind": kind, "reasons": reasons, "cog": cog, "at": utcnow(), "dismissed": None}
    upd: dict = {"baseline_changed": entry}
    if stage_of(other) in ("in-review", "revising", "awaiting-approval"):
        upd["stage"] = "revising"
    rs = rounds(other)
    if rs:
        rs[-1]["stale"] = True
        for res in (rs[-1].get("results") or {}).values():
            res["stale"] = True
        upd["rounds"] = rs
    _set(hub, other, **upd)
    p = _proposal(hub, other)
    if p is not None:
        for c in p.get("changes") or []:
            for d in c.get("decisions") or []:
                d["stale"] = True
        _refresh(hub, _co(hub, other["locator"]), p)
        hub.proposals.save(p)
    who = requester(other)
    if who:
        hub.inbox.notify(who, kind="automation", subject=f"{released['number']} was released: {other['number']} goes back to revising",
                         body=f"Related {'by definition' if kind == 'definition' else 'by meaning' + (f' ({cog})' if cog else '')}: {'; '.join(reasons)}. "
                              "Its reviews and item decisions are stale: run another review round before sending it for approval."
                              + (" You may dismiss this flag with a reason, accepting a warning." if kind == "meaning" else ""),
                         record=other["locator"])
    run = hub.runner.run_for_record(other["locator"])
    if run and run["status"] == "waiting":
        for a in (run.get("pending") or {}).get("assignees") or []:
            hub.inbox.notify(a, kind="automation", subject=f"{other['number']} cannot be signed now",
                             body=f"{released['number']} was released and touches what it changes. Another review round must run first: reject it to "
                                  "send it back, or wait for the requester.", record=other["locator"])


def _flag_cr(hub, cr: dict, released: dict, cog: str, reason: str) -> None:
    approver = next((d["by"] for d in reversed(cr.get("decisions") or []) if d.get("verdict") == "approve"), None) or cr["fields"].get("requested_by")
    task = None
    for owner in (cog, approver):
        try:
            run_id = hub.runner.submit("task", {"record": {"title": f"Review {cr['number']} against {released['number']}",
                                                           "description": f"{cog}: {reason}", "assignee": approver}}, owner=owner)
            task = hub.kernel.get(hub.runner.load(run_id)["record"])
            hub.kernel.link(task["locator"], "references", cr["locator"], by=owner)
            break
        except Exception:
            continue
    hub.kernel.set_fields(cr["locator"], {"flagged": {"by": released["number"], "cog": cog, "reason": reason, "at": utcnow(), "approver": approver,
                                                      "task": task["number"] if task else None, "cleared": None}}, by="hub")
    if approver:
        hub.inbox.notify(approver, kind="task", subject=f"{cr['number']} may be affected by {released['number']}",
                         body=f"{cog}: {reason}. Review it again" + (f" ({task['number']})" if task else "")
                              + "; no change order implementing it may be approved until you have.", record=cr["locator"])


def find_related(hub, co_ref) -> dict:
    """After a release: every open change order related to the released one is sent back. Related by definition (it
    touches the same SOP, process, rule slot, record type, form or role: cannot be dismissed); related by meaning (a
    cog of the released CO's panel, in its area, finds it so; its stand-in finds none). A related CO goes back to
    revising with a ``baseline_changed`` flag, its review results and item decisions stale. An approved change request
    no change order carries yet, found related by meaning, is flagged and its approver given a review task. Returns
    {"cos": [{"number", "kind", "reasons"}], "crs": [{"number", "cog", "reason"}]}."""
    co = _co(hub, co_ref)
    mine = touches(hub, co)
    out: dict = {"cos": [], "crs": []}
    others = open_change_orders(hub, exclude=co["locator"])
    flagged = set()
    for o in others:
        reasons = _overlap(mine, touches(hub, o))
        if reasons:
            _flag_co(hub, o, co, "definition", reasons)
            out["cos"].append({"number": o["number"], "kind": "definition", "reasons": reasons})
            flagged.add(o["number"])
    panel = [e["who"] for e in (current_round(co) or {}).get("panel") or [] if e.get("kind") == "cog" and e.get("member")]
    crs = _approved_uncarried_crs(hub)
    for cog in panel:
        cands = [o for o in others if o["number"] not in flagged] + [c for c in crs if not cr_flag_open(hub, c)]
        for number, reason in _meaning(hub, cog, co, cands):
            target = next((c for c in cands if c["number"] == number), None)
            if target is None:
                continue
            if target["type"] == "change-order":
                _flag_co(hub, _co(hub, target["locator"]), co, "meaning", [f"{cog}: {reason}"], cog=cog)
                out["cos"].append({"number": number, "kind": "meaning", "reasons": [reason], "cog": cog})
                flagged.add(number)
            else:
                _flag_cr(hub, target, co, cog, reason)
                out["crs"].append({"number": number, "cog": cog, "reason": reason})
    return out


def dismiss_related(hub, co_ref, by: str, reason: str, meaning: str) -> dict:
    """The requester dismisses a "related by meaning" flag, with a reason; the flag becomes a warning they accept
    (recorded on the delta), and the reviews and decisions it made stale count again. A flag related by definition
    cannot be dismissed: run another round."""
    co = sync(hub, co_ref)
    if by != requester(co):
        raise ValueError(f"only {requester(co)} (its requester) dismisses this flag")
    bc = co["fields"].get("baseline_changed") or {}
    if not bc:
        raise ValueError(f"{co['number']} has no related-change flag")
    if bc.get("kind") != "meaning":
        raise ValueError(f"{co['number']} is related to {bc.get('by')} by definition ({'; '.join(bc.get('reasons') or [])}): that cannot be "
                         "dismissed; run another review round")
    if not (reason or "").strip() or not (meaning or "").strip():
        raise ValueError("say why it is not related, and what you accept")
    now = utcnow()
    bc = {**bc, "dismissed": {"by": by, "reason": reason, "meaning": meaning, "at": now}}
    rs = rounds(co)
    if rs:
        rs[-1]["stale"] = False
        for res in (rs[-1].get("results") or {}).values():
            res["stale"] = False
    co = _set(hub, co, by=by, baseline_changed=bc, rounds=rs)
    p = _proposal(hub, co)
    if p is not None:
        for c in p.get("changes") or []:
            for d in c.get("decisions") or []:
                d.pop("stale", None)
        p.setdefault("acceptances", {})[f"co|related|related:{bc.get('by')}"] = {"by": by, "meaning": meaning, "at": now}
        _refresh(hub, co, p)
        hub.proposals.save(p)
    return bc


def clear_cr_flag(hub, cr_ref: str, by: str, note: str) -> dict:
    """The approver of a flagged change request has reviewed it again: the flag is cleared (its task, if any, is
    closed by completing it in the task process)."""
    cr = hub.kernel.get(cr_ref) if str(cr_ref).startswith("rec:") else next((r for r in hub.kernel.query(type_="change-request") if r["number"] == cr_ref), None)
    if cr is None:
        raise ValueError(f"there is no change request {cr_ref}")
    fl = cr["fields"].get("flagged") or {}
    if not fl:
        raise ValueError(f"{cr['number']} is not flagged")
    b = _bundle(hub)
    if by != fl.get("approver") and "quality-manager" not in authz.roles_of(b, by):
        raise ValueError(f"only {fl.get('approver')} (who approved it) or a quality manager clears the flag on {cr['number']}")
    return hub.kernel.set_fields(cr["locator"], {"flagged": {**fl, "cleared": {"by": by, "note": note, "at": utcnow()}}}, by=by)


# ---------------------------------------------------------------- the page in one call
def overview(hub, co_ref) -> dict:
    """Everything a change-order page shows: {"co", "stage", "rounds", "round", "panel", "delta", "warnings",
    "questions", "findings", "suggestions", "send_reasons", "approval_reasons", "contributors", "baseline_changed",
    "state_questions", "proposal", "committees"} (``committees``: each committee on the panel and where its consensus stands)."""
    co = sync(hub, co_ref)
    rnd = current_round(co)
    p = _proposal(hub, co)
    return {"co": co, "stage": stage_of(co), "rounds": rounds(co), "round": rnd, "panel": (rnd or {}).get("panel") or [],
            "delta": delta(hub, co), "warnings": co_warnings(hub, co), "questions": questions(hub, co), "findings": findings(hub, co),
            "suggestions": suggestions(hub, co), "send_reasons": send_reasons(hub, co), "approval_reasons": approval_reasons(hub, co),
            "contributors": contributors(hub, co), "baseline_changed": co["fields"].get("baseline_changed"),
            "state_questions": state_questions(hub, co), "proposal": p, "committees": committee_reviews(hub, co)}
