from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class ProjectTeamRuntimeStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class ProjectExecutionMode(StrEnum):
    AUTO = "auto"
    SUPERVISED = "supervised"
    MANUAL_CONTROLLED = "manual_controlled"


class ProjectOperationalStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class HumanProjectRole(StrEnum):
    ORGANIZATION_SUPERVISOR = "organization_supervisor"
    PROJECT_SUPERVISOR = "project_supervisor"
    REPOSITORY_SUPERVISOR = "repository_supervisor"


class CodeAccessLevel(StrEnum):
    NONE = "none"
    READ = "read"
    WRITE = "write"


class HumanControlAction(StrEnum):
    VIEW_DECISIONS = "view_decisions"
    APPROVE_CHECKPOINT = "approve_checkpoint"
    REQUEST_CHANGES = "request_changes"
    PAUSE_PROJECT = "pause_project"
    RESUME_PROJECT = "resume_project"
    CANCEL_PROJECT = "cancel_project"
    EDIT_SPECIFICATION = "edit_specification"


class ProjectCheckpoint(StrEnum):
    REPOSITORY_SCOPE = "repository_scope"
    SPECIFICATION = "specification"
    EXECUTION = "execution"
    VALIDATION = "validation"
    DELIVERY = "delivery"
    EXCEPTION_ESCALATION = "exception_escalation"


class CheckpointDecisionKind(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"


class HumanReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"


@dataclass(frozen=True, slots=True)
class HumanReviewRequestView:
    id: UUID
    project_id: UUID
    checkpoint: ProjectCheckpoint
    evidence_version: str
    title: str
    summary: str
    status: HumanReviewStatus
    repository_id: UUID | None
    requested_by_agent_id: UUID | None
    resolved_by_human_id: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectCheckpointDecisionView:
    id: UUID
    review_request_id: UUID
    project_id: UUID
    checkpoint: ProjectCheckpoint
    human_principal_id: UUID
    decision: CheckpointDecisionKind
    reason: str
    repository_id: UUID | None
    evidence_version: str
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class HumanProjectGrantView:
    human_principal_id: UUID
    role: HumanProjectRole
    code_access: CodeAccessLevel
    control_actions: frozenset[HumanControlAction]
    repository_id: UUID | None = None
    path_patterns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RepositoryTeamView:
    id: UUID
    project_id: UUID
    repository_id: UUID
    leader_agent_id: UUID
    worker_agent_ids: tuple[UUID, ...]
    agentteams_team_name: str
    runtime_status: ProjectTeamRuntimeStatus
    room_id: str | None
    leader_room_id: str | None


@dataclass(frozen=True, slots=True)
class ProjectAgentTopologyView:
    id: UUID
    organization_id: UUID
    project_id: UUID
    organization_leader_id: UUID
    repository_teams: tuple[RepositoryTeamView, ...]
    execution_mode: ProjectExecutionMode = ProjectExecutionMode.AUTO
    required_checkpoints: frozenset[ProjectCheckpoint] = frozenset()
    human_grants: tuple[HumanProjectGrantView, ...] = ()
    operational_status: ProjectOperationalStatus = ProjectOperationalStatus.ACTIVE


class ProjectTopologyReader(Protocol):
    async def get_view(self, project_id: UUID) -> ProjectAgentTopologyView | None: ...


class ProjectTopologyProvisioner(Protocol):
    """Give a project the topology its execution plane requires, once.

    Published as a contract for one caller: a console round reaches
    materialization with a plan and no topology, because nothing on the console
    path creates one — a workspace gets an organization leader and repositories
    get catalog rows, and that is all. Somebody has to bridge the two, and it
    must not be a browser posting a team roster.

    ``ensure`` is idempotent by existence, not by key: a project that already
    has a topology gets that topology back untouched, whatever repositories are
    named. Rebuilding one under a running project would reassign work that is
    already in flight.
    """

    async def ensure(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        organization_leader_id: UUID,
        repository_ids: tuple[UUID, ...],
        idempotency_key: str,
    ) -> ProjectAgentTopologyView: ...


@dataclass(frozen=True, slots=True)
class CheckpointGateDecision:
    allowed: bool
    reason: str


class ProjectCheckpointGateway(Protocol):
    async def operational_gate(self, project_id: UUID) -> CheckpointGateDecision: ...

    async def evaluate(
        self,
        project_id: UUID,
        checkpoint: ProjectCheckpoint,
        evidence_version: str,
        *,
        repository_id: UUID | None = None,
        requested_by_agent_id: UUID | None = None,
        title: str | None = None,
        summary: str | None = None,
    ) -> CheckpointGateDecision: ...


class TopologyAwareCheckpointFallback:
    """Allow only active automatic projects when the real gateway is absent."""

    def __init__(self, topologies: ProjectTopologyReader) -> None:
        self._topologies = topologies

    async def operational_gate(self, project_id: UUID) -> CheckpointGateDecision:
        topology = await self._topologies.get_view(project_id)
        if topology is None:
            return CheckpointGateDecision(False, "project_topology_missing")
        if topology.operational_status is ProjectOperationalStatus.PAUSED:
            return CheckpointGateDecision(False, "project_paused")
        if topology.operational_status is ProjectOperationalStatus.CANCELLED:
            return CheckpointGateDecision(False, "project_cancelled")
        if topology.execution_mode is not ProjectExecutionMode.AUTO:
            return CheckpointGateDecision(False, "checkpoint_gateway_not_configured")
        return CheckpointGateDecision(True, "automatic_project")

    async def evaluate(
        self,
        project_id: UUID,
        checkpoint: ProjectCheckpoint,
        evidence_version: str,
        *,
        repository_id: UUID | None = None,
        requested_by_agent_id: UUID | None = None,
        title: str | None = None,
        summary: str | None = None,
    ) -> CheckpointGateDecision:
        del checkpoint, evidence_version, repository_id, requested_by_agent_id, title, summary
        return await self.operational_gate(project_id)
