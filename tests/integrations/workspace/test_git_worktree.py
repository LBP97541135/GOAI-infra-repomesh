import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from repomesh.integrations.workspace import GitWorktreeManager, WorkspacePreparationError


def _git(directory: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(directory), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()


@pytest.mark.asyncio
async def test_prepare_reuses_mirror_and_isolates_runs(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.email", "test@example.com")
    _git(source, "config", "user.name", "Test")
    (source / "README.md").write_text("v1\n", encoding="utf-8")
    _git(source, "add", "README.md")
    _git(source, "commit", "-m", "initial")
    base_sha = _git(source, "rev-parse", "HEAD")
    repository_id = uuid4()
    manager = GitWorktreeManager(tmp_path / "workspaces")

    first = await manager.prepare(
        repository_id=repository_id,
        repository_url=str(source),
        base_revision=base_sha,
        run_id=uuid4(),
        workspace_id=uuid4(),
    )
    second = await manager.prepare(
        repository_id=repository_id,
        repository_url=str(source),
        base_revision=base_sha,
        run_id=uuid4(),
        workspace_id=uuid4(),
    )

    assert first.path != second.path
    assert first.base_sha == second.base_sha == base_sha
    assert (first.path / "README.md").read_text(encoding="utf-8") == "v1\n"
    assert (tmp_path / "workspaces" / "repositories" / f"{repository_id}.git").is_dir()
    git_pointer = (first.path / ".git").read_text(encoding="utf-8")
    assert git_pointer.startswith("gitdir: ../../")
    assert str(tmp_path) not in git_pointer
    assert _git(first.path, "rev-parse", "HEAD") == base_sha


@pytest.mark.asyncio
async def test_revision_that_looks_like_an_option_is_rejected(tmp_path: Path) -> None:
    manager = GitWorktreeManager(tmp_path / "workspaces")

    with pytest.raises(WorkspacePreparationError, match="cannot start"):
        await manager.prepare(
            repository_id=uuid4(),
            repository_url=str(tmp_path),
            base_revision="--upload-pack=bad",
            run_id=uuid4(),
        )
