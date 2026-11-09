"""Required training, from the documents' Roles and responsibilities tables.

Every document ends with a table of who has a part in it. That table says who must read it: a person must read every
document whose table lists one of their roles (``release.follows_for``). From the tables in force the hub keeps:

* **who is trained on a new revision**: when a release makes a revision effective, every person whose role its table
  lists gets the training-acknowledgement task (``roles_to_train``; a document's hand-set ``training_roles`` field,
  when set, overrides the table);
* **the training matrix**: a controlled document "Required training" in Quality/Training, one section per role with
  every document the role must read (number, title, revision in force, link, and its part from the table) and the cogs
  that follow each. It is generated, never edited by hand: the release automation publishes a new revision whenever a
  release changes a table or a listed document's revision (``publish_matrix``);
* **where each person stands** on each document they must read: acknowledged, due, overdue or not assigned
  (``reading_status``);
* **what each cog is trained on**: for a cog, training is the document in its context when it works. It is trained on
  a revision from the release that put that revision in its context; the release is its training record
  (``cog_training``). Cogs get no acknowledgement task.
"""

from __future__ import annotations

from datetime import date

from .release import follows_for, table_groups, table_roles

MATRIX_TITLE = "Required training"
MATRIX_FOLDER = "Quality/Training"
MATRIX_TYPE = "training-matrix"


def frame_of_document(rec: dict | None) -> str:
    """The SOP or policy a document carries ("" for a document that is not one)."""
    f = (rec or {}).get("fields") or {}
    lineage = str(f.get("lineage") or "")
    return f.get("sop_name") or f.get("frame_name") or (lineage.split(":", 1)[1] if ":" in lineage else "")


def roles_to_train(bundle: dict, rec: dict, override: list[str] | None = None) -> list[str]:
    """Who a new revision trains: its ``training_roles`` field when set (an override), else the roles the Roles and
    responsibilities table of the document it carries lists."""
    override = [r for r in (override if override is not None else ((rec or {}).get("fields") or {}).get("training_roles")) or [] if r]
    if override:
        return list(override)
    frame = frame_of_document(rec)
    return table_roles(bundle, frame) if frame else []


def groups_to_train(bundle: dict, rec: dict, override: list[str] | None = None) -> list[str]:
    """The groups whose members a new revision trains: those its Roles and responsibilities table lists (none when the
    document's ``training_roles`` override the table)."""
    override = [r for r in (override if override is not None else ((rec or {}).get("fields") or {}).get("training_roles")) or [] if r]
    frame = frame_of_document(rec)
    return [] if override or not frame else table_groups(bundle, frame)


def document_in_force(hub, frame: str) -> dict | None:
    from . import sopdocs

    return sopdocs.in_force(hub.kernel, frame)


def reading_status(hub, person: str, frame: str, today: str | None = None) -> dict:
    """Where a person stands on a document they must read: {"status": "done"|"due"|"overdue"|"not assigned"|"no document",
    "document", "revision", "due", "record"}. Done means they acknowledged the revision in force."""
    today = today or date.today().isoformat()
    doc = document_in_force(hub, frame)
    if doc is None:
        return {"status": "no document", "document": None, "revision": None, "due": None, "record": None}
    rev = doc["fields"].get("revision")
    mine = [r for r in hub.kernel.query(type_="training") if r["fields"].get("person") == person
            and r["fields"].get("document") == doc["locator"] and str(r["fields"].get("revision") or "") == str(rev or "")]
    out = {"document": doc["number"], "revision": rev, "due": None, "record": None}
    done = next((r for r in mine if r["state"] == "acknowledged"), None)
    if done:
        return {**out, "status": "done", "record": done["number"], "acknowledged": done["fields"].get("acknowledged")}
    open_ = next((r for r in mine if r["state"] not in ("rejected", "cancelled", "failed")), None)
    if open_:
        due = open_.get("due") or open_["fields"].get("due")
        return {**out, "status": "overdue" if due and due < today else "due", "due": due, "record": open_["number"]}
    return {**out, "status": "not assigned"}


def _revision(hub, frame: str, meta: dict) -> str:
    doc = document_in_force(hub, frame)
    if doc is not None:
        return str(doc["fields"].get("revision") or "")
    return str(next((f.get("version") for f in meta.get("frames") or [] if f.get("name") == frame), "") or "")


def cog_training(hub, cog: str, digest: str | None = None) -> list[dict]:
    """What a cog is trained on: each document in its context (the company scope and the documents whose Roles and
    responsibilities tables name it or its roles), with the revision in force and the release that put that revision
    in its context (the earliest release since which the text is unchanged and the cog has followed it)."""
    releases = hub.releases.list()
    if not releases:
        return []
    idx = next((i for i, m in enumerate(releases) if m["digest"] == digest), None) if digest else None
    if idx is None:
        cur = hub.releases.current()
        idx = next((i for i, m in enumerate(releases) if cur and m["digest"] == cur["digest"]), len(releases) - 1)
    meta = releases[idx]
    bundles: dict[str, dict] = {}

    def follows(m: dict) -> list[str]:
        if m["digest"] not in bundles:
            bundles[m["digest"]] = hub.releases.bundle(m["digest"])
        return list(((bundles[m["digest"]].get("cogs") or {}).get(cog) or {}).get("follows") or [])

    def sha(m: dict, frame: str):
        return next((f.get("sha256") for f in m.get("frames") or [] if f.get("name") == frame), None)

    out = []
    for frame in follows(meta):
        since, want = idx, sha(meta, frame)
        while since > 0 and sha(releases[since - 1], frame) == want and frame in follows(releases[since - 1]):
            since -= 1
        out.append({"frame": frame, "status": "in context", "revision": _revision(hub, frame, meta), "release": releases[since]["digest"],
                    "release_n": since + 1})
    return out


