"""Seed the delivery-console integration/demo database with three delivery shapes.

Purpose: give the delivery console (frontend/) real read-model data to render
during CONS-11/12 integration and the CONS-13 live demo, without touching any
production or long-running environment.

Shapes seeded (one project each):

A) delivered  — replica of the 2026-08-10 two-repository live delivery:
               api -> client dependency order, green CI, two head-bound READY
               governance decisions, both repositories merged. Carries a FROZEN
               ENGINEERING specification (including forbidden_paths) so the
               console's contract card renders.
B) release    — single repository READY_TO_MERGE with green CI but no READY
               governance decision: the decision deck shows one approve item,
               and approving it through POST /deliveries/{id}/governance-decisions
               flips the merge gate open. contract is null here (degraded path).
C) repairing  — CI failed candidate with a rework task in flight and a pending
               recovery plan: repairing/watch surfaces. contract is null.
D) draft      — an un-materialized plan snapshot only: the list shows a virtual
               draft delivery with delivery_id null and phase "plan".

Idempotency: every id derives from a fixed UUIDv5 namespace. If scenario A's
execution plan already exists the script prints the id map and exits without
writing anything, so re-running never duplicates seed data.

Configuration (no credentials are stored here; defaults follow the integration
convention of a throwaway postgres on 127.0.0.1:5533):

    python scripts/seed-console-demo.py [--database-url URL]
    REPOMESH_DATABASE_URL=... python scripts/seed-console-demo.py
"""

import argparse
import asyncio
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

from repomesh.modules.agent_directory.application import CreateAgent, CreateAgentRequest
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.agent_directory.infrastructure import PostgresAgentDirectory
from repomesh.modules.agent_runtime.runner_store import (
    RunnerDispatchRecord,
    RunnerEventRecord,
)
from repomesh.modules.collaboration.infrastructure import CollaborationMessageRecord
from repomesh.modules.delivery import (
    DeliveryService,
    PostgresChangeSetStore,
    delivery_change_set_key,
)
from repomesh.modules.delivery.contracts import (
    CIObservationCommand,
    GovernanceDecisionKind,
    MergeObservationCommand,
    PlanRecoveryCommand,
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RecordGovernanceDecisionCommand,
    RecordMergeRequestedCommand,
    RecoveryTrigger,
    RepositoryCandidateInput,
)
from repomesh.modules.delivery.infrastructure import SCMObservationRecord
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.repository_intelligence.infrastructure import (
    PostgresRepositoryCatalog,
)
from repomesh.modules.repository_intelligence.infrastructure.plan_snapshot_store import (
    PlanSnapshotStore,
)
from repomesh.modules.review_validation import (
    PostgresValidationSnapshotStore,
    ValidationSnapshotService,
)
from repomesh.modules.review_validation.contracts import (
    CreateValidationSnapshotCommand,
    ValidationTestInput,
)
from repomesh.modules.specification import PostgresSpecificationStore
from repomesh.modules.specification.contracts import (
    SpecificationKind,
    SpecificationStatus,
)
from repomesh.modules.specification.domain import (
    Specification,
    SpecificationContent,
    SpecificationVersion,
)
from repomesh.modules.task_orchestration.contracts import TaskStatus
from repomesh.modules.task_orchestration.domain import (
    ExecutionPlan,
    ExecutionPlanStatus,
    PlannedRepositoryTask,
    Task,
)
from repomesh.modules.task_orchestration.infrastructure import (
    PostgresExecutionPlanStore,
    PostgresTaskStore,
)
from repomesh.persistence import Database

DEFAULT_DATABASE_URL = "postgresql+asyncpg://repomesh:repomesh@127.0.0.1:5533/repomesh"

NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "repomesh://console-demo")
REWORK_TITLE = "Repair failed delivery candidate"

