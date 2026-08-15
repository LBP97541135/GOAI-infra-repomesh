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
    AutomaticProjectTopologyCreate,
    BootstrapAdmin,
    CheckpointDecisionCreate,
    ManualAgentTeamCreate,
    NativeAgentCreate,
    ProjectControlCreate,
    ProjectTopologyCreate,
    RepositoryAgentTeamOnboard,
    TopologyPolicyDraftPut,
)
from repomesh.integrations.agentteams import RegisterNativeAgentRequest
from repomesh.modules.agent_directory.application import CreateAgentRequest
from repomesh.modules.agent_directory.contracts import AgentDirectoryError, AgentRole
from repomesh.modules.agent_runtime.ports.agent_team import (
    ManagerProjection,
    TeamMemberProjection,
    TeamProjection,
    TeamRole,
    WorkerProjection,
)
from repomesh.modules.identity_access import (
    LocalAccountConflict,
    LocalAccountValidationError,
    LocalAuthenticationError,
    LocalHumanAccountView,
)
from repomesh.modules.project import (
    ControlProjectCommand,
    CreateAutomaticProjectTopologyRequest,
    CreateProjectAgentTopologyRequest,
    HumanProjectGrantInput,
    RecordCheckpointDecisionCommand,
    RepositoryTeamAssignment,
)
from repomesh.modules.project.contracts import HumanReviewStatus, ProjectCheckpoint
from repomesh.modules.project.domain import (
    HumanProjectGrant,
    ProjectTopologyConflict,
    ProjectTopologyError,
    TopologyPolicyDraft,
    repository_agentteams_team_name,
)
from repomesh.settings import get_settings

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
    # Subclasses first, base last: both subclass LocalAuthenticationError, so
    # any other order makes the base clause swallow them and this endpoint goes
    # back to calling a twelve-character password rule a conflict.
    except LocalAccountValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except LocalAccountConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
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
    # Subclasses before the base, or the base clause wins every time — which is
    # exactly how "password must contain at least 12 characters" and "username
    # already exists" both used to leave here as 403, telling the form it had a
    # permission problem when it had a typing problem.
    except LocalAccountValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except LocalAccountConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except LocalAuthenticationError as error:
        # What is left is the genuine one: the actor is not an administrator.
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
        asdict(item.to_view()) for item in await request.app.state.container.agent_directory.list()
    ]


@router.post("/agents/native", status_code=status.HTTP_201_CREATED)
async def create_native_agent(body: NativeAgentCreate, request: Request) -> dict:
    """Create the AgentTeams runtime resource and its RepoMesh principal binding."""
    actor = await _account(request)
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="local administrator permission is required")
    principal = CreateAgentRequest(
        organization_id=body.organization_id,
        role=body.role,
        leader_agent_id=body.leader_agent_id,
        repository_id=body.repository_id,
        responsibility_paths=tuple(body.responsibility_paths),
        agentteams_resource_name=body.resource_name,
    )
    model = body.model or get_settings().deepseek_model
    manager = None
    worker = None
    if body.role is AgentRole.ORGANIZATION_LEADER:
        manager = ManagerProjection(
            name=body.resource_name,
            model=model,
            runtime=body.manager_runtime,
            skills=("project-management", "task-coordination"),
        )
    else:
        worker = WorkerProjection(
            name=body.resource_name,
            model=model,
            runtime=body.worker_runtime,
            identity=(
                "Repository Leader" if body.role is AgentRole.REPOSITORY_LEADER else "Coding Worker"
            ),
            skills=("git-delegation",),
        )
    try:
        created = await request.app.state.container.native_agent_registration().execute(
            RegisterNativeAgentRequest(principal=principal, manager=manager, worker=worker),
            idempotency_key=body.idempotency_key,
        )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return asdict(created.principal)


