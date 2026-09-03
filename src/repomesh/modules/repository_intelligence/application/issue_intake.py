"""Issue intake (contract v0.3 §1): create an issue as its first draft snapshot.

An issue is a project_id (v0.2 §0 — no Project entity exists). Creating one
means persisting the earliest PlanSnapshot: ``plan_version=1``,
``execution_plan_id=None``, empty DAG/contract fields. Everything the read
model needs (state rule 4 "virtual draft → open", phase rule 3, the
organization chain's third level) derives from that single row.

Idempotency: ``project_id`` is a UUIDv5 of the actor's organization plus the
client-supplied key (v0.3 §6 S-5 — the keyspace is scoped per workspace, so a
guessed or low-entropy key can never land on another workspace's issue), and a
replay hits the same ``(project_id, plan_version=1)`` unique constraint and
returns the existing issue instead of creating a second one. A replay is only
answered when it brings the *same* requirement: the key names one logical
create, and content is part of what it names, so a key reused with different
text is refused (409) rather than answered with the original issue. Key
generation responsibility is the client's (fresh random key per logical
create, same key on retry) — see contract §1.3 — and the server refuses keys
whose entropy is too low to be a random key (§6 S-5).
"""

from __future__ import annotations

from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalReader,
    AgentPrincipalStatus,
    AgentRole,
)
from repomesh.modules.repository_intelligence.contracts import (
    IssueIntakeCommand,
    IssueIntakeReceipt,
)
from repomesh.modules.repository_intelligence.infrastructure.plan_snapshot_store import (
    PlanSnapshotAlreadyExists,
    PlanSnapshotStore,
)
from repomesh.shared.domain import DomainError, new_id
from repomesh.shared.events import ActorType, EventEnvelope

# Stable namespace for key→project_id derivation. Changing this value would
# re-map every existing idempotency key; treat it as frozen.
ISSUE_INTAKE_NAMESPACE = uuid5(NAMESPACE_URL, "repomesh://issue-intake")


class IssueIntakeActorNotFound(DomainError):
    """The requested created_by_agent_id does not exist."""


class IssueIntakeDenied(DomainError):
    """The actor is not an active ORGANIZATION_LEADER (contract §1.2, Q1)."""


class IssueIntakeKeyMismatch(DomainError):
    """The idempotency key already names a different requirement (v0.3 §1.3).

    A key identifies one *logical create*, and the requirement text is part of
    what it names. Reusing the key with different content is a client bug —
    answering it with the original issue would silently substitute a different
    requirement for the one the caller thinks it submitted, and answering with
    a new issue would fork the logical create. Refuse instead.
    """


