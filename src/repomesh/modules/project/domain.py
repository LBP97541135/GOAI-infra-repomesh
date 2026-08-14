from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID

from repomesh.modules.project.contracts import (
    CheckpointDecisionKind,
    CodeAccessLevel,
    HumanControlAction,
    HumanProjectGrantView,
    HumanProjectRole,
    HumanReviewRequestView,
    HumanReviewStatus,
    ProjectAgentTopologyView,
    ProjectCheckpoint,
    ProjectCheckpointDecisionView,
    ProjectExecutionMode,
    ProjectOperationalStatus,
    ProjectTeamRuntimeStatus,
    RepositoryTeamView,
)
from repomesh.shared.domain import new_id


class ProjectTopologyError(RuntimeError):
    pass


class ProjectTopologyConflict(ProjectTopologyError):
    pass


class ProjectTopologyViolation(ProjectTopologyError):
    pass


def repository_agentteams_team_name(repository_id: UUID) -> str:
    """Stable AgentTeams Team binding shared by every project using a repository.

    Delegates to :meth:`RepositoryTeam.canonical_agentteams_team_name` so the
    two lines of development that independently keyed the Team on the
    repository (A-8 on this branch, platform onboarding on main) mint one
    spelling. The ``rm-team-`` template is the one existing rooms were made
    under; the reconcile adopts a repository's real Team either way.
    """

    return RepositoryTeam.canonical_agentteams_team_name(repository_id)


@dataclass(frozen=True, slots=True)
class HumanProjectGrant:
    human_principal_id: UUID
    role: HumanProjectRole
    code_access: CodeAccessLevel
    control_actions: frozenset[HumanControlAction]
    repository_id: UUID | None = None
    path_patterns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.role is HumanProjectRole.REPOSITORY_SUPERVISOR and self.repository_id is None:
            raise ProjectTopologyViolation("repository supervisor requires repository scope")
        if self.role is not HumanProjectRole.REPOSITORY_SUPERVISOR and self.repository_id:
            raise ProjectTopologyViolation("only repository supervisor may have repository scope")
        if self.path_patterns and self.repository_id is None:
            raise ProjectTopologyViolation("path scope requires repository scope")
        if not self.control_actions:
            raise ProjectTopologyViolation("human grant requires control actions")

    def to_view(self) -> HumanProjectGrantView:
        return HumanProjectGrantView(
            human_principal_id=self.human_principal_id,
            role=self.role,
            code_access=self.code_access,
            control_actions=self.control_actions,
            repository_id=self.repository_id,
            path_patterns=self.path_patterns,
        )


@dataclass(frozen=True, slots=True)
class ProjectCheckpointDecision:
    review_request_id: UUID
    project_id: UUID
    checkpoint: ProjectCheckpoint
    human_principal_id: UUID
    decision: CheckpointDecisionKind
    reason: str
    evidence_version: str
    repository_id: UUID | None = None
    id: UUID = field(default_factory=new_id)
    decided_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ProjectTopologyViolation("checkpoint decision reason is required")
        if not self.evidence_version.strip():
            raise ProjectTopologyViolation("checkpoint evidence version is required")

    def to_view(self) -> ProjectCheckpointDecisionView:
        return ProjectCheckpointDecisionView(
            id=self.id,
            review_request_id=self.review_request_id,
            project_id=self.project_id,
            checkpoint=self.checkpoint,
            human_principal_id=self.human_principal_id,
            decision=self.decision,
            reason=self.reason,
            repository_id=self.repository_id,
            evidence_version=self.evidence_version,
            decided_at=self.decided_at,
        )