# ---------------------------------------------------------------- the training matrix
def _cell(text) -> str:
    return " ".join(str(text or "").replace("|", "/").split())


def matrix_text(hub, bundle: dict) -> str:
    """The training matrix as Markdown, from the tables in force: nothing release-specific, so the text (and the
    document) changes only when a table or a listed document's revision does."""
    rows = bundle.get("responsibilities") or []
    frames = sorted({r["frame"] for r in rows})
    cogs = {f: sorted(n for n, c in (bundle.get("cogs") or {}).items() if f in (c.get("follows") or [])) for f in frames}
    where = {}
    for f in frames:
        doc = document_in_force(hub, f)
        where[f] = ((doc["number"], doc["fields"].get("title") or f, doc["fields"].get("revision") or "", f"/document/{doc['locator']}")
                    if doc else (f, f, "", f"/frame/{f}"))
    lines = [f"# {MATRIX_TITLE}", "",
             "Generated from the Roles and responsibilities tables of the documents in force. It is not edited by hand: each release "
             "that changes a table, or the revision of a document listed here, publishes a new revision. A person must read every "
             "document listed under each of their roles and acknowledge each new revision.", ""]
    from .groups import group_members

    whos = sorted({r["who"] for r in rows if r["who"].startswith("role:")}) + sorted({r["who"] for r in rows if r["who"].startswith("group:")})
    for who in whos:
        kind, _, name = who.partition(":")
        head = name.replace("-", " ").capitalize() if kind == "role" else (
            f"The {name.replace('-', ' ')} group ({', '.join(group_members(bundle, name)) or 'nobody'})")
        lines += [f"## {head}", "",
                  "| Document | Title | Revision | Link | Responsibility | Cogs that follow it |", "|---|---|---|---|---|---|"]
        for f in frames:
            mine = [r["text"] for r in rows if r["frame"] == f and r["who"] == who]
            if not mine:
                continue
            number, title, rev, link = where[f]
            lines.append(f"| {_cell(number)} | {_cell(title)} | {_cell(rev) or '-'} | {link} | {_cell('; '.join(mine))} | "
                         f"{_cell(', '.join(cogs[f])) or '-'} |")
        lines.append("")
    cogs_ = sorted(n for n, m in (bundle.get("members") or {}).items() if m.get("type") == "cog" and n in (bundle.get("cogs") or {}))
    if cogs_:
        lines += ["## Cogs", "", "A cog is trained on a document when it is in its context as it works: from the release that put the revision "
                  "there. It acknowledges nothing; the release is its training record.", "",
                  "| Cog | Document | Revision | In context since |", "|---|---|---|---|"]
        for c in cogs_:
            for t in cog_training(hub, c):
                number = where[t["frame"]][0] if t["frame"] in where else t["frame"]
                lines.append(f"| {c} | {_cell(number)} | {_cell(t['revision']) or '-'} | release v{t['release_n']} ({t['release'][:12]}) |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def matrix_document(hub) -> dict | None:
    return next((d for d in hub.kernel.query(type_="document", folder=MATRIX_FOLDER)
                 if d["fields"].get("doc_type") == MATRIX_TYPE and d["state"] == "effective"), None)


def publish_matrix(hub, digest: str, *, by: str = "hub") -> dict | None:
    """Publish the training matrix for the release ``digest``: the first time as revision A, in force; later as a new
    revision when its text changed. Returns the document when it published, None when nothing changed."""
    from .dms import next_revision

    bundle = hub.releases.bundle(digest)
    text = matrix_text(hub, bundle)
    doc = matrix_document(hub)
    from datetime import timedelta

    # In force from the release (an effective document needs a review date: a year on, like any controlled document).
    stamp = {"released_in": digest, "effective_from": date.today().isoformat(), "approved_by": "the release",
             "review_date": (date.today() + timedelta(days=365)).isoformat()}
    if doc is None:
        doc = hub.dms.create(title=MATRIX_TITLE, doc_class="controlled", folder=MATRIX_FOLDER, by=by, content=text.encode("utf-8"),
                             filename="required-training.md", mime="text/markdown", doc_type=MATRIX_TYPE, release=digest,
                             fields={"generated": True, "description": "Who must read which document, from the Roles and responsibilities "
                                                                       "tables in force.", **stamp})
        return hub.kernel.transition(doc["locator"], "effective", by=by, release=digest, reason="generated from the tables in force")
    content, _v = hub.dms.content(doc["locator"])
    if content.decode("utf-8", "replace") == text:
        return None
    hub.dms.add_version(doc["locator"], text.encode("utf-8"), by=by, filename="required-training.md", mime="text/markdown",
                        note=f"generated for release {digest[:12]}", release=digest, system=True)
    return hub.kernel.set_fields(doc["locator"], {**stamp, "revision": next_revision(doc["fields"].get("revision") or "A"), "minor": 0},
                                 by=by, release=digest)
