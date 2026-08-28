from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .contracts import RecoveryAction, RecoveryCaseStatus
from .infrastructure import RecoveryCaseConflict

router = APIRouter(prefix="/recovery-cases", tags=["recovery-cases"])


class RecoveryDecisionBody(BaseModel):
    expected_version: int = Field(ge=1)
    evidence_version: str = Field(min_length=1, max_length=300)
    action: RecoveryAction
    reason: str = Field(min_length=1, max_length=2000)


async def _actor(request: Request, *, admin: bool = False):
    authorization = request.headers.get("Authorization", "")
    token = (
        authorization.removeprefix("Bearer ").strip()
        if authorization.startswith("Bearer ")
        else request.cookies.get("repomesh_session")
    )
    if not token:
        raise HTTPException(status_code=401, detail="local authentication is required")
    try:
        actor = await request.app.state.container.local_account_service().authenticate(token)
    except Exception as error:
        raise HTTPException(status_code=401, detail="invalid local session") from error
    if admin and not actor.is_admin:
        raise HTTPException(status_code=403, detail="administrator permission is required")
    return actor


@router.get("")
async def list_cases(
    request: Request,
    project_id: UUID | None = None,
    status: RecoveryCaseStatus | None = None,
) -> list[dict]:
    await _actor(request, admin=True)
    cases = await request.app.state.container.recovery_case_store().list_cases(
        project_id=project_id, status=status
    )
    return [asdict(case) for case in cases]


@router.get("/{case_id}")
async def get_case(case_id: UUID, request: Request) -> dict:
    await _actor(request, admin=True)
    case = await request.app.state.container.recovery_case_store().get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="recovery case does not exist")
    return asdict(case)


@router.post("/{case_id}/preview")
async def preview_case(
    case_id: UUID, body: RecoveryDecisionBody, request: Request
) -> dict:
    await _actor(request, admin=True)
    case = await request.app.state.container.recovery_case_store().get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="recovery case does not exist")
    if case.version != body.expected_version or case.evidence_version != body.evidence_version:
        raise HTTPException(status_code=409, detail="recovery evidence changed")
    if body.action not in case.available_actions:
        raise HTTPException(status_code=409, detail="recovery action is not available")
    return {
        "case_id": str(case.id), "case_version": case.version,
        "evidence_version": case.evidence_version, "action": body.action.value,
        "project_id": str(case.project_id),
        "repository_id": str(case.repository_id) if case.repository_id else None,
        "task_id": str(case.task_id) if case.task_id else None,
        "change_set_id": str(case.change_set_id) if case.change_set_id else None,
        "side_effects": [],
    }


@router.post("/{case_id}/decisions")
async def decide_case(
    case_id: UUID, body: RecoveryDecisionBody, request: Request
) -> dict:
    actor = await _actor(request, admin=True)
    try:
        decision, operation = await request.app.state.container.recovery_case_store().decide(
            case_id,
            expected_version=body.expected_version,
            evidence_version=body.evidence_version,
            action=body.action,
            decided_by_human_id=actor.id,
            reason=body.reason,
        )
    except RecoveryCaseConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"decision": asdict(decision), "operation": asdict(operation)}
