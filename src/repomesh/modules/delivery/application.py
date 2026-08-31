import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalReader,
    AgentPrincipalStatus,
    AgentRole,
)
from repomesh.modules.task_orchestration.contracts import ExecutionPlanStatus
from repomesh.shared.domain import new_id
from repomesh.shared.events import ActorType, EventEnvelope

from .contracts import (
    MERGE_GATE_GOVERNANCE_MISSING_REASON,
    AppendCandidatesCommand,
    ChangeSetRollbackView,
    ChangeSetStatus,
    ChangeSetView,
    CIObservationCommand,
    DeliveryArchiveView,
    EnqueueSCMCommand,
    GovernanceDecisionKind,
    GovernanceDecisionView,
    MergeGateDecision,
    MergeObservationCommand,
    PlanRecoveryCommand,
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RecordCandidateRevisionCommand,
    RecordCandidateTraceabilityCommand,
    RecordedSCMObservation,
    RecordGovernanceDecisionCommand,
    RecordMergeRequestedCommand,
    RecordRecoveryActionCommand,
    RecordSCMObservationCommand,
    RecoveryActionKind,
    RecoveryActionStatus,
    RecoveryActionView,
    RecoveryPlanView,
    RecoveryTrigger,
    RepositoryCandidateInput,
    RepositoryDeliveryStatus,
    RequestChangeSetRollbackCommand,
    ReviewObservationCommand,
    SCMCommandView,
    SCMObservationView,
)
from .domain import (
    CandidateRevision,
    ChangeSet,
    DeliveryConflict,
    DeliveryDenied,
    DeliveryNotFound,
    GovernanceDecision,
    RecoveryAction,
    RecoveryPlan,
    RepositoryDelivery,
    SCMCommand,
    SCMObservation,
    SCMPollCursor,
)
from .ports import (
    ChangeSetStore,
    ContractCatalogPort,
    DeliveryArchiveStore,
    DeliveryAuditLog,
    DeliveryConflictCasePort,
    ExecutionPlanStatusReader,
    SCMCommandStore,
    SCMObservationStore,
    SCMPollCursorStore,
    ValidationSnapshotReader,
)


def delivery_change_set_key(delivery_id: UUID) -> str:
    """The idempotency key that binds a delivery (execution plan) to its ChangeSet."""

    return f"execution-plan:{delivery_id}:delivery"


class SCMCommandService:
    def __init__(
        self,
        store: SCMCommandStore,
        *,
        lease_seconds: int = 300,
        max_attempts: int = 8,
    ) -> None:
        self._store = store
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts

    async def enqueue(self, command: EnqueueSCMCommand) -> SCMCommandView:
        existing = await self._store.get_by_idempotency_key(command.idempotency_key)
        if existing is not None:
            if (
                existing.change_set_id != command.change_set_id
                or existing.repository_id != command.repository_id
                or existing.kind is not command.kind
                or existing.payload != command.payload
            ):
                raise DeliveryConflict("SCM command idempotency key changed meaning")
            return existing.to_view()
        created = SCMCommand(
            change_set_id=command.change_set_id,
            repository_id=command.repository_id,
            kind=command.kind,
            idempotency_key=command.idempotency_key,
            payload=command.payload,
        )
        try:
            await self._store.add(created)
        except DeliveryConflict:
            existing = await self._store.get_by_idempotency_key(command.idempotency_key)
            if existing is None:
                raise
            return existing.to_view()
        return created.to_view()

    async def claim_batch(
        self, lease_owner: str, *, limit: int = 100
    ) -> tuple[SCMCommandView, ...]:
        claimed = await self._store.claim_batch(
            lease_owner=lease_owner,
            lease_seconds=self._lease_seconds,
            max_attempts=self._max_attempts,
            limit=limit,
        )
        return tuple(item.to_view() for item in claimed)

    async def renew(
        self, command_id: UUID, lease_owner: str, fencing_version: int
    ) -> SCMCommandView:
        renewed = await self._store.renew(
            command_id,
            lease_owner=lease_owner,
            fencing_version=fencing_version,
            lease_seconds=self._lease_seconds,
        )
        return renewed.to_view()

    async def accept(
        self, command_id: UUID, lease_owner: str, fencing_version: int
    ) -> SCMCommandView:
        accepted = await self._store.accept(
            command_id,
            lease_owner=lease_owner,
            fencing_version=fencing_version,
        )
        return accepted.to_view()

    async def fail(
        self, command_id: UUID, error: str, lease_owner: str, fencing_version: int
    ) -> SCMCommandView:
        failed = await self._store.fail(
            command_id,
            error,
            lease_owner=lease_owner,
            fencing_version=fencing_version,
        )
        return failed.to_view()

    async def list_dispatchable(self, *, limit: int = 100) -> tuple[SCMCommandView, ...]:
        items = await self._store.list_dispatchable(
            stale_before=datetime.now(UTC) - timedelta(seconds=self._lease_seconds),
            max_attempts=self._max_attempts,
            limit=limit,
        )
        return tuple(item.to_view() for item in items)

    async def _required(self, command_id: UUID) -> SCMCommand:
        current = await self._store.get(command_id)
        if current is None:
            raise DeliveryNotFound(f"SCM command not found: {command_id}")
        return current


