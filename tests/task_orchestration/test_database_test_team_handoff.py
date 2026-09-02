import json
from uuid import uuid4

import pytest

from repomesh.modules.task_orchestration.contracts import (
    DatabaseChangeKind,
    DatabaseChangeRequirement,
    TaskStatus,
)
from repomesh.modules.task_orchestration.database_test_team import (
    DatabaseTestHandoffStatus,
    DatabaseTestTeamHandoffError,
    DatabaseTestTeamHandoffService,
    InMemoryDatabaseTestTeamHandoffStore,
    build_database_test_team_plan,
    validate_test_team_evidence,
)
from repomesh.modules.task_orchestration.domain import Task


def make_task(*, sha: str = "a" * 40, required: bool = True):
    requirement = DatabaseChangeRequirement(
        declared=True,
        required=required,
        change_kinds=(DatabaseChangeKind.MIGRATION,) if required else (),
        affected_tables=("users",) if required else (),
        migration_required=required,
        required_checks=("migration_apply",) if required else (),
    )
    evidence = {
        "commitSha": sha,
        "changedFiles": ["migrations/0053_users.py"],
        "databaseChange": {
            "migrationFiles": ["migrations/0053_users.py"],
            "backfillFiles": [],
            "affectedTables": ["users"],
            "checks": [{"name": "migration_apply", "exitCode": 0}],
        },
    }
    task = Task(
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_id=uuid4(),
        assigned_by_agent_id=uuid4(),
        assignee_agent_id=uuid4(),
        title="database change",
        instruction="update users",
        acceptance=("passes",),
        status=TaskStatus.SUCCEEDED,
        result_summary=json.dumps(evidence),
        database_change=requirement,
    )
    return task.to_view()


def test_manager_task_creates_a_sha_fenced_test_team_plan() -> None:
    task = make_task()
    plan = build_database_test_team_plan(task, test_team_repository_id="test-assets")

    assert plan.status is DatabaseTestHandoffStatus.PLANNED
    assert plan.required_checks == ("migration_apply",)
    assert plan.affected_tables == ("users",)
    assert plan.evidence_prefix.endswith(task.evidence.commit_sha[:12])
    assert plan.branch_validation_key.endswith(task.evidence.commit_sha)


def test_legacy_or_non_database_task_is_not_sent_to_test_team() -> None:
    with pytest.raises(DatabaseTestTeamHandoffError, match="explicit required"):
        build_database_test_team_plan(
            make_task(required=False), test_team_repository_id="test-assets"
        )


def test_test_team_evidence_cannot_change_commit_or_widen_scope() -> None:
    plan = build_database_test_team_plan(
        make_task(), test_team_repository_id="test-assets"
    )
    reasons = validate_test_team_evidence(
        plan,
        candidate_sha="b" * 40,
        completed_checks=("migration_apply",),
        affected_tables=("users", "payments"),
    )
    assert reasons == ("candidate_sha_mismatch", "undeclared_table:payments")


def test_test_team_must_complete_every_manager_check() -> None:
    task = make_task()
    requirement = DatabaseChangeRequirement(
        declared=True,
        required=True,
        change_kinds=(DatabaseChangeKind.MIGRATION,),
        affected_tables=("users",),
        migration_required=True,
        required_checks=("migration_apply", "historical_data"),
    )
    task = Task(
        **{
            name: getattr(
                Task(
                    organization_id=task.organization_id,
                    project_id=task.project_id,
                    repository_id=task.repository_id,
                    assigned_by_agent_id=task.assigned_by_agent_id,
                    assignee_agent_id=task.assignee_agent_id,
                    title=task.title,
                    instruction=task.instruction,
                    acceptance=task.acceptance,
                    status=task.status,
                    result_summary=task.result_summary,
                    database_change=requirement,
                ),
                name,
            )
            for name in Task.__dataclass_fields__
        }
    ).to_view()
    plan = build_database_test_team_plan(task, test_team_repository_id="test-assets")
    assert validate_test_team_evidence(
        plan,
        candidate_sha="a" * 40,
        completed_checks=("migration_apply",),
        affected_tables=("users",),
    ) == ("required_check_missing:historical_data",)


@pytest.mark.asyncio
async def test_handoff_service_is_idempotent_and_moves_to_evidence_ready() -> None:
    service = DatabaseTestTeamHandoffService(InMemoryDatabaseTestTeamHandoffStore())
    task = make_task()
    first = await service.create_plan(task, test_team_repository_id="test-assets")
    replay = await service.create_plan(task, test_team_repository_id="test-assets")
    assert replay == first

    evidence = await service.submit_evidence(
        first,
        candidate_sha="a" * 40,
        completed_checks=("migration_apply",),
        affected_tables=("users",),
        evidence_ref=f"evidence/{task.id}/aaaaaaaaaaaa/steps.json",
    )
    assert evidence.plan_key == first.branch_validation_key
    updated = await service._store.get(first.branch_validation_key)
    assert updated is not None
    assert updated.status is DatabaseTestHandoffStatus.EVIDENCE_READY
