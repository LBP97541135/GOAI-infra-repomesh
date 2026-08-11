import asyncio
import json
from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from repomesh.api.human_control_models import (
    AccountCreate,
    AccountCredentials,
    BootstrapAdmin,
    CheckpointDecisionCreate,
    ProjectControlCreate,
    ProjectTopologyCreate,
)
from repomesh.modules.identity_access import (
    LocalAuthenticationError,
    LocalHumanAccountView,
)
from repomesh.modules.project import (
    ControlProjectCommand,
    CreateProjectAgentTopologyRequest,
    HumanProjectGrantInput,
    RecordCheckpointDecisionCommand,
    RepositoryTeamAssignment,
)
from repomesh.modules.project.contracts import HumanReviewStatus, ProjectCheckpoint
from repomesh.modules.project.domain import ProjectTopologyConflict, ProjectTopologyError

router = APIRouter(tags=["human-control"])
SESSION_COOKIE = "repomesh_session"


def _bearer(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        return token
    raise HTTPException(status_code=401, detail="local authentication is required")


async def _account(request: Request) -> LocalHumanAccountView:
    try:
        return await request.app.state.container.local_account_service().authenticate(
            _bearer(request)
        )
    except LocalAuthenticationError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error


@router.post("/auth/bootstrap", status_code=status.HTTP_201_CREATED)
async def bootstrap(body: BootstrapAdmin, request: Request) -> dict:
    try:
        account = await request.app.state.container.local_account_service().bootstrap_admin(
            body.username, body.password.get_secret_value(), body.display_name
        )
    except LocalAuthenticationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return asdict(account)


@router.post("/auth/login")
async def login(body: AccountCredentials, request: Request, response: Response) -> dict:
    try:
        token, account = await request.app.state.container.local_account_service().login(
            body.username, body.password.get_secret_value()
        )
    except LocalAuthenticationError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=request.app.state.container.local_account_service().session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=False,
    )
    return {"access_token": token, "token_type": "bearer", "account": asdict(account)}


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response) -> None:
    token = _bearer(request)
    await _account(request)
    await request.app.state.container.local_account_service().logout(token)
    response.delete_cookie(SESSION_COOKIE)


@router.get("/auth/me")
async def me(request: Request) -> dict:
    return asdict(await _account(request))


@router.post("/auth/accounts", status_code=status.HTTP_201_CREATED)
async def create_account(body: AccountCreate, request: Request) -> dict:
    actor = await _account(request)
    try:
        account = await request.app.state.container.local_account_service().create_account(
            actor,
            body.username,
            body.password.get_secret_value(),
            body.display_name,
            is_admin=body.is_admin,
        )
    except LocalAuthenticationError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    return asdict(account)


@router.get("/auth/accounts")
async def list_accounts(request: Request) -> list[dict]:
    actor = await _account(request)
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="local administrator permission is required")
    return [
        asdict(item)
        for item in await request.app.state.container.local_account_service().list_accounts()
    ]


@router.get("/agents")
async def list_agent_principals(request: Request) -> list[dict]:
    await _account(request)
    return [
        asdict(item.to_view())
        for item in await request.app.state.container.agent_directory.list()
    ]


async def _reviews_for(request: Request, actor: LocalHumanAccountView, review_status):
    store = request.app.state.container.human_review_request_store()
    if actor.is_admin:
        return await store.list_all(status=review_status)
    return await store.list_for_human(actor.id, status=review_status)


@router.get("/review-requests")
async def list_review_requests(
    request: Request,
    review_status: Annotated[HumanReviewStatus | None, Query(alias="status")] = None,
) -> list[dict]:
    actor = await _account(request)
    return [asdict(item.to_view()) for item in await _reviews_for(request, actor, review_status)]


@router.get("/review-requests/events")
async def review_request_events(request: Request) -> StreamingResponse:
    actor = await _account(request)

    async def stream():
        previous = ""
        yield "retry: 2000\n\n"
        while not await request.is_disconnected():
            reviews = await _reviews_for(request, actor, HumanReviewStatus.PENDING)
            payload = json.dumps(
                [asdict(item.to_view()) for item in reviews],
                default=str,
                ensure_ascii=False,
            )
            if payload != previous:
                yield f"event: review-requests\ndata: {payload}\n\n"
                previous = payload
            else:
                yield ": keep-alive\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/projects/topologies", status_code=status.HTTP_201_CREATED)