class SCMPollCursorService:
    def __init__(self, store: SCMPollCursorStore, *, interval_seconds: float = 60) -> None:
        self._store = store
        self._interval_seconds = interval_seconds

    async def due(self, change_set_id: UUID, repository_id: UUID) -> bool:
        cursor = await self._store.get(change_set_id, repository_id)
        return cursor is None or cursor.next_poll_at <= datetime.now(UTC)

    async def succeed(self, change_set_id: UUID, repository_id: UUID) -> None:
        now = datetime.now(UTC)
        current = await self._store.get(change_set_id, repository_id)
        if current is None:
            current = SCMPollCursor(change_set_id, repository_id, now)
            expected = None
        else:
            expected = current.version
        await self._store.upsert(
            current.succeed(now, self._interval_seconds), expected_version=expected
        )

    async def fail(
        self,
        change_set_id: UUID,
        repository_id: UUID,
        error: str,
        *,
        retry_after: int | None = None,
    ) -> None:
        now = datetime.now(UTC)
        current = await self._store.get(change_set_id, repository_id)
        if current is None:
            current = SCMPollCursor(change_set_id, repository_id, now)
            expected = None
        else:
            expected = current.version
        await self._store.upsert(
            current.fail(
                now,
                error,
                base_seconds=self._interval_seconds,
                retry_after=retry_after,
            ),
            expected_version=expected,
        )


