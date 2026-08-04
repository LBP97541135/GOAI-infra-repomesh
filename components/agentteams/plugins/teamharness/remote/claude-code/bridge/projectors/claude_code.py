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
import shutil
import sys
from typing import Any

from ..protocol import AssetContext, AssetProjection

# Everything between these two lines belongs to us; everything outside belongs
# to the operator. The marker text is part of the on-disk contract -- changing
# it would orphan the previous section instead of replacing it, so a user who
# upgraded the bridge would find two team contracts in their CLAUDE.md.
MANAGED_BEGIN = "<!-- BEGIN AGENTTEAMS TEAMHARNESS (managed; edits inside are overwritten) -->"
MANAGED_END = "<!-- END AGENTTEAMS TEAMHARNESS -->"

CONTEXT_FILE = "CLAUDE.md"
MCP_FILE = ".mcp.json"
SKILLS_DIR = ".claude/skills"
MCP_SERVER_ID = "teamharness"
# The role is a fact about this member, not a secret, so it is the one entry in
# the projected ``env`` block that may be a literal value.
ROLE_ENV_VAR = "AGENTTEAMS_AGENT_ROLE"

TRAILER = "Do not write secrets, credentials, or live task status into this file."

_COPY_IGNORE = shutil.ignore_patterns("__pycache__", ".DS_Store", "*.pyc")


class ClaudeCodeProjector:
    """``AssetProjector`` for a local Claude Code install."""

    name = "claude-code"

    # ---- projection --------------------------------------------------

    def project(self, ctx: AssetContext) -> AssetProjection:
        warnings: list[str] = []
        files: list[str] = []

        workspace = Path(ctx.workspace)
        workspace.mkdir(parents=True, exist_ok=True)

        files.append(self._write_context(workspace, ctx, warnings))
        skills = self._install_skills(workspace, ctx, warnings)
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
        if self._strip_context(workspace):
            files.append(CONTEXT_FILE)
        # Derived from the manifest rather than from what is on disk: removing
        # "every directory under .claude/skills" would delete skills the
        # operator installed themselves.
        removed = self._remove_skills(workspace, ctx, warnings)
        files.extend(f"{SKILLS_DIR}/{skill}" for skill in removed)
        if self._strip_mcp(workspace):
            files.append(MCP_FILE)

        return AssetProjection(
            files=tuple(sorted(files)),
            mcp_servers=(MCP_SERVER_ID,),
            skills=tuple(removed),
            warnings=tuple(warnings),
        )

    # ---- CLAUDE.md ---------------------------------------------------

    def _write_context(
        self, workspace: Path, ctx: AssetContext, warnings: list[str]
    ) -> str:
        target = workspace / CONTEXT_FILE
        block = self._render_block(ctx, warnings)
        existing = _read_text(target)

        if existing is None:
            new_text = block + "\n"
        else:
            span = _managed_span(existing)
            if span is None:
                # Append, never prepend and never overwrite: the top of someone
                # else's CLAUDE.md is the part they actually read.
                head = existing if existing.endswith("\n") else existing + "\n"
                new_text = f"{head}\n{block}\n"
            else:
                start, end = span
                new_text = existing[:start] + block + existing[end:]

        if new_text != existing:
            target.write_text(new_text, encoding="utf-8")
        return CONTEXT_FILE

    def _render_block(self, ctx: AssetContext, warnings: list[str]) -> str:
        plugin_dir = Path(ctx.plugin_dir)
        lines = [MANAGED_BEGIN, ""]

        contract = plugin_dir / "prompts" / "team" / "TEAMS.md"
        contract_text = _read_text(contract)
        if contract_text:
            lines.append(contract_text.strip())
        else:
            warnings.append(f"team contract not found: {contract}")
            lines.append("# Team Contract")

        role_prompt = plugin_dir / "prompts" / "agent" / f"{_role(ctx)}.md"
        role_text = _read_text(role_prompt)
        if role_text:
            lines.extend(["", role_text.strip()])
        else:
            warnings.append(f"role prompt not found: {role_prompt}")

        lines.extend(["", "## Runtime Team Context", ""])
        for key, value in _facts(ctx):
            if value:
                lines.append(f"- {key}: {value}")
        lines.extend(["", TRAILER, MANAGED_END])
        return "\n".join(lines)

    def _strip_context(self, workspace: Path) -> bool:
        target = workspace / CONTEXT_FILE
        existing = _read_text(target)
        if existing is None:
            return False
        span = _managed_span(existing)
        if span is None:
            return False
        start, end = span
        remainder = existing[:start] + existing[end:]
        if not remainder.strip():
            # The file existed only to carry our section; leaving an empty
            # CLAUDE.md behind would still change how Claude Code starts up.
            target.unlink()
            return True
        target.write_text(remainder.rstrip("\n") + "\n", encoding="utf-8")
        return True

    # ---- skills ------------------------------------------------------

    def _install_skills(
        self, workspace: Path, ctx: AssetContext, warnings: list[str]
    ) -> list[str]:
        installed: list[str] = []
        root = workspace / ".claude" / "skills"
        for skill_id, source in self._skills_for_role(ctx, warnings):
            if not source.is_dir():
                warnings.append(f"skill source missing: {source}")
                continue
            target = root / skill_id
            target.parent.mkdir(parents=True, exist_ok=True)
            # Full replace, not merge: these are managed assets, and a merge
            # would keep files deleted upstream alive forever.
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target, ignore=_COPY_IGNORE)
            installed.append(skill_id)
        return installed

    def _remove_skills(
        self, workspace: Path, ctx: AssetContext, warnings: list[str]
    ) -> list[str]:
        removed: list[str] = []
        root = workspace / ".claude" / "skills"
        for skill_id, _source in self._skills_for_role(ctx, warnings):
            target = root / skill_id
            if target.is_dir():
                shutil.rmtree(target)
                removed.append(skill_id)
        if root.is_dir() and not any(root.iterdir()):
            root.rmdir()
        return removed

    def _skills_for_role(
        self, ctx: AssetContext, warnings: list[str]
    ) -> list[tuple[str, Path]]:
        plugin_dir = Path(ctx.plugin_dir)
        manifest_path = plugin_dir / "plugin.yaml"
        manifest = _read_yaml(manifest_path, warnings)
        skills = manifest.get("skills")
        if not isinstance(skills, dict):
            if manifest:
                warnings.append(f"no skills section in {manifest_path}")
            return []

        aliases = _role_aliases(_role(ctx))
        selected: list[tuple[str, Path]] = []
        for group in ("agent", "team"):
            for entry in skills.get(group) or []:
                if not isinstance(entry, dict):
                    continue
                skill_id = _text(entry.get("id"))
                rel_path = _text(entry.get("path"))
                if not skill_id or not rel_path:
                    continue
                roles = [_text(role) for role in entry.get("roles") or []]
                # An entry with no ``roles`` is unrestricted, which matches how
                # the qwenpaw adapter reads the same manifest.
                if roles and not aliases.intersection(roles):
                    continue
                selected.append((skill_id, plugin_dir / rel_path))
        return selected

    # ---- .mcp.json ---------------------------------------------------

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
        ROLE_ENV_VAR: _role(ctx),
        # MCP frames are UTF-8, but Python's standard streams follow the
        # platform locale: on a Chinese Windows install the server encodes its
        # own tool descriptions as GBK and the client silently sees no tools at
        # all. Current servers pin this themselves; setting it here keeps an
        # older packaged server working too, and costs nothing when redundant.
        "PYTHONIOENCODING": "utf-8",
    }
    for name in ctx.mcp_env_passthrough:
        clean = _text(name)
        # Guard the literal: an operator who lists the role variable in
        # passthrough would otherwise turn it into an unresolvable reference.
        if not clean or clean == ROLE_ENV_VAR:
            continue
        env[clean] = "${" + clean + "}"
    return env


