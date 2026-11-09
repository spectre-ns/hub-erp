"""Which Frames depend on which SOPs, and who works from them.

The release says it all explicitly: each Frame in its bill of materials names the source it was compiled from (the
SOP or policy document and revision, or the Frame file before the SOPs were documents); each scope names the Frames
it compiles and the scopes it layers on. Who works from a Frame is what its Roles and responsibilities table says: a
person must read every document whose table lists one of their roles; a cog follows the company scope and every
document whose table lists it or one of its roles. From those, for any Frame: the scopes it is in, the cogs that
follow it and the roles that must read it; for a change: everything it affects.
"""

from __future__ import annotations

from .release import follows_for, scope_frames, table_roles


def frame_sources(hub, meta: dict | None = None) -> dict[str, dict]:
    """{Frame name: its source as the release recorded it}."""
    meta = meta or hub.releases.current() or {}
    return {f["name"]: f.get("source") or {"file": f.get("path")} for f in meta.get("frames") or []}


def _roles_reading(bundle: dict, frame: str) -> list[str]:
    return table_roles(bundle, frame)


def dependents(bundle: dict, frame: str) -> dict:
    """The scopes a Frame is in (directly or through a scope layered on it), the cogs that follow it and the roles that
    must read it (both from its Roles and responsibilities table), and the cogs that behave by it (a persona)."""
    scopes = bundle.get("scopes") or {}
    in_scopes = sorted(n for n in scopes if frame in scope_frames(scopes, n))
    direct = sorted(n for n, s in scopes.items() if frame in (s.get("compiles") or []))
    cogs = sorted(n for n, c in (bundle.get("cogs") or {}).items() if frame in (c.get("follows") or []))
    persona = next((n for n, p in (bundle.get("personas") or {}).items() if (p.get("source") or {}).get("frame") == frame), None)
    behave = sorted((bundle.get("personas") or {}).get(persona, {}).get("cogs") or []) if persona else []
    roles = _roles_reading(bundle, frame)
    readers = [{"name": n, "of": "cog"} for n in sorted(set(cogs) | set(behave))] + [{"name": r, "of": "role"} for r in roles]
    return {"scopes": in_scopes, "direct_scopes": direct, "cogs": cogs, "roles": roles, "persona": persona, "behave": behave,
            "readers": readers}


def impact(bundle: dict, frames: list[str]) -> dict:
    """What a change to these Frames affects: the scopes they are in, the cogs that follow them or behave by them, and
    the roles that must read them."""
    scopes, readers = set(), {}
    for f in frames:
        d = dependents(bundle, f)
        scopes |= set(d["scopes"])
        for p in d["readers"]:
            readers[(p["of"], p["name"])] = p
    return {"frames": sorted(set(frames)), "scopes": sorted(scopes), "readers": [readers[k] for k in sorted(readers)]}


def required_reading(bundle: dict, person: str) -> list[dict]:
    """The documents a person must read: every document whose Roles and responsibilities table lists one of their roles;
    each with the roles that ask and what the table says each does there."""
    rows = bundle.get("responsibilities") or []
    out = []
    for frame, why in follows_for(bundle, person).items():
        roles = sorted(x.partition(":")[2] for x in why if x.startswith("role:"))
        does = [r["text"] for r in rows if r.get("frame") == frame and r.get("who") in why]
        out.append({"frame": frame, "roles": roles, "responsibilities": does})
    return out


def following(bundle: dict, cog: str) -> list[dict]:
    """What a cog follows and why: [{"frame", "because": [names]}] ("company", or the roles and names its rules name)."""
    prof = (bundle.get("cogs") or {}).get(cog) or {}
    why = prof.get("follows_why") or {f: sorted(w) for f, w in follows_for(bundle, cog).items()}
    return [{"frame": f, "because": list(why.get(f) or [])} for f in prof.get("follows") or list(why)]
