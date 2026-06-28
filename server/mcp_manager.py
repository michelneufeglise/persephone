"""
Manages all running MCP server processes for Persephone.

Reads `mcp_servers` (comma-separated IDs) and optional per-server env
overrides from `app_config`, spawns the matching `MCPClient`s, exposes
their tools in Ollama-tool-calling format, and dispatches tool calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import db as _db
import mcp_catalog as _catalog
from mcp_client import MCPClient, MCPError

log = logging.getLogger("mcp_manager")

# Ollama tool names cannot contain ':' or '/'. We namespace as "<serverId>__<toolName>"
# to keep them unique across servers and easy to decode.
_TOOL_NAME_SEP = "__"
# Servers we should NOT auto-start because they need an API key / human config.
# These still appear as "enabled" but won't be spawned until configured.
_REQUIRES_SETUP_SKIP = set()  # populated lazily from catalog


class MCPManager:
    def __init__(self) -> None:
        self.clients: dict[str, MCPClient] = {}
        self._lock = asyncio.Lock()

    # ── lifecycle ────────────────────────────────────────────────────────
    async def sync_with_config(self) -> dict[str, str]:
        """
        Read `mcp_servers` from app_config and reconcile the running set.
        Returns {server_id: status} ("running" / "starting" / error message).
        """
        raw = await _db.get_config("mcp_servers") or ""
        wanted = [s.strip() for s in raw.split(",") if s.strip()]
        catalog = {s["id"]: s for s in _catalog.list_servers()}

        # Per-server env overrides stored as JSON in `mcp_env_<id>`
        async def env_for(sid: str) -> dict[str, str]:
            blob = await _db.get_config(f"mcp_env_{sid}") or ""
            if not blob:
                return {}
            try:
                return {k: str(v) for k, v in json.loads(blob).items()}
            except Exception:
                return {}

        statuses: dict[str, str] = {}

        async with self._lock:
            # Stop ones that aren't wanted anymore
            for sid in list(self.clients):
                if sid not in wanted:
                    log.info("Stopping MCP server '%s' (no longer enabled)", sid)
                    await self.clients[sid].stop()
                    del self.clients[sid]

            # Start new ones
            for sid in wanted:
                if sid in self.clients and self.clients[sid].is_running:
                    statuses[sid] = "running"
                    continue
                spec = catalog.get(sid)
                if not spec:
                    statuses[sid] = "unknown"
                    continue

                # Skip if it requires manual setup and user hasn't supplied env
                env_overrides = await env_for(sid)
                required = [k for k, v in spec["install"].get("env_vars", {}).items()
                            if isinstance(v, dict) and v.get("required")]
                missing  = [k for k in required if k not in env_overrides and not os.environ.get(k)]
                if missing:
                    statuses[sid] = f"needs: {', '.join(missing)}"
                    continue

                install = spec["install"]

                # Resolve args: expand ~ and env vars; allow user-supplied per-server
                # paths via `mcp_paths_<id>` (newline-separated list).
                args = list(install["args"])
                paths_override = await _db.get_config(f"mcp_paths_{sid}") or ""
                if paths_override:
                    user_paths = [p.strip() for p in paths_override.splitlines() if p.strip()]
                    if user_paths:
                        # Replace the trailing path args with the user's choice.
                        # Heuristic: keep all args that aren't existing paths in the home dir.
                        non_paths = [a for a in args
                                     if not (a.startswith("~") or a.startswith("/"))]
                        args = non_paths + user_paths
                args = [os.path.expandvars(os.path.expanduser(a)) for a in args]

                client = MCPClient(sid, install["command"], args, env_overrides)
                try:
                    await client.start()
                    self.clients[sid] = client
                    statuses[sid] = "running"
                except Exception as exc:
                    await client.stop()
                    log.error("Failed to start MCP server '%s': %s", sid, exc)
                    statuses[sid] = f"error: {exc}"

        return statuses

    async def stop_all(self) -> None:
        async with self._lock:
            for sid, client in list(self.clients.items()):
                try:
                    await client.stop()
                except Exception:
                    pass
            self.clients.clear()

    # ── tool registry ────────────────────────────────────────────────────
    def list_tools_for_ollama(self) -> list[dict]:
        """All tools across all running MCPs, in Ollama tool-calling format.

        Tool descriptions are aggressively shortened (first sentence, ≤200 chars)
        because the full descriptions some MCP servers ship (especially
        @modelcontextprotocol/server-filesystem) are essentially README excerpts
        that bloat the prompt by thousands of tokens with no real benefit to
        the model's selection accuracy.
        """
        result: list[dict] = []
        for sid, client in self.clients.items():
            if not client.is_running:
                continue
            for tool in client.tools:
                name        = tool.get("name", "")
                description = tool.get("description") or ""
                schema      = tool.get("inputSchema") or {"type": "object", "properties": {}}
                ollama_name = f"{sid}{_TOOL_NAME_SEP}{name}"

                if not description:
                    description = f"{name} (from {sid})"
                else:
                    # First sentence only, max 200 chars
                    short = description.strip().split("\n")[0]
                    if "." in short:
                        short = short.split(".", 1)[0] + "."
                    description = short[:200]

                result.append({
                    "type": "function",
                    "function": {
                        "name":        ollama_name,
                        "description": description,
                        "parameters":  schema,
                    },
                })
        return result

    def list_tools_summary(self) -> list[dict]:
        """Compact summary for the /api/mcp/tools endpoint."""
        out: list[dict] = []
        for sid, client in self.clients.items():
            for tool in client.tools:
                out.append({
                    "server":      sid,
                    "name":        tool.get("name"),
                    "description": tool.get("description") or "",
                })
        return out

    def status(self) -> dict[str, dict]:
        return {
            sid: {
                "running":    client.is_running,
                "tool_count": len(client.tools),
            }
            for sid, client in self.clients.items()
        }

    # ── dispatch ─────────────────────────────────────────────────────────
    async def call(self, ollama_tool_name: str, arguments: dict | None) -> str:
        """
        Resolve a namespaced tool name back to (server, tool), execute it,
        and flatten the MCP response into a single text string for the LLM.
        """
        if _TOOL_NAME_SEP not in ollama_tool_name:
            raise MCPError(f"Malformed tool name: {ollama_tool_name!r}")

        sid, tool_name = ollama_tool_name.split(_TOOL_NAME_SEP, 1)
        client = self.clients.get(sid)
        if not client or not client.is_running:
            raise MCPError(f"MCP server '{sid}' is not running")

        log.info("→ %s.%s args=%s", sid, tool_name, _truncate(arguments))
        result = await client.call_tool(tool_name, arguments or {})
        text   = _flatten_result(result)
        log.info("← %s.%s → %d chars", sid, tool_name, len(text))
        return text


# ── helpers ─────────────────────────────────────────────────────────────
def _flatten_result(result: Any) -> str:
    """MCP tools/call returns {content: [{type:'text', text:'...'}, ...], isError?: bool}."""
    if not isinstance(result, dict):
        return json.dumps(result, indent=2)

    if result.get("isError"):
        err = result.get("content") or [{"text": "(unspecified MCP error)"}]
        return "ERROR: " + _join_content(err)

    contents = result.get("content") or []
    return _join_content(contents) or json.dumps(result, indent=2)


def _join_content(items: list) -> str:
    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            parts.append(str(item))
            continue
        t = item.get("type", "")
        if t == "text":
            parts.append(item.get("text", ""))
        elif t == "image":
            parts.append("[image]")
        elif t == "resource":
            parts.append(f"[resource: {item.get('resource', {}).get('uri', '?')}]")
        else:
            parts.append(json.dumps(item))
    return "\n".join(p for p in parts if p)


def _truncate(value: Any, limit: int = 200) -> str:
    s = json.dumps(value, default=str)
    return s if len(s) <= limit else s[:limit] + "…"


# ── singleton ───────────────────────────────────────────────────────────
manager = MCPManager()
