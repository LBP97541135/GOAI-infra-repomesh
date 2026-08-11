"""Shared control-flow errors used across application modules."""


class WorkflowBlocked(RuntimeError):
    """A valid workflow is paused by policy or an external prerequisite."""
