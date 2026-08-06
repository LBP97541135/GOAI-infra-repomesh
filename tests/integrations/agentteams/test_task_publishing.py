import json
from uuid import uuid4

import pytest

from repomesh.integrations.agentteams.task_publishing import AgentTeamsTaskPublisher
from repomesh.modules.task_orchestration.contracts import TaskStatus, TaskView


def task_view() -> TaskView:
    return TaskView(
        id=uuid4(),
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_id=uuid4(),
        parent_task_id=uuid4(),
        assigned_by_agent_id=uuid4(),
        assignee_agent_id=uuid4(),
        title="Fix pricing resolver",
        instruction="Apply the approved pricing change.",
        acceptance=("Pricing tests pass", "Old API remains compatible"),
        status=TaskStatus.ASSIGNED,
        result_summary=None,
        version=0,
    )


@pytest.mark.asyncio
async def test_publishes_agentteams_compatible_task_and_verifies_replay(tmp_path) -> None:
    publisher = AgentTeamsTaskPublisher(tmp_path)
    task = task_view()

    first = await publisher.publish(
        task,
        team_name="pricing-team",
        room_id="!pricing:matrix.local",
        assignee_resource_name="pricing-worker",
        idempotency_key="publish-pricing",
    )
    replay = await publisher.publish(
        task,
        team_name="pricing-team",
        room_id="!pricing:matrix.local",
        assignee_resource_name="pricing-worker",
        idempotency_key="publish-pricing",
    )

    task_dir = tmp_path / first.task_path
    meta = json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))
    manifest = json.loads((task_dir / "manifest.json").read_text(encoding="utf-8"))
    assert meta["assigned_to"] == "pricing-worker"
    assert meta["room_id"] == "!pricing:matrix.local"
    assert "Pricing tests pass" in (task_dir / "spec.md").read_text(encoding="utf-8")
    assert manifest["content_hash"] == first.content_hash
    assert replay == first
