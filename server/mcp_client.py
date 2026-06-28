"""
Minimal MCP (Model Context Protocol) client.

Communicates with one MCP server process via JSON-RPC 2.0 over stdio
(newline-delimited JSON). The full MCP spec covers prompts, resources,
and sampling — this client implements the subset Persephone uses today:
initialize handshake, tools/list, tools/call.

Reference: https://modelcontextprotocol.io
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from typing import Any

log = logging.getLogger("mcp_client")

_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "persephone", "version": "1.0"}


class MCPError(RuntimeError):
    pass


class MCPClient:
    """One MCP server process. Not thread-safe; expects a single asyncio loop."""

    def __init__(
        self,
        server_id: str,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
    ):
        self.server_id = server_id
        self.command   = command
        self.args      = list(args)
        self.env       = env or {}
        self.proc:        asyncio.subprocess.Process | None = None
        self.tools:       list[dict] = []
        self._next_id     = 0
        self._pending:    dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._id_lock     = asyncio.Lock()
        self._write_lock  = asyncio.Lock()
        self._stopped     = False

    # ── lifecycle ──────────────────────────────────────────────────────────
    async def start(self, init_timeout: float = 20.0) -> None:
        """Spawn the subprocess, complete the MCP handshake, and cache tools."""
        cmd_path = shutil.which(self.command) or self.command
        proc_env = {**os.environ, **self.env}

        log.info("[%s] spawning: %s %s", self.server_id, cmd_path, " ".join(self.args))
        self.proc = await asyncio.create_subprocess_exec(
            cmd_path, *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=proc_env,
        )

        self._reader_task = asyncio.create_task(self._read_loop(), name=f"mcp-read-{self.server_id}")
        self._stderr_task = asyncio.create_task(self._stderr_loop(), name=f"mcp-err-{self.server_id}")

        # MCP handshake
        await self._request("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities":    {},
            "clientInfo":      _CLIENT_INFO,
        }, timeout=init_timeout)
        await self._notify("notifications/initialized")

        # List tools
        result = await self._request("tools/list", {}, timeout=10.0)
        self.tools = result.get("tools", []) if isinstance(result, dict) else []
        log.info("[%s] ready with %d tools: %s",
                 self.server_id, len(self.tools),
                 [t.get("name") for t in self.tools])

    async def stop(self) -> None:
        self._stopped = True
        if self._reader_task:
            self._reader_task.cancel()
        if self._stderr_task:
            self._stderr_task.cancel()
        if self.proc:
            try:
                self.proc.terminate()
                try:
                    await asyncio.wait_for(self.proc.wait(), 3.0)
                except asyncio.TimeoutError:
                    self.proc.kill()
                    await self.proc.wait()
            except ProcessLookupError:
                pass
        # Cancel any in-flight requests
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(MCPError(f"MCP server '{self.server_id}' stopped"))
        self._pending.clear()

    @property
    def is_running(self) -> bool:
        return bool(self.proc and self.proc.returncode is None and not self._stopped)

    # ── tool invocation ────────────────────────────────────────────────────
    async def call_tool(self, name: str, arguments: dict | None = None, timeout: float = 45.0) -> dict:
        return await self._request("tools/call", {
            "name":      name,
            "arguments": arguments or {},
        }, timeout=timeout)

    # ── JSON-RPC plumbing ──────────────────────────────────────────────────
    async def _request(self, method: str, params: Any = None, timeout: float = 30.0) -> Any:
        if not self.is_running:
            raise MCPError(f"MCP server '{self.server_id}' is not running")

        async with self._id_lock:
            self._next_id += 1
            req_id = self._next_id

        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending[req_id] = future

        await self._write(msg)
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            raise MCPError(f"'{self.server_id}' timed out on {method!r}")
        finally:
            self._pending.pop(req_id, None)

    async def _notify(self, method: str, params: Any = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        await self._write(msg)

    async def _write(self, msg: dict) -> None:
        assert self.proc and self.proc.stdin
        line = (json.dumps(msg) + "\n").encode("utf-8")
        async with self._write_lock:
            self.proc.stdin.write(line)
            await self.proc.stdin.drain()

    async def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while not self._stopped:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("[%s] non-JSON output: %r", self.server_id, line[:200])
                    continue

                req_id = msg.get("id")
                if req_id is None:
                    # Notification from server — ignored for now
                    continue
                fut = self._pending.get(req_id)
                if not fut or fut.done():
                    continue
                if "error" in msg:
                    err = msg["error"]
                    fut.set_exception(MCPError(
                        f"{err.get('message', 'unknown error')} (code {err.get('code')})"
                    ))
                else:
                    fut.set_result(msg.get("result"))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.exception("[%s] read loop crashed: %s", self.server_id, exc)

    async def _stderr_loop(self) -> None:
        """Forward stderr to logs at debug level — useful for MCP diagnostics."""
        assert self.proc and self.proc.stderr
        try:
            while not self._stopped:
                line = await self.proc.stderr.readline()
                if not line:
                    break
                text = line.decode(errors="replace").rstrip()
                if text:
                    log.debug("[%s err] %s", self.server_id, text)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
