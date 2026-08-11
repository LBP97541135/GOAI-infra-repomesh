"""Workspace (organization) registry — contract v0.3 §2.

Before this module there was no Organization entity: ``organization_id`` was a
bare UUID column scattered across other modules, so the workspace switcher had
nothing to list. The registry stores only what the switcher needs (id, name,
created_at); anything more would be invented data with no consumer.

Creating a workspace (§2.3) = organization row + its ORGANIZATION_LEADER
directory registration. A workspace without a leader cannot open issues
(v0.3 §1.2 actor rule), so the leader is created in the same call. The two
writes are sequential-but-idempotent rather than one physical transaction:
both derive from the request's idempotency key, so a crash between them is
repaired by replaying the same key (reported as an implementation note on the
"同事务" wording — cross-module session sharing would break module boundaries).
"""

from __future__ import annotations

from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from repomesh.modules.identity_access.contracts import (
    CreateOrganizationCommand,
    OrganizationReceipt,
    OrganizationView,
)
from repomesh.shared.domain import DomainError, new_id
from repomesh.shared.events import ActorType, EventEnvelope

# Frozen: changing it would re-map every workspace idempotency key.
ORGANIZATION_NAMESPACE = uuid5(NAMESPACE_URL, "repomesh://organization-registry")


class OrganizationNameConflict(DomainError):
    """The name is already registered under a different idempotency key."""


class OrganizationSnapshot(Protocol):
    """Persisted registry row (store-facing shape)."""

    organization_id: UUID
    name: str
    created_at: str


class OrganizationStore(Protocol):
    async def add(self, organization_id: UUID, name: str) -> None: ...

    async def get(self, organization_id: UUID) -> OrganizationSnapshot | None: ...

    async def get_by_name(self, name: str) -> OrganizationSnapshot | None: ...

    async def list_all(self) -> tuple[OrganizationSnapshot, ...]: ...


class OrganizationLeaderRegistrar(Protocol):
    """Registers the workspace's ORGANIZATION_LEADER via the agent_directory
    contract (wired in the composition root; this module never imports
    another module's application code)."""

    async def ensure_leader(
        self, organization_id: UUID, resource_name: str, idempotency_key: str
    ) -> UUID: ...


class OrganizationAgentCounter(Protocol):
    async def count_active(self, organization_id: UUID) -> int: ...


class OrganizationAuditLog(Protocol):
    async def append(self, event: EventEnvelope) -> None: ...


def _leader_resource_name(name: str) -> str:
    slug = "".join(
        ch if ch.isalnum() else "-" for ch in name.strip().lower()
    ).strip("-")
    return f"rm-org-leader-{slug or 'workspace'}"


class OrganizationRegistryService:
    """Implements the ``OrganizationRegistry`` contract (v0.3 §2)."""

    def __init__(
        self,
        store: OrganizationStore,
        leaders: OrganizationLeaderRegistrar,
        counter: OrganizationAgentCounter,
        audit: OrganizationAuditLog,
    ) -> None:
        self._store = store
        self._leaders = leaders
        self._counter = counter
        self._audit = audit

    async def list_views(self) -> tuple[OrganizationView, ...]:
        rows = await self._store.list_all()
        views = []
        for row in rows:
            views.append(
                OrganizationView(
                    organization_id=row.organization_id,
                    name=row.name,
                    created_at=row.created_at,
                    agent_count=await self._counter.count_active(row.organization_id),
                )
            )
        return tuple(views)

    async def create(self, command: CreateOrganizationCommand) -> OrganizationReceipt:
        name = command.name.strip()
        if not name:
            raise ValueError("name is required")
        key = command.idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")

        organization_id = uuid5(ORGANIZATION_NAMESPACE, key)
        resource_name = (
            command.leader_resource_name.strip()
            if command.leader_resource_name and command.leader_resource_name.strip()
            else _leader_resource_name(name)
        )

        existing = await self._store.get(organization_id)
        created = False
        if existing is None:
            named = await self._store.get_by_name(name)
            if named is not None:
                # Same name under a different key is a real conflict (409),
                # not an idempotent replay.
                raise OrganizationNameConflict(f"organization name in use: {name}")
            await self._store.add(organization_id, name)
            created = True

        # Leader registration is idempotent by the same key; a replay after a
        # crash between the two writes repairs the missing leader here.
        leader_id = await self._leaders.ensure_leader(
            organization_id, resource_name, f"workspace-leader:{key}"
        )

        row = existing or await self._store.get(organization_id)
        if row is None:  # pragma: no cover - the row was just inserted
            raise DomainError("organization row unavailable after insert")

        if created:
            await self._audit.append(
                EventEnvelope(
                    event_type="OrganizationRegistered",
                    actor_type=ActorType.HUMAN,
                    actor_id="console",
                    aggregate_type="Organization",
                    aggregate_id=organization_id,
                    aggregate_version=1,
                    correlation_id=new_id(),
                    organization_id=organization_id,
                    payload={
                        "name": name,
                        "leaderAgentId": str(leader_id),
                        "leaderResourceName": resource_name,
                        "idempotencyKey": key,
                    },
                )
            )
        return OrganizationReceipt(
            organization_id=organization_id,
            name=row.name,
            created_at=row.created_at,
            leader_agent_id=leader_id,
            created=created,
        )
