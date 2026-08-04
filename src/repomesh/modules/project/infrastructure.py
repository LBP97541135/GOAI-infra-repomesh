from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint, delete, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.modules.project.contracts import ProjectTeamRuntimeStatus
from repomesh.modules.project.domain import (
    ProjectAgentTopology,
    ProjectTopologyConflict,
    RepositoryTeam,
)
from repomesh.persistence import Database
from repomesh.persistence.base import Base

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


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


class ProjectRepositoryTeamRecord(Base):
    __tablename__ = "repository_agent_teams"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "repository_id", name="uq_project_repository_agent_team"
        ),
        UniqueConstraint("agentteams_team_name", name="uq_project_agentteams_team_name"),
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
                )
                for team in teams
            ),
        )
