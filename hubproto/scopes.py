"""Which Frames depend on which SOPs, and who layers them.

The release says it all explicitly: each Frame in its bill of materials names the source it was compiled from (the
SOP or policy document and revision, or the Frame file before the SOPs were documents); each scope names the Frames
it compiles and the scopes it layers on; each persona (a cog or a job role) the scopes and Frames it layers. From
those, for any Frame: the scopes it is in and the personas that work from it; for a change: everything it affects.
"""

from __future__ import annotations

from .release import persona_frames, scope_frames


def frame_sources(hub, meta: dict | None = None) -> dict[str, dict]:
    """{Frame name: its source as the release recorded it}."""
    meta = meta or hub.releases.current() or {}
    return {f["name"]: f.get("source") or {"file": f.get("path")} for f in meta.get("frames") or []}


def dependents(bundle: dict, frame: str) -> dict:
    """The scopes a Frame is in (directly or through a scope layered on it) and the personas that layer it."""
    scopes = bundle.get("scopes") or {}
    personas = bundle.get("personas") or {}
    in_scopes = sorted(n for n in scopes if frame in scope_frames(scopes, n))
    direct = sorted(n for n, s in scopes.items() if frame in (s.get("compiles") or []))
    layered = sorted((n, p.get("of")) for n, p in personas.items() if frame in persona_frames(scopes, p))
    return {"scopes": in_scopes, "direct_scopes": direct, "personas": [{"name": n, "of": of} for n, of in layered]}


def impact(bundle: dict, frames: list[str]) -> dict:
    """What a change to these Frames affects: the scopes they are in and every persona that layers them."""
    scopes, personas = set(), {}
    for f in frames:
        d = dependents(bundle, f)
        scopes |= set(d["scopes"])
        for p in d["personas"]:
            personas[p["name"]] = p["of"]
    return {"frames": sorted(set(frames)), "scopes": sorted(scopes),
            "personas": [{"name": n, "of": of} for n, of in sorted(personas.items())]}


def required_reading(bundle: dict, person: str) -> list[dict]:
    """The SOPs a person must read: the Frames of the role personas of each role they hold, with the role that asks."""
    from . import authz

    personas = bundle.get("personas") or {}
    scopes = bundle.get("scopes") or {}
    out: dict[str, set] = {}
    for role in authz.roles_of(bundle, person):
        p = personas.get(role)
        if p and p.get("of") == "role":
            for f in persona_frames(scopes, p):
                out.setdefault(f, set()).add(role)
    return [{"frame": f, "roles": sorted(r)} for f, r in out.items()]
