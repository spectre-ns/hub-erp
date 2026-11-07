"""Record tools: what a cog (or any MCP client) can call to find and read records, always as a person.

Every call is made on behalf of a principal and sees only the folders that principal may read under the release in
force, so a cog in a chat never shows someone a record they could not open themselves. The same tools are served to
the cogs' models (function calling) and over MCP (``/mcp``, JSON-RPC: initialize, tools/list, tools/call).
"""

from __future__ import annotations

import json
import re

from . import authz

SKIP_FIELDS = {"text", "office_comments", "checked_out_via", "editors", "wopi_lock", "content_sha", "provenance", "citations",
               "sources", "attachments", "attachments_filed", "exports", "bom_lines", "parts", "checks", "requirement_trace", "reviews"}

TOOLS = [
    {"name": "search_records",
     "description": "Find records (any type: bills, purchase requests, orders, receipts, items, parts, designs, requirements, nonconformances, "
                    "CAPAs, documents, tasks...) by words in their number, title or fields, optionally narrowed by type, state, folder or assignee. "
                    "Returns up to `limit` matches, newest first.",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string", "description": "words to look for; empty lists the newest"},
         "type": {"type": "string", "description": "record type, e.g. bill, purchase-request, design, ncr, document"},
         "state": {"type": "string", "description": "a state, e.g. posted or awaiting-approve-the-bill; 'open' means not finished yet"},
         "folder": {"type": "string", "description": "a folder, e.g. Finance/Payables"},
         "assignee": {"type": "string", "description": "who it is waiting for"},
         "limit": {"type": "integer", "description": "at most this many (default 10, max 25)"}}}},
    {"name": "get_record",
     "description": "Read one record by its number (e.g. BILL-0012, DSN-2026-0030, DOC-0375): its fields, state, who it waits for, "
                    "its decisions and signatures, links to other records and its documents.",
     "parameters": {"type": "object", "properties": {"number": {"type": "string"}}, "required": ["number"]}},
    {"name": "read_document",
     "description": "Read the text of a document (DOC-...), its latest version unless `version` is given; long texts are cut at `max_chars`.",
     "parameters": {"type": "object", "properties": {"number": {"type": "string"}, "version": {"type": "integer"},
                                                    "max_chars": {"type": "integer", "description": "default 6000"}}, "required": ["number"]}},
    {"name": "waiting_for",
     "description": "What is waiting for a person or a cog to decide or rework (their queue).",
     "parameters": {"type": "object", "properties": {"who": {"type": "string", "description": "a member's name or a role (e.g. payables-reviewer); default: the person asking"}}}},
    {"name": "record_history",
     "description": "The history of a record: who created it, changed it, moved it on, and the run it is in. For a document, also every version: who wrote it, when and the note (which names the cog and who asked).",
     "parameters": {"type": "object", "properties": {"number": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["number"]}},
    {"name": "overview",
     "description": "How many records of each type there are, by state, in the folders the person may read.",
     "parameters": {"type": "object", "properties": {}}},
]


EDITABLE_CONTROLLED_STATES = ("open", "draft", "returned", "failed")
_RECORD_NUMBER = re.compile(r"\b[A-Z]{2,}(?:-\d{4})?-\d{2,}\b")


def state_words(state: str | None) -> str:
    """A state as people say it: 'awaiting-verify-the-actions' -> 'waiting at Verify the actions', 'in-review' -> 'in review'."""
    s = str(state or "")
    if s.startswith("awaiting-"):
        return "waiting at " + s[len("awaiting-"):].replace("-", " ").capitalize()
    return s.replace("-", " ").replace("_", " ")


def may_edit_document(bundle: dict, who: str, rec: dict) -> tuple[bool, str]:
    """Who may change a document's content (the same rule for a person in the editor, a cog asked to edit, an agent
    tool): its author, owner or filer, a document controller, or someone who may create or update documents in its
    folder; never evidence or received documents, never a controlled document in force; a Frame draft only by its
    owner or a policy owner."""
    f = rec.get("fields") or {}
    number = rec.get("number") or rec.get("locator")
    if rec.get("type") != "document":
        return False, f"{number} is not a document"
    if f.get("doc_class") in ("evidence", "external"):
        return False, f"{number} is {'evidence' if f.get('doc_class') == 'evidence' else 'a received document'} and is never changed"
    if f.get("doc_class") == "controlled" and rec.get("state") not in EDITABLE_CONTROLLED_STATES:
        return False, f"{number} is a controlled document in force ({rec.get('state')}); it changes only through a new revision"
    members = (bundle or {}).get("members") or {}
    if who not in members:
        return False, f"{who} is not a member"
    roles = set(authz.roles_of(bundle, who))
    if f.get("doc_type") == "frame-draft":
        if who == f.get("owner") or "policy-owner" in roles:
            return True, ""
        return False, f"{number} is {f.get('owner')}'s draft of a policy; only its author or a policy owner changes it"
    if who in (f.get("author"), f.get("owner")) or who == rec.get("created_by") or "document-controller" in roles:
        return True, ""
    for action in ("update", "create"):
        if authz.check(bundle, who, action, "document", rec.get("folder") or "")["allowed"]:
            return True, ""
    return False, (f"only its author ({f.get('author') or f.get('owner') or '-'}), a document controller or someone who may create "
                   f"documents in {rec.get('folder')} changes {number}")


