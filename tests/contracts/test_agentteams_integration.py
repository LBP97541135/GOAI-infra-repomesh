import json

import httpx
import pytest

from repomesh.integrations.agentteams.control_plane import (
    AGENTTEAMS_COMMIT,
    AGENTTEAMS_VERSION,
    AgentTeamsConflict,
    AgentTeamsControlPlaneClient,
)
from repomesh.integrations.agentteams.matrix import AgentTeamsMatrixClient
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
            "name": "rm-worker-api",
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
                name="rm-worker-api",
                model="qwen3.6-plus",
                runtime=WorkerRuntime.HERMES,
                identity="Backend coding worker",
                skills=("github-operations",),
            ),
            idempotency_key="project-1-worker-api-v1",
        )
    finally:
        await client.close()

    assert worker.name == "rm-worker-api"
    assert worker.phase == "Pending"
    assert [(item.method, item.url.path) for item in requests] == [
        ("GET", "/api/v1/workers/rm-worker-api"),
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
                "name": "rm-worker-api",
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
            WorkerProjection("rm-worker-api", "qwen3.6-plus", WorkerRuntime.HERMES),
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
                name="rm-worker-secure",
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
                "name": "rm-worker-api",
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
                WorkerProjection("rm-worker-api", "qwen3.6-plus", WorkerRuntime.HERMES),
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
                "leaderName": "rm-worker-lead",
                "readyWorkers": 0,
                "totalWorkers": 2,
                "leaderDMRoomID": "!leader:matrix.local",
            },
        )

    projection = TeamProjection(
        name="rm-team-project",
        members=(
            TeamMemberProjection("rm-worker-lead", TeamRole.LEADER),
            TeamMemberProjection("rm-worker-api", TeamRole.WORKER),
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

    assert posted["workerMembers"] == [
        {"name": "rm-worker-lead", "role": "team_leader"},
        {"name": "rm-worker-api", "role": "worker"},
    ]
    assert team.leader_name == "rm-worker-lead"
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
        return response(200, {"name": "rm-worker-api", "phase": "Ready"})

    client = AgentTeamsControlPlaneClient(
        "http://agentteams:8090", transport=httpx.MockTransport(handler)
    )
    try:
        manager = await client.ensure_manager(
            ManagerProjection("rm-manager-main", "qwen3.6-plus"),
            idempotency_key="manager-v1",
        )
        worker = await client.ensure_worker_ready("rm-worker-api", idempotency_key="run-1-ready")
    finally:
        await client.close()

    assert manager.name == "rm-manager-main"
    assert worker.phase == "Ready"
    assert calls[-1] == ("POST", "/api/v1/workers/rm-worker-api/ensure-ready")


@pytest.mark.asyncio
async def test_get_manager_exposes_matrix_identity_for_inbound_authentication() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return response(
            200,
            {
                "name": "rm-manager-main",
                "phase": "Ready",
                "matrixUserID": "@rm-manager-main:matrix.local",
            },
        )

    client = AgentTeamsControlPlaneClient(
        "http://agentteams:8090", transport=httpx.MockTransport(handler)
    )
    try:
        manager = await client.get_manager("rm-manager-main")
    finally:
        await client.close()
    assert manager is not None
    assert manager.matrix_user_id == "@rm-manager-main:matrix.local"


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
                                        "content": {
                                            "msgtype": "m.text",
                                            "body": '{"schema":"repomesh.agent-report.v1"}',
                                        },
                                    },
                                    {"type": "m.room.member", "event_id": "$member"},
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


def test_upstream_pin_matches_repository_contract() -> None:
    assert AGENTTEAMS_VERSION == "v1.2.0"
    assert AGENTTEAMS_COMMIT == "793db242257a569d911b1aa59c1cd554af78511f"
