"""Configuration tools for cogs: every change becomes a proposal the policy owners approve (a Frame draft,
a cog's model, a new member). A cog never changes configuration directly: it drafts, the hub compiles, and the
policy owners review and release through the same paths people use (two owners for anything that widens)."""

from __future__ import annotations

from .agent import intent_change_order, register
from .compile import change_state
from .frames import FrameError

_OWNERS_DECIDE = ("Nothing changes until the policy owners review and release it (two owners for anything that widens what "
                  "someone may do); you never change configuration yourself.")


def _rule_frame(change: dict) -> str:
    return ((change.get("rule") or {}).get("source") or {}).get("frame", "")


def _summary(proposal: dict, frames: set[str] | None = None) -> dict:
    """What a proposal holds, as a cog should hear it: its number, changes (and widenings), errors and reviewers."""
    changes = [c for c in proposal.get("changes") or [] if frames is None or _rule_frame(c) in frames or not _rule_frame(c)]
    errors = [f"{f.get('detail')} ({f.get('frame')}:{f.get('line')})" for f in proposal.get("findings") or [] if f.get("severity") == "error"]
    unread = [f"{q.get('quote')} ({q.get('frame')}:{q.get('line')})" for q in proposal.get("questions") or []
              if q.get("unread_note") and q.get("status", "open") == "open"]
    return {"proposal": proposal.get("number"), "proposal_id": proposal.get("id"), "status": proposal.get("status"),
            "changes": len(changes), "change_list": [c["reason"] for c in changes][:20],
            "widenings": [c["reason"] for c in changes if c.get("widening")],
            "errors": errors, "unread": unread, "blocked": bool(errors or unread), "reviewers": proposal.get("reviewers") or [],
            "note": (("The release is blocked until these errors are fixed: reword the policy and propose again (your draft carries "
                      "forward), or a policy owner withdraws the draft. ") if errors else "")
                    + (("No reader read a rule from the sentences under unread: a person answers each question, or reword the policy "
                        "and propose again (your draft carries forward). ") if unread else "") + _OWNERS_DECIDE}


def _notify_owners(tools, proposal: dict, subject: str, body: str, record: str | None = None) -> None:
    for owner in proposal.get("reviewers") or []:
        tools.hub.inbox.notify(owner, kind="policy-proposal", subject=subject, body=body, record=record)


# ---------------------------------------------------------------- propose_policy_change
def _frontmatter(text: str) -> str:
    """The '---' block at the top of a Frame, with its closing line."""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end < 0:
        return ""
    nl = text.find("\n", end + 4)
    return text[: nl + 1] if nl >= 0 else text + "\n"


def propose_policy_change(tools, a: dict) -> dict:
    from . import edits

    hub = tools.hub
    if hub.settings_source():
        return {"error": "the rules are edited as settings now: Frames are the SOPs people and cogs read. Use list_settings and "
                         "propose_setting_change for a rule; a change to an SOP's wording is a document change (edit its document)."}
    name = str(a.get("frame") or "").strip()
    reason = str(a.get("reason") or "").strip()
    try:
        frame = hub.frames.get(name)
    except FrameError:
        return {"error": f"there is no policy Frame {name!r}; the Frames are: {', '.join(sorted(hub.frames.frames))}"}
    if not reason:
        return {"error": "say why the policy should change (reason): the policy owners read it"}
    draft = hub.frame_draft(name, by=tools.cog)
    current = hub.draft_text(draft)
    report: list[dict] = []
    if a.get("text"):
        new_text = str(a["text"])
        if not new_text.lstrip().startswith("---"):  # a body without its header keeps the Frame's header
            new_text = _frontmatter(current) + new_text.lstrip("\n")
        if not new_text.endswith("\n"):
            new_text += "\n"
        report.append({"op": "rewrite the Frame", "ok": True, "detail": f"{len(new_text.splitlines())} line(s)"})
    else:
        ops = a.get("ops")
        ops = edits.parse_ops(ops) if isinstance(ops, str) else list(ops or [])
        ops = [o for o in ops if isinstance(o, dict) and o.get("op") not in (None, "unknown")]
        if not ops:
            return {"error": "give the change as edit operations (ops, in the '@@' language) or as the full new text (text)"}
        new, report = edits.apply_ops(current.encode("utf-8"), "md", ops)
        if new is None:
            return {"error": "the operations changed nothing in the Frame: " + "; ".join(f"{r['op']} ({r['detail']})" for r in report)}
        new_text = new.decode("utf-8")
    if new_text == current:
        return {"error": "the new text is the same as the draft; nothing to propose"}
    hub.dms.add_version(draft["locator"], new_text.encode("utf-8"), by=tools.cog, filename=frame.path.name, mime="text/markdown",
                        note=f"proposed by {tools.cog}: {reason[:200]}")
    try:
        proposal = hub.compile(overrides={name: new_text}, drafts={name: draft["locator"]})
    except FrameError as e:
        return {"error": f"the changed text is not a readable Frame: {e}"}
    out = _summary(proposal, {name})
    out.update({"ok": True, "frame": name, "draft": draft["number"], "operations": report})
    _notify_owners(tools, proposal, f"{tools.cog} proposes a change to {name}: proposal #{proposal.get('number')}",
                   f"Reason: {reason}\n" + (f"Widenings: {'; '.join(out['widenings'])}\n" if out["widenings"] else "")
                   + (f"Errors block the release: {'; '.join(out['errors'])}\n" if out["errors"] else "")
                   + f"Draft {draft['number']}; review it on proposal #{proposal.get('number')}.", record=draft["locator"])
    return out


