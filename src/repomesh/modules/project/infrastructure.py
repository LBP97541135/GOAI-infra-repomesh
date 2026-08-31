from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    delete,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.modules.project.contracts import (
    CheckpointDecisionKind,
    CodeAccessLevel,
    HumanControlAction,
    HumanProjectRole,
    HumanReviewStatus,
    ProjectCheckpoint,
    ProjectExecutionMode,
    ProjectOperationalStatus,
    ProjectTeamRuntimeStatus,
    TeamDecompositionMode,
)
from repomesh.modules.project.domain import (
    HumanProjectGrant,
    HumanReviewRequest,
    ProjectAgentTopology,
    ProjectCheckpointDecision,
    ProjectTopologyConflict,
    RepositoryTeam,
    TopologyPolicyDraft,
)
from repomesh.modules.project.ports import ProjectTopologyStore
from repomesh.persistence import Database
from repomesh.persistence.base import Base

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


def grant_payloads(grants: Sequence[HumanProjectGrant]) -> list[dict[str, object]]:
    """The stored JSON shape of a project's human grants.

    Shared by ``agent_topologies`` and ``topology_policy_drafts`` on purpose,
    not copied: materialization moves a draft's ``human_grants`` column into
    the topology's ``human_grants`` column whole. Two independent spellings of
    this dict would have to agree forever for that to keep working, and the day
    they stopped agreeing the failure would land at materialization, far from
    whichever of the two was edited.
    """

    return [
        {
            "human_principal_id": str(grant.human_principal_id),
            "role": grant.role.value,
            "code_access": grant.code_access.value,
            "control_actions": sorted(item.value for item in grant.control_actions),
            "repository_id": (str(grant.repository_id) if grant.repository_id else None),
            "path_patterns": list(grant.path_patterns),
        }
        for grant in grants
    ]


def grants_from_payloads(
    payloads: Sequence[dict[str, object]],
) -> tuple[HumanProjectGrant, ...]:
    """Read back what :func:`grant_payloads` wrote, for either table."""

    return tuple(
        HumanProjectGrant(
            human_principal_id=UUID(str(item["human_principal_id"])),
            role=HumanProjectRole(str(item["role"])),
            code_access=CodeAccessLevel(str(item["code_access"])),
            control_actions=frozenset(
                HumanControlAction(str(value)) for value in item["control_actions"]
            ),
            repository_id=(
                UUID(str(item["repository_id"])) if item.get("repository_id") else None
            ),
            path_patterns=tuple(str(value) for value in item["path_patterns"]),
        )
        for item in payloads
    )


class ProjectAgentTopologyRecord(Base):
    __tablename__ = "agent_topologies"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_project_agent_topologies_project"),
        UniqueConstraint("idempotency_key", name="uq_project_agent_topologies_idempotency"),
        {"schema": "project"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    organization_leader_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_fingerprint: Mapped[str] = mapped_column(String(71))
    execution_mode: Mapped[str] = mapped_column(String(30), default="auto")
    operational_status: Mapped[str] = mapped_column(String(30), default="active")
    required_checkpoints: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list)
    human_grants: Mapped[list[dict[str, object]]] = mapped_column(
        JSON_DOCUMENT, default=list
    )


