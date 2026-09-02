import json
from pathlib import Path
from uuid import uuid4

import pytest

from repomesh.api.leader_action_models import PlanDecisionBody
from repomesh.modules.task_orchestration.contracts import (
    DatabaseChangeKind,
    DatabaseChangeRequirement,
    TaskStatus,
)
from repomesh.modules.task_orchestration.database_automation import (
    DatabaseAutomationDisposition,
    evaluate_database_automation,
)
from repomesh.modules.task_orchestration.domain import Task
from repomesh.modules.task_orchestration.infrastructure import PostgresTaskStore


def task(requirement: DatabaseChangeRequirement, document: dict | None = None):
    return Task(
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_id=uuid4(),
        assigned_by_agent_id=uuid4(),
        assignee_agent_id=uuid4(),
        title="database task",
        instruction="change the user schema",
        acceptance=("database validation passes",),
        status=TaskStatus.SUCCEEDED if document else TaskStatus.ASSIGNED,
        result_summary=json.dumps(document) if document else None,
        database_change=requirement,
    ).to_view()


def evidence(*, changed_files=(), database_change=None):
    return {
        "commitSha": "a" * 40,
        "changedFiles": list(changed_files),
        "databaseChange": database_change,
        "testResults": [{"command": "pytest", "exitCode": 0}],
    }


def required() -> DatabaseChangeRequirement:
    return DatabaseChangeRequirement(
        declared=True,
        required=True,
        change_kinds=(
            DatabaseChangeKind.SCHEMA,
            DatabaseChangeKind.MIGRATION,
            DatabaseChangeKind.BACKFILL,
        ),
        affected_tables=("users",),
        migration_required=True,
        backfill_required=True,
        required_checks=("migration_apply", "backfill_idempotency"),
    )


def test_explicit_no_change_differs_from_legacy_undeclared() -> None:
    explicit = evaluate_database_automation(
        task(DatabaseChangeRequirement(declared=True, required=False), evidence())
    )
    legacy = evaluate_database_automation(task(DatabaseChangeRequirement(), evidence()))
    assert explicit.disposition is DatabaseAutomationDisposition.NOT_REQUIRED
    assert legacy.disposition is DatabaseAutomationDisposition.MANAGER_REVIEW
    assert legacy.reasons == ("database_impact_not_declared",)


def test_database_diff_conflicting_with_manager_negative_returns_to_manager() -> None:
    decision = evaluate_database_automation(
        task(
            DatabaseChangeRequirement(declared=True, required=False),
            evidence(changed_files=("migrations/0053_users.sql",)),
        )
    )
    assert decision.disposition is DatabaseAutomationDisposition.MANAGER_REVIEW
    assert decision.reasons == ("database_diff_conflicts_with_manager_declaration",)


def test_missing_worker_database_artifacts_and_checks_request_rework() -> None:
    decision = evaluate_database_automation(
        task(
            required(),
            evidence(
                changed_files=("migrations/0053_users.py",),
                database_change={
                    "migrationFiles": [],
                    "backfillFiles": [],
                    "affectedTables": ["users"],
                    "checks": [{"name": "migration_apply", "exitCode": 1}],
                },
            ),
        )
    )
    assert decision.disposition is DatabaseAutomationDisposition.WORKER_REWORK
    assert set(decision.reasons) == {
        "migration_artifact_missing",
        "backfill_artifact_missing",
        "required_check_failed:migration_apply",
        "required_check_missing:backfill_idempotency",
    }


def test_complete_evidence_creates_sha_bound_idempotent_intent() -> None:
    view = task(
        required(),
        evidence(
            changed_files=("migrations/0053_users.py", "jobs/backfill_users.py"),
            database_change={
                "migrationFiles": ["migrations/0053_users.py"],
                "backfillFiles": ["jobs/backfill_users.py"],
                "affectedTables": ["users"],
                "checks": [
                    {"name": "migration_apply", "exitCode": 0},
                    {"name": "backfill_idempotency", "exitCode": 0},
                ],
            },
        ),
    )
    decision = evaluate_database_automation(view)
    assert decision.disposition is DatabaseAutomationDisposition.REQUEST_VALIDATION
    assert decision.intent is not None
    assert decision.intent.candidate_sha == "a" * 40
    assert decision.intent.idempotency_key == f"database-validation:{view.id}:{'a' * 40}"


def test_manager_plan_body_parses_database_requirement() -> None:
    document = json.loads(
        Path("contracts/leader-actions/v1/fixtures/plan-decision.valid.json").read_text(
            encoding="utf-8"
        )
    )
    decision = PlanDecisionBody.model_validate(document).to_decision()
    assert all(item.database_change.declared for item in decision.worker_tasks)
    assert all(not item.database_change.required for item in decision.worker_tasks)


@pytest.mark.asyncio
async def test_manager_requirement_round_trips_through_task_store(
    application_container,
) -> None:
    store = PostgresTaskStore(application_container.database)
    domain = Task(
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_id=uuid4(),
        assigned_by_agent_id=uuid4(),
        assignee_agent_id=uuid4(),
        title="persist database requirement",
        instruction="add migration",
        acceptance=("migration passes",),
        database_change=required(),
    )
    await store.add(domain, idempotency_key="database-requirement", request_fingerprint="f")
    loaded = await store.get(domain.id)
    assert loaded is not None
    assert loaded.database_change == required()
