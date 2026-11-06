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
         "state": {"type": "string", "description": "a state, e.g. open, posted, awaiting-approve-the-bill"},
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
     "description": "The history of a record: who created it, changed it, moved it on, and the run it is in.",
     "parameters": {"type": "object", "properties": {"number": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["number"]}},
    {"name": "overview",
     "description": "How many records of each type there are, by state, in the folders the person may read.",
     "parameters": {"type": "object", "properties": {}}},
]


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
        return {"number": rec["number"], "type": rec["type"], "title": self._title(rec), "state": rec["state"], "folder": rec["folder"],
                "waiting_for": rec.get("assignees") or [], "updated": rec.get("updated_at") or rec.get("created_at")}

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
    def search_records(self, query: str = "", type: str = "", state: str = "", folder: str = "", assignee: str = "", limit: int = 10) -> dict:
        limit = max(1, min(int(limit or 10), 25))
        recs = self.hub.kernel.query(type_=type or None, state=state or None, assignee=assignee or None)
        words = [w for w in re.split(r"\s+", (query or "").lower()) if w]
        out = []
        for r in sorted(recs, key=lambda r: r.get("updated_at") or r.get("created_at") or "", reverse=True):
            if folder and not (r["folder"] == folder or r["folder"].startswith(folder + "/")):
                continue
            if not self.may_read(r["folder"]):
                continue
            if words:
                hay = (r["number"] + " " + r["type"] + " " + json.dumps({k: v for k, v in r["fields"].items() if k not in ("office_comments",)}, default=str)).lower()
                if not all(w in hay for w in words):
                    continue
            out.append(self._brief(r))
            if len(out) >= limit:
                break
        hidden = sum(1 for f in {r["folder"] for r in recs} if not self.may_read(f))
        res = {"count": len(out), "records": out}
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
        return {"number": rec["number"], "events": [{"at": e["at"], "by": e["by"], "what": e["type"].replace("record.", "").replace("document.", "document "),
                                                     "detail": {k: self._value(v) for k, v in (e["data"] or {}).items() if k not in ("fields", "text_excerpt")}
                                                     if e["type"] != "record.fields_set" else sorted((e["data"] or {}).get("fields", {}))}
                                                    for e in evs]}

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