def _facts(ctx: AssetContext) -> list[tuple[str, str]]:
    """Non-secret runtime facts, in the qwenpaw ``- key: value`` shape."""
    return [
        ("team.name", _text(ctx.team_name)),
        ("team.teamRoomId", _text(ctx.team_room_id)),
        ("team.leaderName", _text(ctx.leader_name)),
        ("member.name", _text(ctx.member_name)),
        ("member.role", _role(ctx)),
        ("member.matrixUserId", _text(ctx.matrix_user_id)),
        ("member.personalRoomId", _text(ctx.personal_room_id)),
    ]


def _role(ctx: AssetContext) -> str:
    return _text(ctx.role) or "remote-member"


def _role_aliases(role: str) -> set[str]:
    """Tolerate the underscore spellings the manifest does not use.

    ``plugin.yaml`` says ``remote-member``; runtime configs have been seen
    carrying ``remote_member`` and ``team_leader``. Matching only the manifest
    spelling would silently install zero skills.
    """
    aliases = {role}
    if role == "remote_member":
        aliases.add("remote-member")
    if role == "team_leader":
        aliases.add("leader")
    return aliases


def _managed_span(text: str) -> tuple[int, int] | None:
    """``(start, end)`` of the managed section, or ``None`` if there is none.

    Both markers must be present and in order. A half-marker means someone
    edited the file by hand, and replacing a span we cannot delimit would eat
    whatever they wrote -- appending a fresh section is the recoverable choice.
    """
    start = text.find(MANAGED_BEGIN)
    if start < 0:
        return None
    end = text.find(MANAGED_END, start)
    if end < 0:
        return None
    return start, end + len(MANAGED_END)


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError, OSError):
        return None


def _read_json(path: Path) -> dict[str, Any]:
    raw = _read_text(path)
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
    if _read_text(path) != text:
        path.write_text(text, encoding="utf-8")


def _read_yaml(path: Path, warnings: list[str]) -> dict[str, Any]:
    """Same parser as ``bootstrap.py``; a missing PyYAML degrades to a warning.

    Unlike the bootstrap file, an unreadable manifest is not fatal: the agent
    still gets its team contract and MCP tools, just no skills.
    """
    try:
        import yaml
    except ImportError:  # pragma: no cover - depends on the environment
        warnings.append("PyYAML is unavailable; no skills were projected")
        return {}

    raw = _read_text(path)
    if raw is None:
        warnings.append(f"plugin manifest not found: {path}")
        return {}
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        warnings.append(f"{path}: invalid YAML: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def _text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple)):
        return ""
    return str(value).strip()
