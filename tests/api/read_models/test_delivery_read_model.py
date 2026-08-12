"""Aggregation over a replica of the 2026-08-10 live two-repository delivery.

The live database that held plan 34fbc214 is not available in this workspace,
so the same shape — two repositories, dependency order, two READY governance
decisions, delivered ChangeSet, runner evidence — is rebuilt through the real
stores and asserted end to end over HTTP.
"""

import asyncio
import json
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from repomesh.bootstrap import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.delivery import delivery_change_set_key
from repomesh.modules.delivery.contracts import (
    CIObservationCommand,
    GovernanceDecisionKind,
    MergeObservationCommand,
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RecordGovernanceDecisionCommand,
    RecordMergeRequestedCommand,
    RepositoryCandidateInput,
)
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.review_validation import ValidationSnapshotService
from repomesh.modules.review_validation.contracts import (
    CreateValidationSnapshotCommand,
    ValidationTestInput,
)
from repomesh.modules.review_validation.infrastructure import (
    PostgresValidationSnapshotStore,
)
from repomesh.modules.task_orchestration.contracts import TaskStatus
from repomesh.modules.task_orchestration.domain import (
    ExecutionPlan,
    ExecutionPlanStatus,
    PlannedRepositoryTask,
    Task,
)
from repomesh.settings import get_settings

API_HEAD = "8deb466aff32990f7acf3858c61c045ebeaff335"
CLIENT_HEAD = "5fdafd67f25de54b1a67c16b1d7d7a7071030693"
BASE = "b" * 40


def _worker_task(
    *,
    organization_id: UUID,
    project_id: UUID,
    repository_id: UUID,
    leader_task_id: UUID,
    leader_agent_id: UUID,
    worker_agent_id: UUID,
    head: str,
    title: str = "Implement the approved scope",
) -> Task:
    evidence = {
        "summary": "runner.completed",
        "changedFiles": ["src/pricing.py", "tests/test_pricing.py"],
        "testResults": [{"command": "pytest", "exitCode": 0}],
        "commitSha": head,
        "runId": str(uuid4()),
        "workspacePath": "C:/ws",
        "baseSha": BASE,
    }
    return Task(
        organization_id=organization_id,
        project_id=project_id,
        repository_id=repository_id,
        parent_task_id=leader_task_id,
        assigned_by_agent_id=leader_agent_id,
        assignee_agent_id=worker_agent_id,
        title=title,
        instruction="Implement the approved scope.",
        acceptance=("Tests pass",),
        status=TaskStatus.SUCCEEDED,
        result_summary=json.dumps(evidence, sort_keys=True),
    )


