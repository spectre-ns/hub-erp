"""Permission checks against a policy release.

Relationship model: a principal (person or cog) holds roles through the
members table and is in groups (``hubproto.groups``: listed, or everyone
holding a role); a permission grants or denies a role, a group (or one cog, by
name) an action on an object type within a folder; a folder permission applies
to its subfolders. Deny wins over allow. Every decision carries the rule and the
policy sentence behind it, which the shell shows as "Why?" (naming the group
when the permission comes through one).

A cog's autonomy is derived per action (``autonomy``): it acts where a
permission (or its step) lets it, proposes where an SOP says a person confirms
("The stores cog may move stock items in Inventory/Stock when a storekeeper
confirms."), and otherwise only reads. A proposing permission is never an
allow for ``check``: it is the cog's own rail, applied by the agent harness.
"""

from __future__ import annotations


def principal_name(principal: str) -> str:
    return principal.split(":", 1)[1] if ":" in principal else principal


def member(bundle: dict, principal: str) -> dict | None:
    return bundle["members"].get(principal_name(principal))


def roles_of(bundle: dict, principal: str) -> set[str]:
    m = member(bundle, principal)
    return set(m["roles"]) if m else set()


def groups_of(bundle: dict, principal: str) -> set[str]:
    """The groups a member is in (listed in them, or holding one of their roles)."""
    from .groups import groups_of as _groups_of

    return set(_groups_of(bundle, principal_name(principal))) if member(bundle, principal) else set()


def subjects_of(bundle: dict, principal: str) -> set[str]:
    """Every subject a permission may name that stands for this member: its roles, its groups and itself."""
    m = member(bundle, principal)
    if not m:
        return set()
    name = principal_name(principal)
    return ({"role:" + r for r in m["roles"]} | {"group:" + g for g in groups_of(bundle, name)}
            | {f"{m.get('type') or 'person'}:{name}"})


def through(rule: dict) -> str:
    """How a permission reaches a member, in words: '' for a role or a name, ' (through the x group)' for a group."""
    kind, _, name = str(rule.get("subject") or "").partition(":")
    return f" (through the {name.replace('-', ' ')} group)" if kind == "group" else ""


def permissions_of(bundle: dict, principal: str) -> list[dict]:
    """What the SOPs let a member do, one entry per permission sentence that names it, one of its roles or one of its
    groups: the rule with ``via`` (the subject it comes through: "role:x", "group:g", "cog:name"). Allows, proposals
    ("when a person confirms") and denies alike; the per-cog listing of what a cog may do (principle 6)."""
    mine = subjects_of(bundle, principal)
    out = [{**r, "via": r["subject"]} for r in bundle.get("permissions") or [] if r.get("subject") in mine]
    order = {"deny": 0, "propose": 1, "allow": 2}
    return sorted(out, key=lambda r: (order.get(r.get("effect"), 3), r.get("resource") or "", r.get("action") or "", r.get("object") or ""))


def _folder_matches(rule_folder: str, folder: str) -> bool:
    return folder == rule_folder or folder.startswith(rule_folder + "/")


def rights_folder(bundle: dict, folder: str) -> str:
    """The folder whose rules apply: a top-level library folder (and anything in it) takes those of the folder it names."""
    for root, src in (bundle.get("library_rights") or {}).items():
        if folder == root or folder.startswith(root + "/"):
            return src + folder[len(root):]
    return folder


def check(bundle: dict, principal: str, action: str, obj: str, folder: str) -> dict:
    from .extract import singular_role  # the sentences say "bills"; the object is "bill"

    obj = singular_role(obj) if obj and obj != "*" else obj
    folder = rights_folder(bundle, folder)  # a top-level library folder has the rights of the folder it names
    roles = roles_of(bundle, principal)
    if not roles:
        return {"allowed": False, "rule": None, "why": f"{principal} is not a member of the organization"}
    applicable = [r for r in _applicable(bundle, principal, roles, action, obj, folder) if r.get("effect") != "propose"]
    denies = [r for r in applicable if r["effect"] == "deny"]
    allows = [r for r in applicable if r["effect"] == "allow"]
    if denies:
        r = denies[0]
        return {"allowed": False, "rule": r, "why": f"denied by {r['source']['frame']} line {r['source']['line']} ({r['source'].get('path') or r['source']['frame']}): {r['source']['text']}{through(r)}"}
    if allows:
        r = allows[0]
        return {"allowed": True, "rule": r, "why": f"allowed by {r['source']['frame']} line {r['source']['line']} ({r['source'].get('path') or r['source']['frame']}): {r['source']['text']}{through(r)}"}
    groups = sorted(groups_of(bundle, principal))
    return {"allowed": False, "rule": None,
            "why": f"no rule in the release lets any of {sorted(roles)}" + (f" (or the groups {groups})" if groups else "") + f" {action} {obj} in {folder}"}


