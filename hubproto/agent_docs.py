"""Document and procedure tools for cogs working on their own: create a working document, apply edit operations
the agent writes itself, list and compare a document's versions, read a procedure with its line numbers, and search
the procedures (a lexical, deterministic retrieval: the passages that best match the question, with where they are)."""

from __future__ import annotations

import difflib
import re

from . import authz, edits, office, wopi
from .agent import register

DOC_EXTS = ("docx", "md", "xlsx", "odt", "ods")
LIVE_STATES = ("open", "draft", "returned", "failed")


def _doc(tools, number: str) -> dict | None:
    rec = tools._find(number)
    return rec if rec is not None and rec["type"] == "document" else None


def _ext(rec: dict, latest: dict | None = None) -> str:
    return wopi.extension_of((latest or {}).get("filename") or rec["fields"].get("filename") or "")


# ---------------------------------------------------------------- create_document
def create_document(tools, a: dict) -> dict:
    hub = tools.hub
    title = str(a.get("title") or "").strip()
    if not title:
        return {"error": "a document needs a title"}
    folder = str(a.get("folder") or "Quality/Controlled Documents").strip()
    ext = str(a.get("format") or "docx").lower().lstrip(".")
    if ext not in DOC_EXTS:
        return {"error": f"format must be one of {', '.join(DOC_EXTS)}"}
    if folder not in (tools.bundle.get("folders") or []):
        return {"error": f"there is no folder {folder!r} in the release"}
    allowed = authz.check(tools.bundle, tools.cog, "create", "document", folder)
    if not allowed["allowed"]:
        return {"error": allowed["why"]}
    content, mime = wopi.blank_document(ext)
    lines = [ln for ln in str(a.get("text") or "").splitlines()]
    report = []
    if any(ln.strip() for ln in lines):
        op = {"op": "append_rows", "lines": lines} if edits.is_sheet(ext) else {"op": "insert", "at": "start", "lines": lines}
        new, report = edits.apply_ops(content, ext, [op])
        content = new or content
    doc = hub.dms.create(title=title, doc_class="working", folder=folder, by=tools.cog, content=content, filename=f"{title}.{ext}",
                         mime=mime, doc_type="document", fields={"drafted_by_run": tools.run_id})
    wopi.WopiHost(hub).index(hub.kernel.get(doc["locator"]), content, "hub")
    return {"ok": True, "document": doc["number"], "folder": folder, "format": ext, "state": doc["state"], "content": report}


register({"name": "create_document",
          "description": "Create a new working document (docx, md, xlsx, odt or ods) in a folder you may create documents in, with "
                         "optional starting content in the writing markup. For a controlled procedure that goes for approval, use "
                         "draft_document instead.",
          "parameters": {"type": "object", "properties": {
              "title": {"type": "string"}, "folder": {"type": "string", "description": "default Quality/Controlled Documents"},
              "format": {"type": "string", "enum": list(DOC_EXTS)},
              "text": {"type": "string", "description": "starting content: lines in the writing markup, or '| a | b |' rows for a sheet"}},
              "required": ["title"]}},
         create_document, write=True)


