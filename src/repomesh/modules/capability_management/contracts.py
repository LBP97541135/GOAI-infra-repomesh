from dataclasses import dataclass
from enum import StrEnum

from repomesh.modules.agent_directory.contracts import AgentRole


class CapabilityKind(StrEnum):
    SKILL = "skill"
    MCP = "mcp"


class CapabilityAccess(StrEnum):
    READ_ONLY = "read_only"
    CONTROLLED_WRITE = "controlled_write"
    EXECUTION = "execution"


@dataclass(frozen=True, slots=True)
class CapabilitySource:
    repository: str
    path: str | None = None
    maintainer: str = ""


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    id: str
    kind: CapabilityKind
    title: str
    source: CapabilitySource
    access: CapabilityAccess
    allowed_roles: frozenset[AgentRole]
    allowed_operations: tuple[str, ...]
    denied_operations: tuple[str, ...] = ()
    local_path: str | None = None
    conditional_on: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class AgentCapabilityBundle:
    role: AgentRole
    skills: tuple[CapabilityDefinition, ...]
    mcp_servers: tuple[CapabilityDefinition, ...]

    @property
    def tool_allowlist(self) -> tuple[str, ...]:
        return tuple(
            operation
            for server in self.mcp_servers
            for operation in server.allowed_operations
        )

