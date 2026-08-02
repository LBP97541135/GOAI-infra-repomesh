from pathlib import Path

import pytest

from repomesh.integrations.coding_agents.base import CliAgentAdapter
from repomesh.integrations.coding_agents.catalog import SPECS
from repomesh.modules.agent_runtime.ports import (
    AdapterCapability,
    AgentLaunchRequest,
    AgentSessionRef,
    PromptDelivery,
)


@pytest.mark.parametrize("spec", SPECS, ids=lambda item: item.id)
def test_every_catalog_entry_builds_a_shell_free_launch_plan(spec) -> None:  # type: ignore[no-untyped-def]
    adapter = CliAgentAdapter(spec, resolved_binary=spec.binaries[0])
    request = AgentLaunchRequest(
        workspace_path=Path("C:/work/repo"),
        prompt="implement the task",
        session_id="session-1",
        environment={"REPOMESH_RUN_ID": "run-1"},
    )

    plan = adapter.build_launch(request)

    assert plan.executable == spec.binaries[0]
    assert plan.working_directory == Path("C:/work/repo")
    assert plan.environment == {"REPOMESH_RUN_ID": "run-1"}
    assert plan.argv[0] == plan.executable
    if plan.prompt_delivery is PromptDelivery.AFTER_START:
        assert plan.prompt_after_start == "implement the task"
        assert "implement the task" not in plan.arguments


@pytest.mark.parametrize("spec", SPECS, ids=lambda item: item.id)
def test_restore_capability_matches_restore_plan(spec) -> None:  # type: ignore[no-untyped-def]
    adapter = CliAgentAdapter(spec, resolved_binary=spec.binaries[0])
    request = AgentLaunchRequest(Path("C:/work/repo"), "", "session-1")
    session = AgentSessionRef("native-1", Path("C:/work/repo"))

    plan = adapter.build_restore(request, session)

    supports_restore = AdapterCapability.RESTORE in adapter.manifest.capabilities
    assert (plan is not None) is supports_restore
    if plan is not None:
        assert "native-1" in plan.arguments


def test_claude_tool_scope_is_rendered_as_native_flags() -> None:
    spec = next(item for item in SPECS if item.id == "claude-code")
    plan = CliAgentAdapter(spec, resolved_binary="claude").build_launch(
        AgentLaunchRequest(
            workspace_path=Path("C:/work/repo"),
            prompt="review only",
            session_id="session-1",
            allowed_tools=("Read", "Bash(git diff:*)"),
            disallowed_tools=("Write", "Edit"),
        )
    )

    assert "--allowedTools" in plan.arguments
    assert "Read,Bash(git diff:*)" in plan.arguments
    assert "--disallowedTools" in plan.arguments
    assert "Write,Edit" in plan.arguments
