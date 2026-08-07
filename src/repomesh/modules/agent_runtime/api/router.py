from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from repomesh.modules.agent_runtime.application import ExecuteCodingRun
from repomesh.modules.agent_runtime.contracts import StartAssignedWorkerTaskCommand
from repomesh.modules.agent_runtime.ports import CodingRunRequest
from repomesh.settings import get_settings

from .models import (
    CodingRunCreate,
    CodingRunView,
    DirectTaskStartCreate,
    RunEventView,
    WorkerTaskStartCreate,
    WorkerTaskStartView,
)

router = APIRouter(tags=["agent-runtime"])


@router.get("/runtime/runner-tasks/next", response_model=None)
async def next_runner_task(
    request: Request,
    worker_agent_id: Annotated[UUID | None, Query(alias="workerAgentId")] = None,
) -> dict[str, object] | Response:
    _authorize_runner(request)
    payload = await request.app.state.container.runner_gateway().next_task(worker_agent_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT) if payload is None else payload


@router.post("/runtime/runner-events", status_code=202)
async def receive_runner_event(body: dict[str, Any], request: Request) -> dict[str, bool]:
    _authorize_runner(request)
    try:
        inserted = await request.app.state.container.runner_gateway().receive_event(body)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"accepted": True, "duplicate": not inserted}


def _authorize_runner(request: Request) -> None:
    expected = get_settings().runner_control_token
    if expected and request.headers.get("Authorization") != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid runner control token")


@router.post(
    "/agent-actions/start-worker-task",
    response_model=WorkerTaskStartView,
    status_code=202,
)
async def start_worker_task(body: WorkerTaskStartCreate, request: Request) -> WorkerTaskStartView:
    _authorize_agent_action(request)
    try:
        started = await request.app.state.container.worker_execution_service().execute(
            StartAssignedWorkerTaskCommand(
                task_id=body.task_id,
                worker_agent_id=body.worker_agent_id,
                adapter_id=body.adapter_id,
                base_revision=body.base_revision,
                task_features=body.task_features,
            )
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    workspace = started.task.workspace
    if workspace is None:
        raise HTTPException(status_code=500, detail="runner task has no prepared workspace")
    return WorkerTaskStartView(
        task_id=started.task.task_id,
        run_id=started.task.run_id,
        status=started.status.value,
        workspace_id=workspace.workspace_id,
        workspace_path=workspace.path,
        base_sha=workspace.base_sha,
    )


@router.post(
    "/agent-actions/start-direct-task",
    response_model=WorkerTaskStartView,
    status_code=202,
)
async def start_direct_task(
    body: DirectTaskStartCreate, request: Request
) -> WorkerTaskStartView:
    _authorize_agent_action(request)
    try:
        started = await request.app.state.container.worker_execution_service().execute_direct(
            StartAssignedWorkerTaskCommand(
                task_id=body.task_id,
                worker_agent_id=body.repository_leader_agent_id,
                adapter_id=body.adapter_id,
                base_revision=body.base_revision,
                task_features=body.task_features,
            )
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    workspace = started.task.workspace
    if workspace is None:
        raise HTTPException(status_code=500, detail="runner task has no prepared workspace")
    return WorkerTaskStartView(
        task_id=started.task.task_id,
        run_id=started.task.run_id,
        status=started.status.value,
        workspace_id=workspace.workspace_id,
        workspace_path=workspace.path,
        base_sha=workspace.base_sha,
    )


def _authorize_agent_action(request: Request) -> None:
    expected = get_settings().agent_action_token
    if not expected:
        raise HTTPException(status_code=503, detail="agent action token is not configured")
    if request.headers.get("Authorization") != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid agent action token")


@router.post("/coding-runs/mock", response_model=CodingRunView, status_code=202)
async def run_mock_agent(body: CodingRunCreate, request: Request) -> CodingRunView:
    container = request.app.state.container
    agent = container.mock_coding_agent_factory(body.scenario)
    result = await ExecuteCodingRun(agent).execute(
        CodingRunRequest(
            task_id=body.task_id,
            repository_url=str(body.repository_url),
            instruction=body.instruction,
            base_revision=body.base_revision,
        )
    )
    return CodingRunView(
        run_id=result.run_id,
        status=result.status,
        adapter=agent.name,
        summary=result.summary,
        changed_files=result.changed_files,
        test_command=result.test_command,
        events=tuple(
            RunEventView(type=event.type, message=event.message) for event in result.events
        ),
    )
