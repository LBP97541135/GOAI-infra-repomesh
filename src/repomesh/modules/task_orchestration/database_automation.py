from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .contracts import TaskStatus, TaskView


class DatabaseAutomationDisposition(StrEnum):
    NOT_REQUIRED = "not_required"
    MANAGER_REVIEW = "manager_review"
    WORKER_REWORK = "worker_rework"
    REQUEST_VALIDATION = "request_validation"


@dataclass(frozen=True, slots=True)
class DatabaseValidationIntent:
    task_id: str
    organization_id: str
    project_id: str
    repository_id: str
    candidate_sha: str
    migration_files: tuple[str, ...]
    backfill_files: tuple[str, ...]
    checks: tuple[str, ...]
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class DatabaseAutomationDecision:
    disposition: DatabaseAutomationDisposition
    reasons: tuple[str, ...] = ()
    intent: DatabaseValidationIntent | None = None


DATABASE_PATH_MARKERS = ("migrations/", "alembic/", "db/migrate/", "schema/")


def evaluate_database_automation(task: TaskView) -> DatabaseAutomationDecision:
    requirement = task.database_change
    evidence = task.evidence
    changed_files = evidence.changed_files if evidence is not None else ()
    detected = tuple(
        path
        for path in changed_files
        if path.lower().endswith(".sql")
        or any(
            marker in path.lower().replace("\\", "/")
            for marker in DATABASE_PATH_MARKERS
        )
    )
    if not requirement.declared:
        reason = "undeclared_database_change" if detected else "database_impact_not_declared"
        return DatabaseAutomationDecision(
            DatabaseAutomationDisposition.MANAGER_REVIEW, (reason,)
        )
    if not requirement.required:
        if detected:
            return DatabaseAutomationDecision(
                DatabaseAutomationDisposition.MANAGER_REVIEW,
                ("database_diff_conflicts_with_manager_declaration",),
            )
        return DatabaseAutomationDecision(DatabaseAutomationDisposition.NOT_REQUIRED)

    reasons: list[str] = []
    if task.status is not TaskStatus.SUCCEEDED or evidence is None or not evidence.commit_sha:
        reasons.append("candidate_commit_missing")
    database = evidence.database_change if evidence is not None else None
    if database is None:
        reasons.append("worker_database_evidence_missing")
    else:
        if requirement.migration_required and not database.migration_files:
            reasons.append("migration_artifact_missing")
        if requirement.backfill_required and not database.backfill_files:
            reasons.append("backfill_artifact_missing")
        checks = {item.command: item.exit_code for item in database.checks}
        for required in requirement.required_checks:
            if required not in checks:
                reasons.append(f"required_check_missing:{required}")
            elif checks[required] != 0:
                reasons.append(f"required_check_failed:{required}")
        undeclared_tables = set(database.affected_tables) - set(requirement.affected_tables)
        if undeclared_tables:
            return DatabaseAutomationDecision(
                DatabaseAutomationDisposition.MANAGER_REVIEW,
                tuple(f"undeclared_table:{table}" for table in sorted(undeclared_tables)),
            )
    if reasons:
        return DatabaseAutomationDecision(
            DatabaseAutomationDisposition.WORKER_REWORK, tuple(reasons)
        )
    assert evidence is not None and evidence.database_change is not None
    assert evidence.commit_sha is not None
    return DatabaseAutomationDecision(
        DatabaseAutomationDisposition.REQUEST_VALIDATION,
        intent=DatabaseValidationIntent(
            task_id=str(task.id),
            organization_id=str(task.organization_id),
            project_id=str(task.project_id),
            repository_id=str(task.repository_id),
            candidate_sha=evidence.commit_sha,
            migration_files=evidence.database_change.migration_files,
            backfill_files=evidence.database_change.backfill_files,
            checks=tuple(item.command for item in evidence.database_change.checks),
            idempotency_key=f"database-validation:{task.id}:{evidence.commit_sha}",
        ),
    )
