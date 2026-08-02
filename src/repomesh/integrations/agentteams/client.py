"""Compatibility exports for the AgentTeams v1.2 control-plane client."""

from .control_plane import (
    AgentTeamsConflict,
    AgentTeamsControlPlaneClient,
    AgentTeamsError,
    AgentTeamsResponseError,
    AgentTeamsUnavailable,
    AgentTeamsVersion,
)

AgentTeamsClient = AgentTeamsControlPlaneClient

__all__ = [
    "AgentTeamsClient",
    "AgentTeamsConflict",
    "AgentTeamsControlPlaneClient",
    "AgentTeamsError",
    "AgentTeamsResponseError",
    "AgentTeamsUnavailable",
    "AgentTeamsVersion",
]
