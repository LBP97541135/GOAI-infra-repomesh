from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from repomesh.modules.agent_runtime.application import ExecuteCodingRun
from repomesh.modules.agent_runtime.application.external_worker import (
    ProvisionExternalWorker,
    ResolveExternalWorkerBinding,
)
from repomesh.modules.agent_runtime.contracts import (
    ExternalWorkerBindingQuery,
    ExternalWorkerRefused,
    ProvisionExternalWorkerCommand,
    StartAssignedWorkerTaskCommand,
    UnknownExternalWorker,
)
from repomesh.modules.agent_runtime.ports import CodingRunRequest
from repomesh.modules.agent_runtime.ports.agent_team import (
    ExternalWorkerProvisioner,
    WorkerBindingReader,
    WorkerControlPlaneUnavailable,
)
from repomesh.settings import get_settings

from .models import (
    CodingRunCreate,
    CodingRunView,
    ExternalWorkerProvisionRequest,
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


@router.put("/runtime/external-workers/{worker_agent_id}", response_model=None)
async def provision_external_worker(
    worker_agent_id: UUID,
    request: Request,
    body: ExternalWorkerProvisionRequest | None = None,
) -> dict[str, object]:
    """Make one registered worker principal an external worker (ADR 0004).

    The production caller of ``ProvisionExternalWorker``, and the counterpart of
    the preflight above: this decides that a worker's body runs outside the
    cluster, and preflight is what a Bridge later reads back. Five things about
    its shape, each a decision rather than an accident:

    *A ``PUT`` keyed on the path id, and a body that says nothing.* External-ness
    is a decision recorded against one principal, so the id is the whole request.
    A caller may not state the controller resource name, the runtime, or
    ``containerManaged``: the first belongs to the agent directory, the second to
    the projection the ordinary project path already uses, and the third is the
    controller's answer, never the request's claim. A body stating one is a 422
    rather than a silent drop, because reading 200 after asking for something
    that was ignored is worse than being told no.

    *An administrator of this RepoMesh, and nothing else.* Not the runner control
    token that guards the reads above, not the agent-action token, not a Bridge's
    credential, and not an AgentTeams admin token: provisioning is a human
    operator's decision about their own installation. The guard is the local
    human session, mirroring ``delivery.api.router``'s — a module may not reach
    back into ``repomesh.api.*`` for its private one, and hoisting that guard
    into a shared contract is a change to two modules, which is its own PR.

    *Idempotent where it counts, at the controller.* The use case derives
    ``external-worker:{agent}:agentteams`` from the agent alone, so a replay is
    the same controller side effect rather than a second one; ``PUT`` is how that
    is spelled on the wire. No ``Idempotency-Key`` header is accepted — a
    request-level key would be a second, weaker answer to a question this one
    already settles.

    *200 both times, not 201 then 200.* ``ensure_worker`` answers the same
    document whether it created the resource or found it, so RepoMesh does not
    hold the fact a 201 would assert. Reporting it would mean widening the port
    to carry a "created" flag for the sake of a status code.

    *No CLI this round.* The ``repomesh`` entry point starts uvicorn and nothing
    else, so a subcommand would bring command parsing, credential storage and a
    second entry point to test with it. If one is ever wanted it should be a thin
    client of this route, authenticating as the same logged-in administrator —
    never reaching the database or the AgentTeams controller directly.

    The status table is the preflight's, on purpose: 404 for a principal RepoMesh
    does not know, 409 for facts that do not add up to an external worker
    (including a conflict the controller itself answered — the composition root
    translates the adapter's own conflict into this module's refusal), 503 for a
    control plane that is unconfigured or did not answer, and 500, untranslated,
    for anything else. A fault dressed as a 409 would send an operator to fix a
    worker that is fine.
    """

    await _authorize_administrator(request)
    container = request.app.state.container
    provisioner: ExternalWorkerProvisioner | None = container.external_worker_provisioner()
    if provisioner is None:
        # Fail-closed, as the preflight does: with no controller there is
        # nothing to provision against, and answering 200 would record a
        # decision that never left this process.
        raise HTTPException(status_code=503, detail="AgentTeams control plane is not configured")
    try:
        worker = await ProvisionExternalWorker(container.agent_directory, provisioner).execute(
            ProvisionExternalWorkerCommand(worker_agent_id=worker_agent_id)
        )
    except UnknownExternalWorker as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ExternalWorkerRefused as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except WorkerControlPlaneUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return worker.to_wire()


async def _authorize_administrator(request: Request) -> None:
    """The local human session guard, mirroring ``delivery.api.router``.

    Copied rather than imported, and copied from the module next door rather
    than from ``repomesh.api.human_control``: a business module reaching back
    into the top-level API package inverts the dependency the packages exist to
    express. The duplication is deliberate and bounded — when a third module
    needs this, the guard becomes a shared contract, which is a change to all of
    them and not this endpoint's to make.
    """

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
