import pytest

from repomesh.integrations.bootstrap import (
    DockerComposeApiTargetSelector,
    DockerTargetSafetyError,
    DockerTargetUnavailable,
)


class FakeDockerRunner:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, ...]] = []

    async def run(self, arguments: tuple[str, ...]) -> str:
        self.calls.append(arguments)
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_selector_returns_unique_api_in_own_project() -> None:
    container_id = "a" * 64
    runner = FakeDockerRunner(["repomesh-project", container_id, "repomesh-project|api"])
    target = await DockerComposeApiTargetSelector(
        runner,
        own_container_id="bootstrap-self",
    ).select()
    assert target.container_id == container_id
    assert target.project == "repomesh-project"
    assert target.service == "api"
    assert runner.calls[1][0] == "ps"
    assert "label=com.docker.compose.service=api" in runner.calls[1]
    assert all("restart" not in call for call in runner.calls)


@pytest.mark.asyncio
async def test_selector_reports_temporarily_missing_api() -> None:
    runner = FakeDockerRunner(["repomesh-project", ""])
    with pytest.raises(DockerTargetUnavailable, match="not running"):
        await DockerComposeApiTargetSelector(
            runner,
            own_container_id="bootstrap-self",
        ).select()


@pytest.mark.asyncio
async def test_selector_rejects_multiple_api_containers_as_safety_failure() -> None:
    runner = FakeDockerRunner(["repomesh-project", f"{'a' * 64}\n{'b' * 64}"])
    with pytest.raises(DockerTargetSafetyError, match="multiple"):
        await DockerComposeApiTargetSelector(
            runner,
            own_container_id="bootstrap-self",
        ).select()


@pytest.mark.asyncio
async def test_selector_rejects_mismatched_labels() -> None:
    runner = FakeDockerRunner(["repomesh-project", "a" * 64, "other-project|api"])
    with pytest.raises(DockerTargetSafetyError, match="labels do not match"):
        await DockerComposeApiTargetSelector(
            runner,
            own_container_id="bootstrap-self",
        ).select()


@pytest.mark.asyncio
async def test_selector_rejects_invalid_project_without_querying_services() -> None:
    runner = FakeDockerRunner(["project name with spaces"])
    with pytest.raises(DockerTargetSafetyError, match="no valid Compose project"):
        await DockerComposeApiTargetSelector(
            runner,
            own_container_id="bootstrap-self",
        ).select()
    assert len(runner.calls) == 1
