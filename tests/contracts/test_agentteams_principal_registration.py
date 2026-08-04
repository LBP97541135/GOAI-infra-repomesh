from uuid import uuid4

import pytest

from repomesh.integrations.agentteams import (
    RegisterNativeAgent,
    RegisterNativeAgentRequest,
)
from repomesh.modules.agent_directory.application import CreateAgentRequest
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.agent_directory.infrastructure import InMemoryAgentDirectory
from repomesh.modules.agent_runtime.ports.agent_team import (
    ManagerProjection,
    ManagerRuntimeRef,
    WorkerProjection,
    WorkerRuntimeRef,
)


class RecordingControlPlane:
    def __init__(self) -> None:
        self.manager = None

    async def ensure_manager(self, projection, *, idempotency_key: str):
        self.manager = projection
        return ManagerRuntimeRef(projection.name, "Ready")

    async def ensure_worker(self, projection, *, idempotency_key: str):
        return WorkerRuntimeRef(projection.name, "Ready")


@pytest.mark.asyncio
async def test_native_manager_is_created_before_business_principal_registration() -> None:
    directory = InMemoryAgentDirectory()
    control_plane = RecordingControlPlane()
    native = ManagerProjection(
        name="native-organization-manager",
        model="qwen3.6-plus",
        soul="Coordinate repository leaders.",
    )
    result = await RegisterNativeAgent(
        control_plane, directory  # type: ignore[arg-type]
    ).execute(
        RegisterNativeAgentRequest(
            principal=CreateAgentRequest(
                organization_id=uuid4(),
                role=AgentRole.ORGANIZATION_LEADER,
                agentteams_resource_name=native.name,
            ),
            manager=native,
        ),
        idempotency_key="organization-bootstrap",
    )

    assert control_plane.manager == native
    assert result.principal.agentteams_resource_name == native.name
    assert not hasattr(result.principal, "model")


def test_native_resource_type_must_match_business_role() -> None:
    with pytest.raises(ValueError, match="requires a native AgentTeams Manager"):
        RegisterNativeAgentRequest(
            principal=CreateAgentRequest(
                organization_id=uuid4(),
                role=AgentRole.ORGANIZATION_LEADER,
                agentteams_resource_name="native-wrong-kind",
            ),
            worker=WorkerProjection("native-wrong-kind", "qwen3.6-plus"),
            manager=None,
        )
