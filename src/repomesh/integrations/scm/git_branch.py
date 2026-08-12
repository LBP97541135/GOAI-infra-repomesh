import asyncio
import base64
import inspect
import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from .contracts import PublishBranchCommand, PublishedBranch, RepositoryRef, SCMConflict

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_BRANCH = re.compile(r"^(?![-/.])(?!.*(?:\.\.|//|@\{|\\))[A-Za-z0-9._/-]+(?<![/.])$")


def is_safe_branch_name(value: str) -> bool:
    """Reject branch names Git would reinterpret (refspecs, lock files, traversal)."""

    return bool(_BRANCH.fullmatch(value)) and not value.endswith(".lock")


def github_credential_environment(token: str) -> dict[str, str]:
    """Build a Git environment that carries an installation token as an HTTP header.

    The token never reaches a working tree, a remote URL or a coding agent: it
    only lives in the per-process ``GIT_CONFIG_*`` variables of the Git command
    RepoMesh itself runs.
    """

    basic_credential = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    environment = dict(os.environ)
    environment.update(
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic_credential}",
        }
    )
    return environment


class GitBranchPublisher:
    """Publishes one frozen commit without granting the coding agent Git credentials."""

    def __init__(
        self,
        workspace_root: Path,
        *,
        git_binary: str = "git",
        token_provider: Callable[[RepositoryRef], str | Awaitable[str]] | None = None,
    ) -> None:
        self._workspace_root = workspace_root.resolve()
        self._git_binary = git_binary
        self._token_provider = token_provider

    async def publish(self, command: PublishBranchCommand) -> PublishedBranch:
        workspace = command.workspace.resolve()
        self._validate(command, workspace)
        head = (await self._run(workspace, "rev-parse", "HEAD")).strip().lower()
        if head != command.expected_head_sha.lower():
            raise SCMConflict("workspace HEAD differs from the frozen ChangeSet candidate")

        environment = await self._git_environment(command.repository)
        remote_before = await self._remote_sha(
            workspace, command.branch_name, environment=environment
        )
        expected_remote = (
            command.expected_remote_sha.lower() if command.expected_remote_sha else "0" * 40
        )
        if remote_before is not None and command.expected_remote_sha is None:
            if remote_before == head:
                return PublishedBranch(command.branch_name, head, remote_before)
            raise SCMConflict("remote branch already exists and no lease SHA was supplied")
        if remote_before != (None if expected_remote == "0" * 40 else expected_remote):
            raise SCMConflict("remote branch changed after the delivery candidate was frozen")
        if remote_before is not None:
            try:
                await self._run(workspace, "merge-base", "--is-ancestor", remote_before, head)
            except SCMConflict as error:
                raise SCMConflict(
                    "candidate update is not a fast-forward of the published branch"
                ) from error

        lease = f"--force-with-lease=refs/heads/{command.branch_name}:{expected_remote}"
        await self._run(
            workspace,
            "-c",
            "remote.origin.mirror=false",
            "push",
            lease,
            "origin",
            f"{head}:refs/heads/{command.branch_name}",
            environment=environment,
        )
        remote_after = await self._remote_sha(
            workspace, command.branch_name, environment=environment
        )
        if remote_after != head:
            raise SCMConflict("remote branch does not match the published candidate")
        return PublishedBranch(command.branch_name, head, remote_after)

    async def _remote_sha(
        self, workspace: Path, branch: str, *, environment: dict[str, str] | None = None
    ) -> str | None:
        output = await self._run(
            workspace,
            "ls-remote",
            "--heads",
            "origin",
            f"refs/heads/{branch}",
            environment=environment,
        )
        if not output.strip():
            return None
        return output.split()[0].lower()

    @staticmethod
    def _validate(command: PublishBranchCommand, workspace: Path) -> None:
        if not workspace.is_dir():
            raise ValueError("workspace must be an existing directory")
        if not _FULL_SHA.fullmatch(command.expected_head_sha.lower()):
            raise ValueError("expected_head_sha must be a full Git object id")
        if command.expected_remote_sha and not _FULL_SHA.fullmatch(
            command.expected_remote_sha.lower()
        ):
            raise ValueError("expected_remote_sha must be a full Git object id")
        if not is_safe_branch_name(command.branch_name):
            raise ValueError("branch_name is not a safe Git branch name")

    async def _git_environment(self, repository: RepositoryRef | None) -> dict[str, str] | None:
        if self._token_provider is None or repository is None:
            return None
        supplied = self._token_provider(repository)
        token = (await supplied if inspect.isawaitable(supplied) else supplied).strip()
        if not token:
            raise SCMConflict("GitHub installation token is unavailable for branch publication")
        return github_credential_environment(token)

    async def _run(
        self,
        workspace: Path,
        *arguments: str,
        environment: dict[str, str] | None = None,
    ) -> str:
        if not workspace.is_relative_to(self._workspace_root):
            raise ValueError("workspace is outside the configured Runner root")
        process = await asyncio.create_subprocess_exec(
            self._git_binary,
            "-C",
            str(workspace),
            *arguments,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            detail = stderr.decode(errors="replace").strip()
            raise SCMConflict(detail or "Git branch publication failed")
        return stdout.decode(errors="replace")
