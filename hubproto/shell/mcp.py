"""MCP over HTTP: the record tools for any MCP client (Claude Desktop, Claude Code, an IDE), as JSON-RPC 2.0 on
POST /mcp. Each call runs as the member named by ``?as=`` (or the X-Hub-As header) and sees only what they may read.

Methods: initialize, notifications/initialized, ping, tools/list, tools/call. Identity is the prototype's ``as``;
production puts the hub's OIDC token here (plan, section 11)."""

from __future__ import annotations

import json

from ..tools import RecordTools, as_text, tool_specs
from .handler import log_line

PROTOCOL = "2025-06-18"


class McpMixin:
    def handle_mcp(self, method: str) -> None:
        if method != "POST":
            return self._json({"error": "POST JSON-RPC to /mcp"}, 405)
        try:
            req = json.loads(self.raw_body or b"{}")
        except ValueError:
            return self._json({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}, 400)
        batch = req if isinstance(req, list) else [req]
        principal = (self.q.get("as", [""])[0] or self.headers.get("X-Hub-As") or "").strip()
        out = [r for r in (self._mcp_one(x, principal) for x in batch) if r is not None]
        if not out:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        return self._json(out if isinstance(req, list) else out[0])

    def _mcp_one(self, req: dict, principal: str):
        rid, m, params = req.get("id"), req.get("method", ""), req.get("params") or {}
        if m.startswith("notifications/"):
            return None
        if m == "initialize":
            return {"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": PROTOCOL, "capabilities": {"tools": {"listChanged": False}},
                                                              "serverInfo": {"name": "hub-records", "version": "0.1"},
                                                              "instructions": "Records of the hub, as the member named by ?as=. Read-only."}}
        if m == "ping":
            return {"jsonrpc": "2.0", "id": rid, "result": {}}
        bundle = self.bundle or {}
        if not principal or principal not in (bundle.get("members") or {}):
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32001, "message": "name a member with ?as= (or the X-Hub-As header)"}}
        if m == "tools/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [{"name": t["name"], "description": t["description"], "inputSchema": t["parameters"]}
                                                                    for t in tool_specs()]}}
        if m == "tools/call":
            name = params.get("name") or ""
            res = RecordTools(self.hub, principal).call(name, params.get("arguments") or {})
            log_line(f"mcp tools/call as={principal} tool={name} ok={'error' not in res}")
            return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": as_text(res, 20000)}], "isError": "error" in res}}
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"no method {m!r}"}}
