from __future__ import annotations

import hmac
import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from repomesh.modules.capability_management import CROSS_REPO_TEST_TEAM_PROFILE
from repomesh.modules.review_validation import (
    DatabaseValidationCommand,
    DatabaseValidationStage,
    StartDatabaseBranchValidation,
)
from repomesh.modules.task_orchestration import (
    AssignTaskCommand,
    DatabaseTestHandoffStatus,
    DatabaseTestTeamHandoffError,
    DatabaseTestTeamPlan,
)

router = APIRouter(prefix="/api/v1/database-test-handoffs", tags=["database-test-team"])


class EvidenceBody(BaseModel):
    candidate_sha: str = Field(min_length=40, max_length=64, alias="candidateSha")
    completed_checks: list[str] = Field(default_factory=list, alias="completedChecks")
    affected_tables: list[str] = Field(default_factory=list, alias="affectedTables")
    evidence_ref: str = Field(min_length=1, max_length=300, alias="evidenceRef")

    model_config = {"extra": "forbid", "populate_by_name": False}


class ApprovalBody(BaseModel):
    approved: bool = True

    model_config = {"extra": "forbid", "populate_by_name": False}


def _auth(request: Request) -> None:
    from repomesh.settings import get_settings

    token = get_settings().agent_action_token
    if not token or request.headers.get("Authorization") != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="invalid agent action token")


def _test_leader_identity(request: Request) -> UUID:
    from repomesh.settings import get_settings

    raw = get_settings().test_team_leader_tokens
    if not raw:
        raise HTTPException(
            status_code=503, detail="test-team leader credentials are not configured"
        )
    try:
        document = json.loads(raw)
    except ValueError as error:
        raise HTTPException(
            status_code=503, detail="test-team leader credentials are invalid"
        ) from error
    presented = request.headers.get("Authorization", "")
    for agent_id, token in document.items() if isinstance(document, dict) else ():
        if isinstance(token, str) and hmac.compare_digest(presented, f"Bearer {token}"):
            try:
                return UUID(str(agent_id))
            except ValueError:
                continue
    raise HTTPException(status_code=401, detail="invalid test-team leader credential")


async def _test_repository_id(container) -> str:
    profiles = await container.repository_catalog.list()
    for profile in profiles:
        if profile.capability_profile == CROSS_REPO_TEST_TEAM_PROFILE:
            return str(profile.id)
    raise HTTPException(status_code=503, detail="cross-repository test team is not configured")


def _plan_wire(plan: DatabaseTestTeamPlan) -> dict[str, object]:
    return {
        "taskId": plan.task_id,
        "organizationId": plan.organization_id,
        "projectId": plan.project_id,
        "repositoryId": plan.repository_id,
        "candidateSha": plan.candidate_sha,
        "testTeamRepositoryId": plan.test_team_repository_id,
        "requiredChecks": list(plan.required_checks),
        "affectedTables": list(plan.affected_tables),
        "evidencePrefix": plan.evidence_prefix,
        "branchValidationKey": plan.branch_validation_key,
        "status": plan.status.value,
    }


@router.post("/{task_id}", status_code=201)
async def create_handoff(task_id: UUID, request: Request) -> dict[str, object]:
    _auth(request)
    container = request.app.state.container
    task = await container.task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task does not exist")
    try:
        plan = await container.database_test_team_handoff_service().create_plan(
            task.to_view(), test_team_repository_id=await _test_repository_id(container)
        )
    except DatabaseTestTeamHandoffError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    # The test-team task is sent only after the durable plan exists. A replay
    # finds the same plan and the TaskOrchestrator's idempotency key prevents a
    # duplicate Matrix message.
    topology = await container.project_topology_store.get_view(task.project_id)
    test_team = (
        next(
            (
                team
                for team in (topology.repository_teams if topology else ())
                if str(team.repository_id) == plan.test_team_repository_id
            ),
            None,
        )
        if topology
        else None
    )
    if test_team is not None and container.task_report_gateway is not None:
        await container.task_report_gateway.assign(
            AssignTaskCommand(
                organization_id=task.organization_id,
                project_id=task.project_id,
                repository_id=UUID(plan.test_team_repository_id),
                assigned_by_agent_id=task.assigned_by_agent_id,
                assignee_agent_id=test_team.leader_agent_id,
                title=f"Database validation for {task.title}",
                instruction=(
                    f"Execute the database test plan for candidate {plan.candidate_sha}. "
                    f"Required checks: {', '.join(plan.required_checks)}. "
                    f"Write evidence under {plan.evidence_prefix}."
                ),
                acceptance=("All required database checks have evidence",),
            ),
            idempotency_key=f"database-test-team:{plan.branch_validation_key}",
        )
        plan = await container.database_test_team_handoff_service().set_status(
            plan, DatabaseTestHandoffStatus.TESTING
        )
    return _plan_wire(plan)


