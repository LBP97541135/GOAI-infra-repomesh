"""The four operations and the three things that guard them.

Every test here runs against the memory plane, because what is under test is the
launcher's face -- which operations exist, who may call them, and what the answer
is allowed to contain -- and none of that should need a Windows process to state.
The Windows plane is verified live, not here.
"""

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from repomesh_local_launcher.app import LAUNCHER_OP_HEADER, create_app
from repomesh_local_launcher.config import LauncherConfig
from repomesh_local_launcher.memory import MemoryMemberProcessPlane
from repomesh_local_launcher.process import (
    MemberProcess,
    StalePidClaim,
    StalePidFileClaimed,
)

CONSOLE_ORIGIN = "http://127.0.0.1:5280"
ENV_FILE_NAME = "e1-members-secret.env"
STALE_PID_FILE_PATH = r"D:\repo\output\bridge-team\e1\pids\beta-leader.pid"

LEADER = MemberProcess(
    agent_id="4d1e6f00-0000-4000-8000-0000000000a1",
    display_name="alpha-leader",
    role="repository_leader",
    running=False,
    pid=None,
    log_path=r"D:\repo\output\bridge-team\e1\logs\alpha-leader.out.log",
)
WORKER = MemberProcess(
    agent_id="4d1e6f00-0000-4000-8000-0000000000a2",
    display_name="alpha-worker",
    role="worker",
    running=True,
    pid=4242,
    log_path=r"D:\repo\output\bridge-team\e1\logs\alpha-worker.out.log",
)

WRITE_OPERATIONS = [
    pytest.param("/v1/members/start", id="start"),
    pytest.param("/v1/members/stop", id="stop"),
    pytest.param(f"/v1/members/{LEADER.agent_id}/restart", id="restart"),
]


@pytest.fixture
def config(tmp_path: Path) -> LauncherConfig:
    return LauncherConfig(
        members_file=tmp_path / "members.json",
        enrollment_dir=tmp_path / "enrollments",
        env_file=tmp_path / ENV_FILE_NAME,
        runtime_dir=tmp_path / "runtime",
        workspace_root=None,
        subset="e1",
        roster_version="e1-2026-08-29",
        allowed_origins=(CONSOLE_ORIGIN,),
        port=8121,
    )


@pytest.fixture
def plane() -> MemoryMemberProcessPlane:
    return MemoryMemberProcessPlane([LEADER, WORKER])


@pytest.fixture
def client(config: LauncherConfig, plane: MemoryMemberProcessPlane) -> TestClient:
    return TestClient(create_app(config, plane))


def write(client: TestClient, path: str) -> httpx.Response:
    """A write the way the Console makes it: allowlisted Origin, custom header, no body."""
    return client.post(path, headers={"Origin": CONSOLE_ORIGIN, LAUNCHER_OP_HEADER: "1"})


def test_status_reports_one_row_per_member_and_the_roster_version(
    client: TestClient, plane: MemoryMemberProcessPlane
) -> None:
    response = client.get("/v1/status")

    assert response.status_code == 200
    assert response.json() == {
        "rosterVersion": "e1-2026-08-29",
        "members": [
            {
                "agentId": LEADER.agent_id,
                "displayName": "alpha-leader",
                "role": "repository_leader",
                "running": False,
                "pid": None,
                "logPath": LEADER.log_path,
            },
            {
                "agentId": WORKER.agent_id,
                "displayName": "alpha-worker",
                "role": "worker",
                "running": True,
                "pid": 4242,
                "logPath": WORKER.log_path,
            },
        ],
    }
    assert plane.calls == ["status"]


def test_start_asks_the_plane_and_answers_with_the_new_process_facts(
    client: TestClient, plane: MemoryMemberProcessPlane
) -> None:
    response = write(client, "/v1/members/start")

    assert response.status_code == 200
    assert plane.calls == ["start_all"]
    assert all(member["running"] for member in response.json()["members"])


