from secrets import compare_digest
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from repomesh.modules.task_orchestration import (
    AppendPlanTaskInput,
    AppendPlanTasksCommand,
    TaskConflict,
)
from repomesh.settings import get_settings

router = APIRouter(prefix="/api/v1/execution-plans", tags=["dynamic-plans"])


class AppendTaskBody(BaseModel):
    repository_id: UUID
    title: str = Field(min_length=1, max_length=500)
    instruction: str = Field(min_length=1, max_length=20000)
    acceptance: tuple[str, ...] = Field(min_length=1)
    depends_on: tuple[UUID, ...] = ()
    tests: tuple[str, ...] = ()
    test_paths: tuple[str, ...] = ()


class AppendTasksBody(BaseModel):
    expected_plan_version: int = Field(ge=1)
    actor_agent_id: UUID
    reason: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=200)
    mode: str = "commit"
    items: tuple[AppendTaskBody, ...] = Field(min_length=1)


def _authorize(request: Request) -> None:
    expected = get_settings().agent_action_token
    if not expected:
        raise HTTPException(status_code=503, detail="write authentication is not configured")
    if not compare_digest(request.headers.get("Authorization", ""), f"Bearer {expected}"):
        raise HTTPException(status_code=401, detail="invalid credentials")


@router.post("/{plan_id}/append-tasks")
async def append_tasks(plan_id: UUID, body: AppendTasksBody, request: Request) -> dict:
    _authorize(request)
    command = AppendPlanTasksCommand(
        plan_id=plan_id,
        expected_plan_version=body.expected_plan_version,
        actor_agent_id=body.actor_agent_id,
        reason=body.reason,
        mode=body.mode,
        items=tuple(
            AppendPlanTaskInput(
                repository_id=item.repository_id,
                title=item.title,
                instruction=item.instruction,
                acceptance=item.acceptance,
                depends_on=item.depends_on,
                tests=item.tests,
                test_paths=item.test_paths,
            )
            for item in body.items
        ),
    )
    try:
        revision = await request.app.state.container.dynamic_plan_revision_service().append(
            command, idempotency_key=body.idempotency_key
        )
    except TaskConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "revision_id": str(revision.id), "plan_id": str(revision.plan_id),
        "revision": revision.revision, "base_plan_version": revision.base_plan_version,
        "result_plan_version": revision.result_plan_version, "status": revision.status,
        "appended_repository_ids": [str(item) for item in revision.appended_repository_ids],
        "previous_batches": [[str(item) for item in batch] for batch in revision.previous_batches],
        "new_batches": [[str(item) for item in batch] for batch in revision.new_batches],
    }
