"""Hosted-native construction (spec ``agentteams-native-execution-mode-spec-20260902``).

One RepoMesh task generation becomes one copaw-native task directory under the
team's shared storage; the copaw worker builds in its own container, the Team
Leader reviews the candidate in its own room, and an independent verifier
re-runs the frozen tests. This package holds the application service around
one such attempt (``round``), the observer that turns shared-directory changes
into idempotent events (``observer``), the observer's auto-approval branch for
the helper command lines (``approval``), the attempt store and the shared
directory readers. It sits beside ``integrations.runner``: cross-module
application code that composes module contracts, never a module of its own.
"""
