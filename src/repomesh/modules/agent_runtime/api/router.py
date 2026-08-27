from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from repomesh.modules.agent_runtime.application import ExecuteCodingRun
from repomesh.modules.agent_runtime.application.external_worker import (
    ResolveExternalWorkerBinding,
)
from repomesh.modules.agent_runtime.contracts import (
    ExternalWorkerBindingQuery,
    ExternalWorkerRefused,
    StartAssignedWorkerTaskCommand,
    UnknownExternalWorker,
)
from repomesh.modules.agent_runtime.ports import CodingRunRequest
from repomesh.modules.agent_runtime.ports.agent_team import (
    WorkerBindingReader,
    WorkerControlPlaneUnavailable,
)
from repomesh.settings import get_settings

from .models import (
    CodingRunCreate,
    CodingRunView,
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


@router.get("/runtime/external-workers/{worker_agent_id}/binding", response_model=None)
async def external_worker_binding(worker_agent_id: UUID, request: Request) -> dict[str, object]:
    """Bridge preflight: the ``repomesh.agent-bridge.binding.v1`` document.

    Read-only, and the only place a Bridge learns that its worker is really
    external and which rooms it may act in — it holds no AgentTeams management
    credential and never calls the Go controller (ADR 0004 decisions 4, 5).

    Authenticated with the runner control token, the same credential the other
    ``/runtime`` reads on this router take: the caller is an out-of-cluster
    runtime process reading control-plane state, which is exactly what that
    token already names, and per ADR 0004 decision 6 the Bridge is its worker's
    Runner consumer, so it holds one already. A worker-scoped credential is PR
    5's subject, not this endpoint's to invent.

    The body is returned as the contract's own dict rather than through a
    response model, so the wire shape is the one ``to_wire`` produces and
    nothing re-derives it.

    The handle this reads through is a ``WorkerBindingReader`` — two reads, no
    writes — so what a Bridge can reach through this endpoint is bounded by a
    port rather than by this function's restraint. Anything not named below
    (an ``ExternalWorkerRefused``, an ``UnknownExternalWorker``, a
    ``WorkerControlPlaneUnavailable``) is a fault of RepoMesh's or the
    controller's, not a verdict on the request, and is deliberately left
    untranslated: a 500 says "this is broken", which is true, where a 409 would
    tell an operator to go and fix a binding that is fine.
    """

    _authorize_runner(request)
    container = request.app.state.container
    control_plane: WorkerBindingReader | None = container.external_worker_binding_control_plane()
    if control_plane is None:
        # Fail-closed: with no controller there is nothing to confirm against,
        # and an unconfirmed binding is worse than no answer.
        raise HTTPException(status_code=503, detail="AgentTeams control plane is not configured")
    try:
        binding = await ResolveExternalWorkerBinding(
            container.agent_directory, control_plane
        ).execute(ExternalWorkerBindingQuery(worker_agent_id=worker_agent_id))
    except UnknownExternalWorker as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ExternalWorkerRefused as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except WorkerControlPlaneUnavailable as error:
        # The controller merely did not answer -- unlike the refusals above,
        # a retry may well outlast this, so it is a 503 rather than a 409.
        raise HTTPException(status_code=503, detail=str(error)) from error
    return binding.to_wire()


def _authorize_runner(request: Request) -> None:
    expected = get_settings().runner_control_token
    if not expected:
        raise HTTPException(status_code=503, detail="runner control token is not configured")
    if request.headers.get("Authorization") != f"Bearer {expected}":
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