A_API_HEAD = "8deb466aff32990f7acf3858c61c045ebeaff335"
A_CLIENT_HEAD = "5fdafd67f25de54b1a67c16b1d7d7a7071030693"
B_HEAD = "c1a0b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3"
C_HEAD = "d4e5f60718293a4b5c6d7e8f90a1b2c3c1a0b2c3"
BASE = "9" * 40


def stable_id(name: str) -> UUID:
    return uuid.uuid5(NAMESPACE, name)


def evidence(name: str, head: str) -> str:
    return json.dumps(
        {
            "summary": "runner.completed",
            "changedFiles": ["src/pricing.py", "tests/test_pricing.py"],
            "testResults": [{"command": "pytest", "exitCode": 0}],
            "commitSha": head,
            "runId": str(stable_id(f"run:{name}")),
            "workspacePath": "C:/ws",
            "baseSha": BASE,
        },
        sort_keys=True,
    )


async def seed_tasks(
    tasks: PostgresTaskStore,
    *,
    key: str,
    organization_id: UUID,
    project_id: UUID,
    repository_id: UUID,
    leader_id: UUID,
    worker_status: TaskStatus,
    result: str | None,
    rework_status: TaskStatus | None = None,
) -> tuple[Task, Task]:
    leader = Task(
        id=stable_id(f"task:{key}:leader"),
        organization_id=organization_id,
        project_id=project_id,
        repository_id=repository_id,
        assigned_by_agent_id=leader_id,
        assignee_agent_id=stable_id(f"agent:{key}:repo-leader"),
        title=f"Deliver {key}",
        instruction="Split into worker tasks.",
        acceptance=("Repo scope delivered",),
        status=TaskStatus.SUCCEEDED,
    )
    await tasks.add(
        leader,
        idempotency_key=f"console-demo:{key}:leader",
        request_fingerprint="sha256:" + "a" * 64,
    )
    worker = Task(
        id=stable_id(f"task:{key}:worker"),
        organization_id=organization_id,
        project_id=project_id,
        repository_id=repository_id,
        parent_task_id=leader.id,
        assigned_by_agent_id=leader_id,
        assignee_agent_id=stable_id(f"agent:{key}:worker"),
        title="Implement the approved scope",
        instruction="Implement the approved scope.",
        acceptance=("Tests pass",),
        status=worker_status,
        result_summary=result,
    )
    await tasks.add(
        worker,
        idempotency_key=f"console-demo:{key}:worker",
        request_fingerprint="sha256:" + "b" * 64,
    )
    if rework_status is not None:
        rework = Task(
            id=stable_id(f"task:{key}:rework"),
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository_id,
            parent_task_id=leader.id,
            assigned_by_agent_id=leader_id,
            assignee_agent_id=worker.assignee_agent_id,
            title=REWORK_TITLE,
            instruction="Repair the failed candidate.",
            acceptance=("CI passes",),
            status=rework_status,
        )
        await tasks.add(
            rework,
            idempotency_key=f"console-demo:{key}:rework",
            request_fingerprint="sha256:" + "c" * 64,
        )
    return leader, worker


