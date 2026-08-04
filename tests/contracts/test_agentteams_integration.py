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
    ManagerProjection,
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
