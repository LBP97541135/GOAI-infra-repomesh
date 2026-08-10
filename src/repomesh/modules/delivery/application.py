import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

from .contracts import (
    ChangeSetStatus,
    ChangeSetView,
    CIObservationCommand,
    MergeGateDecision,
    MergeObservationCommand,
    PlanRecoveryCommand,
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RecordedSCMObservation,
    RecordMergeRequestedCommand,
    RecordRecoveryActionCommand,
    RecordSCMObservationCommand,
    RecoveryActionKind,
    RecoveryActionStatus,
    RecoveryTrigger,
    RepositoryDeliveryStatus,
    ReviewObservationCommand,
    SCMObservationView,
)
from .domain import (
    ChangeSet,
    DeliveryConflict,
    DeliveryNotFound,
    RecoveryAction,
    RecoveryPlan,
    RepositoryDelivery,
    SCMObservation,
    SCMPollCursor,
)
from .ports import ChangeSetStore, SCMObservationStore, SCMPollCursorStore


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
    def __init__(self, store: ChangeSetStore) -> None:
        self._store = store

    async def prepare(
        self, command: PrepareChangeSetCommand, *, idempotency_key: str
    ) -> ChangeSetView:
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
        )
        await self._store.add(change_set, idempotency_key=idempotency_key, fingerprint=fingerprint)
        return change_set.to_view()

    async def observe_pull_request(self, command: PullRequestObservationCommand) -> ChangeSetView:
        return await self._update_repository(
            command.change_set_id,
            command.repository_id,
            lambda item: item.observe_pr(
                command.pull_request_number,
                command.pull_request_url,
                command.head_sha,
            ),
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

    async def evaluate_merge_gate(
        self, change_set_id: UUID, repository_id: UUID
    ) -> MergeGateDecision:
        change_set = await self._required(change_set_id)
        target = self._repository(change_set, repository_id)
        reasons: list[str] = []
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
        payload = json.dumps(asdict(command), default=str, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()
