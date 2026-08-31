from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from repomesh.settings import get_settings

from .presets import SKILLS
from .registry import (
    PostgresSkillRegistry,
    SkillEvaluationInput,
    SkillRegistryConflict,
    SkillReleaseChannel,
)

router = APIRouter(prefix="/skills", tags=["skill-registry"])


class VersionCreate(BaseModel):
    version: str = Field(min_length=1, max_length=80)
    content_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    local_path: str = Field(min_length=1, max_length=1000)


class EvaluationCreate(BaseModel):
    dataset_id: str
    dataset_version: str
    completion_rate: float = Field(ge=0, le=1)
    test_pass_rate: float = Field(ge=0, le=1)
    human_rework_rate: float = Field(ge=0, le=1)
    tool_error_rate: float = Field(ge=0, le=1)
    average_tokens: float = Field(default=0, ge=0)
    average_duration_ms: float = Field(default=0, ge=0)


class ReleaseCreate(BaseModel):
    channel: SkillReleaseChannel
    traffic_percent: int = Field(default=100, ge=0, le=100)


async def _admin(request: Request):
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
        raise HTTPException(status_code=403, detail="administrator permission is required")
    return actor


def _registry(request: Request) -> PostgresSkillRegistry:
    return request.app.state.container.skill_registry()


@router.post("/{skill_id}/versions")
async def create_version(skill_id: str, body: VersionCreate, request: Request) -> dict:
    actor = await _admin(request)
    definition = SKILLS.get(skill_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="Skill is not in the reviewed catalog")
    try:
        await _registry(request).bootstrap_definition(
            definition, get_settings().capability_root
        )
        version_id = await _registry(request).create_version(
            skill_id, body.version, body.content_hash, body.local_path,
            created_by=actor.id,
        )
    except SkillRegistryConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"version_id": str(version_id)}


@router.post("/versions/{version_id}/evaluations")
async def evaluate(version_id: UUID, body: EvaluationCreate, request: Request) -> dict:
    await _admin(request)
    try:
        passed = await _registry(request).evaluate(
            version_id, SkillEvaluationInput(**body.model_dump())
        )
    except SkillRegistryConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"passed": passed, "version": await _registry(request).version_view(version_id)}


@router.post("/versions/{version_id}/release")
async def release(version_id: UUID, body: ReleaseCreate, request: Request) -> dict:
    await _admin(request)
    try:
        release_id = await _registry(request).release(
            version_id, body.channel, traffic_percent=body.traffic_percent
        )
    except SkillRegistryConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"release_id": str(release_id)}


@router.post("/{skill_id}/rollback-canary")
async def rollback(skill_id: str, request: Request) -> dict:
    await _admin(request)
    try:
        await _registry(request).rollback_canary(skill_id)
    except SkillRegistryConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"status": "rolled_back"}


@router.get("/{skill_id}/history")
async def history(skill_id: str, request: Request) -> dict:
    await _admin(request)
    try:
        return await _registry(request).skill_history(skill_id)
    except SkillRegistryConflict as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
