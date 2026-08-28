import hmac
import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from repomesh.modules.agent_runtime.application import ExecuteCodingRun
from repomesh.modules.agent_runtime.application.external_worker import (
    ProvisionExternalMember,
    ProvisionExternalWorker,
    ResolveExternalMemberBinding,
    ResolveExternalWorkerBinding,
)
from repomesh.modules.agent_runtime.contracts import (
    ExternalMemberBindingQuery,
    ExternalWorkerBindingQuery,
    ExternalWorkerRefused,
    ProvisionExternalMemberCommand,
    ProvisionExternalWorkerCommand,
    StartAssignedWorkerTaskCommand,
    UnknownExternalWorker,
    parse_external_member_role,
)
from repomesh.modules.agent_runtime.ports import CodingRunRequest
from repomesh.modules.agent_runtime.ports.agent_team import (
    ExternalMemberProvisioner,
    ExternalWorkerProvisioner,
    WorkerBindingReader,
    WorkerControlPlaneUnavailable,
)
from repomesh.modules.agent_runtime.runner_store import RunnerGatewayForbidden
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
    authenticated = _authorize_runner(request)
    if authenticated is not None:
        # ``workerAgentId`` is a self-report, and a credential that names one
        # worker outranks it: it may only be repeated back, never used to lease
        # somebody else's queue.
        if worker_agent_id is not None and worker_agent_id != authenticated:
            raise HTTPException(
                status_code=403, detail="a worker credential may only lease its own tasks"
            )
        worker_agent_id = authenticated
    payload = await request.app.state.container.runner_gateway().next_task(worker_agent_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT) if payload is None else payload


@router.post("/runtime/runner-events", status_code=202)
async def receive_runner_event(body: dict[str, Any], request: Request) -> dict[str, bool]:
    # The event schema carries no worker id (and must not: it would be the
    # sender's claim), so ownership is settled one layer down by joining the
    # authenticated worker to the dispatch row this event's ``runId`` names.
    authenticated = _authorize_runner(request)
    try:
        inserted = await request.app.state.container.runner_gateway().receive_event(
            body, worker_agent_id=authenticated
        )
    except RunnerGatewayForbidden as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"accepted": True, "duplicate": not inserted}


