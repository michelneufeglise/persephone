#!/usr/bin/env python3
"""
Persephone git remote helpers — a tiny stdlib-only MCP stdio server.

The upstream `mcp-server-git` (uvx mcp-server-git) exposes read/write tools
for local git operations — status, diff, commit, add, log, branch, etc — but
NO remote operations (push, pull, fetch). This companion server fills that
gap for Ornith Coder mode.

Scope: the Persephone repo only. The working directory is baked in via the
PERSEPHONE_GIT_REPO env var (falls back to the repo root the wizard uses).

Protocol: JSON-RPC 2.0 over stdio (line-delimited). Implements the subset
mcp_client.py speaks: initialize, tools/list, tools/call, and the
notifications/initialized notification.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

_DEFAULT_REPO = "/Users/michelneufeglise/private/persephone"
_REPO         = os.environ.get("PERSEPHONE_GIT_REPO") or _DEFAULT_REPO

_TOOLS = [
    {
        "name": "git_push",
        "description": (
            "Push commits from the local Persephone repo to a remote. Defaults to "
            "`origin` and the current branch. Set `force` to force-push with lease."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "remote": {"type": "string", "default": "origin",
                           "description": "Remote name (e.g. 'origin')."},
                "branch": {"type": "string",
                           "description": "Local branch. Defaults to the current branch."},
                "force":  {"type": "boolean", "default": False,
                           "description": "Force-push using --force-with-lease. Never a plain --force."},
            },
        },
    },
    {
        "name": "git_pull",
        "description": "Fetch and merge from a remote branch. Defaults to origin / current branch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "remote": {"type": "string", "default": "origin"},
                "branch": {"type": "string",
                           "description": "Remote branch. Defaults to the tracked upstream."},
                "rebase": {"type": "boolean", "default": False,
                           "description": "Use --rebase instead of merge."},
            },
        },
    },
    {
        "name": "git_fetch",
        "description": "Download refs from a remote without merging. Defaults to origin.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "remote": {"type": "string", "default": "origin"},
                "prune":  {"type": "boolean", "default": False,
                           "description": "Remove local refs whose remote counterpart is gone."},
            },
        },
    },
    {
        "name": "git_current_branch",
        "description": "Return the name of the currently checked-out branch.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "git_remote_v",
        "description": "Show the configured remotes with their URLs (git remote -v).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _run(cmd: list[str], timeout: int = 120) -> str:
    try:
        r = subprocess.run(
            ["git", *cmd], cwd=_REPO,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"[timeout] git {' '.join(cmd)}"
    except FileNotFoundError:
        return "[error] git executable not found on PATH"

    out = r.stdout.strip()
    err = r.stderr.strip()
    body = "\n".join(x for x in (out, err) if x) or "(ok)"
    if r.returncode != 0:
        return f"[git exited {r.returncode}]\n{body}"
    return body


def _call(name: str, args: dict) -> str:
    remote = args.get("remote") or "origin"
    branch = args.get("branch")

    if name == "git_push":
        cmd = ["push", remote]
        if branch:
            cmd.append(branch)
        if args.get("force"):
            cmd.append("--force-with-lease")
        return _run(cmd)

    if name == "git_pull":
        cmd = ["pull"]
        if args.get("rebase"):
            cmd.append("--rebase")
        cmd.append(remote)
        if branch:
            cmd.append(branch)
        return _run(cmd)

    if name == "git_fetch":
        cmd = ["fetch", remote]
        if args.get("prune"):
            cmd.append("--prune")
        return _run(cmd)

    if name == "git_current_branch":
        return _run(["branch", "--show-current"])

    if name == "git_remote_v":
        return _run(["remote", "-v"])

    raise ValueError(f"unknown tool: {name}")


# ── JSON-RPC 2.0 stdio loop ────────────────────────────────────────────────────
def _send(msg_id, result=None, error=None) -> None:
    payload = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        # Notifications have no id → no response.
        if msg_id is None:
            continue

        try:
            if method == "initialize":
                _send(msg_id, result={
                    "protocolVersion": "2024-11-05",
                    "serverInfo":      {"name": "persephone-git-remote", "version": "1.0"},
                    "capabilities":    {"tools": {}},
                })
            elif method == "tools/list":
                _send(msg_id, result={"tools": _TOOLS})
            elif method == "tools/call":
                tool_name = params.get("name", "")
                arguments = params.get("arguments") or {}
                text = _call(tool_name, arguments)
                _send(msg_id, result={"content": [{"type": "text", "text": text}]})
            else:
                _send(msg_id, error={"code": -32601, "message": f"method not found: {method}"})
        except Exception as exc:
            _send(msg_id, error={"code": -32000, "message": str(exc)})


if __name__ == "__main__":
    main()
