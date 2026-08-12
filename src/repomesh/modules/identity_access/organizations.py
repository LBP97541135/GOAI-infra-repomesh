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
from repomesh.shared.events import EventEnvelope

# Frozen: changing it would re-map every workspace idempotency key.
ORGANIZATION_NAMESPACE = uuid5(NAMESPACE_URL, "repomesh://organization-registry")


class OrganizationNameConflict(DomainError):
    """The name is already registered under a different idempotency key."""


class OrganizationInsertConflict(DomainError):
    """The registry insert hit a unique constraint (id or name). Raised by
    the store; the service disambiguates by looking the id row back up
    (present → same-key replay, absent → name conflict)."""


class OrganizationLeaderConflict(DomainError):
    """Leader registration is blocked: the resource name is held by another
    workspace (v0.3 §6 S-8). Repairable, not an orphan state: replaying the
    same idempotency_key with a different leader_resource_name completes the
    registration against the already-inserted organization row."""


class OrganizationSnapshot(Protocol):
    """Persisted registry row (store-facing shape)."""

    organization_id: UUID
    name: str
    created_at: str


class OrganizationStore(Protocol):
    async def add(self, organization_id: UUID, name: str) -> None: ...

    async def get(self, organization_id: UUID) -> OrganizationSnapshot | None: ...

    async def list_all(self) -> tuple[OrganizationSnapshot, ...]: ...


class OrganizationLeaderRegistrar(Protocol):
    """Registers the workspace's ORGANIZATION_LEADER via the agent_directory
    contract (wired in the composition root; this module never imports
    another module's application code).

    Returns ``(leader_id, leader_created)`` — the flag distinguishes a fresh
    registration from converging on an existing leader, which is what lets
    the service audit exactly one completed registration per workspace even
    when the first attempt aborted between the two writes."""

    async def ensure_leader(
        self, organization_id: UUID, resource_name: str, idempotency_key: str
    ) -> tuple[UUID, bool]: ...


class OrganizationAgentCounter(Protocol):
    async def count_active(self, organization_id: UUID) -> int: ...


class OrganizationAuditLog(Protocol):
    async def append(self, event: EventEnvelope) -> None: ...


def _leader_resource_name(name: str, organization_id: UUID) -> str:
    """Auto-derived leader names carry an organization suffix (v0.3 §6 S-8):
    similar workspace names ("web app" / "web-app") slug identically, and the
    AgentTeams binding is unique platform-wide — without the suffix the second
    workspace's leader registration would collide and strand that workspace.
    The suffix derives from the organization id, which derives from the
    idempotency key, so replays compute the same name."""

    slug = "".join(
        ch if ch.isalnum() else "-" for ch in name.strip().lower()
    ).strip("-")
    return f"rm-org-leader-{slug or 'workspace'}-{organization_id.hex[:8]}"


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

    async def list_views(
        self, organization_id: UUID | None = None
    ) -> tuple[OrganizationView, ...]:
        rows = await self._store.list_all()
        # v0.3 §6 S-6: caller-scoped filtering. Honest limit: the shared
        # action token carries no tenant, so this narrows what a caller asks
        # for, not what it may see — real tenant isolation arrives with the
        # subject-carrying credential backlog item.
        if organization_id is not None:
            rows = tuple(row for row in rows if row.organization_id == organization_id)
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
            else _leader_resource_name(name, organization_id)
        )

        # v0.3 §6 S-7: insert first and let the unique constraints arbitrate —
        # same pattern as issue intake. The previous read-then-insert spanned
        # three transactions, so two concurrent creates could both pass the
        # checks and the loser surfaced as a raw 500. After a conflict the id
        # lookup tells the cases apart: row present → same-key replay (200);
        # absent → the name is taken under another key (409).
        created = False
        try:
            await self._store.add(organization_id, name)
            created = True
        except OrganizationInsertConflict:
            if await self._store.get(organization_id) is None:
                raise OrganizationNameConflict(
                    f"organization name in use: {name}"
                ) from None

        # Leader registration is idempotent by the same key; a replay after a
        # crash between the two writes repairs the missing leader here. A
        # genuine resource-name collision raises OrganizationLeaderConflict
        # (409 at the API) and the organization row is deliberately kept:
        # replaying the same idempotency_key with a different
        # leader_resource_name completes the registration (S-8 — no
        # unrepairable orphan state).
        leader_id, leader_created = await self._leaders.ensure_leader(
            organization_id, resource_name, f"workspace-leader:{key}"
        )

        row = await self._store.get(organization_id)
        if row is None:  # pragma: no cover - the row was just inserted
            raise DomainError("organization row unavailable after insert")

        # Audit once per *completed* registration. The event sits after both
        # writes, so gating on `created` alone would lose the trail whenever
        # the first attempt aborted between them (leader conflict, crash) and
        # the replay finished the job with created=False. `leader_created`
        # marks exactly those repairs; a plain replay has both flags False.
        if created or leader_created:
            # v0.3 §6 S-6: attribution comes from the caller-resolved actor
            # (human session when present, else a token fingerprint) — a
            # hardcoded label made every workspace creation untraceable.
            await self._audit.append(
                EventEnvelope(
                    event_type="OrganizationRegistered",
                    actor_type=command.actor_type,
                    actor_id=command.actor_id,
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