def _key_has_entropy(key: str) -> bool:
    """Refuse guessable idempotency keys (v0.3 §6 S-5, service half).

    The model layer already requires at least 8 characters. This second check
    refuses keys whose *character variety* is too low to be a client-generated
    random key (§1.3: fresh random key per logical create): one character
    repeated, a short unit repeated, or a monotone digit run. It is a refusal,
    not a guarantee — no server-side rule can prove a key was random, only
    that it is not trivially predictable. With the keyspace scoped per
    workspace and replays bound to the original requirement (content
    consistency below), a guessed key cannot read back another issue's
    projection; this check keeps guessed keys from being minted at all.
    """

    if len(set(key)) < 3:
        return False
    # A pure repetition of a short unit ("abababab" is "ab" × 4).
    for unit_len in range(1, len(key) // 2 + 1):
        if len(key) % unit_len == 0 and key == key[:unit_len] * (len(key) // unit_len):
            return False
    # A monotone digit run ("12345678").
    if key.isdigit():
        return not (
            all(key[i] < key[i + 1] for i in range(len(key) - 1))
            or all(key[i] > key[i + 1] for i in range(len(key) - 1))
        )
    return True


class IssueIntakeAuditLog(Protocol):
    async def append(self, event: EventEnvelope) -> None: ...


class IssueIntakeService:
    """Implements the ``CreateIssueIntake`` contract."""

    def __init__(
        self,
        snapshots: PlanSnapshotStore,
        directory: AgentPrincipalReader,
        audit: IssueIntakeAuditLog,
    ) -> None:
        self._snapshots = snapshots
        self._directory = directory
        self._audit = audit

    async def execute(self, command: IssueIntakeCommand) -> IssueIntakeReceipt:
        text = command.requirement_text.strip()
        if not text:
            raise ValueError("requirement_text is required")
        key = command.idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")
        if not _key_has_entropy(key):
            raise ValueError(
                "idempotency_key has too little entropy; use a fresh random "
                "key per logical create and reuse it only on retry (§1.3)"
            )

        actor = await self._directory.get_view(command.created_by_agent_id)
        if actor is None:
            raise IssueIntakeActorNotFound(
                f"agent principal not found: {command.created_by_agent_id}"
            )
        if (
            actor.status is not AgentPrincipalStatus.ACTIVE
            or actor.role is not AgentRole.ORGANIZATION_LEADER
        ):
            raise IssueIntakeDenied(
                "issue intake requires an active organization leader"
            )
        # S-4 (v0.3 §6): the caller may state which workspace it thinks it is
        # acting in; a mismatch with the actor's organization means the leader
        # id was taken from another workspace's roster — reject instead of
        # silently attributing the issue to the actor's workspace.
        if (
            command.organization_id is not None
            and command.organization_id != actor.organization_id
        ):
            raise IssueIntakeDenied(
                "created_by_agent_id belongs to a different organization"
            )

        # S-5: scope the derivation by the actor's organization (a server-side
        # fact, never the request body) so idempotency keys collide only
        # within one workspace.
        project_id = uuid5(
            ISSUE_INTAKE_NAMESPACE, f"{actor.organization_id}:{key}"
        )
        try:
            await self._snapshots.save(
                project_id=project_id,
                plan_version=1,
                engineering_spec="",
                contracts=[],
                task_dag=[],
                execution_batches=[],
                graph_edges=[],
                created_by_agent_id=actor.id,
                execution_plan_id=None,
                requirement_text=text,
                document_filename=command.document_filename,
            )
        except PlanSnapshotAlreadyExists:
            # Same key replay: the original snapshot (and its text) stands —
            # but only for the workspace that owns it (S-5). The org-scoped
            # derivation above (project_id = uuid5(ns, f"{org}:{key}")) makes
            # cross-workspace hits structurally impossible, so a row on this
            # keyspace is normally this workspace's own. The owner check below
            # is defence-in-depth for rows that land here by other means
            # (migration, manual repair); the S-5 replay guard test exercises
            # it with a deliberately foreign-owned row. Rows minted under the
            # pre-fix global derivation (uuid5(ns, key)) hash to a different
            # project_id and never collide with this one — such a key replays
            # as a fresh create, migration debt no runtime check can recover.
            existing = await self._snapshots.get_by_version(project_id, 1)
            creator = (
                None
                if existing is None or existing.created_by_agent_id is None
                else await self._directory.get_view(existing.created_by_agent_id)
            )
            if creator is None or creator.organization_id != actor.organization_id:
                raise IssueIntakeDenied(
                    "idempotency key replays an issue outside the actor's workspace"
                ) from None
            # §1.3 content binding: the key names one logical create, and the
            # requirement text is part of what it names. A replay that brings
            # different content is a client bug — the original issue answers,
            # but the caller would be acting on a requirement it never
            # submitted, so refuse instead of silently substituting.
            if existing is not None and (existing.requirement_text or "") != text:
                raise IssueIntakeKeyMismatch(
                    "idempotency key already created an issue with a different "
                    "requirement; reuse a key only when retrying the same "
                    "logical create (§1.3)"
                ) from None
            return IssueIntakeReceipt(project_id=project_id, created=False)

        # Audit only the actual creation — a replay is a no-op (§1.5).
        await self._audit.append(
            EventEnvelope(
                event_type="IssueIntakeCreated",
                actor_type=ActorType.AGENT,
                actor_id=str(actor.id),
                aggregate_type="Project",
                aggregate_id=project_id,
                aggregate_version=1,
                correlation_id=new_id(),
                organization_id=actor.organization_id,
                project_id=project_id,
                payload={
                    "requirementText": text,
                    "createdByAgentId": str(actor.id),
                    "idempotencyKey": key,
                },
            )
        )
        return IssueIntakeReceipt(project_id=project_id, created=True)
