"""Replan request-mode resolution tests (PR-4).

``_resolve_replan_mode`` maps the API request's ``mode`` (auto/preview/
commit) to the effective execution mode, using the server setting
``REPOMESH_REPLAN_AUTO_COMMIT`` only for ``auto``.
"""

from __future__ import annotations

from repomesh.modules.repository_intelligence.api.router import (
    _resolve_replan_mode,
)


def test_auto_follows_server_setting() -> None:
    """``auto`` preserves pre-PR-4 behaviour when auto-commit is enabled and
    switches to a zero-side-effect preview otherwise."""
    assert _resolve_replan_mode("auto", auto_commit=True) == "commit"
    assert _resolve_replan_mode("auto", auto_commit=False) == "preview"


def test_explicit_modes_override_the_setting() -> None:
    """Callers can force either mode regardless of the server default."""
    assert _resolve_replan_mode("preview", auto_commit=True) == "preview"
    assert _resolve_replan_mode("commit", auto_commit=False) == "commit"
