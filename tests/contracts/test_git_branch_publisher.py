import base64
import subprocess
from pathlib import Path

import pytest

from repomesh.integrations.scm.contracts import PublishBranchCommand, SCMConflict
from repomesh.integrations.scm.git_branch import GitBranchPublisher


def git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()


def repository(tmp_path: Path) -> tuple[Path, Path, str]:
    remote = tmp_path / "remote.git"
    workspace = tmp_path / "workspace"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)
    git(workspace, "config", "user.email", "worker@repomesh.test")
    git(workspace, "config", "user.name", "RepoMesh Worker")
    git(workspace, "remote", "add", "origin", str(remote))
    (workspace / "result.txt").write_text("done\n", encoding="utf-8")
    git(workspace, "add", "result.txt")
    git(workspace, "commit", "-m", "fix: complete task")
    return remote, workspace, git(workspace, "rev-parse", "HEAD")


@pytest.mark.asyncio
async def test_github_token_uses_basic_auth_without_modifying_remote(tmp_path: Path) -> None:
    publisher = GitBranchPublisher(tmp_path, token_provider=lambda _: "installation-token")
    environment = await publisher._git_environment(  # noqa: SLF001
        type("Repository", (), {"owner": "acme", "name": "service"})()
    )

    expected = base64.b64encode(b"x-access-token:installation-token").decode()
    assert environment is not None
    assert environment["GIT_CONFIG_VALUE_0"] == f"Authorization: Basic {expected}"


@pytest.mark.asyncio
async def test_publishes_only_the_frozen_head(tmp_path: Path) -> None:
    remote, workspace, head = repository(tmp_path)
    publisher = GitBranchPublisher(tmp_path)

    result = await publisher.publish(
        PublishBranchCommand(workspace, "repomesh/task-1", head)
    )

    assert result.remote_sha == head
    assert git(remote, "rev-parse", "refs/heads/repomesh/task-1") == head


@pytest.mark.asyncio
async def test_publishes_one_branch_from_a_mirror_configured_remote(tmp_path: Path) -> None:
    remote, workspace, head = repository(tmp_path)
    git(workspace, "config", "remote.origin.mirror", "true")

    result = await GitBranchPublisher(tmp_path).publish(
        PublishBranchCommand(workspace, "repomesh/task-1", head)
    )

    assert result.remote_sha == head
    assert git(remote, "rev-parse", "refs/heads/repomesh/task-1") == head


@pytest.mark.asyncio
async def test_rejects_workspace_head_drift(tmp_path: Path) -> None:
    _, workspace, frozen = repository(tmp_path)
    (workspace / "result.txt").write_text("changed\n", encoding="utf-8")
    git(workspace, "add", "result.txt")
    git(workspace, "commit", "-m", "unexpected commit")

    with pytest.raises(SCMConflict, match="workspace HEAD"):
        await GitBranchPublisher(tmp_path).publish(
            PublishBranchCommand(workspace, "repomesh/task-1", frozen)
        )


@pytest.mark.asyncio
async def test_existing_remote_branch_requires_a_lease(tmp_path: Path) -> None:
    _, workspace, head = repository(tmp_path)
    git(workspace, "push", "origin", f"{head}:refs/heads/repomesh/task-1")
    (workspace / "result.txt").write_text("next\n", encoding="utf-8")
    git(workspace, "add", "result.txt")
    git(workspace, "commit", "-m", "next candidate")
    next_head = git(workspace, "rev-parse", "HEAD")

    with pytest.raises(SCMConflict, match="no lease SHA"):
        await GitBranchPublisher(tmp_path).publish(
            PublishBranchCommand(workspace, "repomesh/task-1", next_head)
        )

    result = await GitBranchPublisher(tmp_path).publish(
        PublishBranchCommand(
            workspace,
            "repomesh/task-1",
            next_head,
            expected_remote_sha=head,
        )
    )
    assert result.remote_sha == next_head
