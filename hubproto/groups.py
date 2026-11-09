"""Groups and committees (change control v2, section 11).

A **group** is a named set of members, people and cogs, declared in the organization model under ``## Groups``:

    - quality-board: carol, bob and qa-manager           (listed)
    - quality-managers: everyone holding quality-manager (by role: it follows role changes)
    - reviewers: dave and everyone holding reviewer      (both)

A group may carry permissions as a role does ("Quality boards may read Quality.", or "The quality-board group may ...");
a permission check considers a member's roles and groups (``hubproto.authz``). A group may be named in a Roles and
responsibilities table ("the quality-board group"): its members must read the document, and a cog in it follows it.

A **committee** is defined on a group (``## Committee: <name>`` with ``Group:``, ``Chair:`` (a person) and
``Purpose:``) and decides by **consensus**: an item passes only when every member agrees; there is no standing aside; an
objection with a reason returns the item for revision. When a cog member's approval criteria do not hold, or a member
has not answered by the due date, the decision pauses and the chair intervenes (``hubproto.runner``). Nothing passes by
default. Committees decide at a gate of a process graph ("the design-review committee decides"), review on a change
order's panel (the change-control SOP's review section may name one; every member reviews) and read the SOPs into
rules (the compile-frame committee, on the ``senior-cogs`` group; ``hubproto.llm_extract.CommitteeExtractor``).

Pure functions over a release bundle (or over rules before rendering); standard library only.
"""

from __future__ import annotations

import re

EVERYONE = re.compile(r"^every(?:one|body| member| person and cog)? (?:holding|who holds|with the role|in the role)s? (?P<roles>.+)$")


def _split(text: str) -> list[str]:
    """'a, b and c' -> ['a', 'b', 'c'] (an 'everyone holding x or y' part stays whole)."""
    parts: list[str] = []
    for chunk in [c.strip() for c in text.strip().rstrip(".").split(",") if c.strip()]:
        if EVERYONE.match(chunk):
            parts.append(chunk)
            continue
        sub = [s.strip() for s in re.split(r"\s+and\s+", chunk) if s.strip()]
        # "bob and everyone holding reviewer": the 'everyone' part keeps its own 'or'.
        parts += sub
    return parts


def parse_group(rest: str) -> tuple[list[str], list[str], str]:
    """The right-hand side of a group line: (members listed, roles whose holders are in it, an error or "")."""
    from .extract import slug

    listed: list[str] = []
    roles: list[str] = []
    for part in _split(rest):
        m = EVERYONE.match(part)
        if m:
            for r in re.split(r",\s*|\s+or\s+|\s+and\s+", m.group("roles")):
                r = slug(r.removeprefix("the ").removesuffix(" role"))
                if r and r not in roles:
                    roles.append(r)
            continue
        name = part.strip().strip("`")
        if not re.match(r"^[a-z][a-z0-9-]*$", name):
            return [], [], (f"{part!r} is not a member name: a group lists members ('carol, bob and qa-manager') or says "
                            "'everyone holding <role>'")
        if name not in listed:
            listed.append(name)
    if not listed and not roles:
        return [], [], "a group lists its members or says 'everyone holding <role>'"
    return listed, roles, ""


# ---------------------------------------------------------------- groups in a bundle
def defined(bundle: dict) -> dict:
    return bundle.get("groups") or {}


def group_members(bundle: dict, name: str) -> list[str]:
    """Who is in a group now: the members it lists (in its order), then everyone holding one of its roles (by name).
    Resolved against the members of the release, so a group by role follows role changes."""
    g = defined(bundle).get(name)
    if not g:
        return []
    members = bundle.get("members") or {}
    out = [m for m in g.get("members") or [] if m in members]
    roles = set(g.get("roles") or [])
    if roles:
        out += [p for p, m in sorted(members.items()) if roles & set(m.get("roles") or []) and p not in out]
    return out


def groups_of(bundle: dict, principal: str) -> list[str]:
    """The groups a member is in."""
    name = principal.split(":", 1)[1] if ":" in principal else principal
    return sorted(g for g in defined(bundle) if name in group_members(bundle, g))


def _names(rules_or_bundle) -> tuple[set[str], dict[str, dict]]:
    if isinstance(rules_or_bundle, dict):
        return set(rules_or_bundle.get("roles") or []), dict(defined(rules_or_bundle))
    roles = {r["name"] for r in rules_or_bundle if r.get("kind") == "role"}
    groups = {r["name"]: r for r in rules_or_bundle if r.get("kind") == "group"}
    return roles, groups


def group_named(words: str, groups) -> str:
    """The group a role-like name means: 'role:quality-board' from "Quality boards" is the quality-board group; the
    plural "Administrators" (administrator) is the administrators group. Empty when no group has that name."""
    from .extract import singular_role

    for g in sorted(groups):
        if words in (g, singular_role(g.replace("-", " "))):
            return g
    return ""


def resolve_subject(ref: str, roles: set[str], groups) -> str:
    """A permission subject or route assignee as the rules state it: 'role:x' where x is not a role but names a group
    becomes 'group:<that group>'. Anything else is unchanged."""
    kind, _, name = str(ref or "").partition(":")
    if kind != "role" or name in roles:
        return ref
    g = group_named(name, groups)
    return f"group:{g}" if g else ref


def resolve_group_refs(rules: list[dict]) -> list[dict]:
    """The rules with each permission subject and route assignee that names a group (not a role) made a group reference,
    so "Administrators may ..." gives the administrators group the permission. Returns the same list (rules changed in
    place where they name a group)."""
    roles, groups = _names(rules)
    if not groups:
        return rules
    for r in rules:
        if r.get("kind") == "permission":
            r["subject"] = resolve_subject(r.get("subject"), roles, groups)
        elif r.get("kind") == "routing" and str(r.get("assignee") or "").startswith("role:"):
            r["assignee"] = resolve_subject(r["assignee"], roles, groups)
    return rules


def group_words(name: str) -> str:
    return f"the {name.replace('-', ' ')} group"


# ---------------------------------------------------------------- committees
def committee(bundle: dict, name: str) -> dict | None:
    """A committee of the release, resolved: {"name", "group", "chair", "purpose", "members" (now), "source"}."""
    c = (bundle.get("committees") or {}).get(name)
    if not c:
        return None
    members = group_members(bundle, c["group"]) if c.get("group") else [m for m in c.get("members") or [] if m in (bundle.get("members") or {})]
    return {**c, "name": name, "members": members}


def committees_of(bundle: dict, principal: str) -> list[str]:
    name = principal.split(":", 1)[1] if ":" in principal else principal
    return sorted(n for n in bundle.get("committees") or {} if name in (committee(bundle, n) or {}).get("members", []))


def committee_named(bundle: dict, words: str) -> str:
    """The committee a phrase names ("the design review committee", "design-review"), or ""."""
    from .extract import slug

    w = re.sub(r"^(?:the|a|an)\s+", "", words.strip().lower()).strip(" .")
    w = re.sub(r"\s+committee$", "", w)
    name = slug(w)
    return name if name in (bundle.get("committees") or {}) else ""
