"""Concrete ``AssetProjector`` implementations for the TeamHarness bridge.

One module per local coding agent, each exporting a single class that satisfies
``bridge.protocol.AssetProjector``. A projector owns *only* the mapping from
TeamHarness assets (team contract, role prompt, skills, MCP server definition)
onto whatever files that runtime reads at startup -- ``CLAUDE.md`` +
``.claude/skills/`` + ``.mcp.json`` here, ``AGENTS.md`` + ``config.toml`` for
Codex. Nothing about supervision, dedup, or session durability belongs here,
which is what keeps a second runtime to one new file.

A containerised worker gets its team contract injected by the runtime image. A
remote member has no container, so without a projector the agent joins the team
knowing nothing about it -- that was the observed failure: asked which team it
belonged to, a live remote member truthfully answered that nothing in its
context mentioned one.
"""

from .claude_code import ClaudeCodeProjector

__all__ = ["ClaudeCodeProjector"]