async def seed_room_stream(database: Database) -> None:
    """Idempotently seed the live room stream (runner / matrix / gate facts).

    The delivery read-model events timeline (contract v0.1 §4.1) merges
    agent_runtime.runner_events, collaboration.messages and
    delivery.scm_observations; without rows the console room stream renders
    empty. Stable UUIDv5 ids make reruns a no-op: when the first runner event
    already exists the whole section is skipped.
    """

    async with database.transaction() as session:
        marker = await session.get(
            RunnerEventRecord, stable_id("runner-event:a-api:started")
        )
    if marker is not None:
        return

    organization_id = stable_id("organization")
    changesets = PostgresChangeSetStore(database)
    base = datetime.now(UTC) - timedelta(hours=1)
    scenarios = (
        ("a-api", "plan:a", "repo:a:api", A_API_HEAD, True, False),
        ("a-client", "plan:a", "repo:a:client", A_CLIENT_HEAD, True, False),
        ("b-checkout", "plan:b", "repo:b:checkout", B_HEAD, True, False),
        ("c-billing", "plan:c", "repo:c:billing", C_HEAD, False, True),
    )
    rows: list[object] = []
    for index, (key, plan_name, repo_name, head, ci_passed, rework) in enumerate(
        scenarios
    ):
        plan_id = stable_id(plan_name)
        repository_id = stable_id(repo_name)
        project_id = stable_id(plan_name.replace("plan:", "project:"))
        worker_task_id = stable_id(f"task:{key}:worker")
        run_id = stable_id(f"run:{key}")
        binding = await changesets.get_by_idempotency_key(
            delivery_change_set_key(plan_id)
        )
        change_set_id = binding[0].id if binding is not None else None
        t0 = base + timedelta(minutes=index * 2)

        rows.append(
            CollaborationMessageRecord(
                id=stable_id(f"message:{key}:assignment"),
                organization_id=organization_id,
                project_id=project_id,
                repository_id=repository_id,
                task_id=worker_task_id,
                sender_agent_id=stable_id(f"agent:{key}:repo-leader"),
                recipient_agent_id=stable_id(f"agent:{key}:worker"),
                kind="task_assignment",
                subject=f"任务指派：交付 {key}",
                body="按已冻结的工程契约实现本仓库范围，验收标准见任务卡。",
                room_id="!console-demo:matrix.local",
                status="delivered",
                event_id=f"$seed-{key}-assignment",
                correlation_id=stable_id(f"correlation:{key}"),
                created_at=t0,
                idempotency_key=f"console-demo:msg:{key}:assignment",
                request_fingerprint="sha256:" + "d" * 64,
            )
        )
        rows.append(
            RunnerDispatchRecord(
                run_id=run_id,
                organization_id=organization_id,
                project_id=project_id,
                repository_id=repository_id,
                task_id=worker_task_id,
                worker_agent_id=stable_id(f"agent:{key}:worker"),
                status="completed",
                task_payload={"seed": "console-demo", "scenario": key},
                idempotency_key=f"console-demo:run:{key}",
                attempt=1,
                lease_until=None,
                created_at=t0 + timedelta(minutes=1),
                completed_at=t0 + timedelta(minutes=6),
            )
        )
        rows.append(
            RunnerEventRecord(
                event_id=stable_id(f"runner-event:{key}:started"),
                run_id=run_id,
                sequence=1,
                event_type="runner.started",
                payload={"summary": "runner picked up the task"},
                occurred_at=t0 + timedelta(minutes=1),
                recorded_at=t0 + timedelta(minutes=1),
            )
        )
        rows.append(
            RunnerEventRecord(
                event_id=stable_id(f"runner-event:{key}:completed"),
                run_id=run_id,
                sequence=2,
                event_type="runner.completed",
                payload={"summary": "candidate ready", "commitSha": head},
                occurred_at=t0 + timedelta(minutes=6),
                recorded_at=t0 + timedelta(minutes=6),
            )
        )
        if change_set_id is not None:
            check_payload = {
                "check": "test",
                "conclusion": "success" if ci_passed else "failure",
                "head_sha": head,
            }
            rows.append(
                SCMObservationRecord(
                    id=stable_id(f"observation:{key}:check"),
                    provider="github",
                    source="webhook",
                    external_id=f"console-demo:{key}:check",
                    event_type=(
                        "check_run.completed" if ci_passed else "check_run.failed"
                    ),
                    payload=check_payload,
                    payload_hash=hashlib.sha256(
                        json.dumps(check_payload, sort_keys=True).encode()
                    ).hexdigest(),
                    status="processed",
                    change_set_id=change_set_id,
                    repository_id=repository_id,
                    attempts=1,
                    version=1,
                    last_error=None,
                    observed_at=t0 + timedelta(minutes=8),
                    received_at=t0 + timedelta(minutes=8),
                    claimed_at=None,
                    processed_at=t0 + timedelta(minutes=9),
                )
            )
            if key.startswith("a-"):
                merge_payload = {"action": "merged", "head_sha": head}
                rows.append(
                    SCMObservationRecord(
                        id=stable_id(f"observation:{key}:merged"),
                        provider="github",
                        source="webhook",
                        external_id=f"console-demo:{key}:merged",
                        event_type="pull_request.merged",
                        payload=merge_payload,
                        payload_hash=hashlib.sha256(
                            json.dumps(merge_payload, sort_keys=True).encode()
                        ).hexdigest(),
                        status="processed",
                        change_set_id=change_set_id,
                        repository_id=repository_id,
                        attempts=1,
                        version=1,
                        last_error=None,
                        observed_at=t0 + timedelta(minutes=12),
                        received_at=t0 + timedelta(minutes=12),
                        claimed_at=None,
                        processed_at=t0 + timedelta(minutes=12),
                    )
                )
        if rework:
            rows.append(
                CollaborationMessageRecord(
                    id=stable_id(f"message:{key}:rework"),
                    organization_id=organization_id,
                    project_id=project_id,
                    repository_id=repository_id,
                    task_id=stable_id(f"task:{key}:rework"),
                    sender_agent_id=stable_id(f"agent:{key}:repo-leader"),
                    recipient_agent_id=stable_id(f"agent:{key}:worker"),
                    kind="task_assignment",
                    subject=f"返工指派：修复 {key} 的失败候选",
                    body="CI 未通过，请依据失败证据修复候选并重交。",
                    room_id="!console-demo:matrix.local",
                    status="delivered",
                    event_id=f"$seed-{key}-rework",
                    correlation_id=stable_id(f"correlation:{key}:rework"),
                    created_at=t0 + timedelta(minutes=10),
                    idempotency_key=f"console-demo:msg:{key}:rework",
                    request_fingerprint="sha256:" + "e" * 64,
                )
            )
    async with database.transaction() as session:
        session.add_all(rows)