@router.get("/runtime/external-workers/{worker_agent_id}/binding", response_model=None)
async def external_worker_binding(worker_agent_id: UUID, request: Request) -> dict[str, object]:
    """Bridge preflight: the ``repomesh.agent-bridge.binding.v1`` document.

    Read-only, and the only place a Bridge learns that its worker is really
    external and which rooms it may act in — it holds no AgentTeams management
    credential and never calls the Go controller (ADR 0004 decisions 4, 5).

    Authenticated the way the other ``/runtime`` reads on this router are, and
    since PR 5 that is two credentials rather than one: the managed Runner's
    global control token, which has no subject and may read any worker's
    binding, or a worker's own token, which may read only its own — a Bridge
    holds one per ADR 0004 decision 6. Reading a binding is how a Bridge learns
    which rooms it may act in, so a worker credential pointed at another
    worker's id is a 403 rather than a document.

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

    authenticated = _authorize_runner(request)
    if authenticated is not None and authenticated != worker_agent_id:
        raise HTTPException(
            status_code=403, detail="a worker credential may only read its own binding"
        )
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


@router.get("/runtime/v2/external-members/{member_agent_id}/binding", response_model=None)
async def external_member_binding(
    member_agent_id: UUID,
    request: Request,
    role: Annotated[str, Query(alias="role")],
) -> dict[str, object]:
    """v2 Bridge preflight: the ``repomesh.agent-bridge.binding.v2`` document.

    A sibling of the v1 preflight above, not a replacement for it, and the path
    says so. v1's route, request and response body are byte-for-byte what they
    were: a worker-form Bridge running against v1 documents stays valid
    indefinitely (v2 README), and a deployed one must never discover that the
    endpoint it has always called started answering a longer document. Two
    versions, two paths, and no content negotiation to get wrong.

    What v2 adds is ``role``, and it adds it in both directions.

    *In the response*, confirmed from RepoMesh's own agent directory: a
    Repository Leader may now be bound at all (adjudication D-11), and the
    allowed rooms it is given are its own — the Team room and the leader DM,
    never a worker's.

    *In the request*, as the ``role`` query parameter: the role the Bridge's
    enrollment claims. It is required, because an optional check is one a
    caller can decline to be checked by, and it is deliberately a plain string
    rather than an enum the framework validates. ``organization_leader`` is a
    real RepoMesh role that this contract cannot express, so a Bridge naming it
    has asked a coherent question and gets the coherent answer — 409, the same
    as an Organization Leader found in the directory — instead of a 422 that
    would split one refusal across two status codes.

    Everything else is the v1 endpoint's, including the credential (a worker
    token may read only its own binding; the environment variable keeps its
    historical name under D-6) and the status table: 404 for a principal
    RepoMesh does not know, 409 for facts that do not add up to a binding, 503
    for a control plane that is unconfigured or silent, and 500, untranslated,
    for anything nobody classified.
    """

    authenticated = _authorize_runner(request)
    if authenticated is not None and authenticated != member_agent_id:
        raise HTTPException(
            status_code=403, detail="an external member credential may only read its own binding"
        )
    container = request.app.state.container
    control_plane: WorkerBindingReader | None = container.external_worker_binding_control_plane()
    if control_plane is None:
        raise HTTPException(status_code=503, detail="AgentTeams control plane is not configured")
    try:
        binding = await ResolveExternalMemberBinding(
            container.agent_directory, control_plane
        ).execute(
            ExternalMemberBindingQuery(
                member_agent_id=member_agent_id,
                enrolled_role=parse_external_member_role(role),
            )
        )
    except UnknownExternalWorker as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ExternalWorkerRefused as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except WorkerControlPlaneUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return binding.to_wire()


def _authorize_runner(request: Request) -> UUID | None:
    """Who is calling: ``None`` for the managed Runner, a worker id for a Bridge.

    One auth point for every ``/runtime`` route on this router, and the only
    place a worker identity is established. The global control token has no
    subject, so it keeps meaning "every worker's queue"; a worker token names
    exactly one, and the routes below use the returned id instead of the one
    the caller reports about itself.

    The 503 is today's semantics, widened by one word: no credential of *either*
    kind configured is a deployment that cannot answer, not a request that may
    be refused. Either alone is enough — a deployment whose only Runners are
    out-of-cluster Bridges never sets the global token.
    """

    settings = get_settings()
    credentials = _worker_credentials()
    if not settings.runner_control_token and not credentials:
        raise HTTPException(status_code=503, detail="runner control token is not configured")
    presented = request.headers.get("Authorization", "").strip()
    if not presented:
        raise HTTPException(status_code=401, detail="invalid runner control token")
    if settings.runner_control_token and _presents(request, settings.runner_control_token):
        return None
    worker_agent_id = _authenticate_worker(request, credentials)
    if worker_agent_id is None:
        raise HTTPException(status_code=401, detail="invalid runner control token")
    return worker_agent_id


_MALFORMED_WORKER_TOKENS = "runner worker tokens are not a valid credential document"


def _worker_credentials() -> tuple[tuple[UUID, bytes], ...]:
    """``REPOMESH_RUNNER_WORKER_TOKENS`` as (worker agent id, expected header) pairs.

    A JSON object keyed by worker agent id, one bearer token each: all of PR 5's
    credential storage. No table and no issuance route, because a second answer
    to "who is this Bridge" is worth building only once the first has been used,
    and the pairs are few and rotated by redeploying.

    Kept as a sequence rather than a dict so the scan below compares every
    entry with ``compare_digest``; a dict lookup would decide membership by
    hashing the presented secret instead.

    Anything malformed is a fault of the deployment rather than a verdict on the
    request, so it wears the same 503 as an unconfigured control plane. Never a
    silent skip: an operator who mistyped one entry would otherwise read 401
    from a worker they believe they credentialed.
    """

    raw = get_settings().runner_worker_tokens
    if not raw:
        return ()
    try:
        document = json.loads(raw)
    except ValueError as error:
        raise HTTPException(status_code=503, detail=_MALFORMED_WORKER_TOKENS) from error
    if not isinstance(document, dict):
        raise HTTPException(status_code=503, detail=_MALFORMED_WORKER_TOKENS)
    credentials: list[tuple[UUID, bytes]] = []
    for worker_agent_id, token in document.items():
        if not isinstance(token, str):
            raise HTTPException(status_code=503, detail=_MALFORMED_WORKER_TOKENS)
        try:
            credentials.append((UUID(worker_agent_id), f"Bearer {token}".encode()))
        except ValueError as error:
            raise HTTPException(status_code=503, detail=_MALFORMED_WORKER_TOKENS) from error
    return tuple(credentials)


def _authenticate_worker(
    request: Request, credentials: tuple[tuple[UUID, bytes], ...]
) -> UUID | None:
    presented = request.headers.get("Authorization", "").encode()
    for worker_agent_id, expected in credentials:
        if hmac.compare_digest(presented, expected):
            return worker_agent_id
    return None


def _presents(request: Request, expected: str) -> bool:
    return hmac.compare_digest(
        request.headers.get("Authorization", "").encode(), f"Bearer {expected}".encode()
    )


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


@router.put("/runtime/v2/external-members/{member_agent_id}", response_model=None)
async def provision_external_member(
    member_agent_id: UUID,
    request: Request,
    body: ExternalWorkerProvisionRequest | None = None,
) -> dict[str, object]:
    """Make one registered principal an external *member* (adjudication D-11).

    The sibling of the route above, under the same rule the two preflights
    follow: the v1 entry point keeps refusing everything that is not a worker,
    and the ability to provision a Repository Leader arrives on a new path
    instead of changing what an old one accepts. An operator who has been
    PUTting to ``/runtime/external-workers/{id}`` gets the same answers today as
    yesterday, including the 409 for a leader.

    The request is still nothing but the path id, and for the reason it always
    was: every fact about the resulting resource belongs to somebody who is not
    the caller. Notably including the *role* — it is read from the agent
    directory, and preflight's whole job later is to confirm that RepoMesh and
    the enrollment agree about it, which a caller-stated role here would make
    circular. So the body model is v1's, unchanged and still ``extra=forbid``:
    there is nothing to add to a request that has no fields.

    The response is v1's receipt plus ``role``, and still carries no
    ``schemaVersion``: this answers the human who pressed the button, and the
    document a Bridge binds to is the versioned one preflight returns.

    Same guard (a local administrator's session), same idempotency key (derived
    from the agent alone, so provisioning through either path twice is one
    controller side effect), same status table — with ``organization_leader``
    joining the 409s rather than becoming a new code: RepoMesh has the
    principal, and it is refusing it.
    """

    await _authorize_administrator(request)
    container = request.app.state.container
    provisioner: ExternalMemberProvisioner | None = container.external_member_provisioner()
    if provisioner is None:
        raise HTTPException(status_code=503, detail="AgentTeams control plane is not configured")
    try:
        member = await ProvisionExternalMember(container.agent_directory, provisioner).execute(
            ProvisionExternalMemberCommand(member_agent_id=member_agent_id)
        )
    except UnknownExternalWorker as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ExternalWorkerRefused as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except WorkerControlPlaneUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return member.to_wire()


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
    _authorize_agent_action(request, body.worker_agent_id)
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


def _authorize_agent_action(request: Request, worker_agent_id: UUID) -> None:
    """Either the agent-action token, or the worker's own runner credential.

    The action token stays what it is: a shared secret with no subject, so
    whoever holds it starts any worker's task, exactly as today. A worker token
    names one worker, and starting work is the loudest thing on this router, so
    it is checked against the body rather than trusted to describe itself — the
    id in the body is the caller's claim, the one behind the token is not.

    The 503 widens the same way ``_authorize_runner``'s does: it fires only when
    neither credential kind is configured at all.
    """

    settings = get_settings()
    credentials = _worker_credentials()
    if not settings.agent_action_token and not credentials:
        raise HTTPException(status_code=503, detail="agent action token is not configured")
    if settings.agent_action_token and _presents(request, settings.agent_action_token):
        return
    authenticated = _authenticate_worker(request, credentials)
    if authenticated is None:
        raise HTTPException(status_code=401, detail="invalid agent action token")
    if authenticated != worker_agent_id:
        raise HTTPException(
            status_code=403, detail="a worker credential may only start its own task"
        )


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
