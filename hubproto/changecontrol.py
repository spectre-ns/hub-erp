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
    in_force_from(hub, co)                              what it already brought into force (a release that stopped part-way)

The loop
    request_review(hub, co, by, panel=None)             start a round; ``panel`` (names or {"who", "reason"}) replaces the suggestion
    add_reviewer(hub, co, who, by, reason="") / remove_reviewer(hub, co, who, by)
    submit_review(hub, co, who, findings=(), questions=(), suggestions=(), summary="")
    intervene_review(hub, co, committee, member, by, meaning)  the chair reviews in place of a member that could not
    cog_review(hub, co, n, cog)                         a cog's review of round n (the co-review queue task)
    accept_suggestion(hub, co, sid, by) / reject_suggestion(hub, co, sid, by, reason="")
    answer_question(hub, co, qid, answer, by, line=None)
    add_direct_change(hub, co, rule_or_change, by) / remove_direct_change(hub, co, item_id, by)
    rules_to_change_order(hub, user, new_rules, reason="", co_ref=None)   Settings: direct changes on the person's settings CO
    settings_change_order(hub, user) / rule_changes(base, new_rules)       that CO; the direct changes a new rule set makes
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
DIVERGENCE_DAYS = 30  # a divergence's change request is due within the change-control SLA, else this
COMMITTEE_CODES = ("member-unavailable", "disagreement", "outside-diff", "unread-rule")  # compile-committee questions: its chair (or a system administrator) answers

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


def _human_intervener(hub, chair: str | None) -> bool:
    """Whether the release in force has a person to answer for the compile committee: a person as its chair, or a person
    holding the system-administrator role (a cog in either place does not count)."""
    b = _bundle(hub)
    members = b.get("members") or {}
    if chair and (members.get(chair) or {}).get("type") == "person":
        return True
    return any(m.get("type") == "person" and "system-administrator" in authz.roles_of(b, w) for w, m in members.items())


def _question_items(p: dict | None, q: dict) -> list[str]:
    """The delta items a question is about: the one it names, or those read from the line it asks about."""
    out = []
    for c in (p or {}).get("changes") or []:
        o = c.get("origin") or {}
        if c["id"] == q.get("item") or (q.get("line") and o.get("frame") == q.get("frame") and o.get("line") == q.get("line")):
            out.append(c["id"])
    return out


def _person(hub, who: str) -> bool:
    return ((_bundle(hub).get("members") or {}).get(who) or {}).get("type") == "person"


def _sentence(rule: dict | None) -> str:
    if not rule:
        return ""
    if rule.get("kind") == "process" and rule.get("statuses"):  # a workflow in words is its summary, not its whole text
        return f"the {rule.get('name')} workflow ({len(rule.get('statuses') or [])} statuses, {len(rule.get('transitions') or [])} transitions)"
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


# ---------------------------------------------------------------- public names for the shell
def set_details(hub, co_ref, by: str, *, title: str | None = None, description: str | None = None) -> dict:
    """The change order's title and what it changes and why, set while it is prepared or revised by whoever may change it
    (``may_edit``). The change-order process needs the description before approval."""
    co = _co(hub, co_ref)
    why = may_edit(hub, co, by)
    if why:
        raise ValueError(why)
    if stage_of(co) not in EDITABLE:
        raise ValueError(f"{co['number']} is {stage_of(co).replace('-', ' ')}: its title and description are fixed now")
    fields = {}
    if title is not None:
        if not title.strip():
            raise ValueError("a change order needs a title")
        fields["title"] = title.strip()
    if description is not None:
        if not description.strip():
            raise ValueError("say what it changes and why")
        fields["description"] = description.strip()
    if fields:
        hub.kernel.set_fields(co["locator"], fields, by=by)
    return _co(hub, co["locator"])


def may_edit(hub, co: dict, by: str) -> str:
    """Empty when ``by`` may change the change order (its requester, who prepared it, or a document controller);
    otherwise why not."""
    return _may_edit(hub, co, by)


def may_decide(hub, co: dict, by: str) -> str:
    """Empty when ``by`` may decide delta items (a person who is a policy owner or may approve change orders); otherwise
    why not. Who made an item never decides that item (``decide_item`` says so)."""
    return _may_decide(hub, co, by)


def owners(hub) -> list[str]:
    """The policy owners (people) of the release in force."""
    return _owners(hub)


def items(co: dict) -> list[dict]:
    """The change order's direct changes (and accepted suggestions), as stored on it."""
    return _items(co)


def old_new(item: dict) -> tuple[dict | None, dict | None]:
    """(the rule an item takes out or changes, the rule it puts in)."""
    return _old_new(item)


def apply_items(rules: list[dict], its: list[dict]) -> list[dict]:
    """``rules`` with the items applied."""
    return _apply(rules, its)


def effective(c: dict) -> dict:
    """The rule a delta item brings into force (its override, when it has one)."""
    return _effective(c)


def widening(c: dict) -> bool:
    """Whether a delta item widens anyone's authority (its override's widening, when it has one)."""
    return _widening(c)


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
    sends it back to revising (the record is open again, the rejection kept in ``rejected``); anything it carries that
    changed after it was sent for approval (a revision, a direct, form, record-type or workflow change, the state map)
    sends it back to revising too, its decisions void (``_back_to_revising``)."""
    co = _co(hub, co_ref)
    eco.withdraw_cancelled_drafts(hub, co)
    if co["state"] == "rejected":
        dec = next((d for d in reversed(co.get("decisions") or []) if d.get("verdict") == "reject"), {})
        hub.kernel.transition(co["locator"], "open", by="hub", reason="its approval was rejected: back to revising")
        _hold_drafts(hub, co, False)
        co = _set(hub, co, stage="revising", frozen=None, rejected={"by": dec.get("by"), "meaning": dec.get("meaning"), "at": dec.get("at")})
    elif stage_of(co) == "awaiting-approval" and co["fields"].get("frozen"):
        changed = changed_since_sent(hub, co)
        if changed:
            co = _back_to_revising(hub, co, "changed after it was sent for approval: " + ", ".join(changed))
    return co


def on_approved(hub, co_ref) -> dict:
    """The CO Gate was signed (hubproto.automation calls this before the release starts): what was signed is recorded
    (``signed``: the frozen content, the delta seal, the state map), so the release applies exactly that."""
    co = _co(hub, co_ref)
    f = co["fields"]
    dec = next((d for d in reversed(co.get("decisions") or []) if d.get("verdict") == "approve"), {})
    return _set(hub, co, stage="approved", state_map_signed=copy.deepcopy(f.get("state_map") or {}),
                signed={"frozen": f.get("frozen"), "seal": f.get("delta_seal"), "by": dec.get("by"), "by_type": dec.get("by_type"),
                        "at": dec.get("at"), "signature": dec.get("signature")})


def in_force_from(hub, co_ref) -> list[str]:
    """What this change order already brought into force, in words (empty when nothing is: its release has not run,
    or stopped before any step applied anything): its policy release, the revisions it published and the documents it
    retired, its form and workflow layouts, its link-vocabulary and retention versions, its record-type version. Each is
    found by the change order's number on what is in force or in its history, as the release steps find it."""
    from . import configdocs, formlayout, recordtypes, workflows

    co = _co(hub, co_ref)
    f = co["fields"]
    n = co["number"]
    out = []
    if f.get("released_release"):
        out.append(f"the policy release {f['released_release'][:12]}")
    for c in eco.carried(hub, co):
        d = hub.kernel.get(c["draft"]["locator"])
        if d and d["state"] == "effective":
            out.append(f"{d['number']} published")
    for name, r in eco.retirements(co).items():
        old = hub.kernel.get(r["locator"]) if r.get("locator") else None
        if old and old["state"] == "obsolete" and f.get("released_release"):
            out.append(f"{old['number']} retired")

    def mine(entries) -> dict | None:
        return next((e for e in reversed([e for e in entries if e]) if e.get("released_with") == n), None)

    for type_ in sorted(f.get("form_changes") or {}):
        e = mine([*hub.forms.history(type_), formlayout.active_entry(hub, type_)])
        if e:
            out.append(f"the {type_.replace('-', ' ')} form v{e['version']}")
    if workflows.changes_of(co):
        ws = workflows.store(hub)
        for process in sorted(workflows.changes_of(co)):
            e = mine([*ws.history(process), ws.layouts().get(process)])
            if e:
                out.append(f"the {process} workflow layout v{e['version']}")
    for doc in sorted({c.get("doc") for c in configdocs.changes_of(co) if c.get("doc") in configdocs.DOCS}):
        e = configdocs.released_by(hub, doc, co)
        if e:
            out.append(f"{configdocs.WORDS[doc]} v{e['version']}")
    if recordtypes.changes_of(co):
        rs = recordtypes.store(hub)
        e = mine([*rs.history(), rs.active()])
        if e:
            out.append(f"record types v{e['version']}")
    return out


def in_force_refusal(co: dict, parts: list[str]) -> str:
    """Why a change order part of which is in force is not sent back to revising: what is in force, and what to do."""
    return (f"{co['number']} cannot go back to revising: part of it is already in force ({', '.join(parts)}). Retry its release to bring "
            "the rest into force as signed, or put the rest of the change on a new change order")


def _refuse_if_in_force(hub, co: dict) -> None:
    """A signed change order that a release has partly brought into force is never sent back to revising (issue #53): it
    would be signed again with other content, and what is already in force from it would stay as the first signature
    made it. Its release is retried, or the rest goes on a new change order."""
    if co["state"] == "approved":
        parts = in_force_from(hub, co)
        if parts:
            raise ValueError(in_force_refusal(co, parts))


def reopen(hub, co_ref, by: str, reason: str) -> dict:
    """Send an approved change order that was not released (its release stopped: another release came first, its
    content changed after the signature, a step refused) back to revising: its decisions are void and it is reviewed,
    decided and signed again. Its requester or anyone who may approve change orders does it, with a reason. Refused
    once any part of it is in force (``in_force_from``): retry its release, or put the rest on a new change order."""
    co = _co(hub, co_ref)
    _refuse_if_in_force(hub, co)
    if co["state"] != "approved" or co["fields"].get("released_release"):
        raise ValueError(f"{co['number']} is {co['state']}: only an approved change order that was not released is sent back")
    if by != requester(co) and not authz.check(_bundle(hub), by, "approve", "change-order", co["folder"])["allowed"]:
        raise ValueError(f"only {requester(co)} or an approver of change orders sends {co['number']} back")
    if not (reason or "").strip():
        raise ValueError("say why it goes back to revising")
    return _back_to_revising(hub, co, reason.strip(), by=by)


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
    if (new or old or {}).get("kind") == "process":  # a workflow: its changes in words, never the whole process (workflow_model)
        from .workflow_model import process_change_reason

        return process_change_reason(old, new)
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
        if item.get("op") != "remove":  # the rule keeps a source (a release renders every rule with one)
            item["rule"] = {**item["rule"], "source": {**(rule.get("source") or {}), "frame": item["origin"]["frame"] or "settings",
                                                       "text": _sentence(item["rule"]), "direct": item["id"], "by": by}}
        item["reason"], item["widening"] = _item_reason(item), _item_widening(base, item)
        items.append(item)
    _set(hub, co, by=by, direct_changes=items, direct_base=_digest(hub), rebase_questions=questions)
    _seal(hub, co)
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


def settings_change_order(hub, user: str, reason: str = "", *, create: bool = True) -> dict | None:
    """The person's open settings change order, where Settings records their changes; one is started when they have none
    (``create``), else None."""
    co = next((r for r in hub.kernel.query(type_="change-order") if r["state"] in eco.OPEN_STATES and r["fields"].get("requested_by") == user
               and r["fields"].get("from_settings") and stage_of(r) in EDITABLE), None)
    if co is None and create:
        co = hub.kernel.create("change-order", "Quality/Changes", {"title": f"Policy changes by {user}", "description": reason or "Changes to the policy.",
                                                                   "requested_by": user, "from_settings": True, "stage": "preparing"}, by=user)
    return co


def rule_changes(base: list[dict], new_rules: list[dict]) -> list[dict]:
    """The direct changes that make ``base`` (the rules in force) say what ``new_rules`` says, as Settings records them:
    a change for each rule whose slot holds another rule now, an add or a remove for the rest."""
    from .guards import drift_guard

    return [{"op": "change" if c.get("changes_from") else c["op"], "rule": c["rule"], **({"was": c["changes_from"]} if c.get("changes_from") else {})}
            for c in drift_guard(base, new_rules) if not (c["op"] == "remove" and c.get("changed_to"))]


def rules_to_change_order(hub, user: str, new_rules: list[dict], reason: str = "", co_ref=None) -> dict:
    """Settings as direct changes (change control v2): the difference between the rules in force and ``new_rules`` is
    recorded on the person's open settings change order (one is started when they have none), or on ``co_ref``. Settings
    no longer writes sentences into the SOPs: the review round drafts the prose (the qa-manager cog). Returns
    {"change_order", "locator", "report"}; each report line says what was recorded."""
    base = hub.settings_rules()
    wanted = rule_changes(base, new_rules)
    if not wanted:
        raise ValueError("that changes nothing")
    co = _co(hub, co_ref) if co_ref is not None else settings_change_order(hub, user, reason)
    before = {it["id"] for it in _items(co)}
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
    """Everyone who wrote a revision the change order carries (or opened it for its author: ``started_by``), retires a
    document on it, confirmed or corrected a picture's reading that puts words into an SOP (``picture_readings``), or made
    a direct, suggested (who accepted it), form, record-type, link-vocabulary or retention change on it. None of them may sign its approval (the change-control SOP's segregation sentence)."""
    co = _co(hub, co_ref)
    out = []
    for c in eco.carried(hub, co):
        out += _version_authors(hub, c["draft"]) + [c["draft"]["fields"].get("started_by")]  # who opened it for its author too
    out += [r.get("by") for r in eco.retirements(co).values()]  # who retires a document
    for it in _items(co):
        o = it.get("origin") or {}
        out += [o.get("accepted_by")] if o.get("kind") == "suggested" else [o.get("by")]
    for kind in ("form_changes", "record_type_changes", "workflow_changes", "config_changes"):
        carried = co["fields"].get(kind) or {}
        out += [(v or {}).get("by") for v in (carried.values() if isinstance(carried, dict) else carried)]
    p = _proposal(hub, co)
    out += [(c.get("override") or {}).get("by") for c in (p or {}).get("changes") or []]  # an overrider wrote that value
    out += [(m or {}).get("by") for per in (co["fields"].get("state_map_meta") or {}).values() for m in (per or {}).values()]
    from .picture_reading import confirmers

    out += [w for who in confirmers(co).values() for w in who]  # who confirmed or corrected a picture's reading wrote SOP lines
    return sorted({w for w in out if w and w not in MACHINES})


def revision_authors(hub, co_ref) -> list[str]:
    """Who wrote the revisions the change order carries: none of them may sign it (they never approve their own
    document; the kernel refuses it too)."""
    co = _co(hub, co_ref)
    return sorted({w for c in eco.carried(hub, co) for w in _version_authors(hub, c["draft"])})