@dataclass(frozen=True, slots=True)
class HumanReviewRequest:
    project_id: UUID
    checkpoint: ProjectCheckpoint
    evidence_version: str
    title: str
    summary: str
    repository_id: UUID | None = None
    requested_by_agent_id: UUID | None = None
    status: HumanReviewStatus = HumanReviewStatus.PENDING
    resolved_by_human_id: UUID | None = None
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.evidence_version.strip():
            raise ProjectTopologyViolation("review evidence version is required")
        if not self.title.strip():
            raise ProjectTopologyViolation("review title is required")

    def to_view(self) -> HumanReviewRequestView:
        return HumanReviewRequestView(
            id=self.id,
            project_id=self.project_id,
            checkpoint=self.checkpoint,
            evidence_version=self.evidence_version,
            title=self.title,
            summary=self.summary,
            status=self.status,
            repository_id=self.repository_id,
            requested_by_agent_id=self.requested_by_agent_id,
            resolved_by_human_id=self.resolved_by_human_id,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


@dataclass(frozen=True, slots=True)
class RepositoryTeam:
    project_id: UUID
    repository_id: UUID
    leader_agent_id: UUID
    worker_agent_ids: tuple[UUID, ...]
    id: UUID = field(default_factory=new_id)
    agentteams_team_name: str | None = None
    runtime_status: ProjectTeamRuntimeStatus = ProjectTeamRuntimeStatus.PENDING
    room_id: str | None = None
    leader_room_id: str | None = None

    def __post_init__(self) -> None:
        if not self.worker_agent_ids:
            raise ProjectTopologyViolation("a repository team requires at least one worker")
        if len(set(self.worker_agent_ids)) != len(self.worker_agent_ids):
            raise ProjectTopologyViolation("repository team workers must be unique")
        if self.leader_agent_id in self.worker_agent_ids:
            raise ProjectTopologyViolation("repository leader cannot also be a team worker")
        if self.agentteams_team_name is None:
            object.__setattr__(
                self,
                "agentteams_team_name",
                self.canonical_agentteams_team_name(self.repository_id),
            )

    @staticmethod
    def canonical_agentteams_team_name(repository_id: UUID) -> str:
        """The AgentTeams Team a repository's agents belong to.

        Keyed on the *repository*, not on this row's id — the correction A-8
        is. An AgentTeams Team owns its members exclusively, and a repository's
        leader and workers are directory singletons shared by every project
        that touches the repository, so there can only ever be one Team for
        them. Minting a name per topology row asked the controller for a second
        Team over the same principals, which it refused with
        ``400 ... is already a member of Team ...`` — a refusal no retry could
        clear, because nothing about it was transient.

        That name only ever survived because the script era never put two
        projects on one repository. Sharing the Team (and therefore its two
        Matrix rooms) across those projects is the architecture, not a
        concession: a repository's room is where that repository's agents talk,
        whichever issue they are talking about.
        """

        return f"rm-team-{repository_id.hex}"

    def with_runtime(
        self,
        *,
        status: ProjectTeamRuntimeStatus,
        room_id: str | None,
        leader_room_id: str | None,
        agentteams_team_name: str | None = None,
    ) -> "RepositoryTeam":
        """Write back what the controller actually gave this team.

        ``agentteams_team_name`` joins the room ids because the reconcile may
        have *adopted* a Team created under some other name (a row minted
        before A-8, most of them). The adopted name has to land on the row, or
        the next projection asks the same question again and the row keeps
        pointing at a Team that does not exist.
        """

        return replace(
            self,
            runtime_status=status,
            room_id=room_id,
            leader_room_id=leader_room_id,
            agentteams_team_name=agentteams_team_name or self.agentteams_team_name,
        )

    def to_view(self) -> RepositoryTeamView:
        return RepositoryTeamView(
            id=self.id,
            project_id=self.project_id,
            repository_id=self.repository_id,
            leader_agent_id=self.leader_agent_id,
            worker_agent_ids=self.worker_agent_ids,
            agentteams_team_name=self.agentteams_team_name or "",
            runtime_status=self.runtime_status,
            room_id=self.room_id,
            leader_room_id=self.leader_room_id,
        )


@dataclass(frozen=True, slots=True)
class ProjectAgentTopology:
    organization_id: UUID
    project_id: UUID
    organization_leader_id: UUID
    repository_teams: tuple[RepositoryTeam, ...]
    execution_mode: ProjectExecutionMode = ProjectExecutionMode.AUTO
    required_checkpoints: frozenset[ProjectCheckpoint] = frozenset()
    human_grants: tuple[HumanProjectGrant, ...] = ()
    operational_status: ProjectOperationalStatus = ProjectOperationalStatus.ACTIVE
    id: UUID = field(default_factory=new_id)

    def __post_init__(self) -> None:
        if not self.repository_teams:
            raise ProjectTopologyViolation("a project topology requires repository teams")
        repository_ids = [team.repository_id for team in self.repository_teams]
        if len(set(repository_ids)) != len(repository_ids):
            raise ProjectTopologyViolation("a project can have only one team per repository")
        agent_ids = [
            agent_id
            for team in self.repository_teams
            for agent_id in (team.leader_agent_id, *team.worker_agent_ids)
        ]
        if len(set(agent_ids)) != len(agent_ids):
            raise ProjectTopologyViolation("an agent cannot join multiple repository teams")
        if any(team.project_id != self.project_id for team in self.repository_teams):
            raise ProjectTopologyViolation("repository team project must match topology project")
        if self.execution_mode is ProjectExecutionMode.AUTO and self.required_checkpoints:
            raise ProjectTopologyViolation("automatic projects cannot require human checkpoints")
        if self.execution_mode is not ProjectExecutionMode.AUTO and not self.human_grants:
            raise ProjectTopologyViolation("human-controlled projects require a human grant")
        if self.execution_mode is not ProjectExecutionMode.AUTO and not self.required_checkpoints:
            raise ProjectTopologyViolation("human-controlled projects require checkpoints")
        if (
            self.execution_mode is ProjectExecutionMode.MANUAL_CONTROLLED
            and self.required_checkpoints != frozenset(ProjectCheckpoint)
        ):
            raise ProjectTopologyViolation(
                "manual-controlled projects require every human checkpoint"
            )
        grant_scopes = [
            (grant.human_principal_id, grant.repository_id) for grant in self.human_grants
        ]
        if len(set(grant_scopes)) != len(grant_scopes):
            raise ProjectTopologyViolation("duplicate human grant scope")
        repository_ids = set(repository_ids)
        if any(
            grant.repository_id is not None and grant.repository_id not in repository_ids
            for grant in self.human_grants
        ):
            raise ProjectTopologyViolation("human grant references an unknown project repository")

    def to_view(self) -> ProjectAgentTopologyView:
        return ProjectAgentTopologyView(
            id=self.id,
            organization_id=self.organization_id,
            project_id=self.project_id,
            organization_leader_id=self.organization_leader_id,
            repository_teams=tuple(team.to_view() for team in self.repository_teams),
            execution_mode=self.execution_mode,
            required_checkpoints=self.required_checkpoints,
            human_grants=tuple(grant.to_view() for grant in self.human_grants),
            operational_status=self.operational_status,
        )

    def with_operational_status(
        self, status: ProjectOperationalStatus
    ) -> "ProjectAgentTopology":
        if (
            self.operational_status is ProjectOperationalStatus.CANCELLED
            and status is not ProjectOperationalStatus.CANCELLED
        ):
            raise ProjectTopologyViolation("cancelled project cannot be resumed")
        return replace(self, operational_status=status)
