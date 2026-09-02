from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .contracts import DatabaseChangeRequirement, TaskView


class DatabaseTestHandoffStatus(StrEnum):
    PLANNED = "planned"
    TESTING = "testing"
    EVIDENCE_READY = "evidence_ready"
    TEST_TEAM_REWORK = "test_team_rework"
    BLOCKED_EXTERNAL = "blocked_external"


@dataclass(frozen=True, slots=True)
class DatabaseTestTeamPlan:
    task_id: str
    organization_id: str
    project_id: str
    repository_id: str
    candidate_sha: str
    test_team_repository_id: str
    required_checks: tuple[str, ...]
    affected_tables: tuple[str, ...]
    evidence_prefix: str
    branch_validation_key: str
    status: DatabaseTestHandoffStatus = DatabaseTestHandoffStatus.PLANNED


@dataclass(frozen=True, slots=True)
class DatabaseTestTeamEvidence:
    plan_key: str
    candidate_sha: str
    completed_checks: tuple[str, ...]
    affected_tables: tuple[str, ...]
    evidence_ref: str


class DatabaseTestTeamHandoffError(ValueError):
    pass


class InMemoryDatabaseTestTeamHandoffStore:
    def __init__(self) -> None:
        self.plans: dict[str, DatabaseTestTeamPlan] = {}
        self.evidence: dict[str, DatabaseTestTeamEvidence] = {}

    async def get(self, key: str) -> DatabaseTestTeamPlan | None:
        return self.plans.get(key)

    async def put(self, key: str, plan: DatabaseTestTeamPlan) -> None:
        self.plans[key] = plan

    async def put_evidence(self, key: str, evidence: DatabaseTestTeamEvidence) -> None:
        self.evidence[key] = evidence

    async def get_evidence(self, key: str) -> DatabaseTestTeamEvidence | None:
        return self.evidence.get(key)


class DatabaseTestTeamHandoffService:
    def __init__(self, store: InMemoryDatabaseTestTeamHandoffStore) -> None:
        self._store = store

    async def create_plan(
        self, task: TaskView, *, test_team_repository_id: str
    ) -> DatabaseTestTeamPlan:
        plan = build_database_test_team_plan(task, test_team_repository_id=test_team_repository_id)
        existing = await self._store.get(plan.branch_validation_key)
        if existing is not None:
            return existing
        await self._store.put(plan.branch_validation_key, plan)
        return plan

    async def submit_evidence(
        self,
        plan: DatabaseTestTeamPlan,
        *,
        candidate_sha: str,
        completed_checks: tuple[str, ...],
        affected_tables: tuple[str, ...],
        evidence_ref: str,
    ) -> DatabaseTestTeamEvidence:
        reasons = validate_test_team_evidence(
            plan,
            candidate_sha=candidate_sha,
            completed_checks=completed_checks,
            affected_tables=affected_tables,
        )
        if reasons:
            raise DatabaseTestTeamHandoffError("; ".join(reasons))
        if not evidence_ref.strip().startswith("evidence/"):
            raise DatabaseTestTeamHandoffError("evidence_ref must be an evidence path")
        evidence = DatabaseTestTeamEvidence(
            plan_key=plan.branch_validation_key,
            candidate_sha=plan.candidate_sha,
            completed_checks=tuple(dict.fromkeys(completed_checks)),
            affected_tables=tuple(dict.fromkeys(affected_tables)),
            evidence_ref=evidence_ref.strip(),
        )
        existing = await self._store.get_evidence(plan.branch_validation_key)
        if existing is not None and existing != evidence:
            raise DatabaseTestTeamHandoffError(
                "handoff evidence already exists with different content"
            )
        await self._store.put_evidence(plan.branch_validation_key, evidence)
        await self._store.put(
            plan.branch_validation_key,
            DatabaseTestTeamPlan(
                **{
                    name: DatabaseTestHandoffStatus.EVIDENCE_READY
                    if name == "status"
                    else getattr(plan, name)
                    for name in plan.__dataclass_fields__
                }
            ),
        )
        return evidence

    async def set_status(
        self, plan: DatabaseTestTeamPlan, status: DatabaseTestHandoffStatus
    ) -> DatabaseTestTeamPlan:
        updated = DatabaseTestTeamPlan(
            **{
                name: status if name == "status" else getattr(plan, name)
                for name in plan.__dataclass_fields__
            }
        )
        await self._store.put(plan.branch_validation_key, updated)
        return updated


def build_database_test_team_plan(
    task: TaskView, *, test_team_repository_id: str
) -> DatabaseTestTeamPlan:
    requirement: DatabaseChangeRequirement = task.database_change
    if not requirement.declared or not requirement.required:
        raise DatabaseTestTeamHandoffError(
            "a test-team database plan requires an explicit required database change"
        )
    if task.evidence is None or not task.evidence.commit_sha:
        raise DatabaseTestTeamHandoffError("database handoff requires a candidate commit")
    if not test_team_repository_id.strip():
        raise DatabaseTestTeamHandoffError("test-team repository is required")
    candidate_sha = task.evidence.commit_sha
    return DatabaseTestTeamPlan(
        task_id=str(task.id),
        organization_id=str(task.organization_id),
        project_id=str(task.project_id),
        repository_id=str(task.repository_id),
        candidate_sha=candidate_sha,
        test_team_repository_id=test_team_repository_id.strip(),
        required_checks=requirement.required_checks,
        affected_tables=requirement.affected_tables,
        evidence_prefix=f"evidence/{task.id}/{candidate_sha[:12]}",
        branch_validation_key=f"database-validation:{task.id}:{candidate_sha}",
    )


def validate_test_team_evidence(
    plan: DatabaseTestTeamPlan,
    *,
    candidate_sha: str,
    completed_checks: tuple[str, ...],
    affected_tables: tuple[str, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if candidate_sha.strip().lower() != plan.candidate_sha.lower():
        reasons.append("candidate_sha_mismatch")
    missing = set(plan.required_checks) - set(completed_checks)
    reasons.extend(f"required_check_missing:{check}" for check in sorted(missing))
    widened = set(affected_tables) - set(plan.affected_tables)
    reasons.extend(f"undeclared_table:{table}" for table in sorted(widened))
    return tuple(reasons)
