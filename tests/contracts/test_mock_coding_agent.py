from uuid import uuid4

import pytest

from repomesh.integrations.coding_agents.mock import MockCodingAgent, MockScenario
from repomesh.modules.agent_runtime.ports import CodingRunRequest, RunStatus


def request() -> CodingRunRequest:
    return CodingRunRequest(
        task_id=uuid4(),
        repository_url="https://github.com/example/repo",
        instruction="Implement the task",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "status"),
    [
        (MockScenario.SUCCESS, RunStatus.SUCCEEDED),
        (MockScenario.TEST_FAILED, RunStatus.FAILED),
        (MockScenario.FAILED, RunStatus.FAILED),
        (MockScenario.TIMEOUT, RunStatus.TIMED_OUT),
        (MockScenario.CANCELLED, RunStatus.CANCELLED),
        (MockScenario.INTERRUPTED, RunStatus.INTERRUPTED),
        (MockScenario.QUESTION_REQUIRED, RunStatus.WAITING_FOR_INPUT),
    ],
)
async def test_mock_scenarios(scenario: MockScenario, status: RunStatus) -> None:
    result = await MockCodingAgent(scenario).execute(request())

    assert result.status is status
    assert result.events
