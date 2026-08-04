"""Runtime-neutral bridge core for TeamHarness remote members.

Nothing in this package is Claude Code specific. It lives under
``remote/claude-code/`` only because that is the first runtime to use it; when
``remote/codex-cli/`` lands, this package is what both share, and it should be
promoted to a location both can import rather than copied.
"""