# ---------------------------------------------------------------- propose_cog_model
def propose_cog_model(tools, a: dict) -> dict:
    from .model import resolve_model

    hub = tools.hub
    cog = str(a.get("cog") or tools.cog).strip()
    model = str(a.get("model") or "").strip()
    reason = str(a.get("reason") or "").strip()
    profile = (tools.bundle.get("cogs") or {}).get(cog)
    if (tools.bundle.get("members") or {}).get(cog, {}).get("type") != "cog" or profile is None:
        return {"error": f"{cog!r} is not a cog with a profile in the policy in force"}
    if not model:
        return {"error": "name the model (e.g. ollama/gemma4:12b-it-qat)"}
    if model == (profile.get("model") or ""):
        return {"error": f"{cog} already uses {model}"}
    res = resolve_model(model)
    if not res["available"]:
        return {"error": f"{model} cannot be used here: {res['note']}; a cog is bundled with its model, so it would be unavailable"}
    try:
        proposal = hub.propose_cog_model(cog, model, by=tools.cog)
    except (ValueError, FrameError) as e:
        return {"error": str(e)}
    out = _summary(proposal)
    out.update({"ok": True, "cog": cog, "model": model, "served_by": res["served"]})
    _notify_owners(tools, proposal, f"{tools.cog} proposes {model} for {cog}: proposal #{proposal.get('number')}",
                   (f"Reason: {reason}\n" if reason else "") + f"{cog} uses {profile.get('model') or 'no model'} now.")
    return out


# ---------------------------------------------------------------- propose_member
def _names(v) -> list[str]:
    return [str(x).strip() for x in (v.split(",") if isinstance(v, str) else (v or [])) if str(x).strip()]


def propose_member(tools, a: dict) -> dict:
    hub = tools.hub
    kind = str(a.get("kind") or "person").strip().lower()
    if kind not in ("person", "cog"):
        return {"error": "kind is person or cog"}
    roles = _names(a.get("roles"))
    known = set(tools.bundle.get("roles") or [])
    unknown = [r for r in roles if r not in known]
    if not roles:
        return {"error": "give at least one role; the roles are: " + ", ".join(sorted(known))}
    if unknown:
        return {"error": f"no such role: {', '.join(unknown)}; the roles are: {', '.join(sorted(known))}"}
    reason = str(a.get("reason") or "").strip()
    name = str(a.get("name") or "").strip().lower()
    try:
        if kind == "person":
            proposal = hub.propose_new_person(name=name, roles=roles, by=tools.cog)
        else:
            model = str(a.get("model") or "").strip()
            if model:
                from .model import resolve_model

                res = resolve_model(model)
                if not res["available"]:
                    return {"error": f"{model} cannot be used here: {res['note']}"}
            proposal = hub.propose_new_cog(name=name, roles=roles, model=model, about=str(a.get("about") or a.get("prompt") or ""),
                                           persona=str(a.get("persona") or ""), by=tools.cog)
    except (ValueError, FrameError) as e:
        return {"error": str(e)}
    out = _summary(proposal)
    out.update({"ok": True, "kind": kind, "name": name, "roles": roles})
    _notify_owners(tools, proposal, f"{tools.cog} proposes a new {kind}, {name}: proposal #{proposal.get('number')}",
                   (f"Reason: {reason}\n" if reason else "") + f"Roles: {', '.join(roles)}.")
    return out


# ---------------------------------------------------------------- list_proposals
def list_proposals(tools, a: dict) -> dict:
    out = []
    for p in tools.hub.proposals.list():
        if p.get("status") != "open":
            continue
        changes = p.get("changes") or []
        out.append({"proposal": p.get("number"), "proposal_id": p.get("id"), "created": p.get("created_at"),
                    "frames": sorted((p.get("overrides") or {}).keys()), "changes": len(changes),
                    "widenings": sum(1 for c in changes if c.get("widening")),
                    "decisions": sum(len(c.get("decisions") or []) for c in changes),
                    "errors": sum(1 for f in p.get("findings") or [] if f.get("severity") == "error"),
                    "still_need_decisions": [c["id"] for c in changes if change_state(c) == "pending"][:30],
                    "reviewers": p.get("reviewers") or []})
    return {"open": out, "count": len(out), "note": "Only the policy owners decide and release proposals."}