def test_read_model_aggregates_a_delivered_two_repository_plan(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    get_settings.cache_clear()
    try:
        organization_id = uuid4()
        project_id = uuid4()
        org_leader_id = uuid4()
        api_repo = RepositoryProfile(
            name="repomesh-e2e-api", url="https://github.test/e2e/api.git"
        )
        client_repo = RepositoryProfile(
            name="repomesh-e2e-client", url="https://github.test/e2e/client.git"
        )
        api_leader_task_id = uuid4()
        client_leader_task_id = uuid4()
        plan = ExecutionPlan(
            organization_id=organization_id,
            project_id=project_id,
            created_by_agent_id=org_leader_id,
            status=ExecutionPlanStatus.COMPLETED,
            current_batch_index=1,
            batches=(
                (
                    PlannedRepositoryTask(
                        repository_id=api_repo.id,
                        title="Add discount_amount to pricing",
                        instruction="Add the discount_amount field.",
                        acceptance=("Old clients remain compatible",),
                        leader_task_id=api_leader_task_id,
                    ),
                ),
                (
                    PlannedRepositoryTask(
                        repository_id=client_repo.id,
                        title="Display discount_amount",
                        instruction="Render the new field.",
                        acceptance=("Client shows the discount",),
                        leader_task_id=client_leader_task_id,
                    ),
                ),
            ),
        )
        delivery = application_container.delivery_service()

        async def seed() -> tuple[UUID, UUID]:
            await application_container.repository_catalog.add(api_repo)
            await application_container.repository_catalog.add(client_repo)
            tasks = application_container.task_store
            for leader_id, repository in (
                (api_leader_task_id, api_repo),
                (client_leader_task_id, client_repo),
            ):
                leader = Task(
                    id=leader_id,
                    organization_id=organization_id,
                    project_id=project_id,
                    repository_id=repository.id,
                    assigned_by_agent_id=org_leader_id,
                    assignee_agent_id=uuid4(),
                    title=f"Deliver {repository.name}",
                    instruction="Split into worker tasks.",
                    acceptance=("Repo scope delivered",),
                    status=TaskStatus.SUCCEEDED,
                )
                await tasks.add(
                    leader,
                    idempotency_key=f"leader-{repository.name}",
                    request_fingerprint="sha256:" + "a" * 64,
                )
            api_worker = _worker_task(
                organization_id=organization_id,
                project_id=project_id,
                repository_id=api_repo.id,
                leader_task_id=api_leader_task_id,
                leader_agent_id=org_leader_id,
                worker_agent_id=uuid4(),
                head=API_HEAD,
            )
            client_worker = _worker_task(
                organization_id=organization_id,
                project_id=project_id,
                repository_id=client_repo.id,
                leader_task_id=client_leader_task_id,
                leader_agent_id=org_leader_id,
                worker_agent_id=uuid4(),
                head=CLIENT_HEAD,
            )
            await tasks.add(
                api_worker,
                idempotency_key="worker-api",
                request_fingerprint="sha256:" + "b" * 64,
            )
            await tasks.add(
                client_worker,
                idempotency_key="worker-client",
                request_fingerprint="sha256:" + "c" * 64,
            )
            await application_container.execution_plan_store().add(
                plan, idempotency_key="live-replica-plan"
            )
            await application_container.plan_snapshot_store().save(
                project_id=project_id,
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
                    {
                        "repository": "repomesh-e2e-client",
                        "depends_on": ["repomesh-e2e-api"],
                    },
                ],
                execution_batches=[["repomesh-e2e-api"], ["repomesh-e2e-client"]],
                graph_edges=[],
                created_by_agent_id=org_leader_id,
                execution_plan_id=plan.id,
                requirement_text="API 定价结果增加 discount_amount 并在 Client 展示",
            )
            validation = ValidationSnapshotService(
                PostgresValidationSnapshotStore(application_container.database)
            )
            snapshot = await validation.create(
                CreateValidationSnapshotCommand(
                    organization_id=organization_id,
                    project_id=project_id,
                    specification_version_id=None,
                    candidate_heads={api_repo.id: API_HEAD, client_repo.id: CLIENT_HEAD},
                    tests=(
                        ValidationTestInput(api_repo.id, "pytest", 0),
                        ValidationTestInput(client_repo.id, "pytest", 0),
                    ),
                    environment={
                        "runner_protocol": "v1",
                        "execution_plan": str(plan.id),
                    },
                )
            )
            change_set = await delivery.prepare(
                PrepareChangeSetCommand(
                    organization_id=organization_id,
                    project_id=project_id,
                    created_by_agent_id=org_leader_id,
                    title=f"RepoMesh delivery {str(plan.id)[:8]}",
                    validation_snapshot_id=snapshot.id,
                    candidates=(
                        RepositoryCandidateInput(
                            repository_id=api_repo.id,
                            task_id=api_worker.id,
                            commit_sha=API_HEAD,
                            base_sha=BASE,
                            branch_name=f"repomesh/{str(plan.id)[:8]}/api",
                            required_approvals=0,
                        ),
                        RepositoryCandidateInput(
                            repository_id=client_repo.id,
                            task_id=client_worker.id,
                            commit_sha=CLIENT_HEAD,
                            base_sha=BASE,
                            branch_name=f"repomesh/{str(plan.id)[:8]}/client",
                            depends_on=(api_repo.id,),
                            required_approvals=0,
                        ),
                    ),
                ),
                idempotency_key=delivery_change_set_key(plan.id),
            )
            for number, (repository_id, head) in enumerate(
                ((api_repo.id, API_HEAD), (client_repo.id, CLIENT_HEAD)), start=1
            ):
                await delivery.observe_pull_request(
                    PullRequestObservationCommand(
                        change_set.id,
                        repository_id,
                        number,
                        f"https://github.test/pr/{number}",
                        head,
                    )
                )
                await delivery.observe_ci(
                    CIObservationCommand(
                        change_set.id, repository_id, True, f"ci-{number}", "test passed"
                    )
                )
                await delivery.record_governance_decision(
                    RecordGovernanceDecisionCommand(
                        change_set.id,
                        repository_id,
                        head,
                        GovernanceDecisionKind.READY,
                        org_leader_id,
                        "release approved",
                    )
                )
                await delivery.record_merge_requested(
                    RecordMergeRequestedCommand(change_set.id, repository_id, head)
                )
                await delivery.observe_merge(
                    MergeObservationCommand(change_set.id, repository_id, "d" * 40)
                )
            return change_set.id, snapshot.id

        change_set_id, snapshot_id = asyncio.run(seed())
        headers = {"Authorization": "Bearer internal-secret"}
        with TestClient(create_app(application_container)) as client:
            assert client.get("/api/v1/deliveries").status_code == 401

            listing = client.get("/api/v1/deliveries", headers=headers)
            assert listing.status_code == 200
            projects = listing.json()["projects"]
            assert len(projects) == 1
            project = projects[0]
            assert project["project_id"] == str(project_id)
            assert project["project_key"] is None
            summary = project["deliveries"][0]
            assert summary["delivery_id"] == str(plan.id)
            assert summary["phase"] == "delivered"
            assert summary["pending_decision_count"] == 0
            assert listing.json()["next_cursor"] is None

            detail = client.get(f"/api/v1/deliveries/{plan.id}", headers=headers)
            assert detail.status_code == 200
            body = detail.json()
            assert body["delivery_id"] == str(plan.id)
            assert body["project"]["requirement_text"].startswith("API 定价")
            assert body["contract"] is None  # no engineering spec was created here
            assert body["plan"]["plan_version"] == 1
            assert body["plan"]["execution_batches"] == [
                ["repomesh-e2e-api"],
                ["repomesh-e2e-client"],
            ]
            assert body["plan"]["merge_order"] == [str(api_repo.id), str(client_repo.id)]

            tasks = {task["repository_id"]: task for task in body["tasks"]}
            api_task = tasks[str(api_repo.id)]
            client_task = tasks[str(client_repo.id)]
            assert api_task["display_status"] == "succeeded"
            assert api_task["attempt"] == 1
            assert api_task["escalated_to_human"] is False
            assert client_task["depends_on"] == [api_task["task_id"]]

            change_set = body["change_set"]
            assert change_set["change_set_id"] == str(change_set_id)
            assert change_set["status"] == "delivered"
            repositories = change_set["repositories"]
            assert [item["merge_order"] for item in repositories] == [0, 1]
            assert all(item["gate_display"] == "open" for item in repositories)
            # Contract 889464e: the gate question is moot once merged.
            assert all(item["merge_gate"] is None for item in repositories)
            assert len(change_set["governance_decisions"]) == 2

            validation = body["validation_snapshot"]
            assert validation["id"] == str(snapshot_id)
            assert validation["status"] == "passed"
            assert validation["candidate_heads"][str(api_repo.id)] == API_HEAD

            diffs = {item["repository_id"]: item for item in body["diffs"]}
            assert diffs[str(api_repo.id)]["commit_sha"] == API_HEAD
            assert diffs[str(api_repo.id)]["diffstat"] is None
            assert diffs[str(api_repo.id)]["changed_files"] == [
                "src/pricing.py",
                "tests/test_pricing.py",
            ]

            assert body["cost"] is None
            assert body["trace_id"] is None

            missing = client.get(f"/api/v1/deliveries/{uuid4()}", headers=headers)
            assert missing.status_code == 404
    finally:
        get_settings.cache_clear()