@router.post(
    "/repositories/{repository_id}/agent-team",
    status_code=status.HTTP_201_CREATED,
)
async def onboard_repository_agent_team(
    repository_id: UUID,
    body: RepositoryAgentTeamOnboard,
    request: Request,
) -> dict:
    """Create the durable repository Leader/Workers and their AgentTeams Team."""
    actor = await _account(request)
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="local administrator permission is required")
    repository = await request.app.state.container.repository_catalog.get(repository_id)
    if repository is None:
        raise HTTPException(status_code=404, detail="scan and register the repository first")
    directory = request.app.state.container.agent_directory
    leaders = [
        item
        for item in await directory.list_views()
        if item.organization_id == body.organization_id
        and item.role is AgentRole.ORGANIZATION_LEADER
        and item.status.value == "active"
    ]
    if len(leaders) != 1:
        raise HTTPException(
            status_code=422,
            detail="organization requires exactly one active Organization Leader",
        )
    organization_leader = leaders[0]
    prefix = f"repo-{repository_id.hex[:12]}"
    model = body.model or get_settings().deepseek_model
    registration = request.app.state.container.native_agent_registration()
    leader_name = f"{prefix}-leader"
    try:
        leader = await registration.execute(
            RegisterNativeAgentRequest(
                principal=CreateAgentRequest(
                    organization_id=body.organization_id,
                    role=AgentRole.REPOSITORY_LEADER,
                    agentteams_resource_name=leader_name,
                    leader_agent_id=organization_leader.id,
                    repository_id=repository_id,
                    responsibility_paths=tuple(body.responsibility_paths),
                ),
                worker=WorkerProjection(
                    name=leader_name,
                    model=model,
                    runtime=body.leader_runtime,
                    identity=f"Repository Leader for {repository.name}",
                    skills=("worker-management", "spec-authoring", "code-review"),
                ),
            ),
            idempotency_key=f"{body.idempotency_key}:leader",
        )
        workers = []
        for index in range(1, body.worker_count + 1):
            worker_name = f"{prefix}-worker-{index:02d}"
            workers.append(
                await registration.execute(
                    RegisterNativeAgentRequest(
                        principal=CreateAgentRequest(
                            organization_id=body.organization_id,
                            role=AgentRole.WORKER,
                            agentteams_resource_name=worker_name,
                            leader_agent_id=leader.principal.id,
                            repository_id=repository_id,
                            responsibility_paths=tuple(body.responsibility_paths),
                        ),
                        worker=WorkerProjection(
                            name=worker_name,
                            model=model,
                            runtime=body.worker_runtime,
                            identity=f"Coding Worker for {repository.name}",
                            skills=("git-delegation", "task-execution", "code-self-test"),
                        ),
                    ),
                    idempotency_key=f"{body.idempotency_key}:worker:{index:02d}",
                )
            )
        control_plane = request.app.state.container.agent_team_control_plane
        if control_plane is None:
            raise RuntimeError("AgentTeams control plane is not configured")
        team = await control_plane.ensure_team(
            TeamProjection(
                name=repository_agentteams_team_name(repository_id),
                description=f"Long-lived repository team for {repository.name}",
                members=(
                    TeamMemberProjection(leader_name, TeamRole.LEADER),
                    *(
                        TeamMemberProjection(
                            item.principal.agentteams_resource_name,
                            TeamRole.WORKER,
                        )
                        for item in workers
                    ),
                ),
            ),
            idempotency_key=f"{body.idempotency_key}:team",
        )
    except AgentDirectoryError as error:
        # Caught ahead of RuntimeError, which it subclasses, because the two
        # answers mean opposite things to whoever pressed the button. The
        # directory refuses for reasons no retry outlasts — chiefly the
        # repository-leader singleton (`repository:{id}:leader`): a repository
        # whose leader was already provisioned by a console round's
        # `ProvisionRepositoryAgentTeam` has one, under that path's own name,
        # and asking for a second is permanently refused. Reporting that as
        # 503 invites exactly the retry that cannot work; 409 says the
        # repository is already staffed, which is both true and actionable.
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {
        "repository_id": repository_id,
        "repository_name": repository.name,
        "leader": asdict(leader.principal),
        "workers": [asdict(item.principal) for item in workers],
        "team": asdict(team),
    }