class SCMObservationService:
    def __init__(
        self,
        store: SCMObservationStore,
        *,
        now: Callable[[], datetime] | None = None,
        lease_timeout: timedelta = timedelta(minutes=5),
        max_attempts: int = 5,
    ) -> None:
        if lease_timeout.total_seconds() <= 0 or max_attempts < 1:
            raise ValueError("observation retry policy must be positive")
        self._store = store
        self._now = now or (lambda: datetime.now(UTC))
        self._lease_timeout = lease_timeout
        self._max_attempts = max_attempts

    async def record(self, command: RecordSCMObservationCommand) -> RecordedSCMObservation:
        existing = await self._store.get_by_identity(
            command.provider.strip().lower(), command.source.value, command.external_id.strip()
        )
        if existing is not None:
            self._validate_duplicate(existing, command)
            return RecordedSCMObservation(existing.to_view(), created=False)
        observation = SCMObservation(
            provider=command.provider.strip().lower(),
            source=command.source,
            external_id=command.external_id.strip(),
            event_type=command.event_type.strip().lower(),
            payload=command.payload,
            payload_hash=command.payload_hash.strip().lower(),
            observed_at=command.observed_at,
            change_set_id=command.change_set_id,
            repository_id=command.repository_id,
        )
        try:
            await self._store.add(observation)
        except DeliveryConflict:
            existing = await self._store.get_by_identity(
                observation.provider, observation.source.value, observation.external_id
            )
            if existing is None:
                raise
            self._validate_duplicate(existing, command)
            return RecordedSCMObservation(existing.to_view(), created=False)
        return RecordedSCMObservation(observation.to_view(), created=True)

    async def get(self, observation_id: UUID) -> SCMObservationView:
        return (await self._required(observation_id)).to_view()

    async def claim(self, observation_id: UUID) -> SCMObservationView | None:
        observation = await self._required(observation_id)
        now = self._now()
        if not observation.is_claimable(
            now,
            lease_timeout=self._lease_timeout,
            max_attempts=self._max_attempts,
        ):
            return None
        claimed = observation.claim(now)
        await self._store.update(claimed, expected_version=observation.version)
        return claimed.to_view()

    async def complete(self, observation_id: UUID) -> SCMObservationView:
        observation = await self._required(observation_id)
        completed = observation.complete(self._now())
        await self._store.update(completed, expected_version=observation.version)
        return completed.to_view()

    async def fail(self, observation_id: UUID, error: str) -> SCMObservationView:
        observation = await self._required(observation_id)
        failed = observation.fail(error)
        await self._store.update(failed, expected_version=observation.version)
        return failed.to_view()

    async def list_replayable(self, *, limit: int = 100) -> tuple[SCMObservationView, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        now = self._now()
        items = await self._store.list_replayable(
            stale_before=now - self._lease_timeout,
            max_attempts=self._max_attempts,
            limit=limit,
        )
        return tuple(item.to_view() for item in items)

    async def _required(self, observation_id: UUID) -> SCMObservation:
        observation = await self._store.get(observation_id)
        if observation is None:
            raise DeliveryNotFound(f"SCM observation not found: {observation_id}")
        return observation

    @staticmethod
    def _validate_duplicate(existing: SCMObservation, command: RecordSCMObservationCommand) -> None:
        same = (
            existing.payload_hash == command.payload_hash.strip().lower()
            and existing.event_type == command.event_type.strip().lower()
            and existing.change_set_id == command.change_set_id
            and existing.repository_id == command.repository_id
        )
        if not same:
            raise DeliveryConflict("SCM observation identity was reused for another external fact")


class DeliveryService:
    def __init__(
        self,
        store: ChangeSetStore,
        *,
        require_governance: bool = False,
        require_validation: bool = False,
        validation_reader: ValidationSnapshotReader | None = None,
        contract_catalog: ContractCatalogPort | None = None,
        audit: DeliveryAuditLog | None = None,
        conflict_cases: DeliveryConflictCasePort | None = None,
    ) -> None:
        self._store = store
        self._require_governance = require_governance
        self._require_validation = require_validation
        self._validation_reader = validation_reader
        self._contract_catalog = contract_catalog
        self._audit = audit
        self._conflict_cases = conflict_cases

    async def prepare(
        self, command: PrepareChangeSetCommand, *, idempotency_key: str
    ) -> ChangeSetView:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        fingerprint = self._fingerprint(command)
        existing = await self._store.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            change_set, previous = existing
            if previous != fingerprint:
                raise DeliveryConflict("idempotency key was used for another ChangeSet")
            return change_set.to_view()
        order = self._merge_order(command)
        repositories = tuple(
            RepositoryDelivery(
                repository_id=item.repository_id,
                task_id=item.task_id,
                commit_sha=item.commit_sha,
                base_sha=item.base_sha,
                branch_name=item.branch_name,
                depends_on=item.depends_on,
                merge_order=order[item.repository_id],
                required_checks=tuple(name.strip().lower() for name in item.required_checks),
                required_approvals=item.required_approvals,
                plan_id=item.plan_id,
                run_id=item.run_id,
                worker_agent_id=item.worker_agent_id,
            )
            for item in command.candidates
        )
        change_set = ChangeSet(
            organization_id=command.organization_id,
            project_id=command.project_id,
            created_by_agent_id=command.created_by_agent_id,
            title=command.title.strip(),
            validation_snapshot_id=command.validation_snapshot_id,
            repositories=repositories,
            candidate_revisions=tuple(
                CandidateRevision(
                    repository_id=item.repository_id,
                    task_id=item.task_id,
                    sequence=0,
                    head_sha=item.commit_sha,
                    previous_head_sha=None,
                    reason="initial candidate",
                )
                for item in repositories
            ),
        )
        await self._store.add(change_set, idempotency_key=idempotency_key, fingerprint=fingerprint)
        return change_set.to_view()

    async def get_by_idempotency_key(self, key: str) -> ChangeSetView | None:
        existing = await self._store.get_by_idempotency_key(key)
        return existing[0].to_view() if existing is not None else None

    async def record_candidate_traceability(
        self, command: RecordCandidateTraceabilityCommand
    ) -> ChangeSetView:
        """Idempotently bind plan/run/worker provenance to one candidate."""

        return await self._update_repository(
            command.change_set_id,
            command.repository_id,
            lambda item: item.attach_traceability(
                task_id=command.task_id,
                commit_sha=command.commit_sha,
                plan_id=command.plan_id,
                run_id=command.run_id,
                worker_agent_id=command.worker_agent_id,
            ),
        )

    async def append_candidates(
        self, command: AppendCandidatesCommand, *, idempotency_key: str
    ) -> ChangeSetView:
        """Extend a ChangeSet with a later batch's delivery candidates.

        Batch-by-batch delivery keeps one ChangeSet per plan: the first batch
        creates it via ``prepare`` and later batches append their candidates
        here. Re-appending the same batch is idempotent: repositories already
        present in the ChangeSet are skipped.
        """
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        change_set = await self._required(command.change_set_id)
        known_ids = {item.repository_id for item in change_set.repositories}
        fresh = tuple(
            item for item in command.candidates if item.repository_id not in known_ids
        )
        if not fresh:
            return change_set.to_view()
        combined = known_ids | {item.repository_id for item in fresh}
        for item in fresh:
            if not set(item.depends_on) <= combined:
                raise DeliveryConflict("repository dependency is outside the ChangeSet")
        order = self._merge_order_for_append(change_set, fresh)
        repositories = tuple(
            RepositoryDelivery(
                repository_id=item.repository_id,
                task_id=item.task_id,
                commit_sha=item.commit_sha,
                base_sha=item.base_sha,
                branch_name=item.branch_name,
                depends_on=item.depends_on,
                merge_order=order[item.repository_id],
                required_checks=tuple(name.strip().lower() for name in item.required_checks),
                required_approvals=item.required_approvals,
                plan_id=item.plan_id,
                run_id=item.run_id,
                worker_agent_id=item.worker_agent_id,
            )
            for item in fresh
        )
        appended = change_set.append_repositories(repositories)
        await self._store.update(appended, expected_version=change_set.version)
        return appended.to_view()

    async def find_by_project(self, project_id: UUID) -> tuple[ChangeSetView, ...]:
        """Return delivery state views for all ChangeSets of a project.

        Used by the batch-advancement gate to learn whether the repositories
        of the current batch are already merged.
        """
        return tuple(
            change_set.to_view()
            for change_set in await self._store.find_by_project(project_id)
        )

    async def observe_pull_request(self, command: PullRequestObservationCommand) -> ChangeSetView:
        change_set = await self._required(command.change_set_id)
        target = self._repository(change_set, command.repository_id)
        view = await self._update_repository(
            command.change_set_id,
            command.repository_id,
            lambda item: item.observe_pr(
                command.pull_request_number,
                command.pull_request_url,
                command.head_sha,
            ),
        )
        if target.pull_request_number != command.pull_request_number:
            # A replay (same PR number) already landed; emit only on a new
            # observation, so the decision chain gets one node per PR.
            await self._emit_pull_request_observed(change_set, target, command)
        return view

    async def _emit_pull_request_observed(
        self,
        change_set: ChangeSet,
        repository: RepositoryDelivery,
        command: PullRequestObservationCommand,
    ) -> None:
        """Contract decision-chain v0.1 §3.2 — ``PullRequestObserved``.

        ``organization_id`` / ``project_id`` come from the ChangeSet (E9):
        the PR observation command itself carries neither (E4), so the lookup
        is the one place the chain's L1 is derived for this step.
        """

        if self._audit is None:
            return
        await self._audit.append(
            EventEnvelope(
                event_type="PullRequestObserved",
                actor_type=ActorType.SERVICE,
                actor_id=str(change_set.created_by_agent_id),
                aggregate_type="ChangeSet",
                aggregate_id=change_set.id,
                aggregate_version=change_set.version,
                correlation_id=new_id(),
                organization_id=change_set.organization_id,
                project_id=change_set.project_id,
                payload={
                    "schema_version": 1,
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "change_set_id": str(change_set.id),
                    "repository_id": str(repository.repository_id),
                    "pull_request_number": command.pull_request_number,
                    "pull_request_url": command.pull_request_url,
                    "task_ids": [str(repository.task_id)],
                },
            )
        )

    async def observe_ci(self, command: CIObservationCommand) -> ChangeSetView:
        return await self._update_repository(
            command.change_set_id,
            command.repository_id,
            lambda item: item.observe_ci(
                command.passed,
                command.check_run_id,
                command.summary,
                command.check_name,
            ),
        )

    async def observe_review(self, command: ReviewObservationCommand) -> ChangeSetView:
        return await self._update_repository(
            command.change_set_id,
            command.repository_id,
            lambda item: item.observe_review(
                command.review_id,
                command.reviewer,
                command.state,
                command.head_sha,
                command.summary,
            ),
        )

    async def observe_merge(self, command: MergeObservationCommand) -> ChangeSetView:
        change_set = await self._required(command.change_set_id)
        target = self._repository(change_set, command.repository_id)
        unmet = [
            dependency
            for dependency in target.depends_on
            if self._repository(change_set, dependency).status
            is not RepositoryDeliveryStatus.MERGED
        ]
        if unmet:
            raise DeliveryConflict("upstream repositories must merge first")
        return await self._update_repository(
            command.change_set_id,
            command.repository_id,
            lambda item: item.observe_merge(command.merge_sha),
        )

    async def record_merge_requested(self, command: RecordMergeRequestedCommand) -> ChangeSetView:
        return await self._update_repository(
            command.change_set_id,
            command.repository_id,
            lambda item: item.request_merge(command.head_sha),
        )

    async def record_governance_decision(
        self, command: RecordGovernanceDecisionCommand
    ) -> ChangeSetView:
        change_set = await self._required(command.change_set_id)
        updated = change_set.record_governance(
            GovernanceDecision(
                repository_id=command.repository_id,
                head_sha=command.head_sha.strip().lower(),
                decision=command.decision,
                decided_by_agent_id=command.decided_by_agent_id,
                reason=command.reason.strip(),
            )
        )
        await self._store.update(updated, expected_version=change_set.version)
        return updated.to_view()

    async def record_candidate_revision(
        self, command: RecordCandidateRevisionCommand
    ) -> ChangeSetView:
        change_set = await self._required(command.change_set_id)
        updated = change_set.record_candidate_revision(
            command.repository_id,
            command.task_id,
            command.previous_head_sha,
            command.new_head_sha,
            command.reason,
        )
        await self._store.update(updated, expected_version=change_set.version)
        if self._conflict_cases is not None:
            await self._conflict_cases.resolve_for_revision(
                command.change_set_id,
                command.repository_id,
                command.previous_head_sha.strip().lower(),
            )
        return updated.to_view()

    async def evaluate_merge_gate(
        self, change_set_id: UUID, repository_id: UUID
    ) -> MergeGateDecision:
        change_set = await self._required(change_set_id)
        target = self._repository(change_set, repository_id)
        reasons: list[str] = []
        if self._conflict_cases is not None:
            conflict = await self._conflict_cases.active_for(change_set_id, repository_id)
            if conflict is not None:
                reasons.append(f"delivery conflict is unresolved: {conflict.kind.value}")
        if target.status is not RepositoryDeliveryStatus.READY_TO_MERGE:
            reasons.append("required CI checks have not passed")
        if target.status in {
            RepositoryDeliveryStatus.REVIEW_PENDING,
            RepositoryDeliveryStatus.REVIEW_CHANGES_REQUESTED,
        }:
            reasons.append("required reviews have not passed")
        for dependency in target.depends_on:
            upstream = self._repository(change_set, dependency)
            if upstream.status is not RepositoryDeliveryStatus.MERGED:
                reasons.append(f"upstream repository is not merged: {dependency}")
        if target.merge_order > change_set.merge_cursor and not any(
            reason.startswith("upstream repository") for reason in reasons
        ):
            reasons.append(f"merge cursor is waiting at order {change_set.merge_cursor}")
        earlier = (
            item
            for item in change_set.repositories
            if item.merge_order < target.merge_order and item.repository_id not in target.depends_on
        )
        if any(item.status is RepositoryDeliveryStatus.CI_FAILED for item in earlier):
            reasons.append("an earlier delivery candidate has failed CI")
        if change_set.recovery_plans:
            active = change_set.recovery_plans[-1]
            if any(
                action.status not in {RecoveryActionStatus.SUCCEEDED, RecoveryActionStatus.SKIPPED}
                for action in active.actions
            ):
                reasons.append("an active recovery plan is incomplete")
        if self._require_governance:
            decision = next(
                (
                    item
                    for item in reversed(change_set.governance_decisions)
                    if item.repository_id == repository_id and item.head_sha == target.commit_sha
                ),
                None,
            )
            if decision is None:
                reasons.append(MERGE_GATE_GOVERNANCE_MISSING_REASON)
            elif decision.decision is GovernanceDecisionKind.BLOCKED:
                reasons.append(f"governance blocked delivery: {decision.reason}")
            elif decision.decision is GovernanceDecisionKind.ROLLBACK_REQUIRED:
                reasons.append(f"governance requires rollback: {decision.reason}")
        if self._require_validation:
            if change_set.validation_snapshot_id is None:
                reasons.append("validation snapshot is missing")
            elif self._validation_reader is None:
                reasons.append("validation snapshot reader is unavailable")
            else:
                validation = await self._validation_reader.validate_for_delivery(
                    change_set.validation_snapshot_id,
                    change_set.project_id,
                    {item.repository_id: item.commit_sha for item in change_set.repositories},
                )
                reasons.extend(validation.reasons)
        if self._contract_catalog is not None:
            contracts = await self._contract_catalog.contracts_for_project(
                change_set.project_id
            )
            candidate_ids = {item.repository_id for item in change_set.repositories}
            for contract in contracts:
                if contract.producer != repository_id:
                    continue
                if contract.consumer in candidate_ids:
                    continue
                if contract.consumer_planned:
                    continue
                reasons.append("contract change is missing a consumer adapter candidate")
                break
        return MergeGateDecision(
            change_set_id=change_set.id,
            repository_id=repository_id,
            allowed=not reasons,
            reasons=tuple(reasons),
        )

    async def plan_recovery(self, command: PlanRecoveryCommand) -> ChangeSetView:
        change_set = await self._required(command.change_set_id)
        actions = self._recovery_actions(change_set, command)
        plan = RecoveryPlan(
            trigger=command.trigger,
            reason=command.reason.strip(),
            actions=actions,
        )
        updated = change_set.add_recovery(plan)
        await self._store.update(updated, expected_version=change_set.version)
        return updated.to_view()

    async def preview_recovery(
        self, command: PlanRecoveryCommand
    ) -> tuple[RecoveryActionView, ...]:
        """The actions ``plan_recovery`` would create, without creating them.

        The console's rollback dialog has to show which repository gets a
        revert PR and in which position, and that answer must be the plan the
        Saga will actually run — so it comes from the same generator rather
        than from a second implementation of the reverse-merge-order rule.
        """

        change_set = await self._required(command.change_set_id)
        return tuple(action.to_view() for action in self._recovery_actions(change_set, command))

    async def get(self, change_set_id: UUID) -> ChangeSetView:
        return (await self._required(change_set_id)).to_view()

    async def list_active(self) -> tuple[ChangeSetView, ...]:
        return tuple(item.to_view() for item in await self._store.list_active())

    async def resolve_candidate(
        self, repository_id: UUID, head_sha: str
    ) -> tuple[ChangeSetView, UUID]:
        matches = await self._store.find_by_candidate(repository_id, head_sha)
        active = tuple(
            item
            for item in matches
            if item.status
            not in {
                ChangeSetStatus.DELIVERED,
                ChangeSetStatus.COMPENSATED,
            }
        )
        if not active:
            raise DeliveryNotFound("no active ChangeSet candidate matches repository and SHA")
        if len(active) > 1:
            raise DeliveryConflict("multiple active ChangeSets match repository and SHA")
        return active[0].to_view(), repository_id

    async def record_recovery_action(self, command: RecordRecoveryActionCommand) -> ChangeSetView:
        change_set = await self._required(command.change_set_id)
        updated = change_set.record_recovery_action(
            command.recovery_plan_id,
            command.action_id,
            command.status,
            command.detail,
        )
        await self._store.update(updated, expected_version=change_set.version)
        return updated.to_view()

    async def _update_repository(self, change_set_id, repository_id, operation):
        change_set = await self._required(change_set_id)
        found = False
        repositories = []
        for item in change_set.repositories:
            if item.repository_id == repository_id:
                found = True
                repositories.append(operation(item))
            else:
                repositories.append(item)
        if not found:
            raise DeliveryNotFound(f"repository not in ChangeSet: {repository_id}")
        updated_repositories = tuple(repositories)
        if updated_repositories == change_set.repositories:
            return change_set.to_view()
        updated = change_set.with_repositories(updated_repositories)
        await self._store.update(updated, expected_version=change_set.version)
        return updated.to_view()

    async def _required(self, change_set_id: UUID) -> ChangeSet:
        change_set = await self._store.get(change_set_id)
        if change_set is None:
            raise DeliveryNotFound(f"ChangeSet not found: {change_set_id}")
        return change_set

    @staticmethod
    def _repository(change_set: ChangeSet, repository_id: UUID) -> RepositoryDelivery:
        match = next(
            (item for item in change_set.repositories if item.repository_id == repository_id),
            None,
        )
        if match is None:
            raise DeliveryNotFound(f"repository not in ChangeSet: {repository_id}")
        return match

    @staticmethod
    def _merge_order(command: PrepareChangeSetCommand) -> dict[UUID, int]:
        ids = {item.repository_id for item in command.candidates}
        dependencies = {item.repository_id: set(item.depends_on) for item in command.candidates}
        if any(not values <= ids for values in dependencies.values()):
            raise DeliveryConflict("repository dependency is outside the ChangeSet")
        order: dict[UUID, int] = {}
        remaining = dict(dependencies)
        index = 0
        while remaining:
            ready = sorted(
                (repo for repo, deps in remaining.items() if deps <= order.keys()),
                key=str,
            )
            if not ready:
                raise DeliveryConflict("repository dependency graph contains a cycle")
            for repository_id in ready:
                order[repository_id] = index
                index += 1
                remaining.pop(repository_id)
        return order

    @staticmethod
    def _merge_order_for_append(
        change_set: ChangeSet, candidates: tuple[RepositoryCandidateInput, ...]
    ) -> dict[UUID, int]:
        """Assign merge orders for appended candidates after existing ones.

        Existing repositories keep their merge order; appended candidates are
        ordered after the current maximum and must only depend on repositories
        already present in the ChangeSet.
        """
        order = {item.repository_id: item.merge_order for item in change_set.repositories}
        next_index = max(order.values(), default=-1) + 1
        dependencies = {item.repository_id: set(item.depends_on) for item in candidates}
        remaining = dict(dependencies)
        while remaining:
            ready = sorted(
                (repo for repo, deps in remaining.items() if deps <= order.keys()),
                key=str,
            )
            if not ready:
                raise DeliveryConflict("repository dependency graph contains a cycle")
            for repository_id in ready:
                order[repository_id] = next_index
                next_index += 1
                remaining.pop(repository_id)
        return order

    @staticmethod
    def _recovery_actions(
        change_set: ChangeSet, command: PlanRecoveryCommand
    ) -> tuple[RecoveryAction, ...]:
        if command.trigger in {RecoveryTrigger.RUNNER_FAILED, RecoveryTrigger.RUNNER_INTERRUPTED}:
            kind = (
                RecoveryActionKind.RESUME_RUNNER_SESSION
                if command.native_session_id
                else RecoveryActionKind.RETRY_RUNNER
            )
            return (
                RecoveryAction(
                    sequence=1,
                    kind=kind,
                    repository_id=command.repository_id,
                    run_id=command.run_id,
                    detail=command.reason.strip(),
                ),
            )
        affected = [
            item
            for item in change_set.repositories
            if command.repository_id is None or item.repository_id == command.repository_id
        ]
        actions: list[RecoveryAction] = []
        for item in sorted(affected, key=lambda value: value.merge_order, reverse=True):
            if item.status is RepositoryDeliveryStatus.MERGED:
                actions.append(
                    RecoveryAction(
                        sequence=len(actions) + 1,
                        kind=RecoveryActionKind.CREATE_REVERT_PULL_REQUEST,
                        repository_id=item.repository_id,
                        run_id=None,
                        detail="Create a revert PR; force-push rollback is forbidden.",
                    )
                )
                kind = RecoveryActionKind.MERGE_REVERT_PULL_REQUEST
                detail = "Merge the approved revert PR after required CI checks pass."
            elif item.pull_request_number is not None:
                kind = RecoveryActionKind.CLOSE_PULL_REQUEST
                detail = "Close the unmerged PR and retain its evidence."
            else:
                continue
            actions.append(
                RecoveryAction(
                    sequence=len(actions) + 1,
                    kind=kind,
                    repository_id=item.repository_id,
                    run_id=None,
                    detail=detail,
                )
            )
        actions.append(
            RecoveryAction(
                sequence=len(actions) + 1,
                kind=RecoveryActionKind.REVALIDATE_CHANGESET,
                repository_id=None,
                run_id=None,
                detail="Create a new validation snapshot before delivery resumes.",
            )
        )
        return tuple(actions)

    @staticmethod
    def _fingerprint(command: PrepareChangeSetCommand) -> str:
        raw = asdict(command)
        # Provenance can be back-filled after a stranded publish without
        # changing the frozen delivery candidate or its idempotency identity.
        for candidate in raw["candidates"]:
            candidate.pop("plan_id", None)
            candidate.pop("run_id", None)
            candidate.pop("worker_agent_id", None)
        payload = json.dumps(raw, default=str, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


class DeliveryGovernanceService:
    """API-facing governance recording bound to a delivery (execution plan)."""

    def __init__(
        self,
        delivery: DeliveryService,
        directory: AgentPrincipalReader,
        audit: DeliveryAuditLog,
    ) -> None:
        self._delivery = delivery
        self._directory = directory
        self._audit = audit

    async def record(
        self,
        delivery_id: UUID,
        command: RecordGovernanceDecisionCommand,
        *,
        idempotency_key: str,
    ) -> GovernanceDecisionView:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        change_set = await self._delivery.get_by_idempotency_key(
            delivery_change_set_key(delivery_id)
        )
        if change_set is None:
            raise DeliveryNotFound(f"delivery has no ChangeSet: {delivery_id}")
        if change_set.id != command.change_set_id:
            raise DeliveryConflict("change set does not belong to this delivery")
        actor = await self._authorized_actor(command, change_set)
        head_sha = command.head_sha.strip().lower()
        replayed = self._latest_decision(change_set, command.repository_id, head_sha)
        if replayed is not None and self._same_decision(replayed, command):
            return replayed
        updated = await self._delivery.record_governance_decision(command)
        recorded = self._latest_decision(updated, command.repository_id, head_sha)
        if recorded is None:  # pragma: no cover - record_governance guarantees presence
            raise DeliveryNotFound("governance decision was not recorded")
        await self._audit.append(
            EventEnvelope(
                event_type="DeliveryGovernanceDecisionRecorded",
                actor_type=ActorType.AGENT,
                actor_id=str(actor.id),
                aggregate_type="ChangeSet",
                aggregate_id=updated.id,
                aggregate_version=updated.version,
                correlation_id=new_id(),
                organization_id=updated.organization_id,
                project_id=updated.project_id,
                payload={
                    "deliveryId": str(delivery_id),
                    "repositoryId": str(command.repository_id),
                    "headSha": head_sha,
                    "decision": command.decision.value,
                    "reason": command.reason.strip(),
                    "idempotencyKey": idempotency_key.strip(),
                },
            )
        )
        return recorded

    async def _authorized_actor(
        self, command: RecordGovernanceDecisionCommand, change_set: ChangeSetView
    ):
        actor = await self._directory.get_view(command.decided_by_agent_id)
        if actor is None or actor.status is not AgentPrincipalStatus.ACTIVE:
            raise DeliveryDenied("governance decisions require an active agent principal")
        if actor.organization_id != change_set.organization_id:
            raise DeliveryDenied("governance agent belongs to another organization")
        if actor.role is AgentRole.ORGANIZATION_LEADER:
            return actor
        if (
            actor.role is AgentRole.REPOSITORY_LEADER
            and actor.repository_id == command.repository_id
        ):
            return actor
        raise DeliveryDenied("governance decisions require a leader for this repository")

    @staticmethod
    def _latest_decision(
        change_set: ChangeSetView, repository_id: UUID, head_sha: str
    ) -> GovernanceDecisionView | None:
        return next(
            (
                item
                for item in reversed(change_set.governance_decisions)
                if item.repository_id == repository_id and item.head_sha == head_sha
            ),
            None,
        )

    @staticmethod
    def _same_decision(
        existing: GovernanceDecisionView, command: RecordGovernanceDecisionCommand
    ) -> bool:
        return (
            existing.decision is command.decision
            and existing.decided_by_agent_id == command.decided_by_agent_id
            and existing.reason == command.reason.strip()
        )


_TERMINAL_RECOVERY_STATUSES = frozenset(
    {RecoveryActionStatus.SUCCEEDED, RecoveryActionStatus.SKIPPED}
)


class DeliveryRollbackService:
    """Console face for rolling a whole ChangeSet back (GUI batch E-1).

    The console does not execute anything. It records the human decision and
    lets the machine take over, which is two writes that belong together:

    1. one head-bound ROLLBACK_REQUIRED governance decision per candidate,
       which is what actually closes the merge gate (see
       ``evaluate_merge_gate``) so nothing else merges while the rollback runs;
    2. one OPERATOR_REQUESTED recovery plan, whose actions the recovery Saga
       picks up on its next interval.

    Doing this from the browser as N+1 separate calls would leave the gate half
    closed whenever one of them failed, and would put the reverse-merge-order
    rule in the front end. Hence one endpoint, one service, one audit event.

    This is not a promise of a clean restore: revert PRs still have to pass
    their own CI, and a conflicting revert is handed to a Worker task. The
    console's wording says so; this service only makes sure the wording is not
    contradicted by the machine.
    """

    def __init__(
        self,
        delivery: DeliveryService,
        directory: AgentPrincipalReader,
        audit: DeliveryAuditLog,
    ) -> None:
        self._delivery = delivery
        self._directory = directory
        self._audit = audit

    async def request(
        self,
        delivery_id: UUID,
        command: RequestChangeSetRollbackCommand,
        *,
        idempotency_key: str,
    ) -> ChangeSetRollbackView:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        reason = command.reason.strip()
        if not reason:
            raise ValueError("reason is required")
        change_set = await self._delivery.get_by_idempotency_key(
            delivery_change_set_key(delivery_id)
        )
        if change_set is None:
            raise DeliveryNotFound(f"delivery has no ChangeSet: {delivery_id}")
        if change_set.id != command.change_set_id:
            raise DeliveryConflict("change set does not belong to this delivery")
        actor = await self._authorized_actor(command, change_set)

        # Decide what to do with the recovery plan *before* writing any
        # decision: a request that ends in 409 must not leave the merge gate
        # closed by decisions whose plan never got created.
        existing = change_set.recovery_plans[-1] if change_set.recovery_plans else None
        replaying = (
            existing is not None
            and existing.trigger is RecoveryTrigger.OPERATOR_REQUESTED
            and existing.reason == reason
        )
        if not replaying and existing is not None and self._incomplete(existing):
            raise DeliveryConflict(
                "a recovery plan is already running for this ChangeSet; "
                "wait for it to finish or resolve it before requesting another rollback"
            )

        candidates = tuple(sorted(change_set.repositories, key=lambda item: item.merge_order))
        wrote_decision = False
        for candidate in candidates:
            # §4.4's decisions are stored against the lower-cased head; look up
            # and write against the same form so a replay is recognised.
            head = candidate.commit_sha.strip().lower()
            recorded = self._latest_decision(change_set, candidate.repository_id, head)
            if (
                recorded is not None
                and recorded.decision is GovernanceDecisionKind.ROLLBACK_REQUIRED
                and recorded.decided_by_agent_id == actor.id
                and recorded.reason == reason
            ):
                continue
            change_set = await self._delivery.record_governance_decision(
                RecordGovernanceDecisionCommand(
                    change_set_id=change_set.id,
                    repository_id=candidate.repository_id,
                    head_sha=head,
                    decision=GovernanceDecisionKind.ROLLBACK_REQUIRED,
                    decided_by_agent_id=actor.id,
                    reason=reason,
                )
            )
            wrote_decision = True

        if replaying:
            plan = change_set.recovery_plans[-1]
        else:
            change_set = await self._delivery.plan_recovery(
                PlanRecoveryCommand(
                    change_set_id=change_set.id,
                    trigger=RecoveryTrigger.OPERATOR_REQUESTED,
                    reason=reason,
                )
            )
            plan = change_set.recovery_plans[-1]

        decisions = tuple(
            decision
            for candidate in candidates
            if (
                decision := self._latest_decision(
                    change_set,
                    candidate.repository_id,
                    candidate.commit_sha.strip().lower(),
                )
            )
            is not None
        )
        replayed = replaying and not wrote_decision
        if not replayed:
            await self._audit.append(
                EventEnvelope(
                    event_type="DeliveryRollbackRequested",
                    actor_type=ActorType.AGENT,
                    actor_id=str(actor.id),
                    aggregate_type="ChangeSet",
                    aggregate_id=change_set.id,
                    aggregate_version=change_set.version,
                    correlation_id=new_id(),
                    organization_id=change_set.organization_id,
                    project_id=change_set.project_id,
                    payload={
                        "deliveryId": str(delivery_id),
                        "recoveryPlanId": str(plan.id),
                        "repositoryIds": [str(item.repository_id) for item in candidates],
                        "reason": reason,
                        "idempotencyKey": idempotency_key.strip(),
                    },
                )
            )
        return ChangeSetRollbackView(
            delivery_id=delivery_id,
            change_set_id=change_set.id,
            decisions=decisions,
            recovery_plan=plan,
            replayed=replayed,
        )

    async def _authorized_actor(
        self, command: RequestChangeSetRollbackCommand, change_set: ChangeSetView
    ):
        actor = await self._directory.get_view(command.requested_by_agent_id)
        if actor is None or actor.status is not AgentPrincipalStatus.ACTIVE:
            raise DeliveryDenied("rollback requires an active agent principal")
        if actor.organization_id != change_set.organization_id:
            raise DeliveryDenied("rollback agent belongs to another organization")
        # No repository-leader branch here, unlike §4.4's per-repository
        # decision: this command speaks for every repository in the set, and a
        # repository leader does not.
        if actor.role is not AgentRole.ORGANIZATION_LEADER:
            raise DeliveryDenied("whole-ChangeSet rollback requires an organization leader")
        return actor

    @staticmethod
    def _incomplete(plan: RecoveryPlanView) -> bool:
        return any(action.status not in _TERMINAL_RECOVERY_STATUSES for action in plan.actions)

    @staticmethod
    def _latest_decision(
        change_set: ChangeSetView, repository_id: UUID, head_sha: str
    ) -> GovernanceDecisionView | None:
        return next(
            (
                item
                for item in reversed(change_set.governance_decisions)
                if item.repository_id == repository_id and item.head_sha == head_sha
            ),
            None,
        )


class DeliveryArchiveService:
    """Archive an inactive delivery; archived deliveries keep all data."""

    def __init__(
        self,
        archives: DeliveryArchiveStore,
        delivery: DeliveryService,
        plans: ExecutionPlanStatusReader,
        audit: DeliveryAuditLog,
    ) -> None:
        self._archives = archives
        self._delivery = delivery
        self._plans = plans
        self._audit = audit

    async def archive(self, delivery_id: UUID) -> DeliveryArchiveView:
        existing = await self._archives.get(delivery_id)
        if existing is not None:
            return existing
        plan = await self._plans.get_view(delivery_id)
        if plan is None:
            raise DeliveryNotFound(f"delivery not found: {delivery_id}")
        if plan.status is ExecutionPlanStatus.IN_PROGRESS:
            raise DeliveryConflict("an in-progress delivery cannot be archived")
        change_set = await self._delivery.get_by_idempotency_key(
            delivery_change_set_key(delivery_id)
        )
        if change_set is not None:
            plan_failed = plan.status is ExecutionPlanStatus.FAILED
            self._require_terminal(change_set, plan_failed=plan_failed)
        archive = DeliveryArchiveView(delivery_id=delivery_id, archived_at=datetime.now(UTC))
        try:
            await self._archives.add(archive)
        except DeliveryConflict:
            stored = await self._archives.get(delivery_id)
            if stored is not None:
                return stored
            raise
        await self._audit.append(
            EventEnvelope(
                event_type="DeliveryArchived",
                actor_type=ActorType.SERVICE,
                actor_id="repomesh-api",
                aggregate_type="ExecutionPlan",
                aggregate_id=delivery_id,
                aggregate_version=plan.current_batch_index + 1,
                correlation_id=new_id(),
                organization_id=plan.organization_id,
                project_id=plan.project_id,
                payload={
                    "planStatus": plan.status.value,
                    "changeSetId": str(change_set.id) if change_set is not None else None,
                    "changeSetStatus": (
                        change_set.status.value if change_set is not None else None
                    ),
                },
            )
        )
        return archive

    @staticmethod
    def _require_terminal(change_set: ChangeSetView, *, plan_failed: bool) -> None:
        if change_set.status in {ChangeSetStatus.DELIVERED, ChangeSetStatus.COMPENSATED}:
            return
        failed = plan_failed or change_set.status is ChangeSetStatus.MANUAL_INTERVENTION
        if failed and not DeliveryArchiveService._has_active_recovery(change_set):
            return
        raise DeliveryConflict(
            f"an active delivery cannot be archived (ChangeSet is {change_set.status.value})"
        )

    @staticmethod
    def _has_active_recovery(change_set: ChangeSetView) -> bool:
        if not change_set.recovery_plans:
            return False
        active = change_set.recovery_plans[-1]
        return any(
            action.status not in {RecoveryActionStatus.SUCCEEDED, RecoveryActionStatus.SKIPPED}
            for action in active.actions
        )
