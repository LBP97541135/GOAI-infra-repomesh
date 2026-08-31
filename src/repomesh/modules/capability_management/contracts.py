from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

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
    # Registry-resolved SemVer; None until the registry assembler fills it in.
    version: str | None = None


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


class SkillVersionStatus(StrEnum):
    DRAFT = "draft"
    EVALUATING = "evaluating"
    CANARY = "canary"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"


#: Forward transitions through the pipeline. ``rolled_back`` is terminal for
#: that version number: a re-release bumps the version instead of reusing it.
_ALLOWED_SKILL_TRANSITIONS: dict[SkillVersionStatus, frozenset[SkillVersionStatus]] = {
    SkillVersionStatus.DRAFT: frozenset({SkillVersionStatus.EVALUATING}),
    SkillVersionStatus.EVALUATING: frozenset({SkillVersionStatus.CANARY}),
    SkillVersionStatus.CANARY: frozenset(
        {SkillVersionStatus.PROMOTED, SkillVersionStatus.ROLLED_BACK}
    ),
    SkillVersionStatus.PROMOTED: frozenset({SkillVersionStatus.ROLLED_BACK}),
    SkillVersionStatus.ROLLED_BACK: frozenset(),
}


class SkillLifecycleRefused(ValueError):
    """A lifecycle transition or gate check the domain refuses, with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def assert_skill_transition(current: SkillVersionStatus, target: SkillVersionStatus) -> None:
    allowed = _ALLOWED_SKILL_TRANSITIONS[current]
    if target not in allowed:
        raise SkillLifecycleRefused(
            "illegal_skill_transition",
            f"skill version in state {current.value} cannot transition to {target.value}",
        )


@dataclass(frozen=True, slots=True)
class SkillVersionView:
    skill_id: str
    version: str
    status: SkillVersionStatus
    content_hash: str
    local_path: str
    created_by: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SkillEvaluationInput:
    version_id: UUID
    scenario: str
    negative_case: str
    outcome: bool
    evidence: str
    evaluated_by: str


@dataclass(frozen=True, slots=True)
class SkillVersionSnapshot:
    """The Skill Set snapshot one organization runs against, in Python."""

    id: UUID
    organization_id: UUID | None
    versions: tuple[str, ...]
    created_at: datetime