class TopologyPolicyDraftRecord(Base):
    __tablename__ = "topology_policy_drafts"
    __table_args__ = ({"schema": "project"},)

    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    execution_mode: Mapped[str] = mapped_column(String(30), default="auto")
    required_checkpoints: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list)
    human_grants: Mapped[list[dict[str, object]]] = mapped_column(
        JSON_DOCUMENT, default=list
    )
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    # Spelled out rather than left to the bare ``Mapped[datetime]`` used by the
    # two records below it: the migration makes these ``timestamptz`` and the
    # values are ``datetime.now(UTC)``, and a bare annotation maps to
    # ``TIMESTAMP WITHOUT TIME ZONE``, which asyncpg refuses to bind an aware
    # datetime to. Every other module in the repository spells it this way.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProjectRepositoryTeamRecord(Base):
    __tablename__ = "repository_agent_teams"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "repository_id", name="uq_project_repository_agent_team"
        ),
        # Scoped to the project since A-8 (§8.7.2, migration 20260812_0024).
        # Table-wide it said "no two topology rows may name the same AgentTeams
        # Team", which stopped being true the moment Teams became
        # repository-scoped: every project touching a repository now names the
        # *same* Team on purpose, and the old constraint forbade exactly that.
        # What survives is the half that is still true — within one project the
        # two repositories are two different Teams — and it is worth keeping,
        # because a project whose repositories collapsed onto one Team would
        # route both repositories' traffic into one room with nothing to
        # notice.
        UniqueConstraint(
            "project_id",
            "agentteams_team_name",
            name="uq_project_agentteams_team_name",
        ),
        {"schema": "project"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    topology_id: Mapped[UUID] = mapped_column(
        ForeignKey("project.agent_topologies.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    repository_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    leader_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    worker_agent_ids: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    agentteams_team_name: Mapped[str] = mapped_column(String(253))
    runtime_status: Mapped[str] = mapped_column(String(30), index=True)
    room_id: Mapped[str | None] = mapped_column(Text)
    leader_room_id: Mapped[str | None] = mapped_column(Text)
    decomposition_mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=TeamDecompositionMode.SERVER.value,
        default=TeamDecompositionMode.SERVER.value,
        index=True,
    )
    """Who decomposes this team's repository tasks (adjudication D-2, revision 0037).

    A plain string rather than a PostgreSQL enum, matching how
    ``runtime_status`` beside it and ``phase`` in ``leader_assignments`` are
    stored: the mode is read back through ``TeamDecompositionMode(...)``, and a
    third value arriving one day should be a code change rather than a
    migration that has to run before any row can hold it.

    ``server_default`` as well as ``default`` because rows written before this
    column existed have to mean something, and what they mean is ``server`` —
    no installation had an adopted external leader before 0037. The default is
    on the column rather than applied by a backfill for the same reason: there
    is nothing to compute per row.

    Indexed because ``PersistedTeamDecompositionModeReader`` is asked once per
    batch item on the dispatch path.
    """


class ProjectCheckpointDecisionRecord(Base):
    __tablename__ = "checkpoint_decisions"
    __table_args__ = (
        UniqueConstraint("review_request_id", name="uq_checkpoint_decision_review_request"),
        {"schema": "project"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    review_request_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    checkpoint: Mapped[str] = mapped_column(String(40), index=True)
    repository_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    human_principal_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    decision: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)
    evidence_version: Mapped[str] = mapped_column(String(200), index=True)
    # Same defect as ``HumanReviewRequestRecord``'s timestamps, one step further
    # down the same road: a decision can only be recorded against a review
    # request, so this insert was unreachable while that one was failing.
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HumanReviewRequestRecord(Base):
    __tablename__ = "human_review_requests"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "checkpoint",
            "repository_scope_key",
            "evidence_version",
            name="uq_project_human_review_evidence",
        ),
        {"schema": "project"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    checkpoint: Mapped[str] = mapped_column(String(40), index=True)
    repository_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    repository_scope_key: Mapped[str] = mapped_column(String(36), default="")
    evidence_version: Mapped[str] = mapped_column(String(200), index=True)
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), index=True)
    requested_by_agent_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    resolved_by_human_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    # The bare ``Mapped[datetime]`` these two used to be compiles to TIMESTAMP
    # WITHOUT TIME ZONE, while the migration created them as timestamptz and
    # ``HumanReviewRequest`` stamps ``datetime.now(UTC)``. asyncpg then refuses
    # the aware value outright, so **no review request could ever be written**.
    # It stayed invisible because every project so far ran `auto`: with no
    # checkpoints, this insert never happened. Enabling checkpoints — the entire
    # point of the supervision-policy work — is what makes it fire.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InMemoryProjectTopologyStore:
    def __init__(self) -> None:
        self._topologies: dict[UUID, ProjectAgentTopology] = {}
        self._idempotency: dict[str, tuple[UUID, str]] = {}

    async def add(
        self,
        topology: ProjectAgentTopology,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> None:
        if topology.project_id in self._topologies or idempotency_key in self._idempotency:
            raise ProjectTopologyConflict("project topology already exists")
        self._topologies[topology.project_id] = topology
        self._idempotency[idempotency_key] = (topology.project_id, request_fingerprint)

    async def get(self, project_id: UUID) -> ProjectAgentTopology | None:
        return self._topologies.get(project_id)

    async def get_view(self, project_id: UUID):
        topology = await self.get(project_id)
        return topology.to_view() if topology is not None else None

    async def list_views(self) -> tuple:
        return tuple(topology.to_view() for topology in self._topologies.values())

    async def find_view_by_room(self, room_id: str):
        for topology in self._topologies.values():
            if any(
                room_id in {team.room_id, team.leader_room_id}
                for team in topology.repository_teams
            ):
                return topology.to_view()
        return None

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[ProjectAgentTopology, str] | None:
        binding = self._idempotency.get(idempotency_key)
        if binding is None:
            return None
        project_id, fingerprint = binding
        return self._topologies[project_id], fingerprint

    async def save(self, topology: ProjectAgentTopology) -> None:
        if topology.project_id not in self._topologies:
            raise ProjectTopologyConflict("project topology does not exist")
        self._topologies[topology.project_id] = topology


class InMemoryProjectCheckpointDecisionStore:
    def __init__(self) -> None:
        self._decisions: list[ProjectCheckpointDecision] = []

    async def add(self, decision: ProjectCheckpointDecision) -> None:
        if any(
            item.review_request_id == decision.review_request_id
            for item in self._decisions
        ):
            raise ProjectTopologyConflict("review request was already decided")
        self._decisions.append(decision)

    async def latest(
        self,
        project_id: UUID,
        checkpoint: ProjectCheckpoint,
        repository_id: UUID | None,
    ) -> ProjectCheckpointDecision | None:
        matches = [
            item
            for item in self._decisions
            if item.project_id == project_id
            and item.checkpoint is checkpoint
            and item.repository_id == repository_id
        ]
        return max(matches, key=lambda item: item.decided_at) if matches else None


class InMemoryHumanReviewRequestStore:
    def __init__(self) -> None:
        self._requests: dict[
            tuple[UUID, ProjectCheckpoint, UUID | None, str], HumanReviewRequest
        ] = {}

    async def ensure(self, request: HumanReviewRequest) -> HumanReviewRequest:
        key = (
            request.project_id,
            request.checkpoint,
            request.repository_id,
            request.evidence_version,
        )
        return self._requests.setdefault(key, request)

    async def get_exact(
        self,
        project_id: UUID,
        checkpoint: ProjectCheckpoint,
        repository_id: UUID | None,
        evidence_version: str,
    ) -> HumanReviewRequest | None:
        return self._requests.get(
            (project_id, checkpoint, repository_id, evidence_version)
        )

    async def get_by_id(self, review_request_id: UUID) -> HumanReviewRequest | None:
        return next(
            (item for item in self._requests.values() if item.id == review_request_id),
            None,
        )

    async def list_for_human(
        self, human_principal_id: UUID, *, status: HumanReviewStatus | None = None
    ) -> tuple[HumanReviewRequest, ...]:
        del human_principal_id
        values = [
            item
            for item in self._requests.values()
            if status is None or item.status is status
        ]
        return tuple(sorted(values, key=lambda item: item.updated_at, reverse=True))

    async def list_all(
        self, *, status: HumanReviewStatus | None = None
    ) -> tuple[HumanReviewRequest, ...]:
        return await self.list_for_human(UUID(int=0), status=status)

    async def resolve_pending(
        self,
        review_request_id: UUID,
        decision: CheckpointDecisionKind,
        human_principal_id: UUID,
    ) -> bool:
        binding = next(
            ((key, item) for key, item in self._requests.items() if item.id == review_request_id),
            None,
        )
        if binding is None or binding[1].status is not HumanReviewStatus.PENDING:
            return False
        key, current = binding
        self._requests[key] = replace(
            current,
            status=HumanReviewStatus(decision.value),
            resolved_by_human_id=human_principal_id,
            updated_at=datetime.now(UTC),
        )
        return True


class PostgresProjectTopologyStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add(
        self,
        topology: ProjectAgentTopology,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> None:
        try:
            async with self._database.transaction() as session:
                session.add(
                    ProjectAgentTopologyRecord(
                        id=topology.id,
                        organization_id=topology.organization_id,
                        project_id=topology.project_id,
                        organization_leader_id=topology.organization_leader_id,
                        idempotency_key=idempotency_key,
                        request_fingerprint=request_fingerprint,
                        execution_mode=topology.execution_mode.value,
                        operational_status=topology.operational_status.value,
                        required_checkpoints=sorted(
                            item.value for item in topology.required_checkpoints
                        ),
                        human_grants=grant_payloads(topology.human_grants),
                    )
                )
                session.add_all(self._team_records(topology))
        except IntegrityError as error:
            raise ProjectTopologyConflict("project topology already exists") from error

    async def get(self, project_id: UUID) -> ProjectAgentTopology | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(ProjectAgentTopologyRecord).where(
                    ProjectAgentTopologyRecord.project_id == project_id
                )
            )
            if record is None:
                return None
            teams = (
                await session.scalars(
                    select(ProjectRepositoryTeamRecord)
                    .where(ProjectRepositoryTeamRecord.topology_id == record.id)
                    .order_by(ProjectRepositoryTeamRecord.repository_id)
                )
            ).all()
        return self._to_domain(record, teams)

    async def get_view(self, project_id: UUID):
        topology = await self.get(project_id)
        return topology.to_view() if topology is not None else None

    async def list_views(self) -> tuple:
        """Every topology, for the console's repository grid and team list."""

        async with self._database.transaction() as session:
            project_ids = (
                await session.scalars(
                    select(ProjectAgentTopologyRecord.project_id).order_by(
                        ProjectAgentTopologyRecord.project_id
                    )
                )
            ).all()
        views = []
        for project_id in project_ids:
            view = await self.get_view(project_id)
            if view is not None:
                views.append(view)
        return tuple(views)

    async def find_view_by_room(self, room_id: str):
        """The topology owning a team room or leader DM, or None.

        Added for the delivery read model's room endpoints: a room id is the
        only handle the console has there, and finding its owner by scanning
        would cost one query per issue.
        """

        async with self._database.transaction() as session:
            project_id = await session.scalar(
                select(ProjectRepositoryTeamRecord.project_id).where(
                    or_(
                        ProjectRepositoryTeamRecord.room_id == room_id,
                        ProjectRepositoryTeamRecord.leader_room_id == room_id,
                    )
                )
            )
        if project_id is None:
            return None
        return await self.get_view(project_id)

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[ProjectAgentTopology, str] | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(ProjectAgentTopologyRecord).where(
                    ProjectAgentTopologyRecord.idempotency_key == idempotency_key
                )
            )
            if record is None:
                return None
            teams = (
                await session.scalars(
                    select(ProjectRepositoryTeamRecord)
                    .where(ProjectRepositoryTeamRecord.topology_id == record.id)
                    .order_by(ProjectRepositoryTeamRecord.repository_id)
                )
            ).all()
        return self._to_domain(record, teams), record.request_fingerprint

    async def save(self, topology: ProjectAgentTopology) -> None:
        async with self._database.transaction() as session:
            record = await session.get(ProjectAgentTopologyRecord, topology.id)
            if record is None:
                raise ProjectTopologyConflict("project topology does not exist")
            record.operational_status = topology.operational_status.value
            await session.execute(
                delete(ProjectRepositoryTeamRecord).where(
                    ProjectRepositoryTeamRecord.topology_id == topology.id
                )
            )
            session.add_all(self._team_records(topology))

    @staticmethod
    def _team_records(topology: ProjectAgentTopology) -> Sequence[ProjectRepositoryTeamRecord]:
        return tuple(
            ProjectRepositoryTeamRecord(
                id=team.id,
                topology_id=topology.id,
                project_id=topology.project_id,
                repository_id=team.repository_id,
                leader_agent_id=team.leader_agent_id,
                worker_agent_ids=[str(worker_id) for worker_id in team.worker_agent_ids],
                agentteams_team_name=team.agentteams_team_name,
                runtime_status=team.runtime_status.value,
                room_id=team.room_id,
                leader_room_id=team.leader_room_id,
                decomposition_mode=team.decomposition_mode.value,
            )
            for team in topology.repository_teams
        )

    @staticmethod
    def _to_domain(
        record: ProjectAgentTopologyRecord,
        teams: Sequence[ProjectRepositoryTeamRecord],
    ) -> ProjectAgentTopology:
        return ProjectAgentTopology(
            id=record.id,
            organization_id=record.organization_id,
            project_id=record.project_id,
            organization_leader_id=record.organization_leader_id,
            execution_mode=ProjectExecutionMode(record.execution_mode),
            operational_status=ProjectOperationalStatus(record.operational_status),
            required_checkpoints=frozenset(
                ProjectCheckpoint(value) for value in record.required_checkpoints
            ),
            human_grants=grants_from_payloads(record.human_grants),
            repository_teams=tuple(
                RepositoryTeam(
                    id=team.id,
                    project_id=team.project_id,
                    repository_id=team.repository_id,
                    leader_agent_id=team.leader_agent_id,
                    worker_agent_ids=tuple(UUID(value) for value in team.worker_agent_ids),
                    agentteams_team_name=team.agentteams_team_name,
                    runtime_status=ProjectTeamRuntimeStatus(team.runtime_status),
                    room_id=team.room_id,
                    leader_room_id=team.leader_room_id,
                    # ``or`` rather than a bare read: a row written before
                    # revision 0037 and read back through a session that never
                    # refreshed it carries None, and None is a server team.
                    decomposition_mode=TeamDecompositionMode(
                        team.decomposition_mode or TeamDecompositionMode.SERVER.value
                    ),
                )
                for team in teams
            ),
        )


class PersistedTeamDecompositionModeReader:
    """``TeamDecompositionModeReader`` answered from the topology on disk (A-3).

    The mode is a *persisted* fact, so this reads the row and asks the
    controller nothing. That is the decision, not an optimisation: the question
    "who decomposes this team's tasks" is settled by the adoption pass, and a
    live controller lookup on the dispatch path would let a momentary outage
    change the answer mid-round — the same silent downgrade
    ``RepositoryTeam.with_adopted_leader`` refuses to make one layer down.

    Written against ``ProjectTopologyStore`` rather than against SQLAlchemy, so
    the production reader and the one the tests drive are the same class over
    two stores. A reader with its own hand-written SELECT would be a second
    mapping from row to mode, free to disagree with ``_to_domain`` about a
    value neither of them would ever be asked to reconcile.

    Every absence resolves to ``SERVER``, and the protocol has no error channel
    on purpose: a project with no topology, a repository with no team, and a
    team nobody adopted are three ways of saying the same thing — nothing here
    parks a batch for a leader.
    """

    def __init__(self, store: ProjectTopologyStore) -> None:
        self._store = store

    async def decomposition_mode(
        self, project_id: UUID, repository_id: UUID
    ) -> TeamDecompositionMode:
        topology = await self._store.get(project_id)
        if topology is None:
            return TeamDecompositionMode.SERVER
        for team in topology.repository_teams:
            if team.repository_id == repository_id:
                return team.decomposition_mode
        return TeamDecompositionMode.SERVER


class PostgresTopologyPolicyDraftStore:
    """The supervision policy an admin set, waiting for materialization to read it.

    A separate table rather than a field on the discovery snapshot: the policy
    belongs to this module (``EnsureProjectAgentTopology`` is next door and can
    read it without reaching across a module boundary), the snapshot's shape is
    part of the discovery contract, and the draft outlives the snapshot in both
    directions — it may be set before discovery finishes and it is kept after
    materialization as the record of what was originally asked for.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, project_id: UUID) -> TopologyPolicyDraft | None:
        async with self._database.transaction() as session:
            record = await session.get(TopologyPolicyDraftRecord, project_id)
            if record is None:
                return None
            return self._to_domain(record)

    async def upsert(self, draft: TopologyPolicyDraft) -> TopologyPolicyDraft:
        """Overwrite the project's draft, keeping who first set it and when.

        Whole-document replacement, because that is what the endpoint offers:
        one requirement holds one intent, and ``PUT`` says so. ``created_at``
        and ``created_by`` survive an overwrite — "who first decided this
        project needed watching" is a different fact from "who last touched
        it", and losing the first to record the second would be a poor trade.
        """

        async with self._database.transaction() as session:
            record = await session.get(TopologyPolicyDraftRecord, draft.project_id)
            if record is None:
                record = TopologyPolicyDraftRecord(
                    project_id=draft.project_id,
                    created_by=draft.created_by,
                    created_at=draft.created_at,
                )
                session.add(record)
            record.execution_mode = draft.execution_mode.value
            record.required_checkpoints = sorted(
                item.value for item in draft.required_checkpoints
            )
            record.human_grants = grant_payloads(draft.human_grants)
            record.updated_at = draft.updated_at
            stored = self._to_domain(record)
        return stored

    async def delete(self, project_id: UUID) -> bool:
        """Withdraw the draft. ``False`` means there was nothing to withdraw."""

        async with self._database.transaction() as session:
            result = await session.execute(
                delete(TopologyPolicyDraftRecord).where(
                    TopologyPolicyDraftRecord.project_id == project_id
                )
            )
        return bool(result.rowcount)

    @staticmethod
    def _to_domain(record: TopologyPolicyDraftRecord) -> TopologyPolicyDraft:
        return TopologyPolicyDraft(
            project_id=record.project_id,
            created_by=record.created_by,
            execution_mode=ProjectExecutionMode(record.execution_mode),
            required_checkpoints=frozenset(
                ProjectCheckpoint(value) for value in record.required_checkpoints
            ),
            human_grants=grants_from_payloads(record.human_grants),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class PostgresProjectCheckpointDecisionStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add(self, decision: ProjectCheckpointDecision) -> None:
        try:
            async with self._database.transaction() as session:
                session.add(
                    ProjectCheckpointDecisionRecord(
                    id=decision.id,
                    review_request_id=decision.review_request_id,
                    project_id=decision.project_id,
                    checkpoint=decision.checkpoint.value,
                    repository_id=decision.repository_id,
                    human_principal_id=decision.human_principal_id,
                    decision=decision.decision.value,
                    reason=decision.reason,
                    evidence_version=decision.evidence_version,
                    decided_at=decision.decided_at,
                    )
                )
        except IntegrityError as error:
            raise ProjectTopologyConflict("review request was already decided") from error

    async def latest(
        self,
        project_id: UUID,
        checkpoint: ProjectCheckpoint,
        repository_id: UUID | None,
    ) -> ProjectCheckpointDecision | None:
        async with self._database.transaction() as session:
            statement = select(ProjectCheckpointDecisionRecord).where(
                ProjectCheckpointDecisionRecord.project_id == project_id,
                ProjectCheckpointDecisionRecord.checkpoint == checkpoint.value,
            )
            if repository_id is None:
                statement = statement.where(
                    ProjectCheckpointDecisionRecord.repository_id.is_(None)
                )
            else:
                statement = statement.where(
                    ProjectCheckpointDecisionRecord.repository_id == repository_id
                )
            record = await session.scalar(
                statement.order_by(ProjectCheckpointDecisionRecord.decided_at.desc()).limit(1)
            )
        if record is None:
            return None
        return ProjectCheckpointDecision(
            id=record.id,
            review_request_id=record.review_request_id,
            project_id=record.project_id,
            checkpoint=ProjectCheckpoint(record.checkpoint),
            repository_id=record.repository_id,
            human_principal_id=record.human_principal_id,
            decision=CheckpointDecisionKind(record.decision),
            reason=record.reason,
            evidence_version=record.evidence_version,
            decided_at=record.decided_at,
        )


class PostgresHumanReviewRequestStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def ensure(self, request: HumanReviewRequest) -> HumanReviewRequest:
        scope = str(request.repository_id) if request.repository_id else ""
        async with self._database.transaction() as session:
            existing = await session.scalar(
                select(HumanReviewRequestRecord).where(
                    HumanReviewRequestRecord.project_id == request.project_id,
                    HumanReviewRequestRecord.checkpoint == request.checkpoint.value,
                    HumanReviewRequestRecord.repository_scope_key == scope,
                    HumanReviewRequestRecord.evidence_version == request.evidence_version,
                )
            )
            if existing is not None:
                return self._to_domain(existing)
            record = HumanReviewRequestRecord(
                id=request.id,
                project_id=request.project_id,
                checkpoint=request.checkpoint.value,
                repository_id=request.repository_id,
                repository_scope_key=scope,
                evidence_version=request.evidence_version,
                title=request.title,
                summary=request.summary,
                status=request.status.value,
                requested_by_agent_id=request.requested_by_agent_id,
                resolved_by_human_id=request.resolved_by_human_id,
                created_at=request.created_at,
                updated_at=request.updated_at,
            )
            session.add(record)
            return request

    async def get_exact(
        self,
        project_id: UUID,
        checkpoint: ProjectCheckpoint,
        repository_id: UUID | None,
        evidence_version: str,
    ) -> HumanReviewRequest | None:
        scope = str(repository_id) if repository_id else ""
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(HumanReviewRequestRecord).where(
                    HumanReviewRequestRecord.project_id == project_id,
                    HumanReviewRequestRecord.checkpoint == checkpoint.value,
                    HumanReviewRequestRecord.repository_scope_key == scope,
                    HumanReviewRequestRecord.evidence_version == evidence_version,
                )
            )
        return self._to_domain(record) if record is not None else None

    async def get_by_id(self, review_request_id: UUID) -> HumanReviewRequest | None:
        async with self._database.transaction() as session:
            record = await session.get(HumanReviewRequestRecord, review_request_id)
        return self._to_domain(record) if record is not None else None

    async def list_for_human(
        self, human_principal_id: UUID, *, status: HumanReviewStatus | None = None
    ) -> tuple[HumanReviewRequest, ...]:
        async with self._database.transaction() as session:
            topologies = (
                await session.scalars(select(ProjectAgentTopologyRecord))
            ).all()
            grants_by_project = {
                item.project_id: tuple(
                    grant
                    for grant in item.human_grants
                    if grant.get("human_principal_id") == str(human_principal_id)
                )
                for item in topologies
                if any(
                    grant.get("human_principal_id") == str(human_principal_id)
                    for grant in item.human_grants
                )
            }
            if not grants_by_project:
                return ()
            statement = select(HumanReviewRequestRecord).where(
                HumanReviewRequestRecord.project_id.in_(grants_by_project)
            )
            if status is not None:
                statement = statement.where(HumanReviewRequestRecord.status == status.value)
            records = (
                await session.scalars(
                    statement.order_by(HumanReviewRequestRecord.updated_at.desc())
                )
            ).all()
        return tuple(
            self._to_domain(item)
            for item in records
            if any(
                grant.get("repository_id") is None
                or grant.get("repository_id") == str(item.repository_id)
                for grant in grants_by_project[item.project_id]
            )
        )

    async def list_all(
        self, *, status: HumanReviewStatus | None = None
    ) -> tuple[HumanReviewRequest, ...]:
        async with self._database.transaction() as session:
            statement = select(HumanReviewRequestRecord)
            if status is not None:
                statement = statement.where(HumanReviewRequestRecord.status == status.value)
            records = (
                await session.scalars(
                    statement.order_by(HumanReviewRequestRecord.updated_at.desc())
                )
            ).all()
        return tuple(self._to_domain(item) for item in records)

    async def resolve_pending(
        self,
        review_request_id: UUID,
        decision: CheckpointDecisionKind,
        human_principal_id: UUID,
    ) -> bool:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(HumanReviewRequestRecord).where(
                    HumanReviewRequestRecord.id == review_request_id,
                    HumanReviewRequestRecord.status == HumanReviewStatus.PENDING.value,
                )
            )
            if record is None:
                return False
            record.status = decision.value
            record.resolved_by_human_id = human_principal_id
            record.updated_at = datetime.now(UTC)
            return True

    @staticmethod
    def _to_domain(record: HumanReviewRequestRecord) -> HumanReviewRequest:
        return HumanReviewRequest(
            id=record.id,
            project_id=record.project_id,
            checkpoint=ProjectCheckpoint(record.checkpoint),
            repository_id=record.repository_id,
            evidence_version=record.evidence_version,
            title=record.title,
            summary=record.summary,
            status=HumanReviewStatus(record.status),
            requested_by_agent_id=record.requested_by_agent_id,
            resolved_by_human_id=record.resolved_by_human_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
