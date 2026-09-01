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


#: The team capability profile every repository team assembles under unless an
#: operator has named another one. Published here because other modules store
#: the profile name (the repository catalog) and must validate it against the
#: producer's list rather than their own copy of it.
DEFAULT_TEAM_PROFILE = "default"

#: Teams whose governance repository is the organization's test-asset repository.
#: Assembles `cross-repo-test` onto the team leader and `integration-run` onto
#: its Workers, on top of their role presets — never instead of them.
CROSS_REPO_TEST_TEAM_PROFILE = "cross-repo-test-team"

#: Every profile name this module can assemble. The catalog's update use case
#: refuses names outside this set, so an unknown profile cannot sit in the
#: database waiting to be discovered by a dispatch.
TEAM_CAPABILITY_PROFILES = frozenset(
    {DEFAULT_TEAM_PROFILE, CROSS_REPO_TEST_TEAM_PROFILE}
)


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

