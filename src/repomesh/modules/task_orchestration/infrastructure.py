from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    delete,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.modules.task_orchestration.contracts import (
    AcceptedPlanView,
    AcceptedReviewView,
    ExecutionPlanStatus,
    LeaderAssignmentPhase,
    LeaderAssignmentView,
    LeaderReviewEvidenceView,
    LeaderSafetyEnvelopeView,
    TaskOrigin,
    TaskStatus,
    TaskTestResultView,
    WorkerEvidenceView,
    WorkerRosterEntryView,
)
from repomesh.modules.task_orchestration.domain import (
    DeliveryRefusal,
    ExecutionPlan,
    PlannedRepositoryTask,
    Task,
    TaskConflict,
)
from repomesh.persistence import Database
from repomesh.persistence.base import Base

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


def _as_utc(value: datetime) -> datetime:
    """A stored timestamp read back as an aware one.

    SQLite (the test variant) hands back naive datetimes and an ISO string
    written without an offset parses naive, so the tz is restored rather than
    assumed downstream.
    """

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class TaskRecord(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_tasks_idempotency_key"),
        {"schema": "task_orchestration"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    repository_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    parent_task_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    assigned_by_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    assignee_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    title: Mapped[str] = mapped_column(String(500))
    instruction: Mapped[str] = mapped_column(Text)
    acceptance: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    status: Mapped[str] = mapped_column(String(30), index=True)
    result_summary: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(String(30), server_default=TaskOrigin.PLANNED.value)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_fingerprint: Mapped[str] = mapped_column(String(71))


class ExecutionPlanRecord(Base):
    __tablename__ = "execution_plans"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_execution_plans_idempotency_key"),
        {"schema": "task_orchestration"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    created_by_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    current_batch_index: Mapped[int] = mapped_column(Integer)
    batches: Mapped[list[list[dict[str, object]]]] = mapped_column(JSON_DOCUMENT)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivery_refusal: Mapped[dict[str, object] | None] = mapped_column(
        JSON_DOCUMENT, nullable=True, default=None
    )
    """Defect A-19: the delivering side's last stated refusal for this batch.

    NULL means delivery has made no complaint about the current batch — which
    is not the same as an empty object, and is the value a resolved refusal
    returns to.
    """


class ExecutionPlanTaskRecord(Base):
    """Indexed mapping from an assigned repository-leader task back to its plan."""

    __tablename__ = "execution_plan_tasks"
    __table_args__ = ({"schema": "task_orchestration"},)

    leader_task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    plan_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    batch_index: Mapped[int] = mapped_column(Integer)


class LeaderAssignmentRecord(Base):
    """One repository batch item parked for an external Repository Leader.

    Keyed by the leader task id, which is also the leader-actions surface's
    only path parameter: there is exactly one assignment per leader task, and
    a surrogate key would only add a second way to name the same row.

    The envelope and the roster are stored as documents rather than as tables
    of their own because nothing queries inside them — they are read back
    whole, by the one key above, to be handed to a leader verbatim.
    """

    __tablename__ = "leader_assignments"
    __table_args__ = ({"schema": "task_orchestration"},)

    leader_task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    repository_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    leader_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    phase: Mapped[str] = mapped_column(String(20), index=True)
    safety_envelope: Mapped[dict[str, object]] = mapped_column(JSON_DOCUMENT)
    worker_roster: Mapped[list[dict[str, object]]] = mapped_column(JSON_DOCUMENT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: The state machine's memory, added by revision 0040. Documents rather
    #: than tables for the reason the envelope and roster already are: nothing
    #: queries inside them. They are read back whole, by the key above, to
    #: decide one assignment's next transition and to replay one receipt.
    review_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1", default=1
    )
    accepted_plan: Mapped[dict[str, object] | None] = mapped_column(
        JSON_DOCUMENT, nullable=True, default=None
    )
    review_evidence: Mapped[dict[str, object] | None] = mapped_column(
        JSON_DOCUMENT, nullable=True, default=None
    )
    accepted_reviews: Mapped[list[dict[str, object]]] = mapped_column(
        JSON_DOCUMENT, nullable=False, server_default="[]", default=list
    )


def _encode_leader_assignment(assignment: LeaderAssignmentView) -> dict[str, object]:
    """The two JSON documents, shared by both adapters.

    Both stores encode identically so the behaviour test that runs one suite
    over the pair is comparing the same values, not two dialects of them.
    """

    return {
        "safety_envelope": {
            "allowed_path_roots": list(assignment.safety_envelope.allowed_path_roots),
            "test_paths": list(assignment.safety_envelope.test_paths),
            "test_commands": list(assignment.safety_envelope.test_commands),
        },
        "worker_roster": [
            {
                "worker_agent_id": str(entry.worker_agent_id),
                "worker_name": entry.worker_name,
                "responsibility_paths": list(entry.responsibility_paths),
            }
            for entry in assignment.worker_roster
        ],
        "review_revision": assignment.review_revision,
        "accepted_plan": (
            None
            if assignment.accepted_plan is None
            else {
                "fingerprint": assignment.accepted_plan.fingerprint,
                "plan_revision": assignment.accepted_plan.plan_revision,
                "worker_task_ids": [
                    str(task_id) for task_id in assignment.accepted_plan.worker_task_ids
                ],
                "decision": assignment.accepted_plan.decision,
            }
        ),
        "review_evidence": (
            None
            if assignment.review_evidence is None
            else {
                "review_revision": assignment.review_evidence.review_revision,
                "worker_evidence": [
                    {
                        "worker_task_id": str(item.worker_task_id),
                        "worker_agent_id": str(item.worker_agent_id),
                        "status": item.status.value,
                        "run_id": str(item.run_id) if item.run_id is not None else None,
                        "commit_sha": item.commit_sha,
                        "changed_files": list(item.changed_files),
                        "test_results": [
                            {
                                "command": result.command,
                                "exit_code": result.exit_code,
                                "summary": result.summary,
                            }
                            for result in item.test_results
                        ],
                        "diff_stat": item.diff_stat,
                        "summary": item.summary,
                    }
                    for item in assignment.review_evidence.worker_evidence
                ],
            }
        ),
        "accepted_reviews": [
            {
                "fingerprint": item.fingerprint,
                "verdict": item.verdict,
                "review_revision": item.review_revision,
                "leader_task_status": item.leader_task_status,
                "rework_task_ids": [str(task_id) for task_id in item.rework_task_ids],
            }
            for item in assignment.accepted_reviews
        ],
    }


def _decode_leader_assignment(record: LeaderAssignmentRecord) -> LeaderAssignmentView:
    envelope = record.safety_envelope
    plan = record.accepted_plan
    evidence = record.review_evidence
    return LeaderAssignmentView(
        leader_task_id=record.leader_task_id,
        organization_id=record.organization_id,
        project_id=record.project_id,
        repository_id=record.repository_id,
        leader_agent_id=record.leader_agent_id,
        phase=LeaderAssignmentPhase(record.phase),
        safety_envelope=LeaderSafetyEnvelopeView(
            allowed_path_roots=tuple(envelope["allowed_path_roots"]),
            test_paths=tuple(envelope["test_paths"]),
            test_commands=tuple(envelope["test_commands"]),
        ),
        worker_roster=tuple(
            WorkerRosterEntryView(
                worker_agent_id=UUID(str(entry["worker_agent_id"])),
                worker_name=str(entry["worker_name"]),
                responsibility_paths=tuple(entry["responsibility_paths"]),
            )
            for entry in record.worker_roster
        ),
        # ``or`` rather than a bare read on each: a row written before revision
        # 0040 carries None in columns the model declares NOT NULL, and a
        # pre-0040 assignment is by definition one with no plan, no evidence
        # and no verdicts — which is what these defaults say.
        review_revision=record.review_revision or 1,
        accepted_plan=(
            None
            if plan is None
            else AcceptedPlanView(
                fingerprint=str(plan["fingerprint"]),
                plan_revision=int(plan["plan_revision"]),
                worker_task_ids=tuple(UUID(str(item)) for item in plan["worker_task_ids"]),
                decision=dict(plan["decision"]),
            )
        ),
        review_evidence=(
            None
            if evidence is None
            else LeaderReviewEvidenceView(
                review_revision=int(evidence["review_revision"]),
                worker_evidence=tuple(
                    WorkerEvidenceView(
                        worker_task_id=UUID(str(item["worker_task_id"])),
                        worker_agent_id=UUID(str(item["worker_agent_id"])),
                        status=TaskStatus(str(item["status"])),
                        run_id=(
                            UUID(str(item["run_id"])) if item["run_id"] is not None else None
                        ),
                        commit_sha=item["commit_sha"],
                        changed_files=tuple(item["changed_files"]),
                        test_results=tuple(
                            TaskTestResultView(
                                command=str(result["command"]),
                                exit_code=int(result["exit_code"]),
                                summary=str(result.get("summary") or ""),
                            )
                            for result in item["test_results"]
                        ),
                        diff_stat=item["diff_stat"],
                        summary=item["summary"],
                    )
                    for item in evidence["worker_evidence"]
                ),
            )
        ),
        accepted_reviews=tuple(
            AcceptedReviewView(
                fingerprint=str(item["fingerprint"]),
                verdict=str(item["verdict"]),
                review_revision=int(item["review_revision"]),
                leader_task_status=str(item["leader_task_status"]),
                rework_task_ids=tuple(UUID(str(task)) for task in item["rework_task_ids"]),
            )
            for item in (record.accepted_reviews or ())
        ),
    )


class InMemoryLeaderAssignmentStore:
    def __init__(self) -> None:
        self.assignments: dict[UUID, LeaderAssignmentView] = {}

    async def ensure(self, assignment: LeaderAssignmentView) -> LeaderAssignmentView:
        return self.assignments.setdefault(assignment.leader_task_id, assignment)

    async def get(self, leader_task_id: UUID) -> LeaderAssignmentView | None:
        return self.assignments.get(leader_task_id)

    async def save(self, assignment: LeaderAssignmentView) -> None:
        if assignment.leader_task_id not in self.assignments:
            raise TaskConflict("leader assignment does not exist")
        self.assignments[assignment.leader_task_id] = assignment


class PostgresLeaderAssignmentStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def ensure(self, assignment: LeaderAssignmentView) -> LeaderAssignmentView:
        """Write the row, or read back the one that is already there.

        The read-then-write race is settled by the primary key rather than by
        the check: two concurrent parks of the same leader task both see no
        row, one INSERT wins and the loser's ``IntegrityError`` is turned into
        the same read the fast path takes. So a replay and a race land on the
        same record, which is the property the frozen envelope depends on.
        """

        existing = await self.get(assignment.leader_task_id)
        if existing is not None:
            return existing
        try:
            async with self._database.transaction() as session:
                session.add(
                    LeaderAssignmentRecord(
                        leader_task_id=assignment.leader_task_id,
                        organization_id=assignment.organization_id,
                        project_id=assignment.project_id,
                        repository_id=assignment.repository_id,
                        leader_agent_id=assignment.leader_agent_id,
                        phase=assignment.phase.value,
                        created_at=datetime.now(UTC),
                        **_encode_leader_assignment(assignment),
                    )
                )
        except IntegrityError:
            stored = await self.get(assignment.leader_task_id)
            if stored is None:  # pragma: no cover - the row must exist to conflict
                raise
            return stored
        return assignment

    async def get(self, leader_task_id: UUID) -> LeaderAssignmentView | None:
        async with self._database.transaction() as session:
            record = await session.get(LeaderAssignmentRecord, leader_task_id)
            return _decode_leader_assignment(record) if record is not None else None

    async def save(self, assignment: LeaderAssignmentView) -> None:
        """Write a transition onto the row that already exists.

        Rewrites the whole record rather than patching the changed columns:
        every caller holds a full ``LeaderAssignmentView`` it derived from the
        stored one, so a partial update could only ever create a row that is a
        blend of two states.
        """

        async with self._database.transaction() as session:
            record = await session.get(LeaderAssignmentRecord, assignment.leader_task_id)
            if record is None:
                raise TaskConflict("leader assignment does not exist")
            record.phase = assignment.phase.value
            for column, value in _encode_leader_assignment(assignment).items():
                setattr(record, column, value)


class InMemoryTaskStore:
    def __init__(self) -> None:
        self.tasks: dict[UUID, Task] = {}
        self.idempotency: dict[str, tuple[UUID, str]] = {}

    async def add(
        self,
        task: Task,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> None:
        if task.id in self.tasks or idempotency_key in self.idempotency:
            raise TaskConflict("task already exists")
        self.tasks[task.id] = task
        self.idempotency[idempotency_key] = (task.id, request_fingerprint)

    async def get(self, task_id: UUID) -> Task | None:
        return self.tasks.get(task_id)

    async def get_view(self, task_id: UUID):
        task = await self.get(task_id)
        return task.to_view() if task is not None else None

    async def get_by_idempotency_key(self, idempotency_key: str) -> tuple[Task, str] | None:
        binding = self.idempotency.get(idempotency_key)
        if binding is None:
            return None
        task_id, fingerprint = binding
        return self.tasks[task_id], fingerprint

    async def assignment_key(self, task_id: UUID) -> str | None:
        for key, (bound_id, _) in self.idempotency.items():
            if bound_id == task_id:
                return key
        return None

    async def update(self, task: Task, *, expected_version: int) -> None:
        current = self.tasks.get(task.id)
        if current is None or current.version != expected_version:
            raise TaskConflict("task version changed")
        self.tasks[task.id] = task

    async def list_by_project(self, project_id: UUID) -> tuple[Task, ...]:
        return tuple(task for task in self.tasks.values() if task.project_id == project_id)

    async def list_all(self) -> tuple[Task, ...]:
        return tuple(self.tasks.values())

    async def list_by_parent(self, parent_task_id: UUID) -> tuple[Task, ...]:
        return tuple(task for task in self.tasks.values() if task.parent_task_id == parent_task_id)


class InMemoryExecutionPlanStore:
    def __init__(self) -> None:
        self.plans: dict[UUID, ExecutionPlan] = {}
        self.idempotency: dict[str, UUID] = {}

    async def add(self, plan: ExecutionPlan, *, idempotency_key: str) -> None:
        if plan.id in self.plans or idempotency_key in self.idempotency:
            raise TaskConflict("execution plan already exists")
        self.plans[plan.id] = plan
        self.idempotency[idempotency_key] = plan.id

    async def get(self, plan_id: UUID) -> ExecutionPlan | None:
        return self.plans.get(plan_id)

    async def get_by_idempotency_key(self, idempotency_key: str) -> ExecutionPlan | None:
        plan_id = self.idempotency.get(idempotency_key)
        return self.plans.get(plan_id) if plan_id is not None else None

    async def update(self, plan: ExecutionPlan, *, expected_version: int) -> None:
        current = self.plans.get(plan.id)
        if current is None or current.version != expected_version:
            raise TaskConflict("execution plan version changed")
        self.plans[plan.id] = plan

    async def find_by_leader_task(self, leader_task_id: UUID) -> ExecutionPlan | None:
        for plan in self.plans.values():
            for batch in plan.batches:
                if any(planned.leader_task_id == leader_task_id for planned in batch):
                    return plan
        return None

    async def list_all(self) -> tuple[ExecutionPlan, ...]:
        return tuple(self.plans.values())


class PostgresTaskStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add(
        self,
        task: Task,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> None:
        try:
            async with self._database.transaction() as session:
                session.add(
                    TaskRecord(
                        **self._values(task),
                        idempotency_key=idempotency_key,
                        request_fingerprint=request_fingerprint,
                    )
                )
        except IntegrityError as error:
            raise TaskConflict("task already exists") from error

    async def get(self, task_id: UUID) -> Task | None:
        async with self._database.transaction() as session:
            record = await session.get(TaskRecord, task_id)
        return self._to_domain(record) if record is not None else None

    async def get_view(self, task_id: UUID):
        task = await self.get(task_id)
        return task.to_view() if task is not None else None

    async def get_by_idempotency_key(self, idempotency_key: str) -> tuple[Task, str] | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(TaskRecord).where(TaskRecord.idempotency_key == idempotency_key)
            )
        if record is None:
            return None
        return self._to_domain(record), record.request_fingerprint

    async def assignment_key(self, task_id: UUID) -> str | None:
        async with self._database.transaction() as session:
            return await session.scalar(
                select(TaskRecord.idempotency_key).where(TaskRecord.id == task_id)
            )

    async def update(self, task: Task, *, expected_version: int) -> None:
        async with self._database.transaction() as session:
            result = await session.execute(
                update(TaskRecord)
                .where(TaskRecord.id == task.id, TaskRecord.version == expected_version)
                .values(
                    status=task.status.value,
                    result_summary=task.result_summary,
                    version=task.version,
                )
            )
            if result.rowcount != 1:
                raise TaskConflict("task version changed")

    async def list_by_project(self, project_id: UUID) -> tuple[Task, ...]:
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(TaskRecord)
                    .where(TaskRecord.project_id == project_id)
                    .order_by(TaskRecord.id)
                )
            ).all()
        return tuple(self._to_domain(record) for record in records)

    async def list_all(self) -> tuple[Task, ...]:
        """Every task; the read model's repository grid and roster count across
        projects, and querying project by project would be one round trip each."""

        async with self._database.transaction() as session:
            records = (
                await session.scalars(select(TaskRecord).order_by(TaskRecord.id))
            ).all()
        return tuple(self._to_domain(record) for record in records)

    async def list_by_parent(self, parent_task_id: UUID) -> tuple[Task, ...]:
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(TaskRecord)
                    .where(TaskRecord.parent_task_id == parent_task_id)
                    .order_by(TaskRecord.id)
                )
            ).all()
        return tuple(self._to_domain(record) for record in records)

    @staticmethod
    def _values(task: Task) -> dict[str, object]:
        return {
            "id": task.id,
            "organization_id": task.organization_id,
            "project_id": task.project_id,
            "repository_id": task.repository_id,
            "parent_task_id": task.parent_task_id,
            "assigned_by_agent_id": task.assigned_by_agent_id,
            "assignee_agent_id": task.assignee_agent_id,
            "title": task.title,
            "instruction": task.instruction,
            "acceptance": list(task.acceptance),
            "status": task.status.value,
            "result_summary": task.result_summary,
            "version": task.version,
            "origin": task.origin.value,
        }

    @staticmethod
    def _to_domain(record: TaskRecord) -> Task:
        return Task(
            id=record.id,
            organization_id=record.organization_id,
            project_id=record.project_id,
            repository_id=record.repository_id,
            parent_task_id=record.parent_task_id,
            assigned_by_agent_id=record.assigned_by_agent_id,
            assignee_agent_id=record.assignee_agent_id,
            title=record.title,
            instruction=record.instruction,
            acceptance=tuple(record.acceptance),
            status=TaskStatus(record.status),
            result_summary=record.result_summary,
            version=record.version,
            # Rows written before origin existed read back as the column
            # default; the migration backfills the rework ones.
            origin=TaskOrigin(record.origin or TaskOrigin.PLANNED.value),
        )


class PostgresExecutionPlanStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add(self, plan: ExecutionPlan, *, idempotency_key: str) -> None:
        now = datetime.now(UTC)
        try:
            async with self._database.transaction() as session:
                session.add(
                    ExecutionPlanRecord(
                        id=plan.id,
                        organization_id=plan.organization_id,
                        project_id=plan.project_id,
                        created_by_agent_id=plan.created_by_agent_id,
                        status=plan.status.value,
                        current_batch_index=plan.current_batch_index,
                        batches=self._encode(plan),
                        idempotency_key=idempotency_key,
                        version=plan.version,
                        created_at=now,
                        updated_at=now,
                        delivery_refusal=self._encode_refusal(plan),
                    )
                )
                await self._sync_leader_tasks(session, plan)
        except IntegrityError as error:
            raise TaskConflict("execution plan already exists") from error

    async def get(self, plan_id: UUID) -> ExecutionPlan | None:
        async with self._database.transaction() as session:
            record = await session.get(ExecutionPlanRecord, plan_id)
        return self._to_domain(record) if record is not None else None

    async def get_by_idempotency_key(self, idempotency_key: str) -> ExecutionPlan | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(ExecutionPlanRecord).where(
                    ExecutionPlanRecord.idempotency_key == idempotency_key
                )
            )
        return self._to_domain(record) if record is not None else None

    async def update(self, plan: ExecutionPlan, *, expected_version: int) -> None:
        async with self._database.transaction() as session:
            result = await session.execute(
                update(ExecutionPlanRecord)
                .where(
                    ExecutionPlanRecord.id == plan.id,
                    ExecutionPlanRecord.version == expected_version,
                )
                .values(
                    status=plan.status.value,
                    current_batch_index=plan.current_batch_index,
                    batches=self._encode(plan),
                    version=plan.version,
                    updated_at=datetime.now(UTC),
                    delivery_refusal=self._encode_refusal(plan),
                )
            )
            if result.rowcount != 1:
                raise TaskConflict("execution plan version changed")
            await self._sync_leader_tasks(session, plan)

    async def find_by_leader_task(self, leader_task_id: UUID) -> ExecutionPlan | None:
        async with self._database.transaction() as session:
            binding = await session.get(ExecutionPlanTaskRecord, leader_task_id)
            record = (
                await session.get(ExecutionPlanRecord, binding.plan_id)
                if binding is not None
                else None
            )
        return self._to_domain(record) if record is not None else None

    async def list_all(self) -> tuple[ExecutionPlan, ...]:
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(ExecutionPlanRecord).order_by(ExecutionPlanRecord.created_at)
                )
            ).all()
        return tuple(self._to_domain(record) for record in records)

    async def _sync_leader_tasks(self, session, plan: ExecutionPlan) -> None:
        assigned = {
            planned.leader_task_id: index
            for index, batch in enumerate(plan.batches)
            for planned in batch
            if planned.leader_task_id is not None
        }
        known = set(
            (
                await session.scalars(
                    select(ExecutionPlanTaskRecord.leader_task_id).where(
                        ExecutionPlanTaskRecord.plan_id == plan.id
                    )
                )
            ).all()
        )
        for leader_task_id, batch_index in assigned.items():
            if leader_task_id in known:
                continue
            session.add(
                ExecutionPlanTaskRecord(
                    leader_task_id=leader_task_id,
                    plan_id=plan.id,
                    batch_index=batch_index,
                )
            )
        stale = known - set(assigned)
        if stale:
            await session.execute(
                delete(ExecutionPlanTaskRecord).where(
                    ExecutionPlanTaskRecord.plan_id == plan.id,
                    ExecutionPlanTaskRecord.leader_task_id.in_(stale),
                )
            )

    @staticmethod
    def _encode(plan: ExecutionPlan) -> list[list[dict[str, object]]]:
        return [
            [
                {
                    "repository_id": str(planned.repository_id),
                    "title": planned.title,
                    "instruction": planned.instruction,
                    "acceptance": list(planned.acceptance),
                    "leader_task_id": (
                        str(planned.leader_task_id) if planned.leader_task_id is not None else None
                    ),
                    "tests": list(planned.tests),
                    "test_paths": list(planned.test_paths),
                    "depends_on": [str(item) for item in planned.depends_on],
                }
                for planned in batch
            ]
            for batch in plan.batches
        ]

    @staticmethod
    def _encode_refusal(plan: ExecutionPlan) -> dict[str, object] | None:
        refusal = plan.delivery_refusal
        if refusal is None:
            return None
        return {
            "reason": refusal.reason,
            "batch_index": refusal.batch_index,
            "at": refusal.at.isoformat(),
            "repository_id": (
                str(refusal.repository_id) if refusal.repository_id is not None else None
            ),
            "task_id": str(refusal.task_id) if refusal.task_id is not None else None,
        }

    @staticmethod
    def _decode_refusal(payload: dict[str, object] | None) -> DeliveryRefusal | None:
        if not payload:
            return None
        repository_id = payload.get("repository_id")
        task_id = payload.get("task_id")
        return DeliveryRefusal(
            reason=str(payload.get("reason") or ""),
            batch_index=int(payload.get("batch_index") or 0),
            at=_as_utc(datetime.fromisoformat(str(payload["at"]))),
            repository_id=UUID(str(repository_id)) if repository_id else None,
            task_id=UUID(str(task_id)) if task_id else None,
        )

    @staticmethod
    def _to_domain(record: ExecutionPlanRecord) -> ExecutionPlan:
        return ExecutionPlan(
            id=record.id,
            organization_id=record.organization_id,
            project_id=record.project_id,
            created_by_agent_id=record.created_by_agent_id,
            status=ExecutionPlanStatus(record.status),
            current_batch_index=record.current_batch_index,
            version=record.version,
            # Rows written before defect A-19 have no column value at all.
            delivery_refusal=PostgresExecutionPlanStore._decode_refusal(
                record.delivery_refusal
            ),
            batches=tuple(
                tuple(
                    PlannedRepositoryTask(
                        repository_id=UUID(str(planned["repository_id"])),
                        title=str(planned["title"]),
                        instruction=str(planned["instruction"]),
                        acceptance=tuple(planned["acceptance"]),
                        leader_task_id=(
                            UUID(str(planned["leader_task_id"]))
                            if planned["leader_task_id"] is not None
                            else None
                        ),
                        # Rows persisted before verification commands existed have no key.
                        tests=tuple(planned.get("tests") or ()),
                        test_paths=tuple(planned.get("test_paths") or ()),
                        depends_on=tuple(
                            UUID(str(item)) for item in planned.get("depends_on") or ()
                        ),
                    )
                    for planned in batch
                )
                for batch in record.batches
            ),
        )
