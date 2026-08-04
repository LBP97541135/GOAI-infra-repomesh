#!/usr/bin/env python3
"""Project TeamHarness team assets into a Claude Code workspace.

Three targets, because those are the three things Claude Code reads without
being told to:

``<workspace>/CLAUDE.md``
    Auto-loaded context. Carries the team contract, the role prompt, and the
    runtime team facts.
``<workspace>/.claude/skills/<id>/``
    Workspace skills, filtered to the ones ``plugin.yaml`` grants this role.
``<workspace>/.mcp.json``
    The TeamHarness MCP server, which is where ``taskflow`` / ``artifact`` /
    ``filesync`` / ``inbox`` come from.

The workspace is the operator's own repository, not a container we own. That
single fact drives every design decision below:

- ``CLAUDE.md`` very likely already exists and holds someone's hand-written
  project notes. We own a marker-delimited section of it and nothing else --
  the same trick the qwenpaw adapter uses for its runtime-context block, for
  the same reason.
- ``.mcp.json`` may already list the operator's own servers, so only the
  ``teamharness`` key is touched.
- ``unproject`` removes exactly what ``project`` wrote and leaves anything it
  did not recognise alone. Uninstalling a bridge must not cost someone their
  notes.

Credential red line: ``.mcp.json`` gets ``${VAR}`` *references* built from
``AssetContext.mcp_env_passthrough`` (names only, by construction) and never a
value read out of the environment. The runtime expands them at spawn time.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

from . import _assets
from ..protocol import AssetContext, AssetProjection

# Re-exported: the markers are part of the on-disk contract and several call
# sites (and tests) import them from here.
MANAGED_BEGIN = _assets.MANAGED_BEGIN
MANAGED_END = _assets.MANAGED_END

CONTEXT_FILE = "CLAUDE.md"
MCP_FILE = ".mcp.json"
SKILLS_DIR = ".claude/skills"
MCP_SERVER_ID = "teamharness"
# The role is a fact about this member, not a secret, so it is the one entry in
# the projected ``env`` block that may be a literal value.
ROLE_ENV_VAR = "AGENTTEAMS_AGENT_ROLE"
# Tells the MCP server where the workspace is; see _mcp_env.
SHARED_DIR_ENV_VAR = "TEAMHARNESS_SHARED_DIR"


class ClaudeCodeProjector:
    """``AssetProjector`` for a local Claude Code install."""

    name = "claude-code"

    # ---- projection --------------------------------------------------

    def project(self, ctx: AssetContext) -> AssetProjection:
        warnings: list[str] = []
        files: list[str] = []

        workspace = Path(ctx.workspace)
        workspace.mkdir(parents=True, exist_ok=True)

        files.append(_assets.write_context(workspace / CONTEXT_FILE, ctx, warnings))
        skills = _assets.install_skills(workspace / SKILLS_DIR, ctx, warnings)
        files.extend(f"{SKILLS_DIR}/{skill}" for skill in skills)
        files.append(self._write_mcp(workspace, ctx, warnings))

        return AssetProjection(
            files=tuple(sorted(files)),
            mcp_servers=(MCP_SERVER_ID,),
            skills=tuple(skills),
            warnings=tuple(warnings),
        )

    def unproject(self, ctx: AssetContext) -> AssetProjection:
        warnings: list[str] = []
        files: list[str] = []

        workspace = Path(ctx.workspace)
        if _assets.strip_context(workspace / CONTEXT_FILE):
            files.append(CONTEXT_FILE)
        # Derived from the manifest rather than from what is on disk: removing
        # "every directory under .claude/skills" would delete skills the
        # operator installed themselves.
        removed = _assets.remove_skills(workspace / SKILLS_DIR, ctx, warnings)
        files.extend(f"{SKILLS_DIR}/{skill}" for skill in removed)
        if self._strip_mcp(workspace):
            files.append(MCP_FILE)

        return AssetProjection(
            files=tuple(sorted(files)),
            mcp_servers=(MCP_SERVER_ID,),
            skills=tuple(removed),
            warnings=tuple(warnings),
        )

    # ---- .mcp.json ---------------------------------------------------
    #
    # The only asset with runtime-specific *logic* rather than just a
    # runtime-specific path. Context-file and skill handling live in
    # ``_assets``: the marker algebra and the manifest role filter are
    # identical for every runtime, and a second copy of the code that decides
    # which bytes of someone's CLAUDE.md to replace is how one of them starts
    # eating notes that were never ours.

    def _write_mcp(self, workspace: Path, ctx: AssetContext, warnings: list[str]) -> str:
        target = workspace / MCP_FILE
        server_path = Path(ctx.plugin_dir) / "mcp" / "server.py"
        if not server_path.is_file():
            # Still written: the entry is the shape the runtime expects, and a
            # partially unpacked package is the operator's problem to fix, not
            # a reason to leave the agent without its team tools.
            warnings.append(f"MCP server entry point not found: {server_path}")

        payload = _read_json(target)
        servers = payload.get("mcpServers")
        if not isinstance(servers, dict):
            servers = {}
        servers[MCP_SERVER_ID] = {
            # The interpreter running the bridge, not the bare name "python".
            # Claude Code spawns this server from its own environment, where
            # "python" may be a different install, the Windows launcher, or
            # absent entirely -- and this interpreter is the one already known
            # to satisfy the server's imports. Same choice the qwenpaw adapter
            # makes for the same server.
            "command": sys.executable or "python",
            "args": [str(server_path)],
            "env": _mcp_env(ctx),
        }
        payload["mcpServers"] = servers
        _write_json(target, payload)
        return MCP_FILE

    def _strip_mcp(self, workspace: Path) -> bool:
        target = workspace / MCP_FILE
        if not target.is_file():
            return False
        payload = _read_json(target)
        servers = payload.get("mcpServers")
        if not isinstance(servers, dict) or MCP_SERVER_ID not in servers:
            return False
        servers.pop(MCP_SERVER_ID)
        others = {key: value for key, value in payload.items() if key != "mcpServers"}
        if not servers and not others:
            # The file only ever held our server, so removing it restores the
            # workspace to "no project MCP config" rather than "an empty one".
            target.unlink()
            return True
        payload["mcpServers"] = servers
        _write_json(target, payload)
        return True


# ---- helpers ---------------------------------------------------------


def _mcp_env(ctx: AssetContext) -> dict[str, str]:
    """Literal role, ``${VAR}`` references for everything else.

    ``mcp_env_passthrough`` carries variable *names*; this is the function that
    turns them into references. It must never call ``os.getenv`` -- a resolved
    token here would be written to a file in the operator's repository, which
    is the one failure mode the whole passthrough design exists to prevent.
    """
    env = {
        ROLE_ENV_VAR: _assets.role(ctx),
        # Without this, ``taskflow``/``filesync`` cannot infer where the
        # workspace is and every call fails with "workspaceDir is required".
        # The server derives it from QWENPAW_WORKING_DIR / COPAW_WORKING_DIR
        # inside a worker container; a remote member has neither, so the
        # projector supplies the equivalent. A path, not a credential.
        SHARED_DIR_ENV_VAR: str(Path(ctx.workspace) / "shared"),
        # MCP frames are UTF-8, but Python's standard streams follow the
        # platform locale: on a Chinese Windows install the server encodes its
        # own tool descriptions as GBK and the client silently sees no tools at
        # all. Current servers pin this themselves; setting it here keeps an
        # older packaged server working too, and costs nothing when redundant.
        "PYTHONIOENCODING": "utf-8",
    }
    for name in ctx.mcp_env_passthrough:
        clean = _assets.text(name)
        # Guard the literal: an operator who lists the role variable in
        # passthrough would otherwise turn it into an unresolvable reference.
        if not clean or clean == ROLE_ENV_VAR:
            continue
        env[clean] = "${" + clean + "}"
    return env


def _read_json(path: Path) -> dict[str, Any]:
    raw = _assets.read_text(path)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if _assets.read_text(path) != text:
        path.write_text(text, encoding="utf-8")