def record_numbers_in(text: str) -> list[str]:
    return sorted(set(_RECORD_NUMBER.findall(text or "")))


SEARCH_SKIP = {"office_comments", "conditions", "citations", "provenance", "grant", "content_sha", "mime", "editors", "checks", "exports"}


def _values_text(v) -> str:
    """A field's values as text, without the key names of nested data."""
    if isinstance(v, dict):
        return " ".join(_values_text(x) for x in v.values())
    if isinstance(v, (list, tuple)):
        return " ".join(_values_text(x) for x in v)
    return str(v) if v is not None else ""


def tool_specs() -> list[dict]:
    return TOOLS


class RecordTools:
    """The tools, bound to a hub and the principal on whose behalf they run."""

    def __init__(self, hub, principal: str):
        self.hub = hub
        self.principal = principal
        meta = hub.releases.current()
        self.bundle = hub.releases.bundle(meta["digest"]) if meta else {"members": {}, "permissions": []}
        self._readable: dict[str, bool] = {}
        self.calls: list[dict] = []

    # --- permissions
    def may_read(self, folder: str) -> bool:
        if folder not in self._readable:
            try:
                self._readable[folder] = authz.check(self.bundle, self.principal, "read", "*", folder)["allowed"]
            except Exception:
                self._readable[folder] = False
        return self._readable[folder]

    def _find(self, number: str) -> dict | None:
        number = (number or "").strip()
        if not number:
            return None
        rec = self.hub.kernel.get(number) if number.startswith("rec:") else None
        if rec is None:
            n = number.upper()
            rec = next((r for r in self.hub.kernel.query() if r["number"].upper() == n), None)
        if rec is None or not self.may_read(rec["folder"]):
            return None
        return rec

    # --- shapes
    @staticmethod
    def _title(rec: dict) -> str:
        f = rec["fields"]
        for k in ("title", "statement", "name", "description", "memo"):
            if f.get(k):
                return str(f[k])[:140]
        if f.get("supplier"):
            return f"{f.get('supplier')} {f.get('amount', '')} {f.get('currency', '')}".strip()
        return ""

    def _brief(self, rec: dict) -> dict:
        out = {"number": rec["number"], "type": rec["type"], "title": self._title(rec), "state": rec["state"], "folder": rec["folder"],
               "waiting_for": rec.get("assignees") or [], "updated": rec.get("updated_at") or rec.get("created_at")}
        f = rec["fields"]
        for k in ("severity", "risk", "amount", "currency", "change_type", "due", "priority"):  # enough to tell items apart
            if f.get(k) not in (None, "", [], {}):
                out[k] = f[k]
        return out

    @staticmethod
    def _value(v):
        if isinstance(v, str) and v.startswith("rec:"):
            return v.split(":")[-1]
        if isinstance(v, list):
            return [RecordTools._value(x) for x in v[:20]]
        if isinstance(v, dict):
            s = json.dumps(v, default=str)
            return v if len(s) < 600 else s[:600] + "..."
        return v

    # --- the tools
    DONE_STATES = {"closed", "rejected", "cancelled", "posted", "released", "effective", "accepted", "received", "completed",
                   "superseded", "obsolete", "ordered", "issued", "filed", "active", "done"}

    def _type_of(self, given: str) -> str | None:
        """'ncr', 'NCRs', 'nonconformance', 'purchase requests' -> the record type."""
        if not given:
            return None
        from .shell.text import TYPE_NAMES

        g = given.strip().lower().replace("_", "-")
        g1 = g.rstrip("s")
        types = set(self.hub.kernel.types)
        for cand in (g, g1, g.replace(" ", "-"), g1.replace(" ", "-")):
            if cand in types:
                return cand
        for t, name in TYPE_NAMES.items():
            n = name.lower()
            if g in (n, n + "s") or g1 == n or g.replace("-", " ") in (n, n + "s"):
                return t
        return g

    def _state_ok(self, state: str, want: str) -> bool:
        if not want:
            return True
        w = want.strip().lower()
        if w == state:
            return True
        if w in ("open", "active", "pending", "waiting", "in progress", "not closed", "unfinished"):
            return state not in self.DONE_STATES
        words = [x for x in re.split(r"[\s_-]+", w) if x and x not in ("the", "a", "for")]
        return all(x in state for x in words)

    def search_records(self, query: str = "", type: str = "", state: str = "", folder: str = "", assignee: str = "", limit: int = 10) -> dict:
        from .shell.text import TYPE_NAMES

        limit = max(1, min(int(limit or 10), 25))
        rtype = self._type_of(type)
        recs = self.hub.kernel.query(type_=rtype or None, assignee=assignee or None)
        type_words = {rtype or "", (TYPE_NAMES.get(rtype or "") or "").lower()} if rtype else set()
        words = [w for w in re.split(r"\s+", (query or "").lower()) if w and w.rstrip("s") not in {t.rstrip("s") for t in type_words if t}]
        out, states = [], {}
        for r in sorted(recs, key=lambda r: r.get("updated_at") or r.get("created_at") or "", reverse=True):
            if folder and not (r["folder"] == folder or r["folder"].startswith(folder + "/")):
                continue
            if not self.may_read(r["folder"]):
                continue
            states[r["state"]] = states.get(r["state"], 0) + 1
            if not self._state_ok(r["state"], state):
                continue
            if words:
                # Values only, as words: "high" must not match a field named risk_not_high.
                hay = " ".join([r["number"], r["type"], TYPE_NAMES.get(r["type"]) or "", r["state"].replace("-", " "),
                                *[_values_text(v) for k, v in r["fields"].items() if k not in SEARCH_SKIP]]).lower()
                tokens = set(re.findall(r"[\w.-]+", hay))
                if not all(any(t == w.rstrip("s") or t.rstrip("s") == w.rstrip("s") or (len(w) > 3 and t.startswith(w.rstrip("s"))) for t in tokens)
                           for w in words):
                    continue
            out.append(self._brief(r))
            if len(out) >= limit:
                break
        hidden = sum(1 for f in {r["folder"] for r in recs} if not self.may_read(f))
        res = {"count": len(out), "records": out}
        if not out and states:
            res["hint"] = "no match; the states these records are in: " + ", ".join(f"{k} ({v})" for k, v in sorted(states.items()))
        if hidden:
            res["note"] = (f"only records in folders {self.principal} may read are searched; {hidden} folder(s) with matching types are "
                           "closed to them, so say 'none that you can see' rather than 'none'")
        return res

    def get_record(self, number: str) -> dict:
        rec = self._find(number)
        if rec is None:
            return {"error": f"no record {number!r} that you may read"}
        fields = {k: self._value(v) for k, v in rec["fields"].items() if k not in SKIP_FIELDS and v not in (None, "", [], {})}
        links = [{"relation": l["rel"], "direction": l["direction"], "record": l["target"].split(":")[-1]}
                 for l in self.hub.kernel.links_of(rec["locator"]) if self.may_read((self.hub.kernel.get(l["target"]) or {}).get("folder", ""))][:30]
        docs = [{"number": d["number"], "title": d["fields"].get("title"), "type": d["fields"].get("doc_type"), "state": d["state"]}
                for d in self.hub.dms.documents_of(rec["locator"])][:20]
        decisions = [{"by": d.get("by"), "verdict": d.get("verdict"), "meaning": d.get("meaning"), "at": d.get("at"), "step": d.get("step")}
                     for d in rec.get("decisions") or []]
        run = self.hub.runner.run_for_record(rec["locator"])
        out = {**self._brief(rec), "fields": fields, "decisions": decisions, "links": links, "documents": docs}
        if run:
            out["run"] = {"process": run["process"], "status": run["status"],
                          "waiting_at": (run.get("pending") or {}).get("title"), "waiting_for": (run.get("pending") or {}).get("assignees")}
        if rec["type"] == "document":
            out["has_text"] = bool(rec["fields"].get("text"))
        return out

    def read_document(self, number: str, version: int | None = None, max_chars: int = 6000) -> dict:
        rec = self._find(number)
        if rec is None or rec["type"] != "document":
            return {"error": f"no document {number!r} that you may read"}
        from .dms import extract_text

        try:
            content, v = self.hub.dms.content(rec["locator"], int(version) if version else None)
        except Exception as e:
            return {"error": str(e)}
        text = extract_text(content, v.get("mime") or "", v.get("filename") or "")
        cap = max(500, min(int(max_chars or 6000), 20000))
        out = {"number": rec["number"], "title": rec["fields"].get("title"), "version": v.get("version"), "filename": v.get("filename"),
               "text": text[:cap], "truncated": len(text) > cap}
        comments = rec["fields"].get("office_comments") or []
        if comments:
            out["comments"] = [{"id": c.get("id"), "author": c.get("author"), "text": c.get("text"), "reply_to": c.get("parent")} for c in comments[:30]]
        return out

    def waiting_for(self, who: str = "") -> dict:
        """A member's queue; a role (e.g. payables-reviewer) means every member holding it."""
        from .routing import principals_for

        who = (who or self.principal).strip()
        members = self.bundle.get("members") or {}
        role = who.lower().replace(" ", "-").rstrip("s")
        if who not in members and (role in (self.bundle.get("roles") or []) or who.lower().replace(" ", "-") in (self.bundle.get("roles") or [])):
            role = role if role in (self.bundle.get("roles") or []) else who.lower().replace(" ", "-")
            names = principals_for(self.bundle, f"role:{role}")
            merged = {"who": f"the {role.replace('-', ' ')}s ({', '.join(names)})", "count": 0, "items": []}
            for n in names:
                q = self.waiting_for(n)
                for i in q["items"]:
                    i["waiting_for"] = n
                merged["items"] += q["items"]
            merged["count"] = len(merged["items"])
            return merged
        if who not in members:
            return {"error": f"{who!r} is neither a member nor a role; members: {', '.join(sorted(members))}"}
        items = []
        for r in self.hub.runner.pending_for(who):
            rec = self.hub.kernel.get(r["record"]) if r.get("record") else None
            if rec is not None and not self.may_read(rec["folder"]):
                continue
            p = r.get("pending") or {}
            items.append({"record": rec["number"] if rec else None, "title": self._title(rec) if rec else "", "process": r["process"],
                          "step": p.get("title"), "kind": p.get("kind"), "due": p.get("due")})
        return {"who": who, "count": len(items), "items": items[:40]}

    def record_history(self, number: str, limit: int = 20) -> dict:
        rec = self._find(number)
        if rec is None:
            return {"error": f"no record {number!r} that you may read"}
        evs = self.hub.kernel.events(aggregate=rec["locator"])[-max(1, min(int(limit or 20), 60)):]
        out = {"number": rec["number"], "events": [{"at": e["at"], "by": e["by"], "what": e["type"].replace("record.", "").replace("document.", "document "),
                                                    "detail": {k: self._value(v) for k, v in (e["data"] or {}).items() if k not in ("fields", "text_excerpt")}
                                                    if e["type"] != "record.fields_set" else sorted((e["data"] or {}).get("fields", {}))}
                                                   for e in evs]}
        if rec["type"] == "document" and getattr(self.hub, "dms", None) is not None:
            # Who wrote each version, and why (the note names the cog and the person who asked): the answer to "who removed it?".
            out["versions"] = [{"version": v.get("version"), "at": v.get("at"), "by": v.get("by"), "note": v.get("note") or "",
                                "size": v.get("size")} for v in self.hub.dms.versions(rec["locator"])][-20:]
        return out

    def overview(self) -> dict:
        counts: dict[str, dict[str, int]] = {}
        for r in self.hub.kernel.query():
            if not self.may_read(r["folder"]):
                continue
            counts.setdefault(r["type"], {}).setdefault(r["state"], 0)
            counts[r["type"]][r["state"]] += 1
        return {"types": {t: {"total": sum(s.values()), "by_state": s} for t, s in sorted(counts.items())}}

    # --- dispatch
    def call(self, name: str, arguments: dict | None) -> dict:
        args = arguments or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        fn = getattr(self, name, None) if name in {t["name"] for t in TOOLS} else None
        if fn is None:
            result = {"error": f"no tool {name!r}"}
        else:
            try:
                result = fn(**{k: v for k, v in args.items() if k in TOOL_ARGS[name]})
            except TypeError as e:
                result = {"error": f"bad arguments: {e}"}
            except Exception as e:  # a tool never breaks the conversation
                result = {"error": f"{type(e).__name__}: {e}"}
        self.calls.append({"tool": name, "arguments": args, "ok": "error" not in result})
        return result


TOOL_ARGS = {t["name"]: set((t["parameters"].get("properties") or {}).keys()) for t in TOOLS}


def as_text(result: dict, limit: int = 8000) -> str:
    s = json.dumps(result, default=str, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + '..." (cut)'
