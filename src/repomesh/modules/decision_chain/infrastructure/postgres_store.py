"""Postgres implementations: the chain store and the audit-event source.

``PostgresDecisionChainStore`` is the only writer of ``decision_chain_nodes``.
``append`` is idempotent on ``event_id`` (a replay returns the existing row)
and versions within ``(project_id, step)`` under the unique constraint, so a
concurrent drain cannot double-write.

``PostgresDecisionEventSource`` reads the five chain events out of the shared
``platform.audit_events`` table, oldest first, excluding event ids already
projected — the module's own table, so the drain is naturally incremental.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from repomesh.modules.decision_chain.contracts import (
    CHAIN_EVENT_TYPES,
    DecisionChainNodes,
    DecisionChainSummaryView,
    DecisionNodeInput,
    DecisionNodeView,
    DecisionStatus,
    DecisionStep,
    NodeActor,
    NodeSource,
)
from repomesh.modules.decision_chain.infrastructure._links import (
    legacy_gaps,
    resolve_chain_links,
    summary,
)
from repomesh.modules.decision_chain.infrastructure.models import DecisionNodeRecord
from repomesh.persistence import Database
from repomesh.persistence.models import AuditEventRecord
from repomesh.shared.events import ActorType, EventEnvelope

_logger = logging.getLogger(__name__)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _hydrate(record: DecisionNodeRecord) -> DecisionNodeView:
    actor = record.actor or {}
    return DecisionNodeView(
        decision_id=record.decision_id,
        event_id=record.event_id,
        project_id=record.project_id,
        organization_id=record.organization_id,
        step=DecisionStep(record.step),
        version=record.version,
        status=DecisionStatus(record.status),
        actor=NodeActor(
            type=str(actor.get("type") or "llm"),
            agent_id=UUID(str(actor["agent_id"])) if actor.get("agent_id") else None,
        ),
        upstream_ref=record.upstream_ref,
        evidence_refs=record.evidence_refs or {},
        payload_summary=record.payload_summary or {},
        affected_repository_ids=list(record.affected_repository_ids or []),
        business_time=_aware(record.business_time),
        recorded_at=_aware(record.recorded_at),
        source=NodeSource(record.source),
        event_type=record.event_type,
    )


class PostgresDecisionChainStore:
    """§5 port on ``decision_chain`` schema."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def append(self, node: DecisionNodeInput) -> DecisionNodeView:
        try:
            return await self._append_once(node)
        except IntegrityError:
            # Either the same event was appended concurrently (event_id
            # unique) or a version race lost on (project, step, version).
            # Re-check idempotency, then retry against the fresh state.
            existing = await self._by_event(node.event_id)
            if existing is not None:
                return existing
            return await self._append_once(node)

    async def _append_once(self, node: DecisionNodeInput) -> DecisionNodeView:
        async with self._database.transaction() as session:
            existing = await session.scalar(
                select(DecisionNodeRecord).where(
                    DecisionNodeRecord.event_id == node.event_id
                )
            )
            if existing is not None:
                return _hydrate(existing)
            records = (
                await session.scalars(
                    select(DecisionNodeRecord).where(
                        DecisionNodeRecord.project_id == node.project_id
                    )
                )
            ).all()
            version, upstream_ref = resolve_chain_links(
                [_hydrate(record) for record in records], node
            )
            record = DecisionNodeRecord(
                decision_id=uuid4(),
                event_id=node.event_id,
                project_id=node.project_id,
                organization_id=node.organization_id,
                step=node.step.value,
                version=version,
                status=node.status.value,
                actor={
                    "type": node.actor.type,
                    "agent_id": (
                        str(node.actor.agent_id) if node.actor.agent_id else None
                    ),
                },
                upstream_ref=upstream_ref,
                evidence_refs=node.evidence_refs,
                payload_summary=node.payload_summary,
                affected_repository_ids=list(node.affected_repository_ids),
                business_time=node.business_time,
                recorded_at=datetime.now(UTC),
                source=NodeSource.EVENT.value,
                event_type=node.event_type,
            )
            session.add(record)
            return _hydrate(record)

    async def _by_event(self, event_id: UUID) -> DecisionNodeView | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(DecisionNodeRecord).where(
                    DecisionNodeRecord.event_id == event_id
                )
            )
        return _hydrate(record) if record is not None else None

    async def latest_node(
        self, project_id: UUID, step: DecisionStep
    ) -> DecisionNodeView | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(DecisionNodeRecord)
                .where(
                    DecisionNodeRecord.project_id == project_id,
                    DecisionNodeRecord.step == step.value,
                )
                .order_by(DecisionNodeRecord.version.desc())
                .limit(1)
            )
        return _hydrate(record) if record is not None else None

    async def trace(
        self, *, organization_id: UUID | None, project_id: UUID
    ) -> DecisionChainNodes:
        async with self._database.transaction() as session:
            query = select(DecisionNodeRecord).where(
                DecisionNodeRecord.project_id == project_id
            )
            if organization_id is not None:
                query = query.where(
                    DecisionNodeRecord.organization_id == organization_id
                )
            records = (
                await session.scalars(
                    query.order_by(
                        DecisionNodeRecord.business_time,
                        DecisionNodeRecord.step,
                        DecisionNodeRecord.version,
                    )
                )
            ).all()
        nodes = [_hydrate(record) for record in records]
        return DecisionChainNodes(
            project_id=project_id,
            organization_id=organization_id,
            nodes=nodes,
            legacy_gaps=legacy_gaps(nodes),
        )

    async def find_similar_structural(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        same_repository_ids: tuple[str, ...] = (),
    ) -> list[DecisionChainSummaryView]:
        """§5/Q6: the latest decision of every other project sharing a repo.

        A project is the aggregation unit: it matches when any of its nodes
        carries one of the target repositories, and it contributes its newest
        decision sheet (by business_time/version). The read-side table is
        small, so the overlap filter runs in Python instead of JSONB
        containment SQL — one portable query, no dialect divergence between
        Postgres and the SQLite test twin.

        The repository scope is the explicit ``same_repository_ids`` when
        given, otherwise the target project's own chain nodes. When neither
        exists nothing is returned — an unprovable overlap is not a similarity
        (honest data, the same rule as red line 7).
        """

        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(DecisionNodeRecord).where(
                        DecisionNodeRecord.organization_id == organization_id
                    )
                )
            ).all()
        own_repos = set(same_repository_ids)
        if not own_repos:
            own_repos = {
                repo
                for record in records
                if record.project_id == project_id
                for repo in (record.affected_repository_ids or [])
            }
        if not own_repos:
            return []
        by_project: dict[UUID, list[DecisionNodeView]] = {}
        for record in records:
            if record.project_id == project_id:
                continue
            by_project.setdefault(record.project_id, []).append(_hydrate(record))
        results = []
        for nodes in by_project.values():
            project_repos = {
                repo for node in nodes for repo in node.affected_repository_ids
            }
            if own_repos & project_repos:
                results.append(
                    max(nodes, key=lambda node: (node.business_time, node.version))
                )
        results.sort(key=lambda node: node.business_time, reverse=True)
        return [summary(node) for node in results]