@router.get("/{task_id}")
async def get_handoff(task_id: UUID, request: Request) -> dict[str, object]:
    _auth(request)
    container = request.app.state.container
    task = await container.task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task does not exist")
    plan = await container.database_test_team_handoff_service().create_plan(
        task.to_view(), test_team_repository_id=await _test_repository_id(container)
    )
    return _plan_wire(plan)


@router.post("/{task_id}/approval")
async def approve_handoff(
    task_id: UUID, body: ApprovalBody, request: Request
) -> dict[str, object]:
    container = request.app.state.container
    task = await container.task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task does not exist")
    plan = await container.database_test_team_handoff_service().create_plan(
        task.to_view(), test_team_repository_id=await _test_repository_id(container)
    )
    topology = await container.project_topology_store.get_view(task.project_id)
    team = next(
        (
            item
            for item in (topology.repository_teams if topology else ())
            if str(item.repository_id) == plan.test_team_repository_id
        ),
        None,
    )
    leader_agent_id = _test_leader_identity(request)
    if team is None or team.leader_agent_id != leader_agent_id:
        raise HTTPException(status_code=403, detail="caller is not the test-team leader")
    if not body.approved:
        updated = await container.database_test_team_handoff_service().set_status(
            plan, DatabaseTestHandoffStatus.TEST_TEAM_REWORK
        )
    else:
        updated = await container.database_test_team_handoff_service().set_status(
            plan, DatabaseTestHandoffStatus.TESTING
        )
    return _plan_wire(updated)


@router.post("/{task_id}/evidence")
async def submit_handoff_evidence(
    task_id: UUID, body: EvidenceBody, request: Request
) -> dict[str, object]:
    _auth(request)
    container = request.app.state.container
    task = await container.task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task does not exist")
    try:
        plan = await container.database_test_team_handoff_service().create_plan(
            task.to_view(), test_team_repository_id=await _test_repository_id(container)
        )
        evidence = await container.database_test_team_handoff_service().submit_evidence(
            plan,
            candidate_sha=body.candidate_sha,
            completed_checks=tuple(body.completed_checks),
            affected_tables=tuple(body.affected_tables),
            evidence_ref=body.evidence_ref,
        )
    except DatabaseTestTeamHandoffError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    validation = await container.database_branch_validation_service().start(
        StartDatabaseBranchValidation(
            organization_id=UUID(plan.organization_id),
            project_id=UUID(plan.project_id),
            repository_id=UUID(plan.repository_id),
            candidate_sha=plan.candidate_sha,
            source_database_ref="repository-database-validation-policy",
            commands=tuple(
                DatabaseValidationCommand(
                    DatabaseValidationStage.VERIFICATION,
                    check,
                    f"test-team:{evidence.evidence_ref}:{check}",
                )
                for check in evidence.completed_checks
            ),
            idempotency_key=plan.branch_validation_key,
        )
    )
    return {
        "planKey": evidence.plan_key,
        "candidateSha": evidence.candidate_sha,
        "completedChecks": list(evidence.completed_checks),
        "affectedTables": list(evidence.affected_tables),
        "evidenceRef": evidence.evidence_ref,
        "status": "evidence_ready",
        "branchValidationId": str(validation.id),
        "branchValidationStatus": validation.status.value,
    }