async def seed(database_url: str) -> dict[str, object]:
    database = Database(database_url)
    try:
        plans = PostgresExecutionPlanStore(database)
        plan_a_id = stable_id("plan:a")
        if await plans.get(plan_a_id) is not None:
            organization_id = stable_id("organization")
            directory = PostgresAgentDirectory(database)
            replay = await CreateAgent(directory).execute(
                CreateAgentRequest(
                    organization_id=organization_id,
                    role=AgentRole.ORGANIZATION_LEADER,
                    agentteams_resource_name="console-demo-org-leader",
                ),
                idempotency_key="console-demo-org-leader",
            )
            await seed_room_stream(database)
            return {
                "already_seeded": True,
                "decided_by_agent_id": str(replay.principal.id),
                "A_delivered": str(plan_a_id),
                "B_release_awaiting_approval": str(stable_id("plan:b")),
                "C_repairing_watch": str(stable_id("plan:c")),
                "D_draft_project": str(stable_id("project:d")),
            }

        directory = PostgresAgentDirectory(database)
        catalog = PostgresRepositoryCatalog(database)
        tasks = PostgresTaskStore(database)
        snapshots = PlanSnapshotStore(database)
        specifications = PostgresSpecificationStore(database)
        validation = ValidationSnapshotService(PostgresValidationSnapshotStore(database))
        delivery = DeliveryService(PostgresChangeSetStore(database))

        organization_id = stable_id("organization")
        created = await CreateAgent(directory).execute(
            CreateAgentRequest(
                organization_id=organization_id,
                role=AgentRole.ORGANIZATION_LEADER,
                agentteams_resource_name="console-demo-org-leader",
            ),
            idempotency_key="console-demo-org-leader",
        )
        leader_id = created.principal.id
        out: dict[str, object] = {
            "already_seeded": False,
            "organization_id": str(organization_id),
            "decided_by_agent_id": str(leader_id),
        }

        # ------------- Scenario A: delivered (8-10 replica + frozen spec) ------
        project_a = stable_id("project:a")
        api_repo = RepositoryProfile(
            id=stable_id("repo:a:api"),
            name="repomesh-e2e-api",
            url="https://github.example/console-demo/api.git",
        )
        client_repo = RepositoryProfile(
            id=stable_id("repo:a:client"),
            name="repomesh-e2e-client",
            url="https://github.example/console-demo/client.git",
        )
        await catalog.add(api_repo)
        await catalog.add(client_repo)
        api_leader, api_worker = await seed_tasks(
            tasks,
            key="a-api",
            organization_id=organization_id,
            project_id=project_a,
            repository_id=api_repo.id,
            leader_id=leader_id,
            worker_status=TaskStatus.SUCCEEDED,
            result=evidence("a-api", A_API_HEAD),
        )
        client_leader, client_worker = await seed_tasks(
            tasks,
            key="a-client",
            organization_id=organization_id,
            project_id=project_a,
            repository_id=client_repo.id,
            leader_id=leader_id,
            worker_status=TaskStatus.SUCCEEDED,
            result=evidence("a-client", A_CLIENT_HEAD),
        )
        specification_id = stable_id("spec:a")
        await specifications.add(
            Specification(
                id=specification_id,
                organization_id=organization_id,
                project_id=project_a,
                kind=SpecificationKind.ENGINEERING,
                status=SpecificationStatus.FROZEN,
                title="Add discount_amount across API and client",
                owner_agent_id=leader_id,
                current_version=SpecificationVersion(
                    id=stable_id("spec:a:v1"),
                    specification_id=specification_id,
                    version=1,
                    created_by_agent_id=leader_id,
                    content=SpecificationContent(
                        goal=(
                            "Expose discount_amount in the pricing API "
                            "and render it in the client."
                        ),
                        acceptance=(
                            "Old clients remain compatible",
                            "Client displays discount_amount",
                        ),
                        constraints=("Do not remove existing pricing fields",),
                        tests=("pytest", "npm test"),
                        allowed_paths=("src/pricing/**", "src/components/**", "tests/**"),
                        forbidden_paths=("src/pricing/legacy/**", ".github/**"),
                    ),
                ),
            ),
            idempotency_key="console-demo:spec:a",
            request_fingerprint="sha256:" + "d" * 64,
        )
        plan_a = ExecutionPlan(
            id=plan_a_id,
            organization_id=organization_id,
            project_id=project_a,
            created_by_agent_id=leader_id,
            status=ExecutionPlanStatus.COMPLETED,
            current_batch_index=1,
            batches=(
                (
                    PlannedRepositoryTask(
                        repository_id=api_repo.id,
                        title="Add discount_amount to pricing",
                        instruction="Add the discount_amount field.",
                        acceptance=("Old clients remain compatible",),
                        leader_task_id=api_leader.id,
                    ),
                ),
                (
                    PlannedRepositoryTask(
                        repository_id=client_repo.id,
                        title="Display discount_amount",
                        instruction="Render the new field.",
                        acceptance=("Client shows the discount",),
                        leader_task_id=client_leader.id,
                    ),
                ),
            ),
        )
        await plans.add(plan_a, idempotency_key="console-demo:plan:a")
        await snapshots.save(
            project_id=project_a,
            plan_version=1,
            engineering_spec="Add discount_amount across API and client.",
            contracts=[
                {
                    "producer": "repomesh-e2e-api",
                    "consumer": "repomesh-e2e-client",
                    "interface": "GET /pricing",
                    "agreement": "Expose discount_amount as nullable",
                }
            ],
            task_dag=[
                {"repository": "repomesh-e2e-api", "depends_on": []},
                {"repository": "repomesh-e2e-client", "depends_on": ["repomesh-e2e-api"]},
            ],
            execution_batches=[["repomesh-e2e-api"], ["repomesh-e2e-client"]],
            graph_edges=[],
            created_by_agent_id=leader_id,
            execution_plan_id=plan_a.id,
            requirement_text="API 定价结果增加 discount_amount 并在 Client 展示",
        )
        snapshot_a = await validation.create(
            CreateValidationSnapshotCommand(
                organization_id=organization_id,
                project_id=project_a,
                specification_version_id=None,
                candidate_heads={api_repo.id: A_API_HEAD, client_repo.id: A_CLIENT_HEAD},
                tests=(
                    ValidationTestInput(api_repo.id, "pytest", 0),
                    ValidationTestInput(client_repo.id, "pytest", 0),
                ),
                environment={"runner_protocol": "v1", "execution_plan": str(plan_a.id)},
            )
        )
        change_set_a = await delivery.prepare(
            PrepareChangeSetCommand(
                organization_id=organization_id,
                project_id=project_a,
                created_by_agent_id=leader_id,
                title=f"RepoMesh delivery {str(plan_a.id)[:8]}",
                validation_snapshot_id=snapshot_a.id,
                candidates=(
                    RepositoryCandidateInput(
                        repository_id=api_repo.id,
                        task_id=api_worker.id,
                        commit_sha=A_API_HEAD,
                        base_sha=BASE,
                        branch_name=f"repomesh/{str(plan_a.id)[:8]}/api",
                        required_checks=("test",),
                        required_approvals=0,
                    ),
                    RepositoryCandidateInput(
                        repository_id=client_repo.id,
                        task_id=client_worker.id,
                        commit_sha=A_CLIENT_HEAD,
                        base_sha=BASE,
                        branch_name=f"repomesh/{str(plan_a.id)[:8]}/client",
                        depends_on=(api_repo.id,),
                        required_checks=("test",),
                        required_approvals=0,
                    ),
                ),
            ),
            idempotency_key=delivery_change_set_key(plan_a.id),
        )
        for number, (repository_id, head) in enumerate(
            ((api_repo.id, A_API_HEAD), (client_repo.id, A_CLIENT_HEAD)), start=1
        ):
            await delivery.observe_pull_request(
                PullRequestObservationCommand(
                    change_set_a.id,
                    repository_id,
                    number,
                    f"https://github.example/console-demo/pull/{number}",
                    head,
                )
            )
            await delivery.observe_ci(
                CIObservationCommand(
                    change_set_a.id, repository_id, True, f"ci-{number}", "test passed", "test"
                )
            )
            await delivery.record_governance_decision(
                RecordGovernanceDecisionCommand(
                    change_set_a.id,
                    repository_id,
                    head,
                    GovernanceDecisionKind.READY,
                    leader_id,
                    "release approved",
                )
            )
            await delivery.record_merge_requested(
                RecordMergeRequestedCommand(change_set_a.id, repository_id, head)
            )
            await delivery.observe_merge(
                MergeObservationCommand(change_set_a.id, repository_id, "e" * 40)
            )
        out["A_delivered"] = {
            "delivery_id": str(plan_a.id),
            "change_set_id": str(change_set_a.id),
            "repositories": {"api": str(api_repo.id), "client": str(client_repo.id)},
            "contract": "frozen engineering spec with forbidden_paths",
        }

        # ------------- Scenario B: release, awaiting READY approval ------------
        project_b = stable_id("project:b")
        checkout_repo = RepositoryProfile(
            id=stable_id("repo:b:checkout"),
            name="repomesh-e2e-checkout",
            url="https://github.example/console-demo/checkout.git",
        )
        await catalog.add(checkout_repo)
        b_leader, b_worker = await seed_tasks(
            tasks,
            key="b-checkout",
            organization_id=organization_id,
            project_id=project_b,
            repository_id=checkout_repo.id,
            leader_id=leader_id,
            worker_status=TaskStatus.SUCCEEDED,
            result=evidence("b-checkout", B_HEAD),
        )
        plan_b = ExecutionPlan(
            id=stable_id("plan:b"),
            organization_id=organization_id,
            project_id=project_b,
            created_by_agent_id=leader_id,
            status=ExecutionPlanStatus.COMPLETED,
            batches=(
                (
                    PlannedRepositoryTask(
                        repository_id=checkout_repo.id,
                        title="Free-shipping threshold",
                        instruction="Apply the free-shipping threshold at checkout.",
                        acceptance=("Threshold applies",),
                        leader_task_id=b_leader.id,
                    ),
                ),
            ),
        )
        await plans.add(plan_b, idempotency_key="console-demo:plan:b")
        await snapshots.save(
            project_id=project_b,
            plan_version=1,
            engineering_spec="Apply a free-shipping threshold at checkout.",
            contracts=[],
            task_dag=[{"repository": "repomesh-e2e-checkout", "depends_on": []}],
            execution_batches=[["repomesh-e2e-checkout"]],
            graph_edges=[],
            created_by_agent_id=leader_id,
            execution_plan_id=plan_b.id,
            requirement_text="结算页满额免运费门槛",
        )
        snapshot_b = await validation.create(
            CreateValidationSnapshotCommand(
                organization_id=organization_id,
                project_id=project_b,
                specification_version_id=None,
                candidate_heads={checkout_repo.id: B_HEAD},
                tests=(ValidationTestInput(checkout_repo.id, "pytest", 0),),
                environment={"runner_protocol": "v1", "execution_plan": str(plan_b.id)},
            )
        )
        change_set_b = await delivery.prepare(
            PrepareChangeSetCommand(
                organization_id=organization_id,
                project_id=project_b,
                created_by_agent_id=leader_id,
                title=f"RepoMesh delivery {str(plan_b.id)[:8]}",
                validation_snapshot_id=snapshot_b.id,
                candidates=(
                    RepositoryCandidateInput(
                        repository_id=checkout_repo.id,
                        task_id=b_worker.id,
                        commit_sha=B_HEAD,
                        base_sha=BASE,
                        branch_name=f"repomesh/{str(plan_b.id)[:8]}/checkout",
                        required_checks=("test",),
                        required_approvals=0,
                    ),
                ),
            ),
            idempotency_key=delivery_change_set_key(plan_b.id),
        )
        await delivery.observe_pull_request(
            PullRequestObservationCommand(
                change_set_b.id,
                checkout_repo.id,
                7,
                "https://github.example/console-demo/pull/7",
                B_HEAD,
            )
        )
        await delivery.observe_ci(
            CIObservationCommand(
                change_set_b.id, checkout_repo.id, True, "ci-7", "test passed", "test"
            )
        )
        out["B_release_awaiting_approval"] = {
            "delivery_id": str(plan_b.id),
            "change_set_id": str(change_set_b.id),
            "repository_id": str(checkout_repo.id),
            "head_sha": B_HEAD,
            "contract": None,
        }

        # -------- Scenario C: repairing (CI failed, rework + recovery) ---------
        project_c = stable_id("project:c")
        billing_repo = RepositoryProfile(
            id=stable_id("repo:c:billing"),
            name="repomesh-e2e-billing",
            url="https://github.example/console-demo/billing.git",
        )
        await catalog.add(billing_repo)
        c_leader, c_worker = await seed_tasks(
            tasks,
            key="c-billing",
            organization_id=organization_id,
            project_id=project_c,
            repository_id=billing_repo.id,
            leader_id=leader_id,
            worker_status=TaskStatus.FAILED,
            result=None,
            rework_status=TaskStatus.IN_PROGRESS,
        )
        plan_c = ExecutionPlan(
            id=stable_id("plan:c"),
            organization_id=organization_id,
            project_id=project_c,
            created_by_agent_id=leader_id,
            status=ExecutionPlanStatus.COMPLETED,
            batches=(
                (
                    PlannedRepositoryTask(
                        repository_id=billing_repo.id,
                        title="Invoice rounding fix",
                        instruction="Fix invoice rounding.",
                        acceptance=("Rounding is correct",),
                        leader_task_id=c_leader.id,
                    ),
                ),
            ),
        )
        await plans.add(plan_c, idempotency_key="console-demo:plan:c")
        await snapshots.save(
            project_id=project_c,
            plan_version=1,
            engineering_spec="Fix invoice rounding in billing.",
            contracts=[],
            task_dag=[{"repository": "repomesh-e2e-billing", "depends_on": []}],
            execution_batches=[["repomesh-e2e-billing"]],
            graph_edges=[],
            created_by_agent_id=leader_id,
            execution_plan_id=plan_c.id,
            requirement_text="账单金额四舍五入错误修复",
        )
        change_set_c = await delivery.prepare(
            PrepareChangeSetCommand(
                organization_id=organization_id,
                project_id=project_c,
                created_by_agent_id=leader_id,
                title=f"RepoMesh delivery {str(plan_c.id)[:8]}",
                validation_snapshot_id=None,
                candidates=(
                    RepositoryCandidateInput(
                        repository_id=billing_repo.id,
                        task_id=c_worker.id,
                        commit_sha=C_HEAD,
                        base_sha=BASE,
                        branch_name=f"repomesh/{str(plan_c.id)[:8]}/billing",
                        required_checks=("test",),
                        required_approvals=0,
                    ),
                ),
            ),
            idempotency_key=delivery_change_set_key(plan_c.id),
        )
        await delivery.observe_pull_request(
            PullRequestObservationCommand(
                change_set_c.id,
                billing_repo.id,
                9,
                "https://github.example/console-demo/pull/9",
                C_HEAD,
            )
        )
        await delivery.observe_ci(
            CIObservationCommand(
                change_set_c.id, billing_repo.id, False, "ci-9", "2 tests failed", "test"
            )
        )
        await delivery.plan_recovery(
            PlanRecoveryCommand(
                change_set_id=change_set_c.id,
                trigger=RecoveryTrigger.CI_FAILED,
                reason="CI failed on candidate",
                repository_id=billing_repo.id,
            )
        )
        out["C_repairing_watch"] = {
            "delivery_id": str(plan_c.id),
            "change_set_id": str(change_set_c.id),
            "repository_id": str(billing_repo.id),
            "head_sha": C_HEAD,
            "contract": None,
        }

        # -------- Scenario D: virtual draft (snapshot not materialized) --------
        project_d = stable_id("project:d")
        await snapshots.save(
            project_id=project_d,
            plan_version=1,
            engineering_spec="Draft: notification digest across mailer and web.",
            contracts=[],
            task_dag=[
                {"repository": "repomesh-e2e-mailer", "depends_on": []},
                {"repository": "repomesh-e2e-web", "depends_on": ["repomesh-e2e-mailer"]},
            ],
            execution_batches=[["repomesh-e2e-mailer"], ["repomesh-e2e-web"]],
            graph_edges=[],
            created_by_agent_id=leader_id,
            execution_plan_id=None,
            requirement_text="通知摘要：邮件与站内信合并为每日一封",
        )
        out["D_draft_project"] = {
            "project_id": str(project_d),
            "delivery_id": None,
            "phase": "plan (virtual draft)",
        }
        await seed_room_stream(database)
        return out
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--database-url",
        default=os.environ.get("REPOMESH_DATABASE_URL", DEFAULT_DATABASE_URL),
        help="SQLAlchemy async DSN (default: REPOMESH_DATABASE_URL or the 5533 convention)",
    )
    arguments = parser.parse_args()
    result = asyncio.run(seed(arguments.database_url))
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