def test_stop_asks_the_plane(client: TestClient, plane: MemoryMemberProcessPlane) -> None:
    response = write(client, "/v1/members/stop")

    assert response.status_code == 200
    assert plane.calls == ["stop_all"]
    assert not any(member["running"] for member in response.json()["members"])


def test_restart_names_the_member_the_caller_asked_for(
    client: TestClient, plane: MemoryMemberProcessPlane
) -> None:
    response = write(client, f"/v1/members/{LEADER.agent_id}/restart")

    assert response.status_code == 200
    assert plane.calls == [f"restart:{LEADER.agent_id}"]
    restarted = next(
        member for member in response.json()["members"] if member["agentId"] == LEADER.agent_id
    )
    assert restarted["running"] is True


def test_restart_of_an_unknown_agent_is_a_404(
    client: TestClient, plane: MemoryMemberProcessPlane
) -> None:
    response = write(client, "/v1/members/00000000-0000-4000-8000-000000000999/restart")

    assert response.status_code == 404
    assert plane.calls == ["restart:00000000-0000-4000-8000-000000000999"]


@pytest.mark.parametrize("path", WRITE_OPERATIONS)
def test_write_without_the_custom_header_is_refused(
    client: TestClient, plane: MemoryMemberProcessPlane, path: str
) -> None:
    response = client.post(path, headers={"Origin": CONSOLE_ORIGIN})

    assert response.status_code == 403
    assert plane.calls == []


@pytest.mark.parametrize("path", WRITE_OPERATIONS)
def test_write_with_the_wrong_custom_header_value_is_refused(
    client: TestClient, plane: MemoryMemberProcessPlane, path: str
) -> None:
    response = client.post(path, headers={"Origin": CONSOLE_ORIGIN, LAUNCHER_OP_HEADER: "0"})

    assert response.status_code == 403
    assert plane.calls == []


@pytest.mark.parametrize("path", WRITE_OPERATIONS)
@pytest.mark.parametrize(
    "origin",
    [
        pytest.param("http://evil.example", id="foreign-site"),
        pytest.param("http://127.0.0.1:5281", id="neighbouring-port"),
        pytest.param("null", id="opaque-origin"),
    ],
)
def test_write_from_a_non_allowlisted_origin_is_refused(
    client: TestClient, plane: MemoryMemberProcessPlane, path: str, origin: str
) -> None:
    response = client.post(path, headers={"Origin": origin, LAUNCHER_OP_HEADER: "1"})

    assert response.status_code == 403
    assert plane.calls == []


@pytest.mark.parametrize("path", WRITE_OPERATIONS)
def test_write_without_an_origin_is_refused(
    client: TestClient, plane: MemoryMemberProcessPlane, path: str
) -> None:
    response = client.post(path, headers={LAUNCHER_OP_HEADER: "1"})

    assert response.status_code == 403
    assert plane.calls == []


@pytest.mark.parametrize("path", WRITE_OPERATIONS)
def test_a_refusal_tells_the_caller_nothing_about_the_roster(client: TestClient, path: str) -> None:
    response = client.post(path, headers={"Origin": "http://evil.example"})
    body = response.text

    assert response.status_code == 403
    for secret in (
        LEADER.agent_id,
        WORKER.agent_id,
        "alpha-leader",
        "alpha-worker",
        "repository_leader",
        "e1-2026-08-29",
        ENV_FILE_NAME,
    ):
        assert secret not in body


def test_no_answer_carries_credentials_or_the_env_file(
    client: TestClient, config: LauncherConfig
) -> None:
    bodies = [
        client.get("/v1/status").text,
        write(client, "/v1/members/start").text,
        write(client, "/v1/members/stop").text,
        write(client, f"/v1/members/{LEADER.agent_id}/restart").text,
    ]

    for body in bodies:
        assert ENV_FILE_NAME not in body
        assert str(config.env_file) not in body
        assert str(config.enrollment_dir) not in body
        for banned in ("env", "token", "secret", "credential"):
            assert banned not in _keys(json.loads(body))