def independent_approval(hub, co_ref, p: dict | None = None) -> bool:
    """True when every item of the delta that is decided (not a wording change) is approved by a person who
    contributed nothing to the change order: then a contributor may sign it (whoever made a direct change is not its
    only approver)."""
    co = _co(hub, co_ref)
    p = p if p is not None else _proposal(hub, co)
    items = [c for c in (p or {}).get("changes") or [] if c.get("op") != "text" and not c.get("half_of")]  # a half follows its change
    if not items:
        return False
    contribs = set(contributors(hub, co))
    return all(any(d["verdict"] == "approve" and not d.get("stale") and d["by"] not in contribs for d in c.get("decisions") or [])
               for c in items)


def may_not_sign(hub, co_ref) -> list[str]:
    """Who may not sign the change order's approval (its ``contributor`` field, which the change-control SOP's sentence
    "The contributor and the approver of a change-order must be different people." keeps off the Gate): the authors of
    the revisions it carries, always; every other contributor unless each item has an independent approver."""
    co = _co(hub, co_ref)
    out = set(revision_authors(hub, co))
    if not independent_approval(hub, co):
        out |= set(contributors(hub, co))
    return sorted(out)


# ---------------------------------------------------------------- compiling the delta
def _shas(hub, drafts: dict[str, str | None]) -> dict[str, str]:
    return {n: (str((hub.kernel.get(loc) or {}).get("fields", {}).get("content_sha") or "") if loc else "retired")
            for n, loc in sorted(drafts.items())}


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
    from . import picture_reading  # a reading a person confirmed since the last compile is compiled into the text

    same = (prev is not None and prev.get("status") == "open" and f.get("staged_base") == _digest(hub)
            and (f.get("staged_from") or {}) == _shas(hub, drafts) and not f.get("staged_errors")
            and (f.get("staged_readings") or "") == picture_reading.digest(picture_reading.confirmed_of(co))
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

    co = _set(hub, co, staged_sig=_sig(_items(co)), staged_changes=len(live), staged_widenings=sum(1 for c in live if _widening(c)),
              staged_errors=[f"{x['detail']} ({x['frame']} line {x['line']})" for x in blocking_findings(p)])
    _seal(hub, co)
    return {"proposal": p, "errors": [], "recompiled": recompiled}


def _draft_authors(hub, co: dict) -> dict[str, list[str]]:
    return {c["name"]: _version_authors(hub, c["draft"]) for c in eco.carried(hub, co) if c["name"]}


def _annotate(hub, co: dict, p: dict, prev: dict | None, n: int | None) -> dict:
    """The staged proposal as the delta: each compiled change with its origin (a direct change the prose now states keeps
    its maker: origin ``direct`` or ``suggested``, with the SOP line that states it), every direct change the prose does
    not state (yet) as an item of its own (``unstated``), decisions and overrides carried from the last compile for an
    item that is the same, questions carried (a rebase question for each item a release made moot)."""
    from .picture_reading import confirmer_of

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
                # A rule read from a picture's reading was written by whoever confirmed or corrected that reading.
                reader = confirmer_of(co, where["frame"] or "", where["quote"])
                c["origin"] = {"kind": "prose", **o, **where, "by": reader or o.get("by") or ", ".join(authors.get(where["frame"]) or []) or None,
                               "at": o.get("at") or p.get("created_at"), **({"reading_by": reader} if reader else {})}
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

    no_effect = {c["id"] for c in p.get("changes") or [] if c.get("no_effect") or "no effect" in str(c.get("reason") or "").lower()}

    def attach(about: dict | None, w: dict) -> None:
        about = about or {}
        if about.get("item") in items:
            if about["item"] not in no_effect:  # a "moved / no effect" item needs no acceptance
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
    # A citation of a document not in force (hubproto.references) is a warning the approver accepts when this change
    # order makes it: an SOP it changes cites one, or it retires or supersedes a document an SOP cites. A stale citation
    # in an SOP it does not touch was flagged to that SOP's owner by the release that made it stale.
    changed = set(p.get("overrides") or {})
    for ref in p.get("references") or []:
        r = ref.get("resolved") or {}
        if r.get("status") in (None, "in-force", "self") or not (ref.get("frame") in changed or r.get("by_this_change")):
            continue
        attach({"frame": ref.get("frame"), "line": ref.get("line")},
               {"code": "reference", "ref": f"ref:{ref.get('frame')}:{ref.get('line')}:{ref.get('document')}"
                + (f"@{ref['revision']}" if ref.get("revision") else ""),
                "text": f"{ref.get('frame')} line {ref.get('line')} cites {ref.get('text')}: {r.get('why')}"})
    for q in questions(hub, co):
        if q.get("code") in COMMITTEE_CODES and q.get("status") == "answered":
            # The chair's (or a system administrator's) answer for the committee stays visible, as an accepted warning; a
            # policy owner's answer (no person chairs or administers) is on each item it concerns, so the other owner sees it.
            w = {"code": "question", "ref": q["id"], "text": f"{q['text']} (answered by {q.get('by')}"
                 + (", a policy owner, as no person chairs the committee or is a system administrator" if q.get("answered_as") == "policy-owner" else "")
                 + f": {q.get('answer')})"}
            mine = [i for i in q.get("items") or [] if i in items]
            for i in mine:
                items[i].append(dict(w))
            if not mine:
                general.append(w)
            continue
        if q.get("status", "open") != "open" or "round-trip" in (q.get("kind"), q.get("code")):
            continue  # a round-trip question is the item's round-trip warning
        attach({"item": q.get("item"), "frame": q.get("frame"), "line": q.get("line")}, {"code": "question", "ref": q["id"], "text": q["text"]})
    for f in findings(hub, co):
        if f.get("severity") in ("blocking", "advisory"):  # every unresolved finding is a warning the approver accepts
            attach(f.get("about"), {"code": "finding", "ref": f["id"], "text": f"{f['who']}: {f['text']}", "severity": f.get("severity")})
    rnd = current_round(co)
    for o in (rnd or {}).get("omitted") or []:
        general.append({"code": "panel", "ref": f"panel:{o['who']}", "text": f"the change-control SOP names {o['who']} to review this change "
                        f"({o.get('reason') or 'see Review of changes'}), and {o.get('by') or 'the requester'} left it off the panel"})
    bc = co["fields"].get("baseline_changed") or {}
    if bc.get("dismissed"):
        general.append({"code": "related", "ref": f"related:{bc.get('by')}", "text": f"related by meaning to {bc.get('by')}: {'; '.join(bc.get('reasons') or [])} "
                        f"(dismissed by {bc['dismissed'].get('by')}: {bc['dismissed'].get('reason')})"})
    return items, general


def _makers(c: dict, authors: dict[str, list[str]], co: dict | None = None) -> list[str]:
    """Who made a delta item: whoever wrote it in the prose (the revision's authors) or made it directly, who accepted it
    (a suggestion), who overrode its value, who set a state-map entry, who confirmed or corrected the picture's reading
    it was read from (and, for a wording change, every reading of that SOP). None of them decides it."""
    from .picture_reading import confirmers, writers_of

    o = c.get("origin") or {}
    # A suggestion's maker is who accepted it onto the change order (the reviewer who suggested it reviewed, not authored).
    by = [] if o.get("kind") == "suggested" else [x.strip() for x in str(o.get("by") or "").split(",")]
    out = by + [o.get("accepted_by"), (c.get("override") or {}).get("by")]
    frame = o.get("frame") or c.get("frame") or ""
    if o.get("kind") == "prose" or c.get("op") == "text":
        out += authors.get(frame) or []
    if co is not None:
        out += writers_of(co, frame, o.get("quote") or ((c.get("rule") or {}).get("source") or {}).get("text"))
        if c.get("op") == "text":  # the SOP's wording change holds the readings' lines: their writers made it too
            out += [w for (f, _short), who in confirmers(co).items() if f == frame for w in who]
    return sorted({w for w in out if w and w not in MACHINES})


def _state_map_items(hub, co: dict, p: dict) -> None:
    """Each state-map entry is an item of the delta the approvers decide ("move the 4 nonconformances in Awaiting
    disposition to Open"), rebuilt from the CO's ``state_map``; decisions stay with an entry while it is the same."""
    old = {c["id"]: c for c in p.get("changes") or [] if c.get("op") == "state-map"}
    p["changes"] = [c for c in p.get("changes") or [] if c.get("op") != "state-map"]
    if not co["fields"].get("state_map"):
        return
    meta = co["fields"].get("state_map_meta") or {}
    counts = {(q["process"], q["state"]): q.get("count") or 0 for q in state_questions(hub, co, p=p, smap_only=True)}
    for process, mapping in sorted((co["fields"].get("state_map") or {}).items()):
        for frm, to in sorted((mapping or {}).items()):
            if not to:
                continue
            iid = f"state-map:{process}:{frm}->{to}"
            m = (meta.get(process) or {}).get(frm) or {}
            n = counts.get((process, frm), 0)
            prior = old.get(iid) or {}
            p["changes"].append({"id": iid, "op": "state-map", "rule": {"kind": "state-map", "process": process, "from": frm, "to": to, "count": n},
                                 "widening": False, "reason": f"move the {n} item(s) of {process} in {frm} to {to} when it is released",
                                 "origin": {"kind": "direct", "by": m.get("by"), "at": m.get("at"), "frame": None, "line": None, "quote": None,
                                            "read_by": None},
                                 "decisions": [dict(d) for d in prior.get("decisions") or []], "warnings": [], "stale": False})


def _retire_items(hub, co: dict, p: dict) -> None:
    """Each document the change order retires is an item of the delta the approvers decide ("retire SOP-0007
    (persona-reviewer): it becomes obsolete and its Frame leaves the release"); the rules its Frame stated are taken out
    as items of their own. Decisions stay with an entry while it is the same."""
    old = {c["id"]: c for c in p.get("changes") or [] if c.get("op") == "retire"}
    p["changes"] = [c for c in p.get("changes") or [] if c.get("op") != "retire"]
    retires = eco.retirements(co)
    for c in p["changes"]:  # a rule taken out because its Frame is retired was taken out by whoever retires it
        frame = ((c.get("rule") or {}).get("source") or {}).get("frame")
        if c.get("op") == "remove" and frame in retires and not c.get("direct"):
            c["origin"] = {**(c.get("origin") or {}), "kind": "retirement", "by": retires[frame].get("by"), "frame": frame}
    for name, r in sorted(retires.items()):
        iid = f"retire:{name}"
        prior = old.get(iid) or {}
        p["changes"].append({"id": iid, "op": "retire", "frame": name, "rule": {"kind": "retire", "frame": name, "document": r.get("document")},
                             "widening": False, "reason": f"retire {r.get('document')} ({name}): it becomes obsolete and its Frame leaves the "
                                                          f"release ({r.get('reason')})",
                             "origin": {"kind": "retirement", "by": r.get("by"), "at": r.get("at"), "frame": name, "line": None, "quote": None,
                                        "read_by": None, "reason": r.get("reason")},
                             "decisions": [dict(d) for d in prior.get("decisions") or []], "warnings": [], "stale": False})


def _refresh(hub, co: dict, p: dict) -> dict:
    acc = p.get("acceptances") or {}
    if p.get("status") == "open" and not p.get("applied"):
        _state_map_items(hub, co, p)
        _retire_items(hub, co, p)
    authors = _draft_authors(hub, co)
    contribs = contributors(hub, co)
    ids = {c["id"] for c in p.get("changes") or []}
    for c in p.get("changes") or []:
        c["makers"] = _makers(c, authors, co)
        c["contributors"] = contribs
        # The removal a change supersedes is that change's other half (its "from"): decided with it, never on its own.
        half = "add:" + rule_key(c["changed_to"]) if c.get("op") == "remove" and c.get("changed_to") else None
        if half in ids:
            c["half_of"] = half
        else:
            c.pop("half_of", None)
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
    approver. Stale decisions (made before the baseline changed) do not count, nor does a decision by one of the item's
    makers (who made a change never approves it: a maker is not the second owner on their own widening)."""
    makers = set(c.get("makers") or [])
    if _widening(c):
        # Whoever made a change is not its only approver (the agreed rule): a maker who is a policy owner may be one of
        # the two owners, when the other owner contributed nothing to the change order and approves it too.
        live = [d for d in c.get("decisions") or [] if not d.get("stale")]
        if any(d["verdict"] == "reject" for d in live if d["by"] not in makers):
            return "rejected"
        approvers = {d["by"] for d in live if d["verdict"] == "approve"} & set(owners)
        independent = approvers - makers - set(c.get("contributors") or [])
        return "approved" if len(approvers) >= 2 and independent else "pending"
    live = [d for d in c.get("decisions") or [] if not d.get("stale") and d["by"] not in makers]
    if any(d["verdict"] == "reject" for d in live):
        return "rejected"
    approvers = {d["by"] for d in live if d["verdict"] == "approve"}
    return "approved" if approvers else "pending"


def state_in(c: dict, p: dict, owners) -> str:
    """An item's state in its proposal: the removal a change supersedes (``half_of``) is in the state of that change."""
    if c.get("half_of"):
        whole = next((x for x in p.get("changes") or [] if x["id"] == c["half_of"]), None)
        if whole is not None:
            return item_state(whole, owners)
    return item_state(c, owners)


def halves(p: dict, item_id: str) -> list[dict]:
    """The removals the change ``item_id`` supersedes (its other half), decided with it."""
    return [x for x in p.get("changes") or [] if x.get("half_of") == item_id]


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
    return [{**c, "state": "covered" if c.get("op") == "text" else state_in(c, p, owners)} for c in p.get("changes") or []]


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
    """What the change order changes: {"sop", "process", "slot", "record_type", "form", "role", "kind"} (sets). A link-vocabulary
    or retention change is a kind ("links", "retention")."""
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
    out["kind"] |= {x.get("doc") for x in f.get("config_changes") or [] if isinstance(x, dict)}
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
def _objectors(co: dict) -> dict[str, dict]:
    """Reviewers whose latest review raised a blocking finding (an objection): {who: result}."""
    return {w: res for w, (n, res) in _latest_results(co).items()
            if w != "hub" and any(f.get("severity") == "blocking" for f in res.get("findings") or [])}


def request_review(hub, co_ref, by: str, panel=None, drop_reason: str = "") -> dict:
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
    if not (eco.carried(hub, co) or eco.retirements(co) or _items(co) or f.get("form_changes") or f.get("record_type_changes")
            or f.get("workflow_changes") or f.get("config_changes")):
        raise ValueError(f"{co['number']} changes nothing yet: carry an SOP or policy revision, make a direct change or carry a form first")
    last = current_round(co)
    if last and last.get("status") == "compiling":
        jobs = [j for j in hub.automation.of(co["locator"]) if j["automation"] == "compile-frame" and j["status"] in ("queued", "running")]
        if jobs:
            return last
    rs = rounds(co)
    n = len(rs) + 1
    dropped = []
    if panel is not None:  # leaving off a reviewer who objected needs a reason; the objection stays a warning to accept
        named = {(x.get("who") if isinstance(x, dict) else str(x)) for x in panel}
        prior = {e["who"]: e for r0 in rounds(co) for e in r0.get("panel") or []}
        dropped = [w for w in _objectors(co) if w not in named and not (prior.get(w) or {}).get("committee")]
        if dropped and not (drop_reason or "").strip():
            raise ValueError(f"{', '.join(dropped)} objected (a blocking finding) and is not on this panel: say why it is left off "
                             "(its objection stays a warning the approver accepts)")
    rs.append({"n": n, "requested_by": by, "at": utcnow(), "status": "compiling", "compiled": None, "panel": [], "suggested": [],
               "dropped": [{"who": w, "reason": drop_reason.strip(), "by": by} for w in dropped],
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


def _committee_record(hub) -> dict | None:
    """The compile-frame committee as a round records it: its members (each with the model it reads with, or why it
    cannot read here), its group and its chair."""
    try:
        c = hub.compile_committee()
    except Exception:
        return None
    if not c:
        return None
    un = c.get("unavailable") or {}
    return {"members": [{"who": w, "model": m, **({"unavailable": un[w]} if w in un else {})} for w, m in c.get("members") or []],
            "group": c.get("group"), "chair": c.get("chair")}


def round_committee(hub, rnd: dict) -> dict | None:
    """The compile-frame committee of a round: as the round recorded it when it was compiled, or, for a round compiled
    before rounds recorded it, the committee in force when its compile was read by a committee."""
    if rnd.get("committee"):
        return rnd["committee"]
    if not rnd.get("compiled"):
        return _committee_record(hub) if rnd.get("status") == "compiling" else None
    try:
        p = hub.proposals.load(rnd["compiled"])
    except Exception:
        return None
    return _committee_record(hub) if p.get("extractor") == "committee" else None


def committee_results(p: dict | None, who: str) -> dict:
    """What a compile-frame committee member's reading gave in a compile: the items read by the committee (the agreed
    readings, which name every member) and the questions it asked (alone or with others)."""
    if not p:
        return {"items": 0, "questions": 0}
    items = sum(1 for c in p.get("changes") or [] if "read by the compile-frame committee (" in str(c.get("reason") or "")
                and who in str(c.get("reason") or "").split("read by the compile-frame committee (", 1)[1])
    qs = sum(1 for q in p.get("questions") or [] if who in str(q.get("raised_by") or ""))
    return {"items": items, "questions": qs}


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
    cmt = _committee_record(hub)  # the compile Op's members are on every round's panel: they compile the diff first
    _update_round(hub, co, n, lambda r: r.update({"compiled": (p or {}).get("id"), "compiled_number": (p or {}).get("number"),
                                                  **({"committee": cmt} if cmt else {})}))
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
        p["questions"] = _carrier_questions(hub, co, p, by)
        _refresh(hub, co, p)
        hub.proposals.save(p)
    _update_round(hub, co, n, lambda r: r.update({"drafted": {"by": who, "questions": [q.get("id") for q in qs]}}))
    left = [c for c in (p or {}).get("changes") or [] if c.get("unstated")]
    return (f"{who} wrote the prose for {len(todo)} direct change(s)" if todo else f"{len(items)} direct change(s) already stated") + \
        (f"; {len(left)} not stated as it stands ({len(qs)} round-trip question(s))" if left or qs else "; each reads back as itself")


def _carrier_questions(hub, co: dict, p: dict, by: str) -> list[dict]:
    """A direct change whose SOP the person who ran the round may not revise cannot be written into its prose (only the
    SOP's owner or author, a document controller or a policy owner revises it, so the drafter does not write it for
    someone who may not): a blocking question says who must carry the revision, rather than letting the change come into
    force only as a divergence. Recomputed every round (an answered one keeps its answer)."""
    from . import sopdocs
    from .compile import question

    old = {q["id"]: q for q in p.get("questions") or [] if q.get("code") == "carrier"}
    keep = [q for q in p.get("questions") or [] if q.get("code") != "carrier"]
    b = _bundle(hub)
    for c in p.get("changes") or []:
        if not c.get("unstated"):
            continue
        frame = (c.get("origin") or {}).get("frame")
        doc = sopdocs.in_force(hub.kernel, frame) if frame else None
        why = eco.may_change_sop(b, doc, by) if doc else ""
        if not why:
            continue
        owner = doc["fields"].get("owner") or "its owner"
        q = question(frame, 0, f"{by} may not revise {frame} ({why}), so the prose for “{c.get('reason')}” is not written: {owner}, a "
                     "document controller or a policy owner requests the review (and so carries the revision), or the change comes into "
                     "force only as a divergence", raised_by="hub", code="carrier", item=c["id"], blocking=True)
        prior = old.get(q["id"])
        keep.append({**q, **({k: prior[k] for k in ("status", "answer", "by", "at") if k in prior} if prior and prior.get("status") != "open" else {})})
    return keep


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
    if co["fields"].get("staged_errors") and (co["fields"].get("staged_from") or {}) != _shas(hub, eco.drafts_of(hub, co)):
        compile_delta(hub, co, n=n)  # a retry after the author fixed the draft: compile what changed
        co = _co(hub, co["locator"])
    errors = co["fields"].get("staged_errors") or []
    grants = [{**g, "id": f"R{n}.hub.S{i}"} for i, g in enumerate(role_grant_suggestions(hub, co), 1)]
    if errors:
        _update_round(hub, co, n, lambda r: r.setdefault("results", {}).update(
            {"hub": {"status": "done", "kind": "hub", "at": utcnow(), "findings": [], "questions": [], "suggestions": grants,
                     "summary": "roles the delta's new work needs"}}) if grants else None)
        _round_failed(hub, co, n, "the compiled delta has problems that stop it: " + "; ".join(errors[:3]))
        raise ValueError("the compiled delta has problems that stop it: " + "; ".join(errors[:3]))
    left = [it for it in _items(co) if not it.get("stated")]
    w = int(co["fields"].get("staged_widenings") or 0)
    # What this round reviews: the revisions and the direct changes as they are now (send_reasons compares with it).
    content = content_digest(hub, co)
    seen = {k: content[k] for k in REVIEWED_KEYS if k in content}

    def put(r):
        r["reviewed_from"] = seen
        if grants:
            r.setdefault("results", {})["hub"] = {"status": "done", "kind": "hub", "at": utcnow(), "findings": [], "questions": [],
                                                  "suggestions": grants, "summary": "roles the delta's new work needs"}
    _update_round(hub, co, n, put)
    return ("no guard stops it" + (f"; {w} widening(s) need both policy owners" if w else "")
            + (f"; {len(left)} direct change(s) the prose does not state (round-trip questions)" if left else ""))


def widened_principals(hub, co_ref) -> set[str]:
    """The members whose own authority the change order's delta widens: a cog that may approve more, is routed more, or
    may do more (a permission), a member gaining roles. Like a person, none of them reviews that change."""
    co = _co(hub, co_ref)
    p = _proposal(hub, co)
    out: set[str] = set()
    for c in (p or {}).get("changes") or []:
        if c.get("op") == "text" or not _widening(c):
            continue
        r = _effective(c)
        for ref in (r.get("approver"), r.get("assignee"), r.get("subject"), f"person:{r['principal']}" if r.get("kind") == "member" else None):
            kind, _, name = str(ref or "").partition(":")
            if kind in ("cog", "person") and name:
                out.add(name)
    return out


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
    interested = widened_principals(hub, co)
    left_off = [{"who": e["who"], "reason": "the change widens its own authority: like a person, it does not review its own"}
                for e in panel if e["who"] in interested]
    panel = [e for e in panel if e["who"] not in interested]
    contribs = set(contributors(hub, co))
    for e in panel:
        if e["who"] in contribs:
            e["excluded"] = "it contributed to this change order, so it does not review it"
    on = {e["who"] for e in panel}
    omitted = [{"who": s["who"], "reason": s.get("reason"), "by": rnd["requested_by"]} for s in suggested if s.get("member") and s["who"] not in on]
    _update_round(hub, co, n, lambda r: r.update({"suggested": suggested, "panel": panel, "omitted": omitted, "left_off": left_off}))
    named = [f"{e['who']}" + ("" if e["member"] else " (not a member: shown, not asked)") + (" (a contributor: not asked)" if e.get("excluded") else "")
             for e in panel]
    off = "".join(f"; left off: {x['who']} ({x['reason']})" for x in left_off)
    return (("panel: " + ", ".join(named)) if named else "no reviewer suggested: the section on reviewing changes names none for this delta") + off


def round_queue(hub, co_ref, n: int, by: str) -> str:
    """Round step 5: each member of the panel is asked: a cog through its queue, a person in their inbox."""
    co = _co(hub, co_ref)
    rnd = next(r for r in rounds(co) if r["n"] == n)
    asked = [e for e in rnd.get("panel") or [] if e.get("member") and not e.get("excluded")]
    excluded = [e for e in rnd.get("panel") or [] if e.get("member") and e.get("excluded")]
    now = utcnow()
    _update_round(hub, co, n, lambda r: r.update({"status": "reviewing" if asked else "reviewed",
                                                  "results": {**(r.get("results") or {}),
                                                              **{e["who"]: {"status": "queued", "kind": e["kind"], "at": now, **_due_of(e)} for e in asked},
                                                              **{e["who"]: {"status": "failed", "kind": e["kind"], "at": now, "excluded": True,
                                                                            "error": f"{e['who']} {e['excluded']}", "findings": [], "questions": [],
                                                                            "suggestions": []} for e in excluded}}}),
                  **({} if asked else {"stage": "revising"}))
    for e in asked:
        _ask(hub, co, n, e, by)
    co = _co(hub, co["locator"])
    rnd = next(r for r in rounds(co) if r["n"] == n)
    done = [w for w, res in (rnd.get("results") or {}).items() if res.get("status") in ("done", "failed")]
    return (f"asked {', '.join(e['who'] for e in asked)}" + (f"; {len(done)} reviewed already" if done else "")) if asked else "nobody to ask"


COMMITTEE_REVIEW_DAYS = 5  # a committee member on a panel answers within this; then the chair intervenes


def _due_of(e: dict) -> dict:
    """A committee member's review is a vote with a due date (change control 11: the chair intervenes after it)."""
    from datetime import date, timedelta

    return {"due": (date.today() + timedelta(days=COMMITTEE_REVIEW_DAYS)).isoformat()} if e.get("committee") else {}


def vote_words(name: str) -> str:
    """What a committee member is told about their review on a panel: it is a vote under consensus."""
    return (f"Your vote: the {name} committee decides by consensus; one reasoned objection sends it back. A blocking finding is "
            "your objection (give your reason); a review without one is your agreement. There is no standing aside.")


def _ask(hub, co: dict, n: int, e: dict, by: str) -> None:
    if e["kind"] == "cog":
        hub.queue.submit(e["who"], "co-review", by=by, title=f"Review round {n} of {co['number']}", record=co["locator"],
                         payload={"co": co["locator"], "round": n}, key=f"co-review:{co['locator']}:{n}:{e['who']}")
    elif e.get("committee"):
        due = _due_of(e)["due"]
        hub.inbox.notify(e["who"], kind="review", subject=f"Your vote on {co['number']} (round {n}, {e['committee']} committee), due {due}",
                         body=(f"{by} asks the {e['committee']} committee to review the configuration delta of {co['number']}. "
                               f"{vote_words(e['committee'])} Due {due}; after that its chair intervenes."), record=co["locator"])
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
    mine = [e["who"] for e in entries if e["who"] in contributors(hub, co)]
    if mine:
        raise ValueError(f"segregation of duties: {', '.join(mine)} contributed to {co['number']} and does not review it")
    new = [e for e in entries if e["who"] not in {x["who"] for x in rnd.get("panel") or []}]
    if not new:
        return rnd
    now = utcnow()
    _update_round(hub, co, rnd["n"], lambda r: r.update({"panel": (r.get("panel") or []) + new, "status": "reviewing",
                                                         "results": {**(r.get("results") or {}),
                                                                     **{e["who"]: {"status": "queued", "kind": e["kind"], "at": now, **_due_of(e)} for e in new}}}),
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
    entry = next(e for e in rnd.get("panel") or [] if e["who"] == who)
    gone = [e["who"] for e in rnd.get("panel") or [] if entry.get("committee") and e.get("committee") == entry["committee"]] or [who]
    res = rnd.get("results") or {}
    done = [w for w in gone if (res.get(w) or {}).get("status") == "done"]
    if done:
        raise ValueError(f"{', '.join(done)} has reviewed this round already: a reviewer who returned results stays on its panel (request "
                         "review again for a new round with another panel)")
    for w in gone:
        for task in hub.queue.open_tasks(cog=w, record=co["locator"]):
            if task["kind"] == "co-review" and task["status"] == "queued":
                hub.queue.cancel(task["id"], by, f"taken off the panel of {co['number']}")
    suggested = {s["who"]: s for s in rnd.get("suggested") or [] if s.get("member")}

    def fn(r):
        r["panel"] = [e for e in r.get("panel") or [] if e["who"] not in gone]
        r["removed"] = (r.get("removed") or []) + [{"who": w, "by": by, "at": utcnow()} for w in gone]
        r["omitted"] = (r.get("omitted") or []) + [{"who": w, "reason": suggested[w].get("reason"), "by": by} for w in gone if w in suggested]
        rr = r.get("results") or {}
        for w in gone:
            rr.pop(w, None)
        r["results"] = rr
        _maybe_reviewed(r)
    co = _update_round(hub, co, rnd["n"], fn)
    if (current_round(co) or {}).get("status") == "reviewed" and stage_of(co) == "in-review":
        _set(hub, co, stage="revising")
    _refresh_saved(hub, co)
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
    if cog in contributors(hub, co):
        result = {"status": "failed", "kind": "cog", "at": utcnow(), "excluded": True, "findings": [], "questions": [], "suggestions": [],
                  "error": f"{cog} contributed to {co['number']} (it drafted or made part of it), so it does not review it"}
        _record_result(hub, co, n, cog, result)
        return result
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
    if who in contributors(hub, co):
        raise ValueError(f"segregation of duties: {who} contributed to {co['number']} and does not review it")
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


PROCEDURE = "procedure: a procedure people follow; the hub does not enforce it"
ANSWER_CATEGORIES = {"procedure": PROCEDURE}  # categories an unread-rule answer may take besides "states no rule" and a divergence


def committee_answerer(hub, co_ref, q: dict, by: str, p: dict | None = None) -> tuple[str, str, list[str]]:
    """Who may answer a committee question (``COMMITTEE_CODES``): (why not, or "" when ``by`` may; "chair" | "system
    administrator" | "policy-owner"; the items it concerns). A person answers: the committee's chair or a system
    administrator who contributed nothing to the change order; when the release in force names no person as either
    (a cog in either place does not count), any policy owner who did not make what the question is about."""
    co = _co(hub, co_ref)
    p = p if p is not None else _proposal(hub, co)
    chair = q.get("chair") or ((hub.compile_committee() or {}).get("chair") if hasattr(hub, "compile_committee") else None)
    b = _bundle(hub)
    if not _person(hub, by):
        return f"{by} is a cog: a person answers a {q.get('code')} question for the committee", "", []
    if by == chair or "system-administrator" in authz.roles_of(b, by):
        if by in contributors(hub, co):
            return f"segregation of duties: {by} contributed to {co['number']}; someone who did not answers for the committee", "", []
        return "", "chair" if by == chair else "system administrator", []
    if _human_intervener(hub, chair):
        return f"only the committee's chair ({chair or 'none'}) or a system administrator answers a {q.get('code')} question", "", []
    if by not in _owners(hub):
        return (f"the release in force names no person as the committee's chair or as a system administrator: a policy owner "
                f"({', '.join(_owners(hub)) or 'none'}) answers a {q.get('code')} question"), "", []
    concerned = _question_items(p, q)
    own = {w for c in (p or {}).get("changes") or [] if c["id"] in concerned for w in c.get("makers") or []}
    if not concerned:
        own |= set(_draft_authors(hub, co).get(q.get("frame") or "") or [])  # the line is in their own prose
    if by in own:
        return f"segregation of duties: {by} made what this question is about; the other policy owner answers it", "", []
    if by in contributors(hub, co):  # an answer may write SOP text (a picture's reading): never a contributor's
        return f"segregation of duties: {by} contributed to {co['number']}; a policy owner who did not answers for the committee", "", []
    return "", "policy-owner", concerned


def answer_question(hub, co_ref, qid: str, answer: str, by: str, line: int | None = None, record_divergence: bool = False,
                    category: str | None = None, picture: str | None = None, reading_text: str = "") -> dict:
    """Mark a question answered, with the answer (and, best, the line of the prose that answers it). The next compile
    also closes a compiler question that no longer arises. A committee question (``COMMITTEE_CODES``) is answered by a
    person (``committee_answerer``: its chair or a system administrator; with no person as either in the release in
    force, a policy owner, and the answer is then an accepted warning on each item it concerns). ``record_divergence``
    (a committee question): the line stays unenforced and the release records it as a divergence, with the answer as its
    meaning. ``category="procedure"`` (an ``unread-rule`` question): the sentence is a procedure people follow and the
    hub does not enforce it; not a divergence, an accepted warning like any answer. Other questions: the requester, a
    document controller or a contributor answers.

    ``picture`` (a picture's question, issue #31): "confirm" the compile-frame committee's reading of it, "correct" it
    (``reading_text``: the reading, one sentence a line) or say it states "nothing". The reading confirmed is recorded on
    the change order (``picture_readings``) and is part of the SOP's text from the next compile on (the next review
    round), after the picture's line (``hubproto.picture_reading``); the signed answer holds it."""
    from . import picture_reading

    co = sync(hub, co_ref)
    if picture is not None and not picture_reading.kind_of(picture):
        raise ValueError(f"a picture's question is answered with {', '.join(picture_reading.ANSWERS)}")
    if picture is not None and stage_of(co) not in EDITABLE:  # whatever it says: the text signed is the text compiled
        raise ValueError(f"{co['number']} is {stage_of(co)}: a picture's answer decides the SOP's text, so answer it while the "
                         "change order is being revised (send it back to revising first)")
    if not (answer or "").strip() and picture:
        answer = picture_reading.ANSWERS[picture_reading.kind_of(picture)]
    if not (answer or "").strip():
        raise ValueError("say what the answer is (and, best, which line of the prose says it)")
    if category is not None and category not in ANSWER_CATEGORIES:
        raise ValueError(f"an answer's category is one of: {', '.join(ANSWER_CATEGORIES)}")
    if category and record_divergence:
        raise ValueError("a procedure the hub does not enforce is not a divergence: choose one")
    now = utcnow()
    meaning = (f"{ANSWER_CATEGORIES[category]}. " if category else "") + answer.strip()
    upd = {"status": "answered", "answer": meaning, "by": by, "at": now, "answer_line": line}
    if record_divergence:  # the chair says "record it as a divergence": the release records one (QMS A4); the answer is its meaning
        upd["record_divergence"] = True
    if category:
        upd["category"] = category
    p = _proposal(hub, co)
    q = next((x for x in (p or {}).get("questions") or [] if x["id"] == qid), None)
    committee_q = q is not None and q.get("code") in COMMITTEE_CODES
    concerned: list[str] = []
    if category and (q is None or q.get("code") != "unread-rule"):
        raise ValueError("only an unread-rule question (prose that reads like a rule and configures nothing) is answered as a procedure")
    if q is not None and q.get("picture") and stage_of(co) not in EDITABLE:  # any answer: the text signed is the text compiled
        raise ValueError(f"{co['number']} is {stage_of(co)}: a picture's answer decides the SOP's text, so answer it while the "
                         "change order is being revised (send it back to revising first)")
    reading: list[str] | None = None
    if picture is not None:
        if q is None or not q.get("picture"):
            raise ValueError("only a picture's question is answered with the picture's reading")
        reading = picture_reading.answer_lines(q, picture, reading_text)
        upd.update({"picture_answer": picture, "confirmed_reading": reading})
        if reading:
            upd["answer"] = meaning = f"{meaning} The picture's reading: “{' '.join(reading)}”"
            divs = picture_reading.divergences_of(q, picture)
            if divs:  # the approvers see where it differs, with the answer
                upd["answer"] = meaning = f"{meaning} It differs from the prose: {'; '.join(divs)}."
    if committee_q:
        # The compile committee could not read (a member was unavailable) or did not agree: a person intervenes.
        why, as_, concerned = committee_answerer(hub, co, q, by, p)
        if why:
            raise ValueError(why)
        if as_ == "policy-owner":
            upd["answered_as"] = "policy-owner"
            upd["items"] = concerned
    elif record_divergence:
        raise ValueError("only a committee question (answered by its chair or a system administrator) is recorded as a divergence")
    elif _may_edit(hub, co, by) and by not in contributors(hub, co):
        raise ValueError(f"only {requester(co)}, a document controller or a contributor answers questions on {co['number']}")
    if q is not None:
        q.update(upd)
        _record(hub, co, "question_answered", {**_signed(hub, co, by=by, verdict="answer", meaning=meaning, step=qid,
                                                         payload=_hash(q.get("text")), at=now), "question": qid}, by)
        if reading is not None:  # the change order holds the reading a person confirmed; the next compile puts it in the text
            ref = picture_reading.PICTURE_LINE.fullmatch(str(q.get("quote") or "")).group(2)
            kept = {f: dict(v or {}) for f, v in (co["fields"].get("picture_readings") or {}).items()}
            wrote = picture_reading.writers(kept.get(q.get("frame") or "", {}).get(ref)) + ([by] if reading else [])
            kept.setdefault(q.get("frame") or "", {})[ref] = {
                "lines": reading, "answer": picture_reading.kind_of(picture), "chosen": picture, "by": by, "at": now, "question": qid,
                "writers": list(dict.fromkeys(wrote)),
                "read_by": list((q.get("reading") or {}).get("by") or []), "proposed": list((q.get("reading") or {}).get("proposed") or [])}
            co = _set(hub, co, by=by, picture_readings=kept)
        if committee_q and not reading:  # the intervention is an accepted warning on the change order (and on each item it concerns)
            # An answer that writes SOP lines made its author a contributor: what is raised against those lines (the reading's
            # divergences) stays a warning for an approver who contributed nothing to accept.
            for item, key in [(None, f"co|question|{qid}")] + [(cid, f"{cid}|question|{qid}") for cid in concerned]:
                p.setdefault("acceptances", {})[key] = {"by": by, "meaning": meaning, "at": now}
                _record(hub, co, "warning_accepted", {**_signed(hub, co, by=by, verdict="accept", meaning=meaning, step=key,
                                                                payload=_hash(q.get("text")), at=now), "item": item}, by)
        _refresh(hub, co, p)
        hub.proposals.save(p)
        _seal(hub, co)
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
    """The states items of a process can wait in: its statuses (a process written as a graph, as the workflow editor
    carries it), or the awaiting state of each checkpoint (numbered steps). A status on both sides is not removed."""
    if (proc or {}).get("statuses"):
        return {s["id"] for s in proc["statuses"]}
    return {f"awaiting-{s['id']}" for s in (proc or {}).get("steps") or [] if s.get("kind") == "checkpoint"}


def _state_names(hub, proc: dict | None, name: str) -> dict[str, str]:
    """{state: its name in words} for the messages ("Awaiting disposition the nonconformance", never the id)."""
    try:
        from .workflow_model import graph_of_rule

        return {s["id"]: s["name"] for s in graph_of_rule({"kind": "process", **(proc or {}), "name": name})["statuses"]}
    except Exception:
        return {}


def new_rules(hub, co_ref, p: dict | None = None) -> list[dict]:
    """The rules the change order would bring into force: its compiled delta (the prose as it stands) with every direct
    change the compile does not state yet and every override applied, rejected items left out."""
    co = _co(hub, co_ref)
    p = p if p is not None else _proposal(hub, co)
    live = p is not None and p.get("status") == "open"
    rules = {rule_key(r): r for r in ((p or {}).get("rules") if live else hub.settings_rules()) or []}
    owners = (p or {}).get("reviewers") or _owners(hub)
    in_p = {c.get("direct"): c for c in (p or {}).get("changes") or [] if live and c.get("direct")}
    for it in _items(co):
        c = in_p.get(it["id"])
        if c is not None and not c.get("unstated"):
            continue  # the prose states it: it is in the compiled rules
        if c is not None and item_state(c, owners) == "rejected":
            continue
        old, new = _old_new(it)
        _with(rules, old, ((c or {}).get("override") or {}).get("rule") or new)
    for c in (p or {}).get("changes") or [] if live else []:
        if c.get("override") and not c.get("unstated") and item_state(c, owners) != "rejected":
            rules.pop(rule_key(c["rule"]), None)
            rules[rule_key(c["override"]["rule"])] = c["override"]["rule"]
    return list(rules.values())


def removed_statuses(hub, co_ref, p: dict | None = None) -> dict[str, dict]:
    """For each process the change order changes, the statuses it removes and the statuses it leaves (graph form and
    numbered steps alike: both are workflow graphs in a release): {process: {"removed": {status: items in it},
    "statuses": [status ids of the new graph], "record_type"}}. Only processes that lose a status are listed."""
    from .release import render
    from .runner import items_in_removed_statuses

    co = _co(hub, co_ref)
    before = _bundle(hub).get("workflows") or {}
    try:
        after = render(new_rules(hub, co, p), hub.kernel.types).get("workflows") or {}
    except Exception:
        return {}
    out = {}
    for name, old in sorted(before.items()):
        new = after.get(name) or {}
        old_ids = [s["id"] for s in old.get("statuses") or [] if not s.get("system")] or list(old.get("states") or [])
        new_ids = [s["id"] for s in new.get("statuses") or []] or list(new.get("states") or [])
        gone = [s for s in old_ids if s not in set(new_ids)]
        if not gone:
            continue
        counts = items_in_removed_statuses(hub, old, new) if new else {}
        out[name] = {"removed": {s: int(counts.get(s, 0)) for s in gone}, "statuses": new_ids,
                     "record_type": new.get("record_type") or old.get("record_type")}
    return out


def state_questions(hub, co_ref, p: dict | None = None, smap_only: bool = False) -> list[dict]:
    """Where the delta removes a status that items are in (spec 5.2): [{"process", "state", "count", "record_type",
    "answer"}], for graph-form and numbered-step processes alike. A compiler's question of code "state-map" (keys
    ``process``, ``state``, ``count``) is honoured when its status is one this delta removes."""
    co = _co(hub, co_ref)
    p = p if p is not None else _proposal(hub, co)
    smap = co["fields"].get("state_map") or {}
    rs = removed_statuses(hub, co, p)
    out = []
    for process, r in rs.items():
        for s, n in r["removed"].items():
            if n or (smap_only and (smap.get(process) or {}).get(s)):
                out.append({"process": process, "state": s, "count": n, "record_type": r["record_type"],
                            "answer": (smap.get(process) or {}).get(s)})
    seen = {(x["process"], x["state"]) for x in out}
    for q in (p or {}).get("questions") or []:
        if "state-map" not in (q.get("kind"), q.get("code")):
            continue
        st = q.get("state") or (q.get("status") if q.get("status") not in ("open", "answered", "overridden") else None)
        if q.get("process") and st and (q["process"], st) not in seen and st in (rs.get(q["process"]) or {}).get("removed", {}):
            out.append({"process": q["process"], "state": st, "count": int(q.get("count") or 0), "record_type": q.get("record_type"),
                        "answer": (smap.get(q["process"]) or {}).get(st)})
            seen.add((q["process"], st))
    return out


def state_map_problems(hub, co_ref, p: dict | None = None) -> list[str]:
    """Entries of the state map that do not hold: a process the delta does not change, a status it does not remove, a
    target that is not a status of the new graph."""
    co = _co(hub, co_ref)
    rs = removed_statuses(hub, co, p)
    out = []
    for process, mapping in sorted((co["fields"].get("state_map") or {}).items()):
        for frm, to in sorted((mapping or {}).items()):
            if not to:
                continue
            r = rs.get(process)
            if r is None:
                out.append(f"the state map moves {process} items, but this change removes no status of {process}")
            elif frm not in r["removed"]:
                out.append(f"the state map moves {process} items in {frm}, a status this change does not remove")
            elif to not in r["statuses"]:
                out.append(f"the state map sends {process} items to {to}, which is not a status of {process} as this change leaves it")
    return out


def set_state_map(hub, co_ref, process: str, mapping: dict, by: str) -> dict:
    """Where items in a status the delta removes go: {old status: new status} for ``process`` (merged into the CO's
    ``state_map``; an empty value takes an entry out). Only while the change order is prepared or revised, only for a
    status this delta removes from a process it changes, only to a status of that process's new graph. Each entry is an
    item of the delta the approvers decide; who set it is its maker."""
    co = sync(hub, co_ref)
    why = _may_edit(hub, co, by)
    if why:
        raise ValueError(why)
    if stage_of(co) not in EDITABLE:
        raise ValueError(f"{co['number']} is {stage_of(co).replace('-', ' ')}: its state map is fixed now")
    rs = removed_statuses(hub, co)
    r = rs.get(process)
    for frm, to in (mapping or {}).items():
        if not to:
            continue
        if r is None:
            raise ValueError(f"{co['number']} removes no status of {process}: there is nothing to map")
        if frm not in r["removed"]:
            raise ValueError(f"{frm} is not a status this change removes from {process} (it removes {', '.join(r['removed']) or 'none'})")
        if to not in r["statuses"]:
            raise ValueError(f"{to} is not a status of {process} as this change leaves it ({', '.join(r['statuses'])})")
    smap = copy.deepcopy(co["fields"].get("state_map") or {})
    meta = copy.deepcopy(co["fields"].get("state_map_meta") or {})
    now = utcnow()
    cur = dict(smap.get(process) or {})
    for frm, to in (mapping or {}).items():
        if to:
            cur[frm] = to
            meta.setdefault(process, {})[frm] = {"by": by, "at": now}
        else:
            cur.pop(frm, None)
            (meta.get(process) or {}).pop(frm, None)
    if cur:
        smap[process] = cur
    else:
        smap.pop(process, None)
    co = _set(hub, co, by=by, state_map=smap, state_map_meta=meta)
    _record(hub, co, "state_map_set", {**_signed(hub, co, by=by, verdict="set", meaning=f"{process}: {mapping}", step=f"state-map:{process}",
                                                 payload=_hash(cur), at=now), "process": process, "mapping": mapping}, by)
    _refresh_saved(hub, co)
    _seal(hub, co)
    return _co(hub, co["locator"])["fields"].get("state_map") or {}


# ---------------------------------------------------------------- integrity: what was reviewed, sent and signed
REVIEWED_KEYS = ("drafts", "items", "forms", "record_types", "workflows", "config", "scripts")
CONTENT_WORDS = {"drafts": "the revisions it carries", "items": "its direct changes", "forms": "its form changes",
                 "record_types": "its record-type changes", "workflows": "its workflow changes", "state_map": "its state map",
                 "config": "its link-vocabulary and retention changes", "scripts": "its configuration scripts and their output",
                 "proposal": "its compiled delta"}


def _hash(obj) -> str:
    return sha256_of(obj)[:16]


def content_digest(hub, co_ref) -> dict:
    """What a change order carries, by hash: {"drafts": {SOP: content sha}, "items", "forms", "record_types",
    "workflows", "state_map", "config" (its link-vocabulary and retention changes, when it carries some)}. A review
    round records it (``reviewed_from``), sending for approval freezes it (``frozen``), the signature and the release
    check against it."""
    co = _co(hub, co_ref)
    f = co["fields"]
    return {"drafts": _shas(hub, eco.drafts_of(hub, co)), "items": _sig(_items(co)), "forms": _hash(f.get("form_changes") or {}),
            "record_types": _hash(f.get("record_type_changes") or []), "workflows": _hash(f.get("workflow_changes") or {}),
            "state_map": _hash(f.get("state_map") or {}),
            # Only when it carries some: the digest of a change order without them is what it was before they existed.
            **({"config": _hash(f["config_changes"])} if f.get("config_changes") else {}),
            # The configuration scripts run on it (issue #34: script, output, run record), likewise only when there are some.
            **({"scripts": _hash(f["config_scripts"])} if f.get("config_scripts") else {})}


OPTIONAL_KEYS = ("config", "scripts")  # in a content digest only when the change order carries some: absent on either side is none


def _differs(was: dict, now: dict, keys) -> list[str]:
    return [CONTENT_WORDS[k] for k in keys if (k in (was or {}) or k in OPTIONAL_KEYS) and (was or {}).get(k) != now.get(k)]


def changed_since_sent(hub, co_ref) -> list[str]:
    """What changed since the change order was sent for approval (empty when nothing did, or it was not sent)."""
    co = _co(hub, co_ref)
    fr = co["fields"].get("frozen") or {}
    if not fr:
        return []
    now = {**content_digest(hub, co), "proposal": co["fields"].get("staged_proposal")}
    return _differs(fr, now, REVIEWED_KEYS + ("state_map", "proposal"))


def _hold_drafts(hub, co: dict, hold: bool) -> None:
    """Carried revisions are held while the change order waits for approval (a held document takes no new version)."""
    for c in eco.carried(hub, co):
        d = c["draft"]
        if hold and d["state"] in ("draft", "open", "returned"):
            hub.kernel.transition(d["locator"], "held", by="hub", reason=f"held while {co['number']} waits for approval")
        elif not hold and d["state"] == "held":
            hub.kernel.transition(d["locator"], "draft", by="hub", reason=f"{co['number']} is back to revising")


def _back_to_revising(hub, co: dict, why: str, by: str = "hub") -> dict:
    co = _co(hub, co)
    _refuse_if_in_force(hub, co)  # every path that sends a signed change order back: reopen, a release before it, a related one
    if co["state"] == "approved" and not co["fields"].get("released_release"):
        hub.kernel.transition(co["locator"], "open", by=by, reason=f"back to revising: {why}")
    p = _proposal(hub, co)
    if p is not None and p.get("status") == "open":
        for c in p.get("changes") or []:
            for d in c.get("decisions") or []:
                d["stale"] = True
        p["acceptances_voided"] = {**(p.get("acceptances_voided") or {}), **(p.get("acceptances") or {})}
        p["acceptances"] = {}
        _refresh(hub, co, p)
        hub.proposals.save(p)
    _hold_drafts(hub, co, False)
    co = _set(hub, co, by=by, stage="revising", frozen=None, state_map_signed=None,
              reopened=list(co["fields"].get("reopened") or []) + [{"why": why, "by": by, "at": utcnow()}])
    hub.kernel.append("co.reopened", co["locator"], {"why": why, "by": by}, by=by)
    run = hub.runner.run_for_record(co["locator"])
    waiting = ((run or {}).get("pending") or {}).get("assignees") or [] if run and run.get("status") == "waiting" else []
    for who in dict.fromkeys([requester(co), *waiting]):
        if who and who != by:
            hub.inbox.notify(who, kind="automation", subject=f"{co['number']} is back to revising", body=f"{why}. Its item decisions are void: "
                             "review it again, decide its items and sign it again" + (" (reject the waiting approval, or wait for it to be "
                             "sent again)" if who in waiting else ""), record=co["locator"])
    _seal(hub, co)
    return _co(hub, co["locator"])


def _signed(hub, co: dict, *, by: str, verdict: str, meaning: str, step: str, payload: str, at: str) -> dict:
    """A decision on a change order's delta, signed with the hub's key as Gate decisions are (hubproto.track)."""
    from .track import sign

    d = {"by": by, "verdict": verdict, "meaning": meaning, "payload_digest": payload, "record_digest": co["locator"], "at": at,
         "run_id": co["number"], "step": step, "release_digest": co["fields"].get("staged_base") or ""}
    d["signature"] = sign(hub.runner.secret(), d)
    return d


def _record(hub, co: dict, kind: str, data: dict, by: str) -> None:
    """An append-only, chained kernel event on the change order: every item decision, accepted warning, override,
    answer and state-map entry. A re-decision appends; the proposal file is a projection of these events."""
    hub.kernel.append(f"co.{kind}", co["locator"], data, by=by)


def _events(hub, co: dict) -> list[dict]:
    return [e for e in hub.kernel.events(aggregate=co["locator"]) if str(e["type"]).startswith("co.")]


def verify_delta(hub, co_ref) -> list[str]:
    """Problems with the change order's delta record (empty when it verifies): every decision, accepted warning and
    override in the staged proposal must be the latest signed event of its kind on the change order, and every signed
    event's signature must verify. Catches an edited proposal file (a changed meaning, a relabelled decider)."""
    from .track import verify_signature

    co = _co(hub, co_ref)
    p = _proposal(hub, co)
    if p is None:
        return []
    secret = hub.runner.secret()
    problems = []
    decided, latest, accepted, overridden = set(), {}, set(), set()
    for e in _events(hub, co):
        d = e["data"]
        if d.get("signature") and verify_signature(secret, d) == "forged":
            problems.append(f"the signed {e['type'][3:].replace('_', ' ')} by {d.get('by')} on {d.get('step')} does not verify")
        if e["type"] == "co.item_decided":
            k = (d.get("step"), d.get("by"))
            decided.add((d.get("step"), d.get("by"), d.get("verdict"), d.get("meaning"), d.get("at")))
            latest[k] = (d.get("verdict"), d.get("meaning"), d.get("at"))
        elif e["type"] == "co.warning_accepted":
            accepted.add((d.get("step"), d.get("by"), d.get("meaning"), d.get("at")))
        elif e["type"] == "co.override":
            overridden.add((d.get("item"), d.get("by"), d.get("at"), d.get("payload_digest")))
    for c in list(p.get("changes") or []) + list(p.get("applied_items") or []):
        if c.get("op") == "text":
            continue  # a wording change is not decided: the CO's signature covers it (apply_delta keeps only that)
        if c.get("half_of"):
            continue  # the removal a change supersedes is decided with that change: a decision on it alone counts for nothing
        for d in list(c.get("decisions") or []) + list(c.get("stale_decisions") or []):
            if str(d.get("by") or "").startswith(("change-order:", "document-control:")):
                continue  # the release's own entry for a wording change, covered by the CO signature
            key = (c["id"], d.get("by"), d.get("verdict"), d.get("meaning"), d.get("at"))
            if key not in decided:
                problems.append(f"the decision by {d.get('by')} on “{c.get('reason')}” has no signed record")
            elif not d.get("stale") and latest.get((c["id"], d.get("by"))) != (d.get("verdict"), d.get("meaning"), d.get("at")):
                problems.append(f"the decision by {d.get('by')} on “{c.get('reason')}” is not their latest")
        o = c.get("override")
        if o and (c["id"], o.get("by"), o.get("at"), rule_key(o["rule"])) not in overridden:
            problems.append(f"the override of “{c.get('reason')}” by {o.get('by')} has no signed record")
    for key, a in (p.get("acceptances") or {}).items():
        if (key, (a or {}).get("by"), (a or {}).get("meaning"), (a or {}).get("at")) not in accepted:
            problems.append(f"the acceptance of a warning ({key.split('|', 1)[-1]}) by {(a or {}).get('by')} has no signed record")
    return problems


def delta_digest(hub, co_ref) -> str:
    """A digest of the decided delta and what the change order carries: the CO field ``delta_seal`` holds it, so the
    approver's signature (which binds the CO's fields) covers every decision, acceptance and override, and a change after
    the Gate card was drawn is refused."""
    co = _co(hub, co_ref)
    p = _proposal(hub, co) or {}
    body = {"proposal": p.get("id"), "content": content_digest(hub, co),
            "changes": [[c["id"], rule_key(_effective(c)) if c.get("op") != "text" else "",
                         sorted([str(d.get(k)) for k in ("by", "verdict", "meaning", "at", "stale")] for d in c.get("decisions") or []),
                         str((c.get("override") or {}).get("at"))] for c in p.get("changes") or []],
            "acceptances": sorted([k, str((v or {}).get("by")), str((v or {}).get("at"))] for k, v in (p.get("acceptances") or {}).items())}
    return _hash(body)


def _seal(hub, co_ref) -> dict:
    """Bring the CO's ``delta_seal`` and ``contributor`` (who may not sign it) up to date after a change to its delta."""
    co = _co(hub, co_ref)
    upd = {}
    seal = delta_digest(hub, co)
    if co["fields"].get("delta_seal") != seal:
        upd["delta_seal"] = seal
    excluded = may_not_sign(hub, co)
    if list(co["fields"].get("contributor") or []) != excluded:
        upd["contributor"] = excluded
    return _set(hub, co, **upd) if upd else co


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
    from . import formlayout, picture_reading

    co = _co(hub, co_ref)
    if co["state"] == "approved" and not co["fields"].get("released_release"):
        out = []  # signed but not released yet: what would stop its release
        if co["fields"].get("staged_base") and co["fields"].get("staged_base") != _digest(hub):
            out.append("another release came into force after it was signed: it goes back to revising and is decided and signed again")
        changed = changed_since_sent(hub, co)
        if changed:
            out.append("it changed after it was signed (" + ", ".join(changed) + "): it goes back to revising")
        return out
    if eco.finished(co):
        return []
    f = co["fields"]
    carried = eco.carried(hub, co)
    items = _items(co)
    if not (carried or eco.retirements(co) or items or f.get("form_changes") or f.get("record_type_changes") or f.get("workflow_changes")
            or f.get("config_changes")):
        return ["it changes nothing yet: carry an SOP or policy revision, make a direct change or carry a form"]
    reasons = []
    if not str(f.get("description") or "").strip():
        reasons.append("say what it changes and why (Title and description, at the top of the change order)")
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
        now = content_digest(hub, co)
        if rec.get("drafts") is not None and rec["drafts"] != now["drafts"]:
            reasons.append(f"the revisions changed since round {last['n']}: request review again")
        if rec.get("items") is not None and rec["items"] != now["items"]:
            reasons.append(f"the direct changes changed since round {last['n']}: request review again")
        for k in ("forms", "record_types", "workflows", "config", "scripts"):  # data-model changes carried after the round are reviewed too
            if (k in rec or k in OPTIONAL_KEYS) and rec.get(k) != now.get(k):
                reasons.append(f"{CONTENT_WORDS[k][4:]} changed since round {last['n']}: request review again")
        p = _proposal(hub, co)
        if (carried or eco.retirements(co) or items) and (p is None or p.get("status") != "open"):
            reasons.append("the delta is not compiled (or its compile was replaced): request review again")
        elif p is not None and (f.get("staged_base") != _digest(hub) or (f.get("staged_from") or {}) != _shas(hub, drafts)):
            reasons.append("the delta was compiled against a release no longer in force: it is being compiled again (or request review again)")
        elif p is not None and (f.get("staged_readings") or "") != picture_reading.digest(picture_reading.confirmed_of(co)):
            reasons.append("a picture's reading was confirmed after the last compile: request review again, so it is compiled into "
                           "the SOP's text and read with it")
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
    from . import configdocs  # the link vocabulary and retention classes (issue #33)

    reasons += configdocs.carried_reasons(hub, co)
    from . import workflows  # workstream 8: workflow layouts and graph snapshots carried from the editor

    reasons += workflows.carried_reasons(hub, co)
    reasons += _cr_reasons(hub, co)
    reasons += [c["reason"] for c in committee_reviews(hub, co) if c["status"] != "agreed"]
    reasons += cog_sign_problems(new_rules(hub, co))
    return reasons


def cog_sign_problems(rules: list[dict]) -> list[str]:
    """A person signs a change order (as "a cog never signs its own work", a guard, not configuration): rules that would
    let a cog sign one are refused: approval criteria for a cog on the change-order process, or its approval routed to
    a cog."""
    out = []
    for r in rules:
        if r.get("process") != "change-order":
            continue
        if r.get("kind") == "approval_criteria" and str(r.get("approver") or "").startswith("cog:"):
            out.append(f"a person signs a change order: “{_sentence(r)}” would let {r['approver'][4:]} sign one")
        elif r.get("kind") == "routing" and str(r.get("assignee") or "").startswith("cog:") and (r.get("mode") or "assign") == "assign":
            out.append(f"a person signs a change order: “{_sentence(r)}” routes its approval to a cog")
    return out


# ---------------------------------------------------------------- committees on the panel (groups and committees)
def committee_reviews(hub, co_ref, today: str | None = None) -> list[dict]:
    """Each committee on the current round's panel, by consensus (change control 11): ``agreed`` when every member has
    reviewed and none raised a blocking finding (an objection); ``objected`` when one did (it returns for revision: the
    author revises and requests review again); ``waiting`` while a member has not reviewed; ``paused`` when a member
    could not review (its model is not available) or the chair is asked to intervene. A committee on the panel must
    agree before the change order is sent for approval: nothing passes by default. Each: {"committee", "chair",
    "members", "status", "objections": [{"member", "text"}], "waiting", "paused", "reason", "due", "vote", "interveners"}.
    A member who has not answered by the due date pauses it too (``kind`` late), as one whose review failed (unavailable):
    the chair (or, when the chair is that member or contributed to the change order, the change-order overdue route)
    intervenes with :func:`intervene_review`."""
    from datetime import date

    from .groups import committee

    today = today or date.today().isoformat()
    co = _co(hub, co_ref)
    rnd = current_round(co)
    if rnd is None or rnd.get("status") in ("compiling", "failed"):
        return []
    b = _bundle(hub)
    names = list(dict.fromkeys(e["committee"] for e in rnd.get("panel") or [] if e.get("committee")))
    out = []
    for name in names:
        c = committee(b, name) or {"members": [], "chair": ""}
        # Every member of the committee (from its definition, not the panel): consensus means all of them.
        members = list(dict.fromkeys(list(c.get("members") or []) + [e["who"] for e in rnd.get("panel") or [] if e.get("committee") == name]))
        res = rnd.get("results") or {}
        objections = [{"member": m, "text": f.get("text")} for m in members for f in (res.get(m) or {}).get("findings") or []
                      if f.get("severity") == "blocking" and (res.get(m) or {}).get("status") == "done"]
        paused = [{"member": m, "why": (res.get(m) or {}).get("error") or "its review did not run", "kind": "unavailable"} for m in members
                  if (res.get(m) or {}).get("status") == "failed"]
        waiting = [m for m in members if (res.get(m) or {}).get("status") not in ("done", "failed")]
        paused += [{"member": m, "why": f"has not responded by {(res.get(m) or {}).get('due')}", "kind": "late", "due": (res.get(m) or {}).get("due")}
                   for m in waiting if (res.get(m) or {}).get("due") and res[m]["due"] < today]
        waiting = [m for m in waiting if m not in {p["member"] for p in paused}]
        dues = sorted(x for x in ((res.get(m) or {}).get("due") for m in members) if x)
        chair = c.get("chair") or ""
        whom = {p["member"]: _panel_interveners(hub, co, c, p["member"]) for p in paused}
        for p in paused:
            p["interveners"], p["who"] = whom[p["member"]]
        if objections:
            status = "objected"
            reason = (f"the {name} committee does not agree: " + "; ".join(f"{o['member']} objects: {o['text']}" for o in objections)
                      + " (revise and request review again)")
        elif paused:
            status = "paused"
            people = sorted({x for p in paused for x in p["interveners"]})
            reason = (f"the {name} committee cannot agree: " + "; ".join(f"{p['member']} {p['why']}" for p in paused)
                      + (f"; its chair, {chair}, intervenes" if people == [chair] else f"; {', '.join(people) or 'nobody'} intervenes ({paused[0]['who']})")
                      + " (decide in the member's place, extend the due date, or return it)")
        elif waiting:
            status = "waiting"
            reason = f"the {name} committee has not agreed yet: waiting for {', '.join(waiting)}"
        else:
            status, reason = "agreed", f"the {name} committee agrees ({', '.join(members)})"
        out.append({"committee": name, "chair": chair, "members": members, "status": status, "objections": objections,
                    "waiting": waiting, "paused": paused, "reason": reason, "due": dues[-1] if dues else None, "vote": vote_words(name),
                    "interveners": sorted({x for p in paused for x in p["interveners"]})})
    # A committee that objected in an earlier round and is not on this panel: its objection stands (governance H5) until
    # it withdraws it (on a panel again, reviewing without objecting) or its chair intervenes there.
    latest = _latest_results(co)
    for r in rounds(co)[:-1]:
        for name in dict.fromkeys(e["committee"] for e in r.get("panel") or [] if e.get("committee")):
            if name in names or name in {x["committee"] for x in out}:
                continue
            c = committee(b, name) or {"members": [], "chair": ""}
            members = list(dict.fromkeys(list(c.get("members") or []) + [e["who"] for e in r.get("panel") or [] if e.get("committee") == name]))
            objections = [{"member": m, "text": f.get("text")} for m in members for f in (latest.get(m, (0, {}))[1].get("findings") or [])
                          if f.get("severity") == "blocking"]
            if objections:
                out.append({"committee": name, "chair": c.get("chair") or "", "members": members, "status": "objected", "objections": objections,
                            "waiting": [], "paused": [], "due": None, "vote": vote_words(name), "interveners": [], "dropped": True,
                            "reason": (f"the {name} committee objected and was left off this round's panel: its objection stands ("
                                       + "; ".join(f"{o['member']}: {o['text']}" for o in objections)
                                       + "); put it back on the panel to review again, or its chair intervenes there")})
    return out


def _panel_interveners(hub, co: dict, c: dict, member: str) -> tuple[list[str], str]:
    """Who intervenes for a member of a committee on the panel: its chair; when the chair is that member, contributed to
    the change order (segregation of duties) or is not a person of the organization, the change-order process's overdue
    route. Returns (people, why them)."""
    from .routing import candidates, principals_for

    b = _bundle(hub)
    chair = c.get("chair") or ""
    keep_out = set(contributors(hub, co)) | {member}
    if chair and (b.get("members", {}).get(chair) or {}).get("type") == "person" and chair not in keep_out:
        return [chair], "the chair"
    why = (f"the chair, {chair}, is the member in question" if chair == member else
           f"the chair, {chair}, contributed to {co['number']}" if chair in keep_out else "the chair is absent")
    rules = [r for r in candidates(b, "change-order", "*", {"overdue": True}) if r["step"] == "*" and r.get("mode", "assign") == "assign"]
    people = []
    for r in rules:
        people += [p for p in principals_for(b, r["assignee"]) if (b["members"].get(p) or {}).get("type") == "person"
                   and p not in keep_out and p not in people]
    return people, f"{why}: escalated under the overdue rule" + (f" ({rules[0]['source']['text']})" if rules else "")


def tick_panels(hub, today: str | None = None) -> list[dict]:
    """Tell whoever intervenes, once, when a committee member on an open change order's panel is late. Called by the
    runner's tick."""
    told = []
    for co in open_change_orders(hub):
        rnd = current_round(co)
        if not rnd:
            continue
        for c in committee_reviews(hub, co, today=today):
            for p in c["paused"]:
                if p.get("kind") != "late" or ((rnd.get("results") or {}).get(p["member"]) or {}).get("late_told"):
                    continue
                for who in p["interveners"]:
                    hub.inbox.notify(who, kind="intervention", subject=f"Intervene: {co['number']} ({c['committee']} committee): {p['member']} {p['why']}",
                                     body=(f"The {c['committee']} committee decides by consensus, so {co['number']} cannot be sent for approval. "
                                           f"You are asked as {p['who']}: decide in {p['member']}'s place (agree, or object with your reason), "
                                           "extend the due date, or return the change for revision. Nothing passes by default."), record=co["locator"])
                _update_round(hub, co, rnd["n"], lambda r, m=p["member"]: r["results"][m].update({"late_told": today or utcnow()[:10]}))
                told.append({"co": co["number"], "member": p["member"], "to": p["interveners"]})
    return told


def intervene_review(hub, co_ref, committee_name: str, member: str, by: str, meaning: str, *, action: str = "decide",
                     verdict: str = "agree", due: str | None = None, today: str | None = None) -> dict:
    """Whoever intervenes for a paused member of a committee on the panel (its chair, or the overdue route when the chair
    is that member or contributed): the member could not review (its model is not available) or has not responded by
    the due date. ``action``: ``decide`` in the member's place (``verdict`` agree, or object: a blocking finding with the
    meaning as its reason), ``extend`` (a new ``due`` date: the member is asked again) or ``return`` (the change goes back
    for revision: an objection in the member's place). Every intervention is recorded with its meaning."""
    from datetime import date

    from .groups import committee

    today = today or date.today().isoformat()
    co = sync(hub, co_ref)
    rnd = current_round(co)
    c = committee(_bundle(hub), committee_name)
    if rnd is None or c is None:
        raise ValueError(f"the {committee_name} committee is not on the panel of {co['number']}")
    entry = next((e for e in rnd.get("panel") or [] if e["who"] == member and e.get("committee") == committee_name), None)
    if entry is None:
        raise ValueError(f"{member} is not on the panel for the {committee_name} committee")
    cur = (rnd.get("results") or {}).get(member) or {}
    if cur.get("status") == "done":
        raise ValueError(f"{member} has reviewed already")
    people, who = _panel_interveners(hub, co, c, member)
    if by not in people:
        raise ValueError(f"only the chair of the {committee_name} committee ({c.get('chair') or 'none'}) intervenes"
                         + (f"; here {who}, so {', '.join(people) or 'nobody'}" if people != [c.get("chair")] else ""))
    late = bool(cur.get("due")) and cur["due"] < today and cur.get("status") != "failed"
    if cur.get("status") != "failed" and not late:
        raise ValueError(f"{member} has until {cur.get('due') or 'the end of the round'} to answer: nothing to intervene in yet")
    if not (meaning or "").strip():
        raise ValueError("an intervention needs its meaning: say why you intervene")
    meaning = meaning.strip()
    note = {"by": by, "meaning": meaning, "for": member, "at": utcnow(), "action": action, "who": who,
            "was": cur.get("error") or (f"has not responded by {cur.get('due')}" if late else None)}
    if action == "extend":
        if not due or due <= today:
            raise ValueError("say the new due date (YYYY-MM-DD), after today")
        _update_round(hub, co, rnd["n"], lambda r: r["results"][member].update({"due": due, "late_told": None,
                                                                                 "extended": (r["results"][member].get("extended") or []) + [note]}))
        if entry["kind"] == "person":
            hub.inbox.notify(member, kind="review", subject=f"Your vote on {co['number']} ({committee_name} committee) is now due {due}",
                             body=f"{by} extended the due date: {meaning}. {vote_words(committee_name)}", record=co["locator"])
        return current_round(_co(hub, co["locator"]))["results"][member]
    if action not in ("decide", "return"):
        raise ValueError(f"{action!r} is not an intervention (decide, extend or return)")
    objects = action == "return" or verdict in ("object", "reject")
    findings = [{"id": f"R{rnd['n']}.{member}.F1", "severity": "blocking", "about": {},
                 "text": f"{by} ({who}) objects in {member}'s place: {meaning}"}] if objects else []
    result = {"status": "done", "kind": entry["kind"], "at": utcnow(), "findings": findings, "questions": [], "suggestions": [],
              "summary": (f"{'returned' if action == 'return' else 'decided'} by {by}, {who} of the {committee_name} committee, in {member}'s place: "
                          f"{'objects' if objects else 'agrees'}: {meaning}"),
              "intervention": note, "reviewed": _snapshot(_proposal(hub, co))}
    _record_result(hub, co, rnd["n"], member, result)
    return result


def approval_reasons(hub, co_ref) -> list[str]:
    """Why the CO Gate may not be signed yet (empty when it may): everything send_reasons checks; it is frozen for
    approval and nothing it carries changed since; its delta record verifies (every decision, acceptance and override
    a signed event); every delta item decided, by someone who did not make it (a widening by two policy owners who did
    not); every warning accepted; no decision left stale; no two approved items set one rule; the state map holds (only
    statuses this delta removes, to statuses of the new graph) and answers every removed status items are in; what
    hubproto.validation requires, when that module is there."""
    co = _co(hub, co_ref)
    if eco.finished(co) and co["state"] != "approved":
        return []
    reasons = send_reasons(hub, co)
    if stage_of(co) in ("awaiting-approval", "approved") or co["state"].startswith("awaiting"):
        if not co["fields"].get("frozen"):
            reasons.append("it is not frozen for approval: send it for approval again")
        changed = changed_since_sent(hub, co)
        if changed:
            reasons.append("it changed after it was sent for approval (" + ", ".join(changed) + "): it goes back to revising")
    reasons += verify_delta(hub, co)
    p = _proposal(hub, co)
    if p is not None:
        _refresh(hub, co, p)
        owners = p.get("reviewers") or _owners(hub)
        slots: dict[str, list[str]] = {}
        for c in p.get("changes") or []:
            if c.get("op") == "text":
                continue
            st = state_in(c, p, owners)
            if c.get("half_of"):  # decided with its change (reported there); its warnings are accepted when that is approved
                if st == "approved":
                    for w in c.get("warnings") or []:
                        if not w.get("accepted"):
                            reasons.append(f"accept the warning on “{c.get('reason')}”: [{w['code']}] {w['text']}")
                continue
            if st == "pending":
                live = [d for d in c.get("decisions") or [] if not d.get("stale")]
                if c.get("stale"):
                    reasons.append(f"decided before the baseline changed (or before it went back to revising), decide it again: {c.get('reason')}")
                elif _widening(c):
                    have = sorted({d["by"] for d in live if d["verdict"] == "approve"})
                    reasons.append(f"widening: {c.get('reason')} needs two policy owners, one of whom contributed nothing to it"
                                   + (f" (approved by {', '.join(have)})" if have else ""))
                elif any(d["by"] in (c.get("makers") or []) for d in live):
                    reasons.append(f"not decided by anyone who did not make it: {c.get('reason')}")
                else:
                    reasons.append(f"not decided yet: {c.get('reason')}")
            elif st == "approved":
                for w in c.get("warnings") or []:
                    if not w.get("accepted"):
                        reasons.append(f"accept the warning on “{c.get('reason')}”: [{w['code']}] {w['text']}")
                if c.get("op") in ("add", "change") or (c.get("op") == "remove" and c.get("override")):
                    slots.setdefault(_slot_key(_effective(c)), []).append(c.get("reason") or c["id"])
        for s, reasons_of in slots.items():
            if len(reasons_of) > 1 and _unique_slot(s):
                reasons.append("two approved items set the same rule: " + " and ".join(f"“{r}”" for r in reasons_of) + "; reject one")
        for w in p.get("warnings") or []:
            if not w.get("accepted"):
                reasons.append(f"accept the warning: [{w['code']}] {w['text']}")
    for q in (p or {}).get("questions") or []:
        if q.get("blocking") and q.get("status", "open") == "open":
            reasons.append(f"answer the question first ({q.get('code')}; its chair{(' ' + q['chair']) if q.get('chair') else ''} or a system "
                           f"administrator): {q.get('text')}")
    reasons += state_map_problems(hub, co, p)
    in_force = {r["name"]: r for r in hub.settings_rules() if r.get("kind") == "process"}
    for q in state_questions(hub, co, p):
        if not q.get("answer"):
            sname = _state_names(hub, in_force.get(q["process"]), q["process"]).get(q["state"], q["state"])
            reasons.append(f"{q['count']} {q['record_type']} item(s) are in {sname}, which this change removes from {q['process']}: "
                           "say where they go (state map)")
    try:
        from . import validation  # workstream 3: the IT SOP's validation of configuration deltas
    except ImportError:
        validation = None
    if validation is not None and hasattr(validation, "requirements"):
        reasons += list(validation.requirements(hub, co["locator"]) or [])
    return reasons


def validation_items(hub, co_ref) -> list[dict]:
    """The delta items with what the IT SOP's risk-based validation says of each (hubproto.validation.annotate: its risk
    class and the evidence it needs), for the CO page; the items as they are when that module is not there."""
    items_ = delta(hub, co_ref)
    try:
        from . import validation
    except ImportError:
        return items_
    return validation.annotate(items_) if hasattr(validation, "annotate") else items_


UNIQUE_KINDS = ("threshold", "sla", "process", "cog", "approval_criteria")  # one rule per slot (a member or group may be stated in several Frames)


def _unique_slot(slot_key: str) -> bool:
    try:
        return json.loads(slot_key)[0] in UNIQUE_KINDS
    except Exception:
        return False


# ---------------------------------------------------------------- sending, deciding, approving
def send_for_approval(hub, co_ref, by: str) -> dict:
    """The requester sends the change order for approval: it must be ready (send_reasons). It is frozen: what it
    carries is recorded by hash (``frozen``), its revisions are held (they take no new version), and from then on any
    change to it sends it back to revising with its decisions void. The change-order process takes it to the quality
    manager's Gate (a Gate already waiting for it, after it went back to revising, simply waits again). Returns the
    run's state."""
    from .runner import RunError

    co = sync(hub, co_ref)
    if by not in (requester(co), co.get("created_by")) and "document-controller" not in authz.roles_of(_bundle(hub), by):
        raise ValueError(f"only {requester(co)} (its requester) or a document controller sends {co['number']} for approval")
    run = hub.runner.run_for_record(co["locator"])
    waiting = bool(run and run["status"] == "waiting" and co["state"].startswith("awaiting"))
    if run and run["status"] in ("submitted", "running", "waiting") and not waiting:
        raise ValueError(f"{co['number']} is already in the change-order process ({run['status']})")
    st = stage_of(co)
    if st == "preparing":
        raise ValueError(f"{co['number']} has not been reviewed: request review first")
    if st not in ("in-review", "revising") and not (waiting and st == "awaiting-approval" and not co["fields"].get("frozen")):
        raise ValueError(f"{co['number']} is {st.replace('-', ' ')}")
    reasons = send_reasons(hub, co)
    if reasons:
        raise ValueError("it cannot be sent for approval yet: " + "; ".join(reasons))
    _hold_drafts(hub, co, True)
    frozen = {**content_digest(hub, co), "proposal": co["fields"].get("staged_proposal"), "at": utcnow(), "by": by}
    co = _set(hub, co, by=by, stage="awaiting-approval", frozen=frozen, rejected=None)
    hub.kernel.append("co.sent", co["locator"], {"frozen": frozen, "by": by}, by=by)
    _seal(hub, co)
    if waiting:
        return hub.runner.load(run["id"])
    try:
        run_id = hub.runner.submit("change-order", {"record": {}}, owner=by, record=co["locator"])
    except RunError as e:
        _hold_drafts(hub, co, False)
        _set(hub, _co(hub, co["locator"]), stage=st, frozen=None)
        raise ValueError(str(e)) from None
    state = hub.runner.load(run_id)
    if state["status"] not in ("waiting", "completed", "running", "submitted"):
        why = next((e.get("reason") for e in reversed(hub.runner.track(run_id)) if e.get("reason")), state["status"])
        _hold_drafts(hub, co, False)
        _set(hub, _co(hub, co["locator"]), stage=st, frozen=None)
        raise ValueError(f"the change-order process stopped: {why}")
    return state


def _accepts(accept, w: dict) -> bool:
    accept = set(accept or ())
    return bool(accept & {"*", "all", w.get("ref"), w.get("code"), w.get("text")})


def decide_item(hub, co_ref, item_id: str, verdict: str, by: str, meaning: str, accept_warnings=(), override: dict | None = None) -> dict:
    """An approver's decision on one delta item: approve or reject, with a meaning. To approve, every warning the item
    carries must be accepted (``accept_warnings``: refs, codes, or "all"); each acceptance is recorded. Who made the item
    (wrote it, made it directly, accepted it as a suggestion, overrode it, set the state-map entry) does not decide it.
    ``override`` edits the item's value (a rule of the same kind and slot, or fields to change in it): the overrider
    becomes a maker of the item, so another approver approves the new value; an override is a warning, and may leave a
    divergence between configuration and prose. A widening needs two policy owners, one of whom contributed nothing. Every
    decision, acceptance and override is a signed, chained event on the change order; a re-decision appends. Returns
    the item."""
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
    _refresh(hub, co, p)
    c = next((x for x in p.get("changes") or [] if x["id"] == item_id), None)
    if c is None:
        raise ValueError(f"{co['number']} has no item {item_id}")
    if c.get("op") == "text":
        raise ValueError("a wording change is the revision itself: the change order's approval approves it")
    if c.get("half_of"):  # the removal a change supersedes is decided with that change: nothing to record for it here
        if override is not None:
            raise ValueError(f"this removal is the other half of a change ({c.get('reason')}): override the change it is superseded by")
        return {**c, "state": state_in(c, p, p.get("reviewers") or _owners(hub)), "note": "decided with the change it is superseded by"}
    if by in (c.get("makers") or []) and not (_widening(c) and by in (p.get("reviewers") or _owners(hub)) and override is None):
        raise ValueError(f"segregation of duties: {by} made this change ({c.get('reason')}); someone who did not decides it "
                         f"({CHANGE_CONTROL_SOP}: “{SOD_SENTENCE}”; a policy owner who made a widening may be one of its two owners)")
    now = utcnow()
    acc = dict(p.get("acceptances") or {})
    if override is not None:
        if verdict != "approve":
            raise ValueError("an override sets the value the item comes into force with: give it with approve")
        if c.get("op") in ("state-map", "retire"):
            raise ValueError(f"{'a state-map entry is changed by setting the state map' if c['op'] == 'state-map' else 'a retirement is taken off the change order'}, not by an override")
        new = dict(override) if override.get("kind") else {**c["rule"], **override}
        new = {k: v for k, v in new.items() if k not in ("source", "key")}
        if new.get("kind") != c["rule"].get("kind") or _slot(new) != _slot(c["rule"]):
            raise ValueError("an override changes the item's value; it keeps its kind and what it is about (its slot)")
        new["source"] = {**((c["rule"].get("source")) or {}), "override": {"by": by, "at": now, "co": co["number"]}}
        problems = validate_rule(new)
        if problems:
            raise ValueError("; ".join(problems))
        if rule_key(new) == rule_key(c["rule"]):
            raise ValueError("that override is the item as it stands")
        base = hub.settings_rules()
        was = c.get("changes_from") or c.get("was")
        target = _apply(base, [{"op": "change" if was else "add", "rule": new, **({"was": was} if was else {})}]
                        + ([{"op": "remove", "rule": c["rule"]}] if c.get("op") == "remove" else []))
        errs = [x for x in consistency_guard(target, hub.registry.ids(), hub.kernel.types) if x.severity == "error" and x.rule_key == rule_key(new)]
        if errs:
            raise ValueError("the override does not hold: " + "; ".join(x.detail for x in errs[:3]))
        widen = any(d.get("widening") for d in drift_guard(base, target) if d["op"] == "add" and rule_key(d["rule"]) == rule_key(new))
        for d in c.get("decisions") or []:
            d["stale"] = True  # they decided another value
        c["override"] = {"rule": new, "by": by, "at": now, "meaning": meaning, "widening": widen}
        _record(hub, co, "override", {**_signed(hub, co, by=by, verdict="override", meaning=meaning, step=item_id, payload=rule_key(new), at=now),
                                      "item": item_id, "rule": new}, by)
        p["acceptances"] = acc
        _refresh(hub, co, p)
        hub.proposals.save(p)
        _seal(hub, co)
        return {**c, "state": item_state(c, p.get("reviewers") or _owners(hub)),
                "note": "overridden: another approver, who did not make it, approves the new value"}
    taken = []
    if verdict == "approve":
        own = [(item_id, w) for w in c["warnings"]] + [(h["id"], w) for h in halves(p, item_id) for w in h.get("warnings") or []]
        missing = [w for _, w in own if not w.get("accepted") and not _accepts(accept_warnings, w)]
        if missing:
            raise ValueError("accept each warning to approve it (“I accept: …”): " + "; ".join(f"[{w['code']}] {w['text']}" for w in missing))
        for wid, w in own:
            if not w.get("accepted"):
                key = _wkey(wid, w)
                acc[key] = {"by": by, "meaning": meaning, "at": now}
                _record(hub, co, "warning_accepted", {**_signed(hub, co, by=by, verdict="accept", meaning=meaning, step=key,
                                                                payload=_hash(w.get("text")), at=now), "item": wid}, by)
            taken.append(w.get("ref"))
    decision = {"by": by, "verdict": verdict, "at": now, "meaning": meaning, "change_digest": rule_key(_effective(c)), "accepted_warnings": taken}
    _record(hub, co, "item_decided", {**_signed(hub, co, by=by, verdict=verdict, meaning=meaning, step=item_id, payload=rule_key(_effective(c)),
                                                at=now), "item": item_id, "accepted_warnings": taken, "proposal": p["id"]}, by)
    c["decisions"] = [d for d in c.get("decisions") or [] if d["by"] != by or d.get("stale")] + [decision]
    p["acceptances"] = acc
    _refresh(hub, co, p)
    hub.proposals.save(p)
    _seal(hub, co)
    return {**c, "state": state_in(c, p, p.get("reviewers") or _owners(hub))}


def accept_warning(hub, co_ref, ref: str, by: str, meaning: str) -> dict:
    """Accept a warning on the change order as a whole (a kept divergence, a finding or question about no single
    item): an approver who contributed nothing to the change order ticks "I accept: ..."; recorded as a signed event."""
    co = sync(hub, co_ref)
    why = _may_decide(hub, co, by)
    if why:
        raise ValueError(why)
    if by in contributors(hub, co):
        raise ValueError(f"segregation of duties: {by} contributed to {co['number']}; someone independent accepts what is raised against it")
    if not (meaning or "").strip():
        raise ValueError("say what you accept, in a few words")
    p = _proposal(hub, co)
    if p is None:
        raise ValueError(f"{co['number']} has no compiled delta")
    _refresh(hub, co, p)
    w = next((x for x in p.get("warnings") or [] if x.get("ref") == ref), None)
    if w is None:
        raise ValueError(f"{co['number']} has no warning {ref}")
    now = utcnow()
    key = _wkey("co", w)
    p.setdefault("acceptances", {})[key] = {"by": by, "meaning": meaning, "at": now}
    _record(hub, co, "warning_accepted", {**_signed(hub, co, by=by, verdict="accept", meaning=meaning, step=key, payload=_hash(w.get("text")),
                                                    at=now), "item": None}, by)
    _refresh(hub, co, p)
    hub.proposals.save(p)
    _seal(hub, co)
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
    if decision.get("by_type") not in (None, "person") or not _person(hub, approver):
        raise ValueError(f"{co['number']} was signed by {approver}, which is not a person: a person signs a change order")
    if approver in may_not_sign(hub, co):
        raise ValueError(f"segregation of duties: {approver} contributed to {co['number']} and may not approve it "
                         f"({CHANGE_CONTROL_SOP}: “{SOD_SENTENCE}”)")
    frozen = co["fields"].get("frozen") or {}
    grant = state.get("grant") or {}
    done = []
    for c in eco.carried(hub, co):
        d = hub.kernel.get(c["draft"]["locator"])
        f = d["fields"]
        sha = str(f.get("content_sha") or "")
        if (frozen.get("drafts") or {}).get(c["name"], sha) != sha:  # one approved already counts only as signed (issue #53)
            raise ValueError(f"{d['number']} changed after {co['number']} was sent for approval: it is not what was signed")
        if d["state"] == "effective" or (d["state"] == "approved" and f.get("held_for") == co["number"] and f.get("approved_run")):
            done.append(d["number"])
            continue
        if approver in _version_authors(hub, d):
            raise ValueError(f"segregation of duties: {approver} wrote {d['number']} and may not approve it (document-control-sop)")
        # The CO's signature, unchanged (it verifies), with what it approves here beside it.
        sig = {k: decision.get(k) for k in ("by", "by_type", "verdict", "meaning", "at", "payload_digest", "record_digest", "signature",
                                            "run_id", "step", "release_digest")}
        hub.kernel.append("record.decision", d["locator"], {**sig, "approves": d["number"], "via": co["number"], "approved_sha": sha,
                                                             "note": f"approved with {co['number']}: the change order's signature approves this revision"},
                          run_id=state["id"], release=state["release"], by=approver)
        hub.kernel.set_fields(d["locator"], {"held_for": co["number"], "approved_by": approver, "approved_run": state["id"], "approved_grant": grant.get("id"),
                                             "approved_release": state["release"], "approved_with": co["number"], "approved_sha": sha},
                              by="hub", run_id=state["id"], release=state["release"])
        if d["state"] != "approved":
            hub.kernel.transition(d["locator"], "approved", by="hub", run_id=state["id"], release=state["release"],
                                  reason=f"approved with {co['number']} by {approver}")
        done.append(d["number"])
    return done


# ---------------------------------------------------------------- release: the decided delta, divergences
def apply_delta(hub, co_ref) -> dict:
    """Before the release: the decided delta goes into the staged proposal's rules. Stale decisions are set aside; an
    approved override replaces its rule (and the change carries the override's widening, so the compiler's own check
    of approvals agrees); an approved direct change the prose does not state replaces whatever holds its slot. Such
    items and the state-map entries leave the proposal's changes (``applied_items``), so the compiler's release logic
    sees only prose changes. What the prose states is kept as ``prose_rules`` (divergences are measured against it).
    Refuses two rules for one slot. Safe to repeat."""
    co = _co(hub, co_ref)
    p = _proposal(hub, co)
    if p is None or p.get("applied") or p.get("status") != "open":
        return {"overrides": 0, "direct": 0}
    _refresh(hub, co, p)
    owners = p.get("reviewers") or _owners(hub)
    prose = {rule_key(r): r for r in p["rules"]}
    for f in p.get("findings") or []:  # what the prose itself says where a kept divergence put the rule in force back
        if f.get("guard") == "divergence":
            d = next((x for x in open_divergences(hub, p.get("base")) if x["id"] == str(f.get("rule_key") or "").split(":", 1)[-1]), None)
            if d and d.get("in_force") is not None:
                prose.pop(rule_key(d["in_force"]), None)
            if d and d.get("prose_rule") is not None:
                prose[rule_key(d["prose_rule"])] = d["prose_rule"]
    p["prose_rules"] = copy.deepcopy(list(prose.values()))
    rules = {rule_key(r): r for r in p["rules"]}
    applied, keep, n_over, n_direct = [], [], 0, 0
    touched: set[str] = set()

    def put(old, new, why):
        if old is not None:
            rules.pop(rule_key(old), None)
        if new is not None:
            if not new.get("source"):
                new = {**new, "source": {"frame": "settings", "version": "", "line": 0, "text": _sentence(new), "direct": why}}
            if _unique_slot(_slot_key(new)):
                for k in [k for k, r in rules.items() if _slot(r) == _slot(new)]:
                    rules.pop(k)  # the decided value holds the slot alone
            rules[rule_key(new)] = new
            touched.add(_slot_key(new))

    for c in p.get("changes") or []:
        if c.get("op") == "text":  # only the CO's signature decides a wording change (a decision made elsewhere does not)
            c["set_aside"] = [d for d in c.get("decisions") or [] if not str(d.get("by") or "").startswith("change-order:")]
            c["decisions"] = [d for d in c.get("decisions") or [] if str(d.get("by") or "").startswith("change-order:")]
            keep.append(c)
            continue
        c["stale_decisions"] = [d for d in c.get("decisions") or [] if d.get("stale")]
        c["decisions"] = [d for d in c.get("decisions") or [] if not d.get("stale")]
        st = state_in(c, p, owners)
        if c.get("op") in ("state-map", "retire"):
            applied.append({**c, "state": st})
            continue
        if c.get("unstated"):
            if st == "approved":
                old, new = _old_new(c)
                put(old, (c.get("override") or {}).get("rule") or new, c["id"])
                n_direct += 1
            applied.append({**c, "state": st})
            continue
        if c.get("override") and st == "approved":
            put(c["rule"], c["override"]["rule"], c["id"])
            c["widening"] = bool(c["override"].get("widening"))  # what was approved is the override
            n_over += 1
        keep.append(c)
    counts: dict[str, int] = {}
    for r in rules.values():
        if _slot_key(r) in touched and _unique_slot(_slot_key(r)):
            counts[_slot_key(r)] = counts.get(_slot_key(r), 0) + 1
    twice = [s for s, n in counts.items() if n > 1]
    if twice:
        raise ValueError("the decided delta would put two rules in one slot: " + "; ".join(twice[:3]) + ": reject one of them and sign again")
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
    final = hub.releases.rules(digest)
    final_keys = {rule_key(r) for r in final}
    final_slots = {_slot_key(r) for r in final}

    def still(d):  # the configuration still differs from the prose there as the divergence says
        inf, pr = d.get("in_force"), d.get("prose_rule")
        holds = (rule_key(inf) in final_keys) if inf is not None else (pr is None or rule_key(pr) not in final_keys)
        return holds and (inf is not None or d.get("slot") not in final_slots or pr is not None)

    kept_ids = {str(f.get("rule_key") or "").split(":", 1)[-1] for f in p.get("findings") or [] if f.get("guard") == "divergence"}
    texts = hub.releases.frame_texts(digest) if hasattr(hub.releases, "frame_texts") else {}

    def unread_still(d):  # a sentence the hub does not enforce: open while the SOP still says it
        return bool(d.get("prose")) and d["prose"] in (texts.get(d.get("frame")) or "")

    kept = [d for d in before if (unread_still(d) if d.get("kind") == "unread" else d["id"] in kept_ids and still(d))]
    resolved = [d["id"] for d in before if d not in kept]
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
    # The committee's chair answered "record as a divergence" for prose the hub cannot read: the sentence is in force as
    # text, not as configuration; that is a divergence of its own (QMS A4).
    for q in p.get("questions") or []:
        if q.get("record_divergence") and q.get("status") == "answered":
            quote = q.get("quote") or q.get("text") or ""
            new.append({"id": f"{co['number']}/D{len(new) + 1}", "kind": "unread", "frame": q.get("frame"), "line": q.get("line"),
                        "prose": quote, "prose_rule": None, "in_force": None, "slot": f"unread:{q.get('frame')}:{q.get('line')}",
                        "co": co["number"], "accepted_by": q.get("by"), "meaning": q.get("answer"), "at": utcnow(), "status": "open",
                        "question": q["id"]})
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
        days = int(((((_bundle(hub).get("workflows") or {}).get("change-control") or {}).get("sla") or {}).get("approve-the-change")) or 0) \
            or DIVERGENCE_DAYS
        from datetime import date, timedelta

        due = (date.today() + timedelta(days=days)).isoformat()
        hub.kernel.assign(cr["locator"], [owner], by="hub", due=due)
        d["change_request"], d["due"] = cr["number"], due
        out.append(cr["number"])
        hub.inbox.notify(owner, kind="task", subject=f"{cr['number']}: bring {where} in line with what is in force",
                         body=f"{co['number']} was released with a divergence ({d['id']}). Raise the change through a change order.",
                         record=cr["locator"])
    if out:
        _annotate_release(hub, digest, divergences=divs)
    return out


# ---------------------------------------------------------------- release: every open CO, related changes
def open_change_orders(hub, exclude: str | None = None) -> list[dict]:
    """Every change order not yet released: preparing, in review, revising, waiting at its Gate, or approved but not
    released yet (its release job has not run, or stopped)."""
    return [r for r in hub.kernel.query(type_="change-order") if r["locator"] != exclude
            and (not eco.finished(r) or (r["state"] == "approved" and not r["fields"].get("released_release")))]


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
    except Exception as e:  # said in the release job: "none found" and "could not look" are not the same
        raise LookupError(f"{cog} could not look ({type(e).__name__}: {e})") from None
    if out.get("stand_in"):
        return []  # the stand-in finds no relation by meaning
    numbers = {c["number"] for c in candidates}
    found = []
    for line in (out.get("text") or "").splitlines():
        m = re.match(r"^\s*[-*]\s*([A-Z]+-[\w-]+)\s*:\s*(.+)$", line)
        if m and m.group(1) in numbers:
            found.append((m.group(1), m.group(2).strip()))
    return found


def _flag_co(hub, other: dict, released: dict, kind: str, reasons: list[str], cog: str | None = None) -> bool:
    """Send a change order related to the released one back with a ``baseline_changed`` flag (True), unless it is signed
    and part of it is already in force (False: it is not sent back; ``find_related`` says so)."""
    if other["state"] == "approved" and in_force_from(hub, other):
        return False  # part of it is in force: it is not sent back, and nothing it was signed with is made stale (issue #53)
    f = other["fields"]
    cur = f.get("baseline_changed") or {}
    if cur and not cur.get("dismissed") and cur.get("by") == released["number"]:
        kind = "definition" if "definition" in (kind, cur.get("kind")) else kind
        reasons = list(dict.fromkeys(list(cur.get("reasons") or []) + reasons))
    entry = {"by": released["number"], "kind": kind, "reasons": reasons, "cog": cog, "at": utcnow(), "dismissed": None}
    if other["state"] == "approved" and not f.get("released_release"):
        other = _back_to_revising(hub, other, f"{released['number']} was released after it was signed and is related to it")
        f = other["fields"]
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
    return True


def _flag_cr(hub, cr: dict, released: dict, cog: str, reason: str) -> None:
    approver = next((d["by"] for d in reversed(cr.get("decisions") or []) if d.get("verdict") == "approve"), None) or cr["fields"].get("requested_by")
    task, failed = None, []
    for owner in (cog, approver):
        try:
            run_id = hub.runner.submit("task", {"record": {"title": f"Review {cr['number']} against {released['number']}",
                                                           "description": f"{cog}: {reason}", "assignee": approver}}, owner=owner)
            task = hub.kernel.get(hub.runner.load(run_id)["record"])
            hub.kernel.link(task["locator"], "references", cr["locator"], by=owner)
            break
        except Exception as e:
            failed.append(f"{owner}: {e}")
            continue
    hub.kernel.set_fields(cr["locator"], {"flagged": {"by": released["number"], "cog": cog, "reason": reason, "at": utcnow(), "approver": approver,
                                                      "task": task["number"] if task else None, "task_error": "; ".join(failed) if not task else None,
                                                      "cleared": None}}, by="hub")
    if approver:
        hub.inbox.notify(approver, kind="task", subject=f"{cr['number']} may be affected by {released['number']}",
                         body=f"{cog}: {reason}. Review it again" + (f" ({task['number']})" if task else "")
                              + "; no change order implementing it may be approved until you have.", record=cr["locator"])


def find_related(hub, co_ref) -> dict:
    """After a release: every open change order related to the released one is sent back. Related by definition (it
    touches the same SOP, process, rule slot, record type, form or role: cannot be dismissed); related by meaning (a
    cog of the released CO's panel, in its area, finds it so; its stand-in finds none). A related CO goes back to
    revising with a ``baseline_changed`` flag, its review results and item decisions stale. An approved change request
    no change order carries yet, found related by meaning, is flagged and its approver given a review task. A signed
    change order part of which is already in force is not sent back (``kept``: the sentence why, issue #53). Returns
    {"cos": [{"number", "kind", "reasons"}], "crs": [{"number", "cog", "reason"}], "kept": [str]}."""
    co = _co(hub, co_ref)
    mine = touches(hub, co)
    out: dict = {"cos": [], "crs": [], "kept": []}
    others = open_change_orders(hub, exclude=co["locator"])
    flagged = set()

    def kept(o: dict) -> None:  # signed, and part of it in force: not sent back (issue #53)
        out["kept"].append(in_force_refusal(o, in_force_from(hub, o)))
        flagged.add(o["number"])

    for o in others:
        reasons = _overlap(mine, touches(hub, o))
        if reasons:
            if not _flag_co(hub, o, co, "definition", reasons):
                kept(o)
                continue
            out["cos"].append({"number": o["number"], "kind": "definition", "reasons": reasons})
            flagged.add(o["number"])
    panel = [e["who"] for e in (current_round(co) or {}).get("panel") or [] if e.get("kind") == "cog" and e.get("member")]
    crs = _approved_uncarried_crs(hub)
    out["errors"] = []
    for cog in panel:
        cands = [o for o in others if o["number"] not in flagged] + [c for c in crs if not cr_flag_open(hub, c)]
        try:
            found = _meaning(hub, cog, co, cands)
        except LookupError as e:
            out["errors"].append(str(e))
            continue
        for number, reason in found:
            target = next((c for c in cands if c["number"] == number), None)
            if target is None:
                continue
            if target["type"] == "change-order":
                if not _flag_co(hub, _co(hub, target["locator"]), co, "meaning", [f"{cog}: {reason}"], cog=cog):
                    kept(target)
                    continue
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
        key = f"co|related|related:{bc.get('by')}"
        p.setdefault("acceptances", {})[key] = {"by": by, "meaning": meaning, "at": now}
        _record(hub, co, "warning_accepted", {**_signed(hub, co, by=by, verdict="accept", meaning=meaning, step=key, payload=_hash(reason),
                                                        at=now), "item": None, "dismissed": reason}, by)
        _refresh(hub, co, p)
        hub.proposals.save(p)
    _seal(hub, co)
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


# ---------------------------------------------------------------- role grants and what a change does to each cog
def _assignee_name(rule: dict) -> str | None:
    who = rule.get("assignee") if rule.get("kind") == "routing" else rule.get("approver") if rule.get("kind") == "approval_criteria" else None
    if not who:
        return None
    who = str(who)
    if who.startswith(("role:", "field:", "group:")):
        return None
    return who.split(":", 1)[1] if who.startswith(("cog:", "person:")) else who


def role_grant_suggestions(hub, co_ref) -> list[dict]:
    """When the delta gives a cog or a person work at a step that their roles do not cover (routing to them, or approval
    criteria for them, where they may not approve that kind of item in its folder), a suggested change to the org model
    adds the step's role to them. Never a grant by itself: the author accepts or rejects it like any suggestion."""
    from .release import render

    co = _co(hub, co_ref)
    rules = new_rules(hub, co)
    try:
        after = render(rules, hub.kernel.types)
    except Exception:
        return []
    members = after.get("members") or {}
    out, seen = [], set()
    for c in delta(hub, co):
        if c.get("op") in ("text", "state-map", "retire", "remove") or c.get("state") == "rejected":
            continue
        r = _effective(c)
        name = _assignee_name(r)
        if not name or name not in members or name in seen:
            continue
        proc = (after.get("processes") or {}).get(r.get("process")) or {}
        if not proc:
            continue
        if authz.check(after, name, "approve", proc.get("object") or "", proc.get("folder") or "")["allowed"]:
            continue
        cps = [s for s in proc.get("steps") or [] if s.get("kind") == "checkpoint"]
        step = next((s for s in cps if s.get("id") == r.get("step")), cps[0] if cps else None)
        role = (step or {}).get("role")
        member = next((m for m in rules if m.get("kind") == "member" and m.get("principal") == name), None)
        if not role or member is None or role in (member.get("roles") or []):
            continue
        seen.add(name)
        grant = {**{k: v for k, v in member.items() if k != "source"}, "roles": list(member.get("roles") or []) + [role]}
        out.append({"text": f"{name} is given work at {(step or {}).get('title') or r.get('step')} in {r.get('process')} that its roles do not "
                            f"cover: grant it the {role} role? (a suggestion: never granted by itself)",
                    "change": {"op": "change", "rule": grant, "was": member}, "frame": (member.get("source") or {}).get("frame"),
                    "status": "open", "by": "hub", "kind": "role-grant"})
    return out


def cog_impact(hub, co_ref) -> dict[str, list[dict]]:
    """What the change order does to each cog, as decided so far (rejected items left out): {cog: [{"item", "state",
    "what"}]}: work routed to it or taken from it, its approval criteria, its roles, its profile and persona, the
    permissions of roles it holds, the groups and committees it is in, the text of the SOPs it works from."""
    co = _co(hub, co_ref)
    b = _bundle(hub)
    members = b.get("members") or {}
    cogs = {n for n, m in members.items() if m.get("type") == "cog"}
    holders = lambda role: {n for n in cogs if role in authz.roles_of(b, n)}
    profiles = b.get("cogs") or {}
    out: dict[str, list[dict]] = {}
    for c in delta(hub, co):
        if c.get("state") == "rejected":
            continue
        names: set[str] = set()
        if c.get("op") == "text":
            names = {n for n, prof in profiles.items() if c.get("frame") in (prof.get("follows") or [])}
        else:
            for r in [_effective(c)] + [x for x in (c.get("changes_from"), c.get("was")) if x]:
                k = r.get("kind")
                if k in ("routing", "approval_criteria"):
                    names.add(_assignee_name(r) or "")
                    if str(r.get("assignee") or "").startswith("role:"):
                        names |= holders(r["assignee"][5:])
                elif k == "member":
                    names.add(r.get("principal"))
                elif k == "cog":
                    names.add(r.get("name"))
                elif k == "persona":
                    names |= {n for n, prof in profiles.items() if prof.get("persona") == r.get("name")}
                elif k == "permission" and str(r.get("subject") or "").startswith("role:"):
                    names |= holders(r["subject"][5:])
                elif k in ("group", "committee"):
                    names |= set(r.get("members") or [])
        for n in names & cogs:
            out.setdefault(n, []).append({"item": c["id"], "state": c.get("state"), "what": c.get("reason")})
    return out


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
            "state_questions": state_questions(hub, co), "proposal": p, "committees": committee_reviews(hub, co),
            "cog_impact": cog_impact(hub, co), "may_not_sign": list(co["fields"].get("contributor") or [])}