async def create_project_topology(body: ProjectTopologyCreate, request: Request) -> dict:
    actor = await _account(request)
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="local administrator permission is required")
    account_service = request.app.state.container.local_account_service()
    for grant in body.human_grants:
        if await account_service.get_account(grant.human_principal_id) is None:
            raise HTTPException(status_code=422, detail="human grant account does not exist")
    try:
        topology = await request.app.state.container.project_topology_creator().execute(
            CreateProjectAgentTopologyRequest(
                organization_id=body.organization_id,
                project_id=body.project_id,
                organization_leader_id=body.organization_leader_id,
                repository_teams=tuple(
                    RepositoryTeamAssignment(
                        repository_id=item.repository_id,
                        leader_agent_id=item.leader_agent_id,
                        worker_agent_ids=tuple(item.worker_agent_ids),
                    )
                    for item in body.repository_teams
                ),
                execution_mode=body.execution_mode,
                required_checkpoints=frozenset(body.required_checkpoints),
                human_grants=tuple(
                    HumanProjectGrantInput(
                        human_principal_id=item.human_principal_id,
                        role=item.role,
                        code_access=item.code_access,
                        control_actions=frozenset(item.control_actions),
                        repository_id=item.repository_id,
                        path_patterns=tuple(item.path_patterns),
                    )
                    for item in body.human_grants
                ),
            ),
            idempotency_key=body.idempotency_key,
        )
    except ProjectTopologyError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return asdict(topology)


@router.get("/projects/{project_id}/topology")
async def get_project_topology(project_id: UUID, request: Request) -> dict:
    actor = await _account(request)
    topology = await request.app.state.container.topology_reader().get_view(project_id)
    if topology is None:
        raise HTTPException(status_code=404, detail="project topology does not exist")
    if not actor.is_admin and not any(
        grant.human_principal_id == actor.id for grant in topology.human_grants
    ):
        raise HTTPException(status_code=403, detail="human project membership is required")
    return asdict(topology)


@router.get("/projects/{project_id}/checkpoint-gate")
async def evaluate_checkpoint_gate(
    project_id: UUID,
    request: Request,
    checkpoint: Annotated[ProjectCheckpoint, Query()],
    evidence_version: Annotated[str, Query(min_length=1)],
    repository_id: UUID | None = None,
) -> dict:
    actor = await _account(request)
    topology = await request.app.state.container.topology_reader().get_view(project_id)
    if topology is None:
        raise HTTPException(status_code=404, detail="project topology does not exist")
    if not actor.is_admin and not any(
        grant.human_principal_id == actor.id for grant in topology.human_grants
    ):
        raise HTTPException(status_code=403, detail="human project membership is required")
    return asdict(
        await request.app.state.container.project_checkpoint_service().evaluate(
            project_id,
            checkpoint,
            evidence_version,
            repository_id=repository_id,
        )
    )


@router.post("/projects/{project_id}/checkpoint-decisions")
async def record_checkpoint_decision(
    project_id: UUID, body: CheckpointDecisionCreate, request: Request
) -> dict:
    actor = await _account(request)
    try:
        decision = await request.app.state.container.project_checkpoint_service().record(
            RecordCheckpointDecisionCommand(
                project_id=project_id,
                review_request_id=body.review_request_id,
                human_principal_id=actor.id,
                decision=body.decision,
                reason=body.reason,
            )
        )
    except ProjectTopologyConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ProjectTopologyError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    return asdict(decision)


@router.post("/projects/{project_id}/control")
async def control_project(
    project_id: UUID, body: ProjectControlCreate, request: Request
) -> dict:
    actor = await _account(request)
    try:
        topology = await request.app.state.container.project_lifecycle_service().control(
            ControlProjectCommand(
                project_id=project_id,
                human_principal_id=actor.id,
                action=body.action,
            )
        )
    except ProjectTopologyError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    return asdict(topology)
