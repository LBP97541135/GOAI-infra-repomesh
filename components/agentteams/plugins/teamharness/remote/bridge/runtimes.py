#!/usr/bin/env python3
"""Registry mapping a runtime name to its two specialized leaves.

This module exists so that adding a runtime does not edit ``supervisor.py``.
The supervision logic must not learn runtime names: the moment it does,
"which CLI am I driving" becomes a condition inside deadline handling, dedup,
and session bookkeeping, and the per-runtime rewrite that sank PR #828 starts
over from the inside.

Adding a runtime touches exactly three places:

1. ``drivers/{runtime}.py``   -- how to execute one turn
2. ``projectors/{runtime}.py`` -- how to project assets into it
3. one entry in ``_RUNTIMES`` below

The design note in the top-level docs claims the first two. The third is a
registration line, not a design seam -- but it is honest to name it rather
than to claim a number that is off by one. If a fourth place is ever needed,
that is the signal the abstraction has drifted.

Imports are deferred into the factory functions on purpose: a laptop with only
one CLI installed should not pay the import cost -- or the failure -- of the
other one's module just to start the bridge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .protocol import AssetContext, AssetProjector, RuntimeDriver

DEFAULT_RUNTIME = "claude-code"


@dataclass(frozen=True)
class RuntimeBinding:
    """How to construct one runtime's leaves.

    ``driver_factory`` takes the operator's verbatim extra argv because the
    supervisor builds a fresh driver per concurrent task, plus the
    ``AssetContext`` -- some runtimes deliver part of their configuration as
    invocation arguments rather than as projected files, and that decision
    belongs to the runtime leaf, not to the supervisor. ``projector_factory``
    takes nothing because projection is a one-shot, stateless write.
    """

    name: str
    driver_factory: Callable[[tuple[str, ...], AssetContext], RuntimeDriver]
    projector_factory: Callable[[], AssetProjector]
    # Shown when the runtime is unusable, so the operator is told how to fix it
    # rather than just that something is missing.
    sign_in_hint: str


def _claude_code() -> RuntimeBinding:
    def driver(extra_args: tuple[str, ...], ctx: AssetContext) -> RuntimeDriver:
        from .drivers.claude_code import ClaudeCodeDriver

        # Claude Code reads its MCP config from the projected ``.mcp.json``,
        # so the context carries nothing the driver needs.
        del ctx
        return ClaudeCodeDriver(extra_args=extra_args)

    def projector() -> AssetProjector:
        from .projectors.claude_code import ClaudeCodeProjector

        return ClaudeCodeProjector()

    return RuntimeBinding(
        name="claude-code",
        driver_factory=driver,
        projector_factory=projector,
        sign_in_hint="run `claude` once and sign in",
    )


def _codex_cli() -> RuntimeBinding:
    def driver(extra_args: tuple[str, ...], ctx: AssetContext) -> RuntimeDriver:
        from .drivers.codex_cli import CodexCliDriver, mcp_config_args

        # Codex has no project-level MCP config -- servers live globally in
        # ``~/.codex/config.toml``. Rather than mutate the operator's global
        # machine config, the server is declared per invocation with ``-c``
        # overrides. Nothing is written, so nothing needs uninstalling.
        return CodexCliDriver(extra_args=(*mcp_config_args(ctx), *extra_args))

    def projector() -> AssetProjector:
        from .projectors.codex_cli import CodexCliProjector

        return CodexCliProjector()

    return RuntimeBinding(
        name="codex-cli",
        driver_factory=driver,
        projector_factory=projector,
        sign_in_hint="run `codex login` once",
    )


_RUNTIMES: dict[str, Callable[[], RuntimeBinding]] = {
    "claude-code": _claude_code,
    "codex-cli": _codex_cli,
}


def runtime_names() -> tuple[str, ...]:
    """Registered names, for argparse choices and error messages."""
    return tuple(sorted(_RUNTIMES))


def load_runtime(name: str) -> RuntimeBinding:
    """Resolve a runtime name to its binding.

    Raises ``ValueError`` naming the registered alternatives -- an operator who
    typed ``codex`` instead of ``codex-cli`` should be told what to type, not
    handed a KeyError.
    """
    key = (name or "").strip()
    build = _RUNTIMES.get(key)
    if build is None:
        raise ValueError(
            f"unknown runtime {name!r}; registered runtimes: {', '.join(runtime_names())}"
        )
    return build()
