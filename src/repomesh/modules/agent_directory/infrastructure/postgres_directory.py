from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentRole,
)
from repomesh.modules.agent_directory.domain import AgentAlreadyExists, AgentPrincipal
from repomesh.persistence import Database
from repomesh.persistence.models import AuditEventRecord, OutboxEventRecord, StateEventRecord
from repomesh.shared.events import EventEnvelope

from .models import AgentPrincipalRecord


class PostgresAgentDirectory:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add(
        self,
        principal: AgentPrincipal,
        *,
        idempotency_key: str,
        request_fingerprint: str,
        events: Sequence[EventEnvelope] = (),
    ) -> None:
        try:
            async with self._database.transaction() as session:
                session.add(self._to_record(principal, idempotency_key, request_fingerprint))
                for event in events:
                    session.add(StateEventRecord.from_envelope(event))
                    session.add(AuditEventRecord.from_envelope(event))
                    session.add(OutboxEventRecord.from_envelope(event))
        except IntegrityError as error:
            raise AgentAlreadyExists(
                "agent principal, AgentTeams binding, or idempotency key already exists"
            ) from error

    async def get(self, agent_id: UUID) -> AgentPrincipal | None:
        async with self._database.transaction() as session:
            record = await session.get(AgentPrincipalRecord, agent_id)
        return self._to_domain(record) if record is not None else None

    async def get_view(self, agent_id: UUID):
        principal = await self.get(agent_id)
        return principal.to_view() if principal is not None else None

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[AgentPrincipal, str] | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(AgentPrincipalRecord).where(
                    AgentPrincipalRecord.idempotency_key == idempotency_key
                )
            )
        if record is None:
            return None
        return self._to_domain(record), record.request_fingerprint

    @staticmethod
    def _to_record(
        principal: AgentPrincipal,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> AgentPrincipalRecord:
        return AgentPrincipalRecord(
            id=principal.id,
            organization_id=principal.organization_id,
            role=principal.role.value,
            leader_agent_id=principal.leader_agent_id,
            singleton_key=principal.singleton_key,
            repository_id=principal.repository_id,
            responsibility_paths=list(principal.responsibility_paths),
            agentteams_resource_kind=(
                "manager"
                if principal.role is AgentRole.ORGANIZATION_LEADER
                else "worker"
            ),
            agentteams_resource_name=principal.agentteams_resource_name,
            status=principal.status.value,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )

    @staticmethod
    def _to_domain(record: AgentPrincipalRecord) -> AgentPrincipal:
        return AgentPrincipal(
            id=record.id,
            organization_id=record.organization_id,
            role=AgentRole(record.role),
            leader_agent_id=record.leader_agent_id,
            singleton_key=record.singleton_key,
            repository_id=record.repository_id,
            responsibility_paths=tuple(record.responsibility_paths),
            agentteams_resource_name=record.agentteams_resource_name,
            status=AgentPrincipalStatus(record.status),
        )
