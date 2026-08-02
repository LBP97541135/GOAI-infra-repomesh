import os

import pytest

from repomesh.integrations.agentteams import AgentTeamsControlPlaneClient

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_live_agentteams_controller_contract() -> None:
    base_url = os.environ.get("REPOMESH_AGENTTEAMS_TEST_URL")
    if not base_url:
        pytest.skip("set REPOMESH_AGENTTEAMS_TEST_URL to run the AgentTeams live contract")

    client = AgentTeamsControlPlaneClient(
        base_url,
        token=os.environ.get("REPOMESH_AGENTTEAMS_TEST_TOKEN"),
    )
    try:
        assert await client.health() is True
        version = await client.version()
    finally:
        await client.close()

    assert version.controller
    assert version.kube_mode in {"embedded", "k8s", "aliyun"}
