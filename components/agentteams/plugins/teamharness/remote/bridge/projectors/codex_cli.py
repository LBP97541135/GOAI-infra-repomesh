#!/usr/bin/env python3
"""``AssetProjector`` for a local Codex CLI install.

Codex reads the same two kinds of asset Claude Code does, under different
names: ``AGENTS.md`` in the workspace root instead of ``CLAUDE.md``, and
``.codex/skills/`` instead of ``.claude/skills/``. Both are project-scoped, so
both are projected exactly the way the Claude projector projects its
equivalents -- marker-delimited managed section, role-filtered skills, nothing
outside the section touched.

The third asset does **not** map. Claude Code takes MCP servers from a
project-level ``.mcp.json``; Codex has no project-level equivalent, only the
global ``~/.codex/config.toml`` shared with everything else the operator runs.
Writing there would make joining a team mutate machine-wide configuration, so
this projector deliberately projects **no MCP config at all** --
``drivers/codex_cli.mcp_config_args`` declares the server with per-invocation
``-c`` overrides instead. ``mcp_servers`` in the returned projection is
therefore empty, and that is a statement, not an oversight.

Everything genuinely shared with the Claude projector -- reading the contract,
rendering facts, filtering skills by role, the marker algebra -- is imported
from ``_assets`` rather than reimplemented. A second copy of the marker logic
is how one of them starts eating user files.
"""

from __future__ import annotations

from pathlib import Path

from . import _assets
from ..protocol import AssetContext, AssetProjection

CONTEXT_FILE = "AGENTS.md"
SKILLS_DIR = ".codex/skills"


class CodexCliProjector:
    """``AssetProjector`` for a local Codex CLI install."""

    name = "codex-cli"

    # ---- projection --------------------------------------------------

    def project(self, ctx: AssetContext) -> AssetProjection:
        warnings: list[str] = []
        files: list[str] = []

        workspace = Path(ctx.workspace)
        workspace.mkdir(parents=True, exist_ok=True)

        files.append(_assets.write_context(workspace / CONTEXT_FILE, ctx, warnings))
        skills = _assets.install_skills(workspace / SKILLS_DIR, ctx, warnings)
        files.extend(f"{SKILLS_DIR}/{skill}" for skill in skills)

        return AssetProjection(
            files=tuple(sorted(files)),
            # Intentionally empty: see the module docstring. The TeamHarness
            # server is declared per invocation, not installed.
            mcp_servers=(),
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
        # "every directory under .codex/skills" would delete skills the
        # operator installed themselves.
        removed = _assets.remove_skills(workspace / SKILLS_DIR, ctx, warnings)
        files.extend(f"{SKILLS_DIR}/{skill}" for skill in removed)
        # No MCP config was ever written, so there is nothing to remove -- the
        # ``-c`` overrides live and die with each invocation.

        return AssetProjection(
            files=tuple(sorted(files)),
            mcp_servers=(),
            skills=tuple(removed),
            warnings=tuple(warnings),
        )