def _subject_applies(rule: dict, principal: str, roles: set[str], groups: set[str] | None = None, bundle: dict | None = None) -> bool:
    kind, _, name = rule["subject"].partition(":")
    if kind == "role":
        return name in roles
    if kind == "group":
        return name in (groups if groups is not None else groups_of(bundle or {}, principal))
    return name == principal_name(principal)  # "cog:stores" or "person:bob": a permission naming one member


def _applicable(bundle: dict, principal: str, roles: set[str], action: str, obj: str, folder: str) -> list[dict]:
    out = []
    groups = groups_of(bundle, principal)
    for rule in bundle["permissions"]:
        if not _subject_applies(rule, principal, roles, groups):
            continue
        if rule["action"] != action:
            continue
        if rule["object"] not in ("*", obj):
            continue
        if not _folder_matches(rule["resource"].partition(":")[2], folder):
            continue
        out.append(rule)
    return out


def _cite(r: dict) -> str:
    s = r.get("source") or {}
    return f"{s.get('frame')} line {s.get('line')} ({s.get('path') or s.get('frame')}): {s.get('text')}"


def autonomy(bundle: dict, principal: str, action: str, obj: str, folder: str, *, also_allowed: str = "") -> dict:
    """How far a cog may go on its own with one action on an object in a folder, derived from the SOPs:
    ``act`` where a permission for it (or one of its roles) allows it, ``propose`` where an SOP says a person confirms
    it, ``refuse`` otherwise (or where a deny applies). The most restrictive applicable sentence wins: a deny, then a
    "when a person confirms" sentence (it narrows a broader permission), then an allow. ``also_allowed`` is a reason the
    action is allowed other than a permission (the author of a draft may change it). Returns {"mode", "rule", "why",
    "confirmer"}."""
    from .extract import singular_role

    obj = singular_role(obj) if obj and obj != "*" else obj
    folder = rights_folder(bundle, folder)
    roles = roles_of(bundle, principal)
    name = principal_name(principal)
    if not roles:
        return {"mode": "refuse", "rule": None, "confirmer": None, "why": f"{name} is not a member of the organization"}
    rules = _applicable(bundle, principal, roles, action, obj, folder)
    denies = [r for r in rules if r["effect"] == "deny"]
    if denies:
        return {"mode": "refuse", "rule": denies[0], "confirmer": None, "why": "denied by " + _cite(denies[0]) + through(denies[0])}
    proposes = [r for r in rules if r["effect"] == "propose"]
    if proposes:
        r = proposes[0]
        who = r.get("confirmer") or "person"
        return {"mode": "propose", "rule": r, "confirmer": who,
                "why": f"{'a person' if who == 'person' else 'a ' + who.replace('-', ' ')} confirms it first: " + _cite(r) + through(r)}
    allows = [r for r in rules if r["effect"] == "allow"]
    if allows:
        return {"mode": "act", "rule": allows[0], "confirmer": None, "why": "allowed by " + _cite(allows[0]) + through(allows[0])}
    if also_allowed:
        return {"mode": "act", "rule": None, "confirmer": None, "why": also_allowed}
    what = "anything" if obj == "*" else obj.replace("-", " ")
    return {"mode": "refuse", "rule": None, "confirmer": None,
            "why": f"no SOP lets {name} (or its roles {', '.join(sorted(roles))}) {action} {what} in {folder}"}


def may_confirm(bundle: dict, person: str, confirmer: str | None, *, requester: str = "") -> bool:
    """Whether this person may confirm a cog's proposed action: a person in the confirming role, or (when the sentence
    says "a person") the person who gave the cog the task, or a policy owner."""
    if (member(bundle, person) or {}).get("type") != "person":
        return False
    roles = roles_of(bundle, person)
    if confirmer and confirmer != "person":
        return confirmer in roles
    return person == requester or "policy-owner" in roles


def confirmed_view(bundle: dict, principal: str, person: str, *, requester: str = "") -> dict:
    """The release as it reads once ``person`` has confirmed the cog's proposed actions: its proposing permissions whose
    confirmer that person is become allows, for this one cog. Everything else is unchanged."""
    roles = roles_of(bundle, principal)
    groups = groups_of(bundle, principal)
    perms = []
    for r in bundle.get("permissions") or []:
        if r.get("effect") == "propose" and _subject_applies(r, principal, roles, groups) and may_confirm(bundle, person, r.get("confirmer"), requester=requester):
            r = {**r, "effect": "allow", "subject": f"cog:{principal_name(principal)}", "confirmed_by": person}
        perms.append(r)
    return {**bundle, "permissions": perms}


def readable_folders(bundle: dict, principal: str) -> list[str]:
    return [f for f in bundle["folders"] if check(bundle, principal, "read", "*", f)["allowed"]
            or any(check(bundle, principal, a, o, f)["allowed"] for a in ("create", "approve") for o in _objects(bundle))]


def _objects(bundle: dict) -> set[str]:
    return {p["object"] for p in bundle["processes"].values()}