class PostgresDecisionEventSource:
    """Reads unprojected chain events from ``platform.audit_events``."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def list_chain_events(self, limit: int = 200) -> list[EventEnvelope]:
        projected = select(DecisionNodeRecord.event_id)
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(AuditEventRecord)
                    .where(
                        AuditEventRecord.event_type.in_(CHAIN_EVENT_TYPES),
                        AuditEventRecord.event_id.not_in(projected),
                    )
                    .order_by(
                        AuditEventRecord.occurred_at,
                        AuditEventRecord.event_id,
                    )
                    .limit(limit)
                )
            ).all()
        return [_envelope(record) for record in records]


def _envelope(record: AuditEventRecord) -> EventEnvelope:
    return EventEnvelope(
        event_type=record.event_type,
        actor_type=ActorType(record.actor_type),
        actor_id=record.actor_id,
        aggregate_type=record.aggregate_type,
        aggregate_id=record.aggregate_id,
        aggregate_version=record.aggregate_version,
        payload=record.payload or {},
        correlation_id=record.correlation_id,
        schema_version=record.schema_version,
        event_id=record.event_id,
        occurred_at=_aware(record.occurred_at),
        organization_id=record.organization_id,
        project_id=record.project_id,
        workstream_id=record.workstream_id,
        task_id=record.task_id,
        run_id=record.run_id,
        causation_id=record.causation_id,
    )
