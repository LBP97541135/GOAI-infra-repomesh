import asyncio
import contextlib
import logging

from repomesh.modules.agent_runtime.recovery import (
    PostgresWorkerRecoveryStore,
    WorkerRecoveryState,
)
from repomesh.modules.delivery.application import DeliveryService
from repomesh.modules.delivery.conflicts import (
    DeliveryConflictCaseStatus,
    PostgresDeliveryConflictCaseStore,
)
from repomesh.modules.delivery.contracts import RecoveryActionStatus
from repomesh.modules.project.contracts import HumanReviewStatus, ProjectTopologyReader
from repomesh.modules.project.ports import HumanReviewRequestStore
from repomesh.modules.recovery_management.contracts import (
    RecoveryAction,
    RecoveryCaseUpsert,
    RecoverySeverity,
    RecoverySourceType,
)
from repomesh.modules.recovery_management.infrastructure import PostgresRecoveryCaseStore
from repomesh.modules.task_orchestration.ports import TaskStore

logger = logging.getLogger(__name__)


class RecoverySourceProjector:
    def __init__(
        self,
        cases: PostgresRecoveryCaseStore,
        worker_recoveries: PostgresWorkerRecoveryStore,
        delivery_conflicts: PostgresDeliveryConflictCaseStore,
        tasks: TaskStore,
        delivery: DeliveryService | None = None,
        reviews: HumanReviewRequestStore | None = None,
        topologies: ProjectTopologyReader | None = None,
        *,
        interval_seconds: float = 10,
    ) -> None:
        self._cases = cases
        self._worker_recoveries = worker_recoveries
        self._delivery_conflicts = delivery_conflicts
        self._tasks = tasks
        self._delivery = delivery
        self._reviews = reviews
        self._topologies = topologies
        self._interval_seconds = interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def run_once(self) -> None:
        for recovery in await self._worker_recoveries.list_all():
            if recovery.state is WorkerRecoveryState.COMPLETED:
                await self._cases.resolve_source(
                    RecoverySourceType.WORKER_EXECUTION, recovery.id
                )
                continue
            task = await self._tasks.get(recovery.task_id)
            if task is None:
                continue
            await self._cases.ensure_case(
                RecoveryCaseUpsert(
                    source_type=RecoverySourceType.WORKER_EXECUTION,
                    source_id=recovery.id,
                    organization_id=task.organization_id,
                    project_id=task.project_id,
                    repository_id=task.repository_id,
                    task_id=task.id,
                    evidence_version=(
                        f"worker-recovery:{recovery.id}:g"
                        f"{recovery.assignment_generation or 0}:a{recovery.attempts}"
                    ),
                    summary=f"Worker execution recovery required: {recovery.reason}",
                    severity=RecoverySeverity.CRITICAL,
                    available_actions=(
                        RecoveryAction.RESUME_SESSION,
                        RecoveryAction.REASSIGN_WORKER,
                        RecoveryAction.RETRY,
                    ),
                    automatic=recovery.state in {
                        WorkerRecoveryState.PENDING,
                        WorkerRecoveryState.RUNNING,
                    },
                )
            )

        for conflict in await self._delivery_conflicts.list_all():
            if conflict.status is DeliveryConflictCaseStatus.RESOLVED:
                await self._cases.resolve_source(
                    RecoverySourceType.DELIVERY_CONFLICT, conflict.id
                )
                continue
            await self._cases.ensure_case(
                RecoveryCaseUpsert(
                    source_type=RecoverySourceType.DELIVERY_CONFLICT,
                    source_id=conflict.id,
                    organization_id=conflict.organization_id,
                    project_id=conflict.project_id,
                    repository_id=conflict.repository_id,
                    change_set_id=conflict.change_set_id,
                    task_id=conflict.repair_task_id,
                    evidence_version=(
                        f"delivery-conflict:{conflict.id}:v{conflict.version}:"
                        f"{conflict.candidate_head_sha}"
                    ),
                    summary=f"Delivery conflict requires recovery: {conflict.kind.value}",
                    severity=RecoverySeverity.CRITICAL,
                    available_actions=(
                        RecoveryAction.CREATE_CONFLICT_TASK,
                        RecoveryAction.RETRY,
                    ),
                    automatic=conflict.repair_task_id is not None,
                )
            )

        if self._delivery is not None:
            for change_set in await self._delivery.list_active():
                for plan in change_set.recovery_plans:
                    terminal = all(
                        action.status in {
                            RecoveryActionStatus.SUCCEEDED,
                            RecoveryActionStatus.SKIPPED,
                        }
                        for action in plan.actions
                    )
                    if terminal:
                        await self._cases.resolve_source(
                            RecoverySourceType.DELIVERY_RECOVERY, plan.id
                        )
                        continue
                    evidence = ":".join(
                        f"{action.id}={action.status.value}" for action in plan.actions
                    )
                    await self._cases.ensure_case(
                        RecoveryCaseUpsert(
                            source_type=RecoverySourceType.DELIVERY_RECOVERY,
                            source_id=plan.id,
                            organization_id=change_set.organization_id,
                            project_id=change_set.project_id,
                            change_set_id=change_set.id,
                            evidence_version=f"delivery-recovery:{plan.id}:{evidence}",
                            summary=f"ChangeSet recovery is in progress: {plan.reason}",
                            severity=RecoverySeverity.CRITICAL,
                            available_actions=(
                                RecoveryAction.RETRY,
                            ),
                            automatic=True,
                        )
                    )

        if self._reviews is not None and self._topologies is not None:
            for review in await self._reviews.list_all():
                if review.status is not HumanReviewStatus.PENDING:
                    await self._cases.resolve_source(
                        RecoverySourceType.HUMAN_REVIEW, review.id
                    )
                    continue
                topology = await self._topologies.get_view(review.project_id)
                if topology is None:
                    continue
                await self._cases.ensure_case(
                    RecoveryCaseUpsert(
                        source_type=RecoverySourceType.HUMAN_REVIEW,
                        source_id=review.id,
                        organization_id=topology.organization_id,
                        project_id=review.project_id,
                        repository_id=review.repository_id,
                        evidence_version=review.evidence_version,
                        summary=review.title,
                        severity=RecoverySeverity.WARNING,
                        available_actions=(),
                        automatic=False,
                    )
                )

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="recovery-source-projector")

    async def close(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("Unified Recovery Case projection failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), self._interval_seconds)