register({"name": "propose_policy_change",
          "description": "Propose a change to a policy Frame (an SOP or policy) as a draft the policy owners review. Give the change as edit "
                         "operations in the '@@' language (ops) or the full new text (text), and the reason. The hub compiles it into a "
                         "proposal and tells you its changes, widenings and any errors. " + _OWNERS_DECIDE,
          "parameters": {"type": "object", "properties": {
              "frame": {"type": "string", "description": "the Frame's name, e.g. finance-policy or document-control-sop"},
              "ops": {"type": "string", "description": "edit operations, e.g. '@@ replace: 10,000 USD\\n@@ with: 15,000 USD'"},
              "text": {"type": "string", "description": "the whole new text (instead of ops)"},
              "reason": {"type": "string"}}, "required": ["frame", "reason"]}},
         propose_policy_change, write=True, intent=intent_change_order)
register({"name": "propose_cog_model",
          "description": "Propose that a cog use a different model, as a policy proposal. The model must be one this hub can reach. " + _OWNERS_DECIDE,
          "parameters": {"type": "object", "properties": {"cog": {"type": "string", "description": "the cog (default: you)"},
                                                         "model": {"type": "string"}, "reason": {"type": "string"}}, "required": ["model"]}},
         propose_cog_model, write=True, intent=intent_change_order)
register({"name": "propose_member",
          "description": "Propose a new person or cog for the organization, with its roles (and for a cog what it does, its model and its "
                         "persona; what it follows and may do come from the SOPs that name its roles), as a policy proposal. " + _OWNERS_DECIDE,
          "parameters": {"type": "object", "properties": {
              "kind": {"type": "string", "enum": ["person", "cog"]}, "name": {"type": "string"},
              "roles": {"type": "array", "items": {"type": "string"}}, "model": {"type": "string"},
              "about": {"type": "string", "description": "a cog: what it does, in one paragraph"},
              "persona": {"type": "string", "description": "a cog: how it behaves (reviewer, executor, author, advisor)"},
              "reason": {"type": "string"}}, "required": ["kind", "name", "roles"]}},
         propose_member, write=True, intent=intent_change_order)
register({"name": "list_proposals",
          "description": "List the open policy proposals: their changes, widenings, errors, decisions so far and what still needs a decision. "
                         "Only the policy owners decide and release them.",
          "parameters": {"type": "object", "properties": {}}},
         list_proposals, write=False)


# ---------------------------------------------------------------- settings (the rules as data)
def list_settings(tools, a: dict) -> dict:
    """The rules in force, as data with a key and the sentence each reads as, narrowed by kind and words."""
    from .rules import rule_key
    from .settings import sentence

    hub = tools.hub
    procs = (hub.current_bundle() or {}).get("processes") or {}
    kind = str(a.get("kind") or "").strip()
    words = [w for w in str(a.get("contains") or "").lower().split() if w]
    out = []
    for r in hub.settings_rules():
        if kind and r.get("kind") != kind:
            continue
        text = sentence(r, procs)
        if words and not all(w in text.lower() for w in words):
            continue
        out.append({"key": rule_key(r), "kind": r["kind"], "reads": text, "rule": {k: v for k, v in r.items() if k != "source"},
                    "from": (r.get("source") or {}).get("frame")})
        if len(out) >= int(a.get("limit") or 30):
            break
    return {"count": len(out), "settings": out, "source_of_truth": "settings" if hub.settings_source() else "frames"}


def propose_setting_change(tools, a: dict) -> dict:
    """One change to the rules as data (add, replace or remove a rule), proposed to the policy owners at once."""
    hub = tools.hub
    action = str(a.get("action") or "")
    reason = str(a.get("reason") or "").strip()
    if action not in ("add", "replace", "remove"):
        return {"error": "action must be add, replace or remove"}
    if not reason:
        return {"error": "say why (reason): the policy owners read it"}
    hub.settings_discard(tools.cog)  # a cog's change is proposed on its own, never mixed with a stale draft
    try:
        hub.settings_edit(tools.cog, {"action": action, "key": str(a.get("key") or ""), "rule": a.get("rule") or {}})
        p = hub.settings_propose(tools.cog, reason=f"{reason} (proposed by {tools.cog}, agent run {tools.run_id})")
    except ValueError as e:
        hub.settings_discard(tools.cog)
        return {"error": str(e)}
    return {"ok": True, **_summary(p)}


register({"name": "list_settings",
          "description": "Look up the rules in force as data (roles, members, folders, permissions, thresholds, routing, due dates, "
                         "approval criteria, segregation of duties, cog profiles) with each one's key and the sentence it reads as.",
          "parameters": {"type": "object", "properties": {"kind": {"type": "string", "description": "e.g. approval_criteria, permission, threshold"},
                                                         "contains": {"type": "string", "description": "words the sentence must contain"},
                                                         "limit": {"type": "integer"}}}}, list_settings, write=False)
register({"name": "propose_setting_change",
          "description": "Propose one change to the rules as data (add, replace or remove a rule found with list_settings); it goes to the "
                         "policy owners as a proposal. " + _OWNERS_DECIDE,
          "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["add", "replace", "remove"]},
                                                         "key": {"type": "string", "description": "the rule's key from list_settings (replace, remove)"},
                                                         "rule": {"type": "object", "description": "the new rule's fields, as list_settings shows them (add, replace)"},
                                                         "reason": {"type": "string"}},
                         "required": ["action", "reason"]}}, propose_setting_change, write=True, intent=intent_change_order)
