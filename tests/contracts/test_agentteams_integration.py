import json
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest

from repomesh.integrations.agentteams.control_plane import (
    AGENTTEAMS_COMMIT,
    AGENTTEAMS_VERSION,
    AgentTeamsConflict,
    AgentTeamsControlPlaneClient,
)
from repomesh.integrations.agentteams.matrix import AgentTeamsMatrixClient
from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.agent_runtime.application.external_worker import (
    ResolveExternalWorkerBinding,
)
from repomesh.modules.agent_runtime.contracts import (
    EXTERNAL_WORKER_BINDING_SCHEMA_VERSION,
    ExternalWorkerBindingQuery,
    ExternalWorkerRefused,
)
from repomesh.modules.agent_runtime.ports.agent_team import (
    ChannelPolicyProjection,
    ManagerProjection,
    McpServerProjection,
    TeamMemberProjection,
    TeamProjection,
    TeamRole,
    WorkerProjection,
    WorkerRuntime,
)


def response(status: int, payload: dict[str, object] | None = None) -> httpx.Response:
    return httpx.Response(status, json=payload) if payload is not None else httpx.Response(status)


@pytest.mark.asyncio
async def test_health_and_version_use_v12_controller_endpoints() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/healthz":
            return httpx.Response(200, text="ok")
        assert request.headers["Authorization"] == "Bearer controller-token"
        return response(200, {"controller": "v1.2.0", "kubeMode": "embedded"})

    client = AgentTeamsControlPlaneClient(
        "http://agentteams:8090",
        "controller-token",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert await client.health() is True
        version = await client.version()
    finally:
        await client.close()

    assert version.controller == "v1.2.0"
    assert version.kube_mode == "embedded"
    assert paths == ["/healthz", "/api/v1/version"]


@pytest.mark.asyncio
async def test_ensure_worker_gets_before_create_and_sends_v12_payload() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return response(404, {"error": "not found"})
        payload = json.loads(request.content)
        assert payload == {
            "name": "repomesh-worker-api",
            "model": "qwen3.6-plus",
            "runtime": "hermes",
            "skills": ["github-operations"],
            "state": "Running",
            "identity": "Backend coding worker",
        }
        return response(201, {**payload, "phase": "Pending"})

    client = AgentTeamsControlPlaneClient(
        "http://agentteams:8090", transport=httpx.MockTransport(handler)
    )
    try:
        worker = await client.ensure_worker(
            WorkerProjection(
                name="repomesh-worker-api",
                model="qwen3.6-plus",
                runtime=WorkerRuntime.HERMES,
                identity="Backend coding worker",
                skills=("github-operations",),
            ),
            idempotency_key="project-1-worker-api-v1",
        )
    finally:
        await client.close()

    assert worker.name == "repomesh-worker-api"
    assert worker.phase == "Pending"
    assert [(item.method, item.url.path) for item in requests] == [
        ("GET", "/api/v1/workers/repomesh-worker-api"),
        ("POST", "/api/v1/workers"),
    ]
    assert requests[1].headers["Idempotency-Key"] == "project-1-worker-api-v1"


@pytest.mark.asyncio
async def test_ensure_worker_reuses_matching_projection_without_post() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return response(
            200,
            {
                "name": "repomesh-worker-api",
                "model": "qwen3.6-plus",
                "runtime": "hermes",
                "phase": "Ready",
            },
        )

    client = AgentTeamsControlPlaneClient(
        "http://agentteams:8090", transport=httpx.MockTransport(handler)
    )
    try:
        worker = await client.ensure_worker(
            WorkerProjection("repomesh-worker-api", "qwen3.6-plus", WorkerRuntime.HERMES),
            idempotency_key="same-request",
        )
    finally:
        await client.close()

    assert worker.phase == "Ready"
    assert methods == ["GET"]


@pytest.mark.asyncio
async def test_worker_creation_projects_identity_prompts_mcp_and_channel_policy() -> None:
    posted: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posted
        if request.method == "GET":
            return response(404, {"error": "not found"})
        posted = json.loads(request.content)
        return response(201, {**posted, "phase": "Pending"})

    client = AgentTeamsControlPlaneClient(
        "http://agentteams:8090", transport=httpx.MockTransport(handler)
    )
    try:
        await client.ensure_worker(
            WorkerProjection(
                name="repomesh-worker-secure",
                model="qwen3.6-plus",
                identity="Repository worker",
                soul="Be precise.",
                agents="Follow the assigned task only.",
                skills=("task-management",),
                mcp_servers=(
                    McpServerProjection("github", "https://gateway.example/mcp/github"),
                ),
                channel_policy=ChannelPolicyProjection(
                    dm_deny_extra=("unrelated-worker",),
                ),
            ),
            idempotency_key="secure-worker-v1",
        )
    finally:
        await client.close()

    assert posted["soul"] == "Be precise."
    assert posted["agents"] == "Follow the assigned task only."
    assert posted["mcpServers"] == [
        {
            "name": "github",
            "url": "https://gateway.example/mcp/github",
            "transport": "http",
        }
    ]
    assert posted["channelPolicy"] == {
        "groupAllowExtra": [],
        "groupDenyExtra": [],
        "dmAllowExtra": [],
        "dmDenyExtra": ["unrelated-worker"],
    }


@pytest.mark.asyncio
async def test_ensure_worker_rejects_different_existing_projection() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return response(
            200,
            {
                "name": "repomesh-worker-api",
                "model": "different-model",
                "runtime": "hermes",
                "phase": "Ready",
            },
        )

    client = AgentTeamsControlPlaneClient(
        "http://agentteams:8090", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(AgentTeamsConflict, match="model"):
            await client.ensure_worker(
                WorkerProjection("repomesh-worker-api", "qwen3.6-plus", WorkerRuntime.HERMES),
                idempotency_key="conflicting-request",
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_team_references_independently_created_workers() -> None:
    posted: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posted
        if request.method == "GET":
            return response(404, {"error": "not found"})
        posted = json.loads(request.content)
        return response(
            201,
            {
                **posted,
                "phase": "Pending",
                "leaderName": "repomesh-worker-lead",
                "readyWorkers": 0,
                "totalWorkers": 2,
                "leaderDMRoomID": "!leader:matrix.local",
            },
        )

    projection = TeamProjection(
        name="repomesh-team-project",
        members=(
            TeamMemberProjection("repomesh-worker-lead", TeamRole.LEADER),
            TeamMemberProjection("repomesh-worker-api", TeamRole.WORKER),
        ),
        description="RepoMesh project team",
        heartbeat_every="30m",
    )
    client = AgentTeamsControlPlaneClient(
        "http://agentteams:8090", transport=httpx.MockTransport(handler)
    )
    try:
        team = await client.ensure_team(projection, idempotency_key="project-1-team-v1")
    finally:
        await client.close()

    assert posted["leader"] == {"name": "repomesh-worker-lead"}
    assert posted["workerMembers"] == [
        {"name": "repomesh-worker-lead", "role": "team_leader"},
        {"name": "repomesh-worker-api", "role": "worker"},
    ]
    assert team.leader_name == "repomesh-worker-lead"
    assert team.total_workers == 2
    assert team.leader_room_id == "!leader:matrix.local"


@pytest.mark.asyncio
async def test_manager_and_worker_lifecycle_use_distinct_endpoints() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return response(404, {"error": "not found"})
        if request.url.path == "/api/v1/managers":
            payload = json.loads(request.content)
            return response(201, {**payload, "phase": "Pending"})
        return response(200, {"name": "repomesh-worker-api", "phase": "Ready"})

    client = AgentTeamsControlPlaneClient(
        "http://agentteams:8090", transport=httpx.MockTransport(handler)
    )
    try:
        manager = await client.ensure_manager(
            ManagerProjection("repomesh-manager-main", "qwen3.6-plus"),
            idempotency_key="manager-v1",
        )
        worker = await client.ensure_worker_ready(
            "repomesh-worker-api", idempotency_key="run-1-ready"
        )
    finally:
        await client.close()

    assert manager.name == "repomesh-manager-main"
    assert worker.phase == "Ready"
    assert calls[-1] == ("POST", "/api/v1/workers/repomesh-worker-api/ensure-ready")


@pytest.mark.asyncio
async def test_get_manager_exposes_matrix_identity_for_inbound_authentication() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return response(
            200,
            {
                "name": "repomesh-manager-main",
                "phase": "Ready",
                "matrixUserID": "@repomesh-manager-main:matrix.local",
            },
        )

    client = AgentTeamsControlPlaneClient(
        "http://agentteams:8090", transport=httpx.MockTransport(handler)
    )
    try:
        manager = await client.get_manager("repomesh-manager-main")
    finally:
        await client.close()
    assert manager is not None
    assert manager.matrix_user_id == "@repomesh-manager-main:matrix.local"


@pytest.mark.asyncio
async def test_matrix_task_uses_transaction_id_as_idempotency_key() -> None:
    captured: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return response(200, {"event_id": "$event-1"})

    client = AgentTeamsMatrixClient(
        "http://matrix:6167",
        "matrix-token",
        transport=httpx.MockTransport(handler),
    )
    try:
        event_id = await client.send_task(
            "!team:matrix.local",
            "Implement task PRJ-1-api-01",
            transaction_id="run-1-attempt-1",
        )
    finally:
        await client.close()

    assert captured is not None
    assert captured.method == "PUT"
    assert captured.url.path.endswith(
        "/rooms/!team:matrix.local/send/m.room.message/run-1-attempt-1"
    )
    assert captured.headers["Authorization"] == "Bearer matrix-token"
    assert json.loads(captured.content) == {
        "msgtype": "m.text",
        "body": "Implement task PRJ-1-api-01",
    }
    assert event_id == "$event-1"


@pytest.mark.asyncio
async def test_matrix_sync_extracts_joined_text_messages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/_matrix/client/v3/sync"
        assert request.url.params["since"] == "batch-1"
        return response(
            200,
            {
                "next_batch": "batch-2",
                "rooms": {
                    "join": {
                        "!team:matrix.local": {
                            "timeline": {
                                "events": [
                                    {
                                        "type": "m.room.message",
                                        "event_id": "$report-1",
                                        "sender": "@worker:matrix.local",
                                        "origin_server_ts": 1_787_000_000_000,
                                        "content": {
                                            "msgtype": "m.text",
                                            "body": '{"schema":"repomesh.agent-report.v1"}',
                                        },
                                    },
                                    {"type": "m.room.member", "event_id": "$member"},
                                    # No ``origin_server_ts``: dropped rather
                                    # than stamped with our clock, because the
                                    # room's order is the room's own (PR 9).
                                    {
                                        "type": "m.room.message",
                                        "event_id": "$undated",
                                        "sender": "@worker:matrix.local",
                                        "content": {"msgtype": "m.text", "body": "hello"},
                                    },
                                ]
                            }
                        }
                    }
                },
            },
        )

    client = AgentTeamsMatrixClient(
        "http://matrix:6167",
        "matrix-token",
        transport=httpx.MockTransport(handler),
    )
    try:
        batch = await client.sync_once(since="batch-1", timeout_ms=1000)
    finally:
        await client.close()
    assert batch.next_batch == "batch-2"
    assert len(batch.messages) == 1
    assert batch.messages[0].event_id == "$report-1"
    assert batch.messages[0].room_id == "!team:matrix.local"
    assert batch.messages[0].origin_server_ts == 1_787_000_000_000


# ---------------------------------------------------------------------------
# External workers: containerManaged travels the wire (ADR 0004 decision 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_default_worker_projection_stays_container_managed() -> None:
    """The key is *absent* from the payload, and that absence is the assertion.

    The controller defaults ``containerManaged`` to true
    (``resource_handler.go``), so a managed worker must keep asking for exactly
    the document RepoMesh asks for today. A new key in the managed payload
    would be a second spelling of the same resource, which is the 409 the
    field-for-field rule exists to avoid.
    """

    posted: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posted
        if request.method == "GET":
            return response(404, {"error": "not found"})
        posted = json.loads(request.content)
        return response(201, {**posted, "phase": "Pending", "containerManaged": True})

    client = AgentTeamsControlPlaneClient(
        "http://agentteams:8090", transport=httpx.MockTransport(handler)
    )
    try:
        worker = await client.ensure_worker(
            WorkerProjection("repomesh-worker-api", "qwen3.6-plus", WorkerRuntime.HERMES),
            idempotency_key="managed-worker-v1",
        )
    finally:
        await client.close()

    assert WorkerProjection("repomesh-worker-api", "qwen3.6-plus").container_managed is True
    assert "containerManaged" not in posted
    assert worker.container_managed is True


@pytest.mark.asyncio
async def test_an_explicit_external_worker_asks_for_container_managed_false() -> None:
    """The only path that ever sends the field, and it sends ``false``."""

    posted: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posted
        if request.method == "GET":
            return response(404, {"error": "not found"})
        posted = json.loads(request.content)
        return response(201, {**posted, "phase": "Pending"})

    client = AgentTeamsControlPlaneClient(
        "http://agentteams:8090", transport=httpx.MockTransport(handler)
    )
    try:
        worker = await client.ensure_worker(
            WorkerProjection(
                "repomesh-worker-bridge",
                "qwen3.6-plus",
                WorkerRuntime.HERMES,
                container_managed=False,
            ),
            idempotency_key="external-worker-v1",
        )
    finally:
        await client.close()

    assert posted["containerManaged"] is False
    assert worker.container_managed is False


@pytest.mark.asyncio
async def test_an_existing_managed_worker_refuses_an_external_projection() -> None:
    """managed → external is a conflict, never a silent conversion."""

    def handler(request: httpx.Request) -> httpx.Response:
        return response(
            200,
            {
                "name": "repomesh-worker-api",
                "model": "qwen3.6-plus",
                "runtime": "hermes",
                "phase": "Ready",
                "containerManaged": True,
            },
        )

    client = AgentTeamsControlPlaneClient(
        "http://agentteams:8090", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(AgentTeamsConflict, match="containerManaged"):
            await client.ensure_worker(
                WorkerProjection(
                    "repomesh-worker-api",
                    "qwen3.6-plus",
                    WorkerRuntime.HERMES,
                    container_managed=False,
                ),
                idempotency_key="convert-to-external",
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_an_existing_external_worker_refuses_a_managed_projection() -> None:
    """And the other direction, which is the one a stray default would take.

    A Bridge-backed worker adopted by the ordinary project path would have its
    container created under it — the controller would start a second body for
    an identity a local process is already serving.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return response(
            200,
            {
                "name": "repomesh-worker-bridge",
                "model": "qwen3.6-plus",
                "runtime": "hermes",
                "phase": "Ready",
                "containerManaged": False,
            },
        )

    client = AgentTeamsControlPlaneClient(
        "http://agentteams:8090", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(AgentTeamsConflict, match="containerManaged"):
            await client.ensure_worker(
                WorkerProjection(
                    "repomesh-worker-bridge", "qwen3.6-plus", WorkerRuntime.HERMES
                ),
                idempotency_key="adopt-as-managed",
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_worker_document_without_the_field_cannot_confirm_external() -> None:
    """Silence is not confirmation.

    The v1.2.0 controller always writes ``containerManaged`` on a worker
    document, so a document without it did not come from a controller that
    knows the field. Reusing it for an external request would adopt a resource
    whose container the controller may well be managing.
    """

    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return response(
            200,
            {
                "name": "repomesh-worker-bridge",
                "model": "qwen3.6-plus",
                "runtime": "hermes",
                "phase": "Ready",
            },
        )

    client = AgentTeamsControlPlaneClient(
        "http://agentteams:8090", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(AgentTeamsConflict, match="containerManaged"):
            await client.ensure_worker(
                WorkerProjection(
                    "repomesh-worker-bridge",
                    "qwen3.6-plus",
                    WorkerRuntime.HERMES,
                    container_managed=False,
                ),
                idempotency_key="unconfirmed-external",
            )
    finally:
        await client.close()

    assert methods == ["GET"]


@pytest.mark.asyncio
async def test_a_create_that_races_into_409_is_reconciled_on_container_managed() -> None:
    """The other arrival at an existing resource: POST first, 409, then read.

    ``_create_or_reconcile`` treats a matching existing resource as success, so
    this is the path where a managed worker could be handed back as if the
    external request had succeeded.
    """

    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return response(409, {"error": "already exists"})
        if len([item for item in calls if item[0] == "GET"]) == 1:
            return response(404, {"error": "not found"})
        return response(
            200,
            {
                "name": "repomesh-worker-bridge",
                "model": "qwen3.6-plus",
                "runtime": "hermes",
                "phase": "Ready",
                "containerManaged": True,
            },
        )

    client = AgentTeamsControlPlaneClient(
        "http://agentteams:8090", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(AgentTeamsConflict, match="containerManaged"):
            await client.ensure_worker(
                WorkerProjection(
                    "repomesh-worker-bridge",
                    "qwen3.6-plus",
                    WorkerRuntime.HERMES,
                    container_managed=False,
                ),
                idempotency_key="racing-external",
            )
    finally:
        await client.close()

    assert [method for method, _ in calls] == ["GET", "POST", "GET"]


@pytest.mark.asyncio
async def test_get_worker_observes_container_managed_alongside_team_and_identity() -> None:
    """One read answers every fact the preflight binding is made of."""

    def handler(request: httpx.Request) -> httpx.Response:
        return response(
            200,
            {
                "name": "repomesh-worker-bridge",
                "phase": "Ready",
                "runtime": "hermes",
                "containerManaged": False,
                "matrixUserID": "@repomesh-worker-bridge:matrix.local",
                "roomID": "!worker-bridge:matrix.local",
                "team": "repomesh-team-pricing",
            },
        )

    client = AgentTeamsControlPlaneClient(
        "http://agentteams:8090", transport=httpx.MockTransport(handler)
    )
    try:
        worker = await client.get_worker("repomesh-worker-bridge")
    finally:
        await client.close()

    assert worker is not None
    assert worker.container_managed is False
    assert worker.team == "repomesh-team-pricing"
    assert worker.matrix_user_id == "@repomesh-worker-bridge:matrix.local"
    assert worker.room_id == "!worker-bridge:matrix.local"


# ---------------------------------------------------------------------------
# Preflight: the controller's documents become the frozen v1 binding
# ---------------------------------------------------------------------------

BINDING_SCHEMA = json.loads(
    (
        Path(__file__).parents[2]
        / "contracts"
        / "agent-bridge"
        / "v1"
        / "external-worker-binding.schema.json"
    ).read_text(encoding="utf-8")
)


class _OneWorkerDirectory:
    """The RepoMesh half of the binding: one principal, read by id."""

    def __init__(self, principal: AgentPrincipalView) -> None:
        self._principal = principal

    async def get_view(self, agent_id: UUID) -> AgentPrincipalView | None:
        return self._principal if agent_id == self._principal.id else None

    async def list_views(self) -> tuple[AgentPrincipalView, ...]:
        return (self._principal,)


def _worker_principal(agent_id: UUID, organization_id: UUID) -> AgentPrincipalView:
    return AgentPrincipalView(
        id=agent_id,
        organization_id=organization_id,
        role=AgentRole.WORKER,
        leader_agent_id=uuid4(),
        repository_id=uuid4(),
        responsibility_paths=("**",),
        agentteams_resource_name="repomesh-worker-bridge",
        status=AgentPrincipalStatus.ACTIVE,
    )


def _bridge_worker_controller() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/workers/repomesh-worker-bridge":
            return response(
                200,
                {
                    "name": "repomesh-worker-bridge",
                    "phase": "Ready",
                    "runtime": "hermes",
                    "containerManaged": False,
                    "matrixUserID": "@repomesh-worker-bridge:matrix.local",
                    "roomID": "!worker-bridge:matrix.local",
                    "team": "repomesh-team-pricing",
                },
            )
        if request.url.path == "/api/v1/teams/repomesh-team-pricing":
            return response(
                200,
                {
                    "name": "repomesh-team-pricing",
                    "phase": "Ready",
                    "teamRoomID": "!team-pricing:matrix.local",
                    "leaderDMRoomID": "!lead-pricing:matrix.local",
                    "leaderName": "repomesh-worker-lead",
                    "readyWorkers": 2,
                    "totalWorkers": 2,
                },
            )
        return response(404, {"error": "not found"})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_preflight_answers_the_frozen_v1_binding_document() -> None:
    """The wire shape PR 2's Bridge validates, produced from controller reads.

    Every field is read back from the controller rather than echoed from a
    request: the worker name, the Matrix identity, the Team, and the rooms all
    come out of the two documents above. That is what makes the binding
    network-authoritative instead of a restatement of the enrollment file.
    """

    agent_id, organization_id = uuid4(), uuid4()
    client = AgentTeamsControlPlaneClient(
        "http://agentteams:8090", transport=_bridge_worker_controller()
    )
    try:
        binding = await ResolveExternalWorkerBinding(
            _OneWorkerDirectory(_worker_principal(agent_id, organization_id)),
            client,
        ).execute(ExternalWorkerBindingQuery(worker_agent_id=agent_id))
    finally:
        await client.close()

    wire = binding.to_wire()
    assert wire == {
        "schemaVersion": EXTERNAL_WORKER_BINDING_SCHEMA_VERSION,
        "organizationId": str(organization_id),
        "teamName": "repomesh-team-pricing",
        "workerAgentId": str(agent_id),
        "workerName": "repomesh-worker-bridge",
        "matrixUserId": "@repomesh-worker-bridge:matrix.local",
        "allowedRoomIds": ["!team-pricing:matrix.local", "!worker-bridge:matrix.local"],
        "containerManaged": False,
    }
    # And it is the *frozen* document, not merely a dict that looks like one.
    schema_properties = BINDING_SCHEMA["properties"]
    assert schema_properties["schemaVersion"]["const"] == EXTERNAL_WORKER_BINDING_SCHEMA_VERSION
    assert set(BINDING_SCHEMA["required"]).issubset(wire)
    assert set(wire).issubset(schema_properties)
    assert wire["containerManaged"] is schema_properties["containerManaged"]["const"]


@pytest.mark.asyncio
async def test_preflight_refuses_a_worker_the_controller_does_not_hold() -> None:
    """Fail-closed over the wire, not only over a double.

    A 404 from the controller is the ordinary shape of "this worker was never
    provisioned"; it must surface as a refusal rather than as a partial binding
    or as the integration's own error taxonomy.
    """

    agent_id, organization_id = uuid4(), uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        return response(404, {"error": "not found"})

    client = AgentTeamsControlPlaneClient(
        "http://agentteams:8090", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(ExternalWorkerRefused, match="not provisioned"):
            await ResolveExternalWorkerBinding(
                _OneWorkerDirectory(_worker_principal(agent_id, organization_id)),
                client,
            ).execute(ExternalWorkerBindingQuery(worker_agent_id=agent_id))
    finally:
        await client.close()


def test_upstream_pin_matches_repository_contract() -> None:
    assert AGENTTEAMS_VERSION == "v1.2.0"
    assert AGENTTEAMS_COMMIT == "793db242257a569d911b1aa59c1cd554af78511f"


def test_repomesh_runner_wire_value_matches_controller_and_crd() -> None:
    """The Python enum, Go constant, and CRD enum must agree on one string.

    The Go side is asserted through the vendored sources so a subtree update
    that drops or renames the runtime fails here, not at deploy time.
    """
    import re
    from pathlib import Path

    wire_value = WorkerRuntime.REPOMESH_RUNNER.value
    assert wire_value == "repomesh-runner"

    controller = Path(__file__).parents[2] / "components" / "agentteams" / "agentteams-controller"
    interface_go = (controller / "internal" / "backend" / "interface.go").read_text(
        encoding="utf-8"
    )
    assert re.search(rf'RuntimeRepomeshRunner\s*=\s*"{wire_value}"', interface_go)

    crd = (controller / "config" / "crd" / "workers.agentteams.io.yaml").read_text(
        encoding="utf-8"
    )
    assert wire_value in crd
