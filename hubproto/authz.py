"""Permission checks against a policy release.

Relationship model: a principal (person or cog) holds roles through the
members table; a permission grants or denies a role an action on an object
type within a folder; a folder permission applies to its subfolders. Deny
wins over allow. Every decision carries the rule and the policy sentence
behind it, which the shell shows as "Why?".
"""

from __future__ import annotations


def principal_name(principal: str) -> str:
    return principal.split(":", 1)[1] if ":" in principal else principal


def member(bundle: dict, principal: str) -> dict | None:
    return bundle["members"].get(principal_name(principal))


def roles_of(bundle: dict, principal: str) -> set[str]:
    m = member(bundle, principal)
    return set(m["roles"]) if m else set()


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
    applicable = []
    for rule in bundle["permissions"]:
        subject_role = rule["subject"].partition(":")[2]
        if subject_role not in roles:
            continue
        if rule["action"] != action:
            continue
        if rule["object"] not in ("*", obj):
            continue
        if not _folder_matches(rule["resource"].partition(":")[2], folder):
            continue
        applicable.append(rule)
    denies = [r for r in applicable if r["effect"] == "deny"]
    allows = [r for r in applicable if r["effect"] == "allow"]
    if denies:
        r = denies[0]
        return {"allowed": False, "rule": r, "why": f"denied by {r['source']['frame']} line {r['source']['line']} ({r['source'].get('path') or r['source']['frame']}): {r['source']['text']}"}
    if allows:
        r = allows[0]
        return {"allowed": True, "rule": r, "why": f"allowed by {r['source']['frame']} line {r['source']['line']} ({r['source'].get('path') or r['source']['frame']}): {r['source']['text']}"}
    return {"allowed": False, "rule": None,
            "why": f"no rule in the release lets any of {sorted(roles)} {action} {obj} in {folder}"}


def readable_folders(bundle: dict, principal: str) -> list[str]:
    return [f for f in bundle["folders"] if check(bundle, principal, "read", "*", f)["allowed"]
            or any(check(bundle, principal, a, o, f)["allowed"] for a in ("create", "approve") for o in _objects(bundle))]


def _objects(bundle: dict) -> set[str]:
    return {p["object"] for p in bundle["processes"].values()}