@router.post("/agent-teams", status_code=status.HTTP_201_CREATED)
async def create_manual_agent_team(body: ManualAgentTeamCreate, request: Request) -> dict:
    """Compose an AgentTeams Team from existing RepoMesh agent principals."""
    actor = await _account(request)
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="local administrator permission is required")
    directory = request.app.state.container.agent_directory
    leader = await directory.get_view(body.leader_agent_id)
    if leader is None:
        raise HTTPException(status_code=422, detail="Leader Agent does not exist")
    if leader.organization_id != body.organization_id:
        raise HTTPException(status_code=422, detail="Leader Agent belongs to another organization")
    if leader.role is not AgentRole.REPOSITORY_LEADER:
        raise HTTPException(status_code=422, detail="Team Leader must be a Repository Leader")
    if len(set(body.member_agent_ids)) != len(body.member_agent_ids):
        raise HTTPException(status_code=422, detail="Team members must be unique")
    if body.leader_agent_id in body.member_agent_ids:
        raise HTTPException(status_code=422, detail="Leader cannot also be a Team member")

    members = []
    for agent_id in body.member_agent_ids:
        member = await directory.get_view(agent_id)
        if member is None:
            raise HTTPException(status_code=422, detail=f"Team member does not exist: {agent_id}")
        if member.organization_id != body.organization_id:
            raise HTTPException(
                status_code=422,
                detail="Team member belongs to another organization",
            )
        if member.role is not AgentRole.WORKER or member.leader_agent_id != leader.id:
            raise HTTPException(
                status_code=422,
                detail="Team members must be Workers managed by the selected Leader",
            )
        members.append(member)

    control_plane = request.app.state.container.agent_team_control_plane
    if control_plane is None:
        raise HTTPException(status_code=503, detail="AgentTeams control plane is not configured")
    try:
        team = await control_plane.ensure_team(
            TeamProjection(
                name=body.name,
                description=body.description.strip() or None,
                members=(
                    TeamMemberProjection(leader.agentteams_resource_name, TeamRole.LEADER),
                    *(
                        TeamMemberProjection(
                            member.agentteams_resource_name,
                            TeamRole.WORKER,
                        )
                        for member in members
                    ),
                ),
            ),
            idempotency_key=body.idempotency_key,
        )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {
        "team": asdict(team),
        "leader": asdict(leader),
        "members": [asdict(member) for member in members],
    }


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
    # Subclass before base, or the base clause takes both — which is how "this
    # project already has a topology" and "your grant is scoped wrong" left
    # here under the same code, telling a caller who cannot fix anything to go
    # fix their request. Same family as the two account clauses above.
    except ProjectTopologyConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ProjectTopologyError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return asdict(topology)


