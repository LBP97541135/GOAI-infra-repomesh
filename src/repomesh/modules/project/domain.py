from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID

from repomesh.modules.agent_runtime.contracts import AGENTTEAMS_NAME_PREFIX
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
    TeamDecompositionMode,
)
from repomesh.modules.project.errors import (
    ProjectTopologyConflict,
    ProjectTopologyError,
    ProjectTopologyViolation,
)
from repomesh.modules.project.supervision_policy import assert_supervision_policy
from repomesh.shared.domain import new_id

__all__ = [
    "HumanProjectGrant",
    "HumanReviewRequest",
    "ProjectAgentTopology",
    "ProjectCheckpointDecision",
    "ProjectTopologyConflict",
    "ProjectTopologyError",
    "ProjectTopologyViolation",
    "RepositoryTeam",
    "TopologyPolicyDraft",
    "repository_agentteams_team_name",
]


def repository_agentteams_team_name(repository_id: UUID) -> str:
    """Stable AgentTeams Team binding shared by every project using a repository.

    Delegates to :meth:`RepositoryTeam.canonical_agentteams_team_name` so the
    two lines of development that independently keyed the Team on the
    repository (A-8 on this branch, platform onboarding on main) mint one
    spelling. That spelling changed once, from ``rm-team-`` to
    ``repomesh-team-`` (see :data:`AGENTTEAMS_NAME_PREFIX`); rooms made under
    the old template are not renamed, and the reconcile adopts a repository's
    real Team either way.
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
    #: Who decomposes this team's repository tasks (adjudication D-2).
    #:
    #: Defaulted to ``SERVER`` rather than required, which is the whole of D-2
    #: expressed in one line: every construction site that existed before this
    #: field keeps today's behavior, and ``LEADER`` is something the adoption
    #: path below has to go out of its way to say.
    decomposition_mode: TeamDecompositionMode = TeamDecompositionMode.SERVER

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

        return f"{AGENTTEAMS_NAME_PREFIX}-team-{repository_id.hex}"

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

    def with_adopted_leader(self, *, external: bool) -> "RepositoryTeam":
        """Raise this team into leader mode when its leader runs outside the cluster.

        A one-way latch, and that is the point rather than an oversight. Once a
        team is in ``LEADER`` mode a batch parks for its Repository Leader
        instead of being decomposed server-side, so a *silent* fall back to
        ``SERVER`` would not restore old behavior — it would decompose and
        dispatch work the leader was in the middle of planning, from a plan
        nobody submitted. The inputs that could cause it are exactly the ones
        that go wrong transiently: a controller that did not answer this pass,
        or a worker document that came back without ``containerManaged``.

        So ``external=False`` means "this pass did not observe an external
        leader", never "this team is not a leader team". Turning a leader team
        back into a server team is a decision with consequences for parked
        work, and it needs its own use case and its own operator intent — not a
        reconcile that happened to run during an outage.

        Idempotent by construction: adopting an already-adopted team returns
        the same object, which is what makes a re-run of materialize a no-op
        here rather than a rewrite.
        """

        if not external or self.decomposition_mode is TeamDecompositionMode.LEADER:
            return self
        return replace(self, decomposition_mode=TeamDecompositionMode.LEADER)

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
            decomposition_mode=self.decomposition_mode,
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
        # Everything above needs the repository teams; everything in here does
        # not, which is exactly why it is shared with the policy draft written
        # before any team exists. See ``supervision_policy`` for the seam.
        assert_supervision_policy(
            execution_mode=self.execution_mode,
            required_checkpoints=self.required_checkpoints,
            human_grants=self.human_grants,
        )
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


@dataclass(frozen=True, slots=True)
class TopologyPolicyDraft:
    """What an admin decided about supervision, before there is a topology.

    Same three fields ``ProjectAgentTopology`` carries, and validated by the
    same function, so a draft that constructs is a draft that will not be
    rejected on those grounds later. The one rule it cannot run is the one that
    needs repository teams — a grant may name a repository that the plan later
    drops — so materialization stays able to refuse, and must say so rather
    than silently discard the grant.

    ``project_id`` is the identity: a requirement has one supervision intent,
    and changing your mind overwrites it. What is worth keeping a history of is
    the *decisions* made at checkpoints, which ``checkpoint_decisions`` already
    records; the deliberation before them is not evidence of anything.
    """

    project_id: UUID
    created_by: UUID
    execution_mode: ProjectExecutionMode = ProjectExecutionMode.AUTO
    required_checkpoints: frozenset[ProjectCheckpoint] = frozenset()
    human_grants: tuple[HumanProjectGrant, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        assert_supervision_policy(
            execution_mode=self.execution_mode,
            required_checkpoints=self.required_checkpoints,
            human_grants=self.human_grants,
        )