def _keys(payload: object) -> set[str]:
    """Every key name anywhere in a decoded response body."""
    if isinstance(payload, dict):
        return set(payload) | {key for value in payload.values() for key in _keys(value)}
    if isinstance(payload, list):
        return {key for item in payload for key in _keys(item)}
    return set()


def test_the_write_routes_accept_no_body(
    client: TestClient, plane: MemoryMemberProcessPlane
) -> None:
    """FR-09's "no commands, paths or credentials from the page", at the schema level.

    A body is not rejected, it is not read: there is no field for the page to put
    a script path into, so a caller that sends one is answered exactly as one that
    sends nothing.
    """
    response = client.post(
        "/v1/members/start",
        headers={"Origin": CONSOLE_ORIGIN, LAUNCHER_OP_HEADER: "1"},
        json={"script": r"C:\evil.ps1", "envFile": "steal-me"},
    )

    assert response.status_code == 200
    assert plane.calls == ["start_all"]
    assert "evil" not in response.text


class BlockedPlane:
    """A plane that cannot start anything because a PID file is in the way."""

    def __init__(self) -> None:
        self.claims = (StalePidClaim(member_name="beta-leader", pid_file=STALE_PID_FILE_PATH),)

    def status(self) -> tuple[MemberProcess, ...]:
        return (LEADER,)

    def start_all(self) -> tuple[MemberProcess, ...]:
        raise StalePidFileClaimed(self.claims)

    def stop_all(self) -> tuple[MemberProcess, ...]:
        return (LEADER,)

    def restart(self, agent_id: str) -> tuple[MemberProcess, ...]:
        raise StalePidFileClaimed(self.claims)


@pytest.fixture
def blocked_client(config: LauncherConfig) -> TestClient:
    return TestClient(create_app(config, BlockedPlane()))


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/v1/members/start", id="start"),
        pytest.param(f"/v1/members/{LEADER.agent_id}/restart", id="restart"),
    ],
)
def test_a_pid_file_in_the_way_is_a_409_that_says_which_file(
    blocked_client: TestClient, path: str
) -> None:
    """The one refusal that has to be actionable.

    A 500 here would leave the operator with a member that is down, a launcher
    that will not start it, and nothing on the page saying why. This body names
    the member and the file to delete -- safe to say over loopback, to a page the
    Origin allowlist already vouched for, about a file on the reader's own disk.
    """
    headers = {"Origin": CONSOLE_ORIGIN, LAUNCHER_OP_HEADER: "1"}
    response = blocked_client.post(path, headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "stale_pid_file",
        "message": "Delete the PID file named for each member, then start again.",
        "members": [{"displayName": "beta-leader", "pidFile": STALE_PID_FILE_PATH}],
    }


def test_the_409_still_carries_no_credentials(blocked_client: TestClient) -> None:
    body = blocked_client.post(
        "/v1/members/start", headers={"Origin": CONSOLE_ORIGIN, LAUNCHER_OP_HEADER: "1"}
    ).text

    assert ENV_FILE_NAME not in body
    for banned in ("env", "token", "secret", "credential"):
        assert banned not in _keys(json.loads(body))


def test_generated_documentation_routes_are_not_served(client: TestClient) -> None:
    """"Four routes and no others" is a claim about what this process serves."""
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_preflight_survives_the_custom_header_requirement(client: TestClient) -> None:
    """The header is only a CORS trigger if the preflight it triggers can pass.

    A browser never sends ``X-RepoMesh-Launcher-Op`` on the OPTIONS request, so a
    guard that ran ahead of CORS would refuse every preflight and lock the Console
    out of the very operations the header is meant to protect.
    """
    response = client.options(
        "/v1/members/start",
        headers={
            "Origin": CONSOLE_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": LAUNCHER_OP_HEADER,
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == CONSOLE_ORIGIN
    assert LAUNCHER_OP_HEADER.lower() in response.headers["access-control-allow-headers"].lower()
