"""Canonical enrollment/binding payloads and the doubles the Bridge tests share.

The payloads are the shapes ``contracts/agent-bridge/v1`` freezes, spelled once:
a test that needs an invalid document mutates one field of a valid one, so the
thing under test is always the single difference.
"""

import pytest

from repomesh_agent_bridge.adapters.memory import InMemoryWorkerBindingPort
from repomesh_agent_bridge.contracts import (
    BINDING_SCHEMA_VERSION,
    ENROLLMENT_SCHEMA_VERSION,
    ExternalWorkerEnrollment,
    WorkerBinding,
)

ORGANIZATION_ID = "00000000-0000-0000-0000-000000000001"
WORKER_AGENT_ID = "00000000-0000-0000-0000-000000000002"
WORKER_NAME = "pricing-codex-worker"
TEAM_NAME = "pricing-repo-team"
MATRIX_USER_ID = "@pricing-codex-worker:matrix.example.org"
HOMESERVER_URL = "https://matrix.example.org"
REPOMESH_ENDPOINT = "https://repomesh.example.org"
TEAM_ROOM = "!team-pricing:matrix.example.org"
WORKER_ROOM = "!worker-bridge:matrix.example.org"

REPOMESH_TOKEN_VAR = "REPOMESH_BRIDGE_TOKEN"
REPOMESH_TOKEN_REF = f"env:{REPOMESH_TOKEN_VAR}"
REPOMESH_TOKEN_VALUE = "s3cret-runner-control-token"


def enrollment_wire(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": ENROLLMENT_SCHEMA_VERSION,
        "organizationId": ORGANIZATION_ID,
        "workerAgentId": WORKER_AGENT_ID,
        "workerName": WORKER_NAME,
        "teamName": TEAM_NAME,
        "matrixUserId": MATRIX_USER_ID,
        "matrixHomeserverUrl": HOMESERVER_URL,
        "allowedRoomIds": [TEAM_ROOM, WORKER_ROOM],
        "repomeshEndpoint": REPOMESH_ENDPOINT,
        "codingProfile": "codex",
        "credentialRefs": {
            "matrix": "env:REPOMESH_BRIDGE_MATRIX_TOKEN",
            "repomesh": REPOMESH_TOKEN_REF,
        },
    }
    payload.update(overrides)
    return payload


def binding_wire(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": BINDING_SCHEMA_VERSION,
        "organizationId": ORGANIZATION_ID,
        "teamName": TEAM_NAME,
        "workerAgentId": WORKER_AGENT_ID,
        "workerName": WORKER_NAME,
        "matrixUserId": MATRIX_USER_ID,
        "allowedRoomIds": [TEAM_ROOM, WORKER_ROOM],
        "containerManaged": False,
    }
    payload.update(overrides)
    return payload


class WireBindingPort:
    """A control plane that answers with a wire body.

    Parses it exactly the way the HTTP adapter does, so a preflight refusal that
    belongs to the wire reader and one that belongs to the application are
    reached through the same interface and are indistinguishable to the caller —
    which is the point of them being one exception type.
    """

    requires_credential = False

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls = 0

    async def fetch_binding(
        self, enrollment: ExternalWorkerEnrollment, *, credential: str | None
    ) -> WorkerBinding:
        self.calls += 1
        return WorkerBinding.from_wire(self.payload)


@pytest.fixture
def enrollment() -> ExternalWorkerEnrollment:
    return ExternalWorkerEnrollment.from_wire(enrollment_wire())


@pytest.fixture
def binding() -> WorkerBinding:
    return WorkerBinding.from_wire(binding_wire())


@pytest.fixture
def binding_port(binding: WorkerBinding) -> InMemoryWorkerBindingPort:
    return InMemoryWorkerBindingPort(binding)


@pytest.fixture
def default_state_home(tmp_path, monkeypatch) -> object:
    """Point ``default_state_dir()`` at ``tmp_path`` on every platform.

    Lets a test arrange "another instance already holds this worker" at the very
    path the command under test would use, instead of at a path it could be
    ignoring by accident.
    """

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path
