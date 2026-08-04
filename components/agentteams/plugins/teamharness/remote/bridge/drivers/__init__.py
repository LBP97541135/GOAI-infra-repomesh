"""Concrete ``RuntimeDriver`` implementations for the TeamHarness bridge.

One module per headless protocol, each exporting a single driver class that
satisfies ``bridge.protocol.RuntimeDriver``. Drivers own process spawning and
frame translation only; supervision, deadlines, dedup, and session durability
stay in the runtime-neutral bridge core so that adding ``codex_cli.py`` next to
``claude_code.py`` costs one file instead of a fork.
"""
