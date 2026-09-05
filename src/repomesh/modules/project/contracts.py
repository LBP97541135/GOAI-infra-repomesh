from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from repomesh.modules.agent_runtime.contracts import WorkerRuntime


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


class TeamDecompositionMode(StrEnum):
    """Who decomposes a repository-level task into worker tasks for this team.

    ``SERVER`` is today's behavior: the platform decomposes and dispatches in the
    same breath as leader assignment. ``LEADER`` parks the batch after the leader
    task is dispatched and waits for the external Repository Leader to submit a
    plan over the leader-actions surface (``contracts/leader-actions/v1``).

    Frozen as part of the wave-0 contract baseline (2026-08-28). Producer:
    project topology (persisted by PR 5.5B, set to ``LEADER`` only by the formal
    materialize/adoption use case, adjudication D-2). Consumer:
    task_orchestration's batch assignment (PR 7), which must read it through
    this module's contracts, never the project schema.
    """

    SERVER = "server"
    LEADER = "leader"


class ConstructionMode(StrEnum):
    """Who builds a repository team's code, and where (hosted-native spec D-1, D-17).

    ``HOSTED_NATIVE`` is the product's default: the team's copaw workers build
    inside their own controller-managed containers, and nothing else — no
    coding CLI in the container, no runner dispatch (D-2). ``LOCAL_CLI`` is the
    Bridge line: every member's body is a process an operator runs outside the
    cluster, so the controller keeps the Matrix identity and the rooms but no
    container (ADR 0004).

    The mode is a persisted fact on the team row, chosen when the team is
    staffed. Everything the two lines used to configure separately — whether
    the controller containerizes a worker, which controller runtime it asks
    for, and whether the team may be raised into ``LEADER`` decomposition — is
    *derived* from it through :func:`derive_runtime`, so there is one value to
    set instead of three that had to agree (D-17: the onboarding request and
    the settings each defaulted a runtime, and they defaulted different ones).
    """

    HOSTED_NATIVE = "hosted_native"
    LOCAL_CLI = "local_cli"


@dataclass(frozen=True, slots=True)
class DerivedRuntime:
    """What a construction mode implies for the controller-side projection.

    ``container_managed`` is the field ``WorkerProjection`` carries under that
    name; ``worker_runtime`` is the controller runtime the projection asks for;
    ``decomposition_default`` is where a fresh team of this mode starts before
    adoption has anything to say.
    """

    container_managed: bool
    worker_runtime: WorkerRuntime
    decomposition_default: TeamDecompositionMode


_DERIVED: dict[ConstructionMode, DerivedRuntime] = {
    # Both modes ask the controller for copaw: hosted-native because that is
    # the worker that builds (D-2), local CLI because the resource still needs
    # a Matrix identity and a room and copaw is the runtime this deployment
    # pairs an image with (settings ``agentteams_worker_runtime``). The one
    # field that differs is the body.
    ConstructionMode.HOSTED_NATIVE: DerivedRuntime(
        container_managed=True,
        worker_runtime=WorkerRuntime.COPAW,
        decomposition_default=TeamDecompositionMode.SERVER,
    ),
    ConstructionMode.LOCAL_CLI: DerivedRuntime(
        container_managed=False,
        worker_runtime=WorkerRuntime.COPAW,
        decomposition_default=TeamDecompositionMode.SERVER,
    ),
}


def derive_runtime(mode: ConstructionMode) -> DerivedRuntime:
    """The projection facts a construction mode settles (spec §4.2 M7).

    Total over the enum by construction: a third mode is a code change that
    has to add its row here, never a value that quietly projects as one of
    the existing two.
    """

    return _DERIVED[mode]


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
    decomposition_mode: TeamDecompositionMode = TeamDecompositionMode.SERVER
    construction_mode: ConstructionMode = ConstructionMode.HOSTED_NATIVE


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


class TeamDecompositionModeReader(Protocol):
    """The one question task_orchestration's assignment path asks per batch item.

    Deliberately narrower than ``ProjectTopologyReader``: the caller sits inside
    a dispatch loop that already knows project and repository, and handing it
    the whole topology would invite it to consume facts this contract does not
    freeze. A team that does not exist resolves to ``SERVER`` — absence of an
    adopted external leader is exactly what ``SERVER`` means, so the reader has
    no error channel to misuse.
    """

    async def decomposition_mode(
        self, project_id: UUID, repository_id: UUID
    ) -> TeamDecompositionMode: ...


class TeamConstructionModeReader(Protocol):
    """The one question the delivery fork asks per assignment (spec §5.3.2).

    Same shape and same reasoning as ``TeamDecompositionModeReader``: the
    caller — ``_deliver_assignment`` deciding between a hosted-native round and
    the publish-and-send path, the readiness gate, the shared-directory
    observer — already knows project and repository and must not be handed the
    whole topology. A team that does not exist resolves to ``HOSTED_NATIVE``,
    the product default, so the protocol has no error channel to misuse; a
    missing row is not a reason to start a Bridge dispatch for nobody.
    """

    async def construction_mode(
        self, project_id: UUID, repository_id: UUID
    ) -> ConstructionMode: ...


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