# ---------------------------------------------------------------- apply_document_ops
def apply_document_ops(tools, a: dict) -> dict:
    hub = tools.hub
    rec = _doc(tools, str(a.get("number") or ""))
    if rec is None:
        return {"error": f"no document {a.get('number')!r} that {tools.cog} may read"}
    f = rec["fields"]
    machines = set(hub.registry.ids()) | {"hub"}
    if f.get("checked_out_by") and f["checked_out_by"] not in machines:
        return {"error": f"{rec['number']} is being edited by {f['checked_out_by']}; comment on it instead"}
    if f.get("doc_class") in ("evidence", "external") or (f.get("doc_class") in ("controlled", "template") and rec["state"] not in LIVE_STATES):
        return {"error": f"{rec['number']} is not a working document ({f.get('doc_class')}, {rec['state']}); propose a change through its process instead"}
    raw = str(a.get("ops") or "")
    # Here the agent writes the operations itself: text without any '@@' line is refused, not inserted at the top.
    ops = edits.parse_ops(raw) if re.search(r"(?m)^\s*@@", raw) else []
    unknown = [o for o in ops if o["op"] == "unknown"]
    ops = [o for o in ops if o["op"] != "unknown"]
    if not ops:
        return {"error": "no operations I could read; write lines starting with '@@' (for example '@@ replace: old' then '@@ with: new')",
                "unknown": [o.get("text") for o in unknown]}
    content, latest = hub.dms.content(rec["locator"])
    ext = _ext(rec, latest)
    new, report = edits.apply_ops(content, ext, ops)
    report += [{"op": edits.describe(o), "ok": False, "detail": "not an operation I know"} for o in unknown]
    if new is None:
        return {"error": "nothing was changed", "changes": report}
    hub.dms.add_version(rec["locator"], new, by="hub", filename=latest.get("filename") or "", mime=latest.get("mime") or "",
                        note=f"edited by {tools.cog} (agent run {tools.run_id})")
    wopi.WopiHost(hub).index(hub.kernel.get(rec["locator"]), new, "hub")
    return {"ok": True, "document": rec["number"], "version": hub.dms.versions(rec["locator"])[-1]["version"], "changes": report}


register({"name": "apply_document_ops",
          "description": "Change a working document with edit operations you write yourself: insert at a place, replace, delete text, a "
                         "paragraph or a section, rewrite; in sheets set, clear, insert or delete rows. `ops` uses the '@@' lines "
                         "(e.g. '@@ replace: shall' then '@@ with: must'; '@@ delete section: Records'; '@@ insert after: <text>' then "
                         "the lines). You get a report per operation.",
          "parameters": {"type": "object", "properties": {"number": {"type": "string"}, "ops": {"type": "string"}}, "required": ["number", "ops"]}},
         apply_document_ops, write=True)


# ---------------------------------------------------------------- document_versions
def document_versions(tools, a: dict) -> dict:
    hub = tools.hub
    rec = _doc(tools, str(a.get("number") or ""))
    if rec is None:
        return {"error": f"no document {a.get('number')!r} that {tools.cog} may read"}
    vs = hub.dms.versions(rec["locator"])
    out = {"document": rec["number"], "title": rec["fields"].get("title"), "state": rec["state"],
           "versions": [{"version": v["version"], "by": v.get("by"), "at": v.get("at"), "note": v.get("note") or "", "size": v.get("size")} for v in vs]}
    cmp = a.get("compare")
    if cmp:
        try:
            if isinstance(cmp, str):
                cmp = [int(x) for x in re.findall(r"\d+", cmp)]
            v1, v2 = int(cmp[0]), int(cmp[1])
            texts = []
            for v in (v1, v2):
                c, meta = hub.dms.content(rec["locator"], v)
                texts.append(office.extract_text(c, _ext(rec, meta), limit=200000).splitlines())
        except (StopIteration, IndexError, ValueError, TypeError):
            return {**out, "error": "compare takes two existing version numbers, e.g. [1, 3]"}
        diff = "\n".join(difflib.unified_diff(texts[0], texts[1], f"version {v1}", f"version {v2}", lineterm="", n=1))
        out["diff"] = (diff[:3000] + "\n[... diff cut]") if len(diff) > 3000 else (diff or "(no difference in the text)")
    return out


register({"name": "document_versions",
          "description": "List a document's versions (who saved each, when, why) and optionally compare two of them as a text diff.",
          "parameters": {"type": "object", "properties": {"number": {"type": "string"},
                                                          "compare": {"type": "array", "items": {"type": "integer"}, "description": "two version numbers"}},
                         "required": ["number"]}},
         document_versions, write=False)


# ---------------------------------------------------------------- procedures
def _released_frames(tools) -> list:
    """The Frames of the release in force (falling back to the store when the release lists none)."""
    hub = tools.hub
    meta = hub.releases.current() or {}
    names = [x.get("name") for x in meta.get("frames") or [] if isinstance(x, dict)]
    store = hub.frames
    out = []
    for n in names or sorted(store.frames):
        try:
            out.append(store.get(n))
        except Exception:
            continue
    return out