def test_unmaterialized_snapshot_appears_as_virtual_draft(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    get_settings.cache_clear()
    try:
        project_id = uuid4()

        async def seed() -> None:
            await application_container.plan_snapshot_store().save(
                project_id=project_id,
                plan_version=1,
                engineering_spec="Draft only.",
                contracts=[],
                task_dag=[{"repository": "solo-repo", "depends_on": []}],
                execution_batches=[["solo-repo"]],
                graph_edges=[],
                requirement_text="尚未物化的一句话需求",
            )

        asyncio.run(seed())
        with TestClient(create_app(application_container)) as client:
            listing = client.get(
                "/api/v1/deliveries",
                headers={"Authorization": "Bearer internal-secret"},
            )
        assert listing.status_code == 200
        deliveries = listing.json()["projects"][0]["deliveries"]
        assert deliveries[0]["delivery_id"] is None
        assert deliveries[0]["phase"] == "plan"
        assert deliveries[0]["title"].startswith("尚未物化")
    finally:
        get_settings.cache_clear()


def test_rework_chain_drives_attempt_repairing_and_escalation(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    get_settings.cache_clear()
    try:
        from repomesh.api.read_models import REWORK_TASK_TITLE
        from repomesh.modules.delivery.contracts import (
            PlanRecoveryCommand,
            RecoveryTrigger,
        )

        organization_id = uuid4()
        project_id = uuid4()
        org_leader_id = uuid4()
        repo = RepositoryProfile(name="repo-a", url="https://github.test/e2e/a.git")
        leader_task_id = uuid4()
        plan = ExecutionPlan(
            organization_id=organization_id,
            project_id=project_id,
            created_by_agent_id=org_leader_id,
            status=ExecutionPlanStatus.COMPLETED,
            batches=(
                (
                    PlannedRepositoryTask(
                        repository_id=repo.id,
                        title="Deliver repo-a",
                        instruction="Do it.",
                        acceptance=("Tests pass",),
                        leader_task_id=leader_task_id,
                    ),
                ),
            ),
        )
        delivery = application_container.delivery_service()

        async def seed() -> None:
            await application_container.repository_catalog.add(repo)
            tasks = application_container.task_store
            leader = Task(
                id=leader_task_id,
                organization_id=organization_id,
                project_id=project_id,
                repository_id=repo.id,
                assigned_by_agent_id=org_leader_id,
                assignee_agent_id=uuid4(),
                title="Deliver repo-a",
                instruction="Split into worker tasks.",
                acceptance=("Repo scope delivered",),
                status=TaskStatus.IN_PROGRESS,
            )
            await tasks.add(
                leader, idempotency_key="rw-leader", request_fingerprint="sha256:" + "a" * 64
            )
            worker_agent = uuid4()
            original = Task(
                organization_id=organization_id,
                project_id=project_id,
                repository_id=repo.id,
                parent_task_id=leader_task_id,
                assigned_by_agent_id=org_leader_id,
                assignee_agent_id=worker_agent,
                title="Implement repo-a scope",
                instruction="Implement.",
                acceptance=("Tests pass",),
                status=TaskStatus.FAILED,
            )
            rework = Task(
                organization_id=organization_id,
                project_id=project_id,
                repository_id=repo.id,
                parent_task_id=leader_task_id,
                assigned_by_agent_id=org_leader_id,
                assignee_agent_id=worker_agent,
                title=REWORK_TASK_TITLE,
                instruction="Repair the failed candidate.",
                acceptance=("CI passes",),
                status=TaskStatus.IN_PROGRESS,
            )
            await tasks.add(
                original, idempotency_key="rw-original", request_fingerprint="sha256:" + "b" * 64
            )
            await tasks.add(
                rework, idempotency_key="rw-rework", request_fingerprint="sha256:" + "c" * 64
            )
            await application_container.execution_plan_store().add(
                plan, idempotency_key="rw-plan"
            )
            change_set = await delivery.prepare(
                PrepareChangeSetCommand(
                    organization_id=organization_id,
                    project_id=project_id,
                    created_by_agent_id=org_leader_id,
                    title="Rework delivery",
                    validation_snapshot_id=uuid4(),
                    candidates=(
                        RepositoryCandidateInput(
                            repository_id=repo.id,
                            task_id=original.id,
                            commit_sha="e" * 40,
                            base_sha=BASE,
                            branch_name="repomesh/rework",
                            required_approvals=0,
                        ),
                    ),
                ),
                idempotency_key=delivery_change_set_key(plan.id),
            )
            await delivery.plan_recovery(
                PlanRecoveryCommand(
                    change_set_id=change_set.id,
                    trigger=RecoveryTrigger.OPERATOR_REQUESTED,
                    reason="manual review required",
                    repository_id=repo.id,
                )
            )

        asyncio.run(seed())
        with TestClient(create_app(application_container)) as client:
            detail = client.get(
                f"/api/v1/deliveries/{plan.id}",
                headers={"Authorization": "Bearer internal-secret"},
            )
        assert detail.status_code == 200
        body = detail.json()
        by_title = {task["title"]: task for task in body["tasks"]}
        original_view = by_title["Implement repo-a scope"]
        rework_view = by_title[REWORK_TASK_TITLE]
        # attempt is a number in a sequence, so the two rows cannot share one.
        # This used to assert 2 and 2: the original reported the total while
        # the rework reported its position, and the collision was written down
        # as the expected result.
        assert original_view["attempt"] == 1
        assert original_view["display_status"] == "failed"
        assert rework_view["attempt"] == 2
        assert rework_view["display_status"] == "repairing"
        assert any("返工任务" in item["what"] for item in original_view["repair_timeline"])
        assert body["change_set"]["recovery_plans"]
    finally:
        get_settings.cache_clear()
