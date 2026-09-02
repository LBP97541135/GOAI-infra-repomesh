import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from repomesh.modules.capability_management.contracts import (
    SkillLifecycleRefused,
    SkillVersionStatus,
)
from repomesh.modules.capability_management.infrastructure import (
    SkillRegistryService,
)

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/capability-governance", tags=["capability-governance"])


class SkillVersionRegistration(BaseModel):
    skill_id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=5, max_length=20)
    local_path: str = Field(min_length=1, max_length=500)
    content_hash: str = Field(min_length=1, max_length=80)


class EvaluationRecordBody(BaseModel):
    scenario: str = Field(min_length=1, max_length=500)
    negative_case: str = Field(min_length=1, max_length=500)
    outcome: bool
    evidence: str = Field(min_length=1, max_length=2000)


class TransitionBody(BaseModel):
    actor: str = Field(min_length=1, max_length=200)


class McpPolicyUpdate(BaseModel):
    timeout_seconds: int = Field(ge=1, le=600)
    max_retries: int = Field(ge=0, le=5)
    retryable_only_reads: bool = True
    degraded_block_writes: bool = True


def _registry(request: Request) -> SkillRegistryService:
    return request.app.state.container.skill_registry_service()


async def _require_admin(request: Request):
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
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="local administrator permission is required")
    return actor


def _refusal(error: SkillLifecycleRefused) -> HTTPException:
    _logger.info(
        "Skill lifecycle refused code=%s detail=%s", error.code, str(error)
    )
    return HTTPException(status_code=409, detail={"code": error.code, "detail": str(error)})


@router.get("/skill-versions")
async def list_skill_versions(request: Request, skill_id: str | None = None):
    await _require_admin(request)
    rows = await _registry(request).list_versions(skill_id)
    return {
        "versions": [
            {
                "id": str(row.id),
                "skillId": row.skill_id,
                "version": row.version,
                "status": row.status,
                "localPath": row.local_path,
                "contentHash": row.content_hash,
                "createdBy": row.created_by,
            }
            for row in rows
        ]
    }


@router.post("/skill-versions", status_code=201)
async def register_skill_version(request: Request, body: SkillVersionRegistration):
    await _require_admin(request)
    try:
        record = await _registry(request).register_version(
            skill_id=body.skill_id,
            version=body.version,
            local_path=body.local_path,
            content_hash=body.content_hash,
            created_by="api",
        )
    except SkillLifecycleRefused as error:
        raise _refusal(error) from error
    return {"id": str(record.id), "status": record.status}


@router.post("/skill-versions/{version_id}/evaluations", status_code=201)
async def record_evaluation(request: Request, version_id: UUID, body: EvaluationRecordBody):
    actor = await _require_admin(request)
    try:
        await _registry(request).record_evaluation(
            version_id=version_id,
            scenario=body.scenario,
            negative_case=body.negative_case,
            outcome=body.outcome,
            evidence=body.evidence,
            evaluated_by=actor.name,
        )
    except SkillLifecycleRefused as error:
        raise _refusal(error) from error
    return {"recorded": True}


@router.post("/skill-versions/{version_id}/submit-evaluation")
async def submit_evaluation(request: Request, version_id: UUID, body: TransitionBody):
    await _require_admin(request)
    try:
        record = await _registry(request).transition(
            version_id, SkillVersionStatus.EVALUATING, actor=body.actor
        )
    except SkillLifecycleRefused as error:
        raise _refusal(error) from error
    return {"id": str(record.id), "status": record.status}


@router.post("/skill-versions/{version_id}/canary")
async def enter_canary(request: Request, version_id: UUID, body: TransitionBody):
    await _require_admin(request)
    try:
        record = await _registry(request).transition(
            version_id, SkillVersionStatus.CANARY, actor=body.actor
        )
    except SkillLifecycleRefused as error:
        raise _refusal(error) from error
    return {"id": str(record.id), "status": record.status}


@router.post("/skill-versions/{version_id}/promote")
async def promote(request: Request, version_id: UUID, body: TransitionBody):
    await _require_admin(request)
    try:
        record = await _registry(request).transition(
            version_id, SkillVersionStatus.PROMOTED, actor=body.actor
        )
    except SkillLifecycleRefused as error:
        raise _refusal(error) from error
    return {"id": str(record.id), "status": record.status}


@router.post("/skill-versions/{version_id}/rollback")
async def rollback(request: Request, version_id: UUID, body: TransitionBody):
    await _require_admin(request)
    try:
        record = await _registry(request).rollback(version_id, actor=body.actor)
    except SkillLifecycleRefused as error:
        raise _refusal(error) from error
    return {"id": str(record.id), "status": record.status}


@router.get("/mcp-policies")
async def list_mcp_policies(request: Request):
    await _require_admin(request)
    rows = await _registry(request).list_mcp_policies()
    return {
        "policies": [
            {
                "id": row.id,
                "timeoutSeconds": row.timeout_seconds,
                "maxRetries": row.max_retries,
                "retryableOnlyReads": row.retryable_only_reads,
                "degradedBlockWrites": row.degraded_block_writes,
                "requiredTaskFeatures": row.required_task_features,
            }
            for row in rows
        ]
    }


@router.put("/mcp-policies/{policy_id}")
async def update_mcp_policy(request: Request, policy_id: str, body: McpPolicyUpdate):
    await _require_admin(request)
    try:
        row = await _registry(request).update_mcp_policy(
            policy_id,
            timeout_seconds=body.timeout_seconds,
            max_retries=body.max_retries,
            retryable_only_reads=body.retryable_only_reads,
            degraded_block_writes=body.degraded_block_writes,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail="unknown mcp policy") from error
    return {"id": row.id, "timeoutSeconds": row.timeout_seconds, "maxRetries": row.max_retries}
