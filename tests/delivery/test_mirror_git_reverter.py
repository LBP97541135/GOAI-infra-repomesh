"""The Git data plane behind a revert PR.

These tests drive real ``git`` against throwaway repositories inside
``tmp_path``. Nothing here touches GitHub or any repository the operator owns:
the "remote" is a bare repository created for the test and discarded with it.
"""

import asyncio
import subprocess
from pathlib import Path

import pytest

from repomesh.integrations.scm.contracts import RepositoryRef, SCMProvider
from repomesh.integrations.scm.recovery import RevertConflict
from repomesh.integrations.scm.revert import MirrorGitReverter, RevertBranchRequest

BRANCH = "repomesh/revert/abcdefab/12345678-0123456789ab"


def git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def author(cwd: Path) -> None:
    git(cwd, "config", "user.name", "Fixture")
    git(cwd, "config", "user.email", "fixture@repomesh.invalid")


@pytest.fixture
def fake_remote(tmp_path: Path):
    """A bare "GitHub" plus a work tree whose main branch has a merge commit."""

    remote = tmp_path / "remotes" / "acme" / "pricing.git"
    remote.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(remote)],
        capture_output=True,
        check=True,
    )
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(work)], capture_output=True, check=True
    )
    author(work)
    write(work / "pricing.txt", "base\n")
    git(work, "add", ".")
    git(work, "commit", "-m", "base")
    git(work, "checkout", "-b", "feature")
    write(work / "pricing.txt", "base\nfeature\n")
    git(work, "commit", "-am", "feature")
    git(work, "checkout", "main")
    git(work, "merge", "--no-ff", "-m", "merge feature", "feature")
    merge_sha = git(work, "rev-parse", "HEAD").lower()
    git(work, "remote", "add", "origin", str(remote).replace("\\", "/"))
    git(work, "push", "origin", "main")
    return remote, work, merge_sha


def reverter_for(tmp_path: Path) -> MirrorGitReverter:
    return MirrorGitReverter(
        tmp_path / "mirrors",
        clone_base=str(tmp_path / "remotes").replace("\\", "/"),
    )


def request_for(merge_sha: str) -> RevertBranchRequest:
    return RevertBranchRequest(
        repository=RepositoryRef(SCMProvider.GITHUB, "acme", "pricing"),
        base_branch="main",
        merge_sha=merge_sha,
        branch_name=BRANCH,
    )


@pytest.mark.asyncio
async def test_revert_branch_undoes_the_merge_without_rewriting_main(
    tmp_path: Path, fake_remote
) -> None:
    remote, work, merge_sha = fake_remote

    published = await reverter_for(tmp_path).ensure(request_for(merge_sha))

    assert published.created
    assert published.head_sha != merge_sha
    assert git(remote, "rev-parse", f"refs/heads/{BRANCH}").lower() == published.head_sha
    # main is untouched: rollback never force-resets shared history.
    assert git(remote, "rev-parse", "refs/heads/main").lower() == merge_sha
    # The revert commit restores the pre-merge content.
    assert git(remote, "show", f"{published.head_sha}:pricing.txt") == "base"


@pytest.mark.asyncio
async def test_replayed_revert_reuses_the_published_branch(
    tmp_path: Path, fake_remote
) -> None:
    _, _, merge_sha = fake_remote
    reverter = reverter_for(tmp_path)

    first = await reverter.ensure(request_for(merge_sha))
    second = await reverter.ensure(request_for(merge_sha))

    assert first.created and not second.created
    assert first.head_sha == second.head_sha


@pytest.mark.asyncio
async def test_a_fresh_mirror_reproduces_the_same_revert_branch(
    tmp_path: Path, fake_remote
) -> None:
    """Restart safety: the mirror holds no state a replay depends on."""

    _, _, merge_sha = fake_remote

    first = await reverter_for(tmp_path).ensure(request_for(merge_sha))
    second = await MirrorGitReverter(
        tmp_path / "other-mirrors",
        clone_base=str(tmp_path / "remotes").replace("\\", "/"),
    ).ensure(request_for(merge_sha))

    assert first.head_sha == second.head_sha
    assert not second.created


@pytest.mark.asyncio
async def test_conflicting_revert_is_reported_as_a_revert_conflict(
    tmp_path: Path, fake_remote
) -> None:
    _, work, merge_sha = fake_remote
    write(work / "pricing.txt", "base\nfeature edited downstream\n")
    git(work, "commit", "-am", "downstream edit")
    git(work, "push", "origin", "main")

    with pytest.raises(RevertConflict, match="conflicts with main"):
        await reverter_for(tmp_path).ensure(request_for(merge_sha))


@pytest.mark.asyncio
async def test_a_conflict_leaves_no_half_applied_revert_behind(
    tmp_path: Path, fake_remote
) -> None:
    _, work, merge_sha = fake_remote
    write(work / "pricing.txt", "base\nfeature edited downstream\n")
    git(work, "commit", "-am", "downstream edit")
    git(work, "push", "origin", "main")
    reverter = reverter_for(tmp_path)

    with pytest.raises(RevertConflict):
        await reverter.ensure(request_for(merge_sha))

    mirror = tmp_path / "mirrors" / "acme__pricing"
    assert not (mirror / ".git" / "REVERT_HEAD").exists()
    # A second pass fails on the conflict again rather than on a dirty mirror.
    with pytest.raises(RevertConflict, match="conflicts with main"):
        await reverter.ensure(request_for(merge_sha))


@pytest.mark.asyncio
async def test_concurrent_passes_publish_one_revert_branch(
    tmp_path: Path, fake_remote
) -> None:
    remote, _, merge_sha = fake_remote
    reverter = reverter_for(tmp_path)

    results = await asyncio.gather(
        reverter.ensure(request_for(merge_sha)),
        reverter.ensure(request_for(merge_sha)),
    )

    assert {result.head_sha for result in results} == {
        git(remote, "rev-parse", f"refs/heads/{BRANCH}").lower()
    }
    assert [result.created for result in results] == [True, False]
