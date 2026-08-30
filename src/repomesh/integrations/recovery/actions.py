from repomesh.modules.agent_runtime.recovery import (
    PostgresWorkerRecoveryStore,
    WorkerRecoveryDecision,
    WorkerRecoveryState,
)
from repomesh.modules.delivery.conflicts import (
    DeliveryConflictCaseStatus,
    PostgresDeliveryConflictCaseStore,
)
from repomesh.modules.recovery_management.contracts import (
    RecoveryAction,
    RecoveryOperationView,
    RecoverySourceType,
)
from repomesh.modules.recovery_management.infrastructure import PostgresRecoveryCaseStore


class UnifiedRecoveryActionHandlers:
    def __init__(
        self,
        cases: PostgresRecoveryCaseStore,
        worker_recoveries: PostgresWorkerRecoveryStore,
        delivery_conflicts: PostgresDeliveryConflictCaseStore,
    ) -> None:
        self._cases = cases
        self._worker_recoveries = worker_recoveries
        self._delivery_conflicts = delivery_conflicts

    def handlers(self):
        return {
            RecoveryAction.RESUME_SESSION: self.resume_session,
            RecoveryAction.REASSIGN_WORKER: self.reassign_worker,
            RecoveryAction.CREATE_CONFLICT_TASK: self.retry,
            RecoveryAction.RETRY: self.retry,
            RecoveryAction.MANUAL_RESOLUTION: self.manual_resolution,
        }

    async def resume_session(self, operation: RecoveryOperationView) -> None:
        case = await self._required_case(operation)
        if case.source_type is not RecoverySourceType.WORKER_EXECUTION:
            raise ValueError("resume_session is only valid for Worker recovery")
        await self._worker_recoveries.retry(
            case.source_id, WorkerRecoveryDecision.RESUME
        )

    async def reassign_worker(self, operation: RecoveryOperationView) -> None:
        case = await self._required_case(operation)
        if case.source_type is not RecoverySourceType.WORKER_EXECUTION:
            raise ValueError("reassign_worker is only valid for Worker recovery")
        await self._worker_recoveries.retry(
            case.source_id, WorkerRecoveryDecision.REASSIGN
        )

    async def retry(self, operation: RecoveryOperationView) -> None:
        case = await self._required_case(operation)
        await self._cases.mark_automatic(case.id)

    async def manual_resolution(self, operation: RecoveryOperationView) -> None:
        case = await self._required_case(operation)
        resolved = False
        if case.source_type is RecoverySourceType.WORKER_EXECUTION:
            source = next(
                (
                    item for item in await self._worker_recoveries.list_all()
                    if item.id == case.source_id
                ),
                None,
            )
            resolved = source is not None and source.state is WorkerRecoveryState.COMPLETED
        elif case.source_type is RecoverySourceType.DELIVERY_CONFLICT:
            source = next(
                (
                    item for item in await self._delivery_conflicts.list_all()
                    if item.id == case.source_id
                ),
                None,
            )
            resolved = (
                source is not None
                and source.status is DeliveryConflictCaseStatus.RESOLVED
            )
        if not resolved:
            raise ValueError("source failure is not resolved")
        await self._cases.resolve_source(case.source_type, case.source_id)

    async def _required_case(self, operation: RecoveryOperationView):
        case = await self._cases.get(operation.case_id)
        if case is None:
            raise ValueError("recovery case does not exist")
        return case