def read_procedure(tools, a: dict) -> dict:
    name = str(a.get("name") or "").strip()
    frames = {f.name: f for f in _released_frames(tools)}
    frame = frames.get(name)
    if frame is None:
        near = difflib.get_close_matches(name, list(frames), n=3, cutoff=0.4)
        return {"error": f"no procedure named {name!r} in the release" + (f"; did you mean {', '.join(near)}?" if near else ""),
                "procedures": sorted(frames)}
    lines = frame.file_lines
    lo = max(int(a.get("from_line") or frame.body_start), 1)
    hi = min(int(a.get("to_line") or len(lines)), len(lines))
    text, size = [], 0
    for n in range(lo, hi + 1):
        row = f"{n:4d}: {lines[n - 1]}"
        if size + len(row) > 20000:
            text.append(f"[... cut at line {n - 1}; ask again with from_line={n}]")
            break
        text.append(row)
        size += len(row) + 1
    return {"procedure": frame.name, "version": frame.version, "description": frame.description, "path": frame.relpath,
            "lines": f"{lo}-{hi}", "text": "\n".join(text)}


register({"name": "read_procedure",
          "description": "Read a procedure (an SOP or policy in the release) in full, with its file line numbers so you can cite them.",
          "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "e.g. document-control-sop"},
                                                          "from_line": {"type": "integer"}, "to_line": {"type": "integer"}},
                         "required": ["name"]}},
         read_procedure, write=False)


_STOP = {"the", "and", "for", "who", "what", "which", "when", "where", "how", "does", "are", "with", "that", "this", "from", "into",
         "may", "can", "should", "would", "will", "our", "their", "they", "them", "its", "any", "all", "not", "but", "has", "have"}


def _terms(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z][a-z0-9-]{2,}", (text or "").lower()):
        for suffix in ("ing", "ed", "es", "s"):
            if len(w) > len(suffix) + 3 and w.endswith(suffix):
                w = w[: -len(suffix)]
                break
        if w not in _STOP:
            out.append(w)
    return out


def _passages(frame) -> list[tuple[int, int, str]]:
    """Paragraphs of a Frame's body (blank-line separated; a heading starts a new one), with file line numbers."""
    out, start, buf = [], None, []
    for i, line in enumerate(frame.lines):
        n = frame.body_start + i
        if not line.strip() or (line.startswith("#") and buf):
            if buf:
                out.append((start, n - 1, "\n".join(buf)))
            buf, start = [], None
            if not line.strip():
                continue
        if start is None:
            start = n
        buf.append(line)
    if buf:
        out.append((start, frame.body_start + len(frame.lines) - 1, "\n".join(buf)))
    return out


def search_procedures(tools, a: dict) -> dict:
    query = str(a.get("query") or "")
    q = set(_terms(query))
    if not q:
        return {"error": "say what to look for, in a few words"}
    k = max(1, min(int(a.get("k") or 5), 10))
    scored = []
    for frame in _released_frames(tools):
        heading = ""
        for lo, hi, text in _passages(frame):
            if text.startswith("#"):
                heading = text.splitlines()[0].lstrip("# ").strip()
            words = _terms(text)
            hits = q & set(words)
            if not hits:
                continue
            # Coverage of the question first, then density: short passages that hold many of the terms win.
            score = len(hits) / len(q) + 0.15 * sum(words.count(t) for t in hits) / (len(words) ** 0.5)
            scored.append((score, frame.name, lo, hi, heading, text))
    scored.sort(key=lambda x: (-x[0], x[1], x[2]))
    return {"query": query, "results": [
        {"procedure": name, "lines": f"{lo}-{hi}", "section": heading, "score": round(score, 3),
         "text": text if len(text) <= 600 else text[:599] + "…"}
        for score, name, lo, hi, heading, text in scored[:k]]}


register({"name": "search_procedures",
          "description": "Search all procedures for the passages that answer a question; each result names the procedure and its line "
                         "numbers, so read_procedure can open it and you can cite it.",
          "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer", "description": "how many passages (1-10, default 5)"}},
                         "required": ["query"]}},
         search_procedures, write=False)