@router.post("/projects/automatic-topologies", status_code=status.HTTP_201_CREATED)
async def create_automatic_project_topology(
    body: AutomaticProjectTopologyCreate,
    request: Request,
) -> dict:
    actor = await _account(request)
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="local administrator permission is required")
    account_service = request.app.state.container.local_account_service()
    for grant in body.human_grants:
        if await account_service.get_account(grant.human_principal_id) is None:
            raise HTTPException(status_code=422, detail="human grant account does not exist")
    try:
        topology = await request.app.state.container.automatic_project_topology_creator().execute(
            CreateAutomaticProjectTopologyRequest(
                organization_id=body.organization_id,
                project_id=body.project_id,
                repository_ids=tuple(body.repository_ids),
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
    # The same two clauses as its sibling above, for the same reason:
    # ``CreateAutomaticProjectTopology.execute`` delegates to
    # ``CreateProjectAgentTopology.execute``, so both conflict flavours arrive
    # here too. Fixing only the sibling would have been worse than fixing
    # neither — a half-fixed error map reads as a finished one.
    except ProjectTopologyConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
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


@router.put("/projects/{project_id}/policy-draft")
async def put_project_policy_draft(
    project_id: UUID, body: TopologyPolicyDraftPut, request: Request
) -> dict:
    """Set the supervision policy that materialization will read back.

    Behind an admin session while materialization stays behind the console's
    shared action token, and that asymmetry is the point: a holder of the
    shared token can still *trigger* a policy — it always could, that is what
    the materialize button is — but has no way to *set* one, and triggering can
    only make supervision stricter than it was.

    Whole-document overwrite. ``PUT`` rather than ``POST`` because a
    requirement holds one supervision intent and changing your mind replaces
    it; there is no second draft to be idempotent about, so no idempotency key
    either.
    """

    actor = await _account(request)
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="local administrator permission is required")
    account_service = request.app.state.container.local_account_service()
    for grant in body.human_grants:
        if await account_service.get_account(grant.human_principal_id) is None:
            raise HTTPException(status_code=422, detail="human grant account does not exist")
    try:
        # Real ``HumanProjectGrant`` objects, not payload rows forwarded to
        # ``assert_supervision_policy``. That function takes a Protocol and
        # reads two fields off each grant; the four rules about a *single*
        # grant — a repository supervisor needs a repository, the other two
        # roles may not have one, path scope needs repository scope, at least
        # one control action — are in ``HumanProjectGrant.__post_init__`` and
        # run only if the object is actually constructed. Handing over
        # something merely shaped like a grant would let a draft save with a
        # repository supervisor scoped to nothing and then refuse to
        # materialize, minutes later and somewhere else.
        draft = TopologyPolicyDraft(
            project_id=project_id,
            created_by=actor.id,
            execution_mode=body.execution_mode,
            required_checkpoints=frozenset(body.required_checkpoints),
            human_grants=tuple(
                HumanProjectGrant(
                    human_principal_id=item.human_principal_id,
                    role=item.role,
                    code_access=item.code_access,
                    control_actions=frozenset(item.control_actions),
                    repository_id=item.repository_id,
                    path_patterns=tuple(item.path_patterns),
                )
                for item in body.human_grants
            ),
        )
    # Verbatim, never folded into a generic "invalid request": every sentence
    # here names the rule and the field, and the caller is the person who has
    # to change one of them. The one rule that cannot be answered now is
    # whether a grant's repository survives into the plan — only the topology
    # has the teams to check that — so materialization keeps the right to
    # refuse, loudly.
    except ProjectTopologyError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    stored = await request.app.state.container.topology_policy_draft_store().upsert(draft)
    return asdict(stored)


@router.get("/projects/{project_id}/policy-draft")
async def get_project_policy_draft(project_id: UUID, request: Request) -> dict:
    """Read the draft. Absent is 404, the same word ``GET .../topology`` uses.

    Not a 200 with an empty policy: "nobody has decided yet" and "someone
    decided on nothing in particular" are different facts, and the console
    already branches on the 404 for the topology.
    """

    actor = await _account(request)
    draft = await request.app.state.container.topology_policy_draft_store().get(project_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="project policy draft does not exist")
    if not actor.is_admin and not any(
        grant.human_principal_id == actor.id for grant in draft.human_grants
    ):
        raise HTTPException(status_code=403, detail="human project membership is required")
    return asdict(draft)


@router.delete("/projects/{project_id}/policy-draft", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_policy_draft(project_id: UUID, request: Request) -> None:
    """Withdraw the draft, putting the project back to running unattended.

    Admin session, for the same reason the write is: this is the one way to
    make a project *less* supervised, and the shared action token must not
    reach it. Nothing to withdraw answers 404 rather than a cheerful 204 —
    ``delete`` returns that fact on purpose, and an admin who thinks they just
    cancelled a policy that some other admin cancelled first should be told.
    """

    actor = await _account(request)
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="local administrator permission is required")
    withdrawn = await request.app.state.container.topology_policy_draft_store().delete(project_id)
    if not withdrawn:
        raise HTTPException(status_code=404, detail="project policy draft does not exist")


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
async def control_project(project_id: UUID, body: ProjectControlCreate, request: Request) -> dict:
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
