"""GitHub side of the post-merge rollback: revert branches and revert PRs.

GitHub exposes no revert API, so a merged candidate can only be rolled back by
a real ``git revert`` commit published as a branch and merged through a normal
pull request. Shared history is never force-reset and coding agents never see
the credentials used here.

Every gateway method is replay-safe, because the recovery Saga re-runs an
action that is still ``running`` on the next pass:

* the revert branch name is derived from the ChangeSet, the repository and the
  reverted merge commit, so a replay recomputes the same handle;
* a revert branch that already exists remotely is reused instead of rebuilt,
  which keeps the revert commit SHA stable across replays;
* opening the revert PR reuses the PR already opened from that branch;
* merging re-reads the PR first and reports an already merged revert as done.
"""

import asyncio
import inspect
import re
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from repomesh.modules.delivery.contracts import ChangeSetView, RepositoryDeliveryView
from repomesh.modules.repository_intelligence.ports.catalog import RepositoryCatalog

from .contracts import (
    CreateDraftPullRequestCommand,
    MergePullRequestCommand,
    PullRequestObservation,
    PullRequestState,
    RepositoryRef,
    SCMConflict,
    SCMNotFound,
)
from .delivery import parse_repository_ref
from .git_branch import github_credential_environment, is_safe_branch_name
from .github import GitHubAdapter
from .recovery import RecoveryExecutionContext, RecoveryWaiting, RevertConflict

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_ZERO_SHA = "0" * 40


@dataclass(frozen=True, slots=True)
class RevertBranchRequest:
    repository: RepositoryRef
    base_branch: str
    merge_sha: str
    branch_name: str


@dataclass(frozen=True, slots=True)
class RevertBranch:
    branch_name: str
    head_sha: str
    created: bool


class GitRevertPublisher(Protocol):
    async def ensure(self, request: RevertBranchRequest) -> RevertBranch: ...


class MirrorGitReverter:
    """Produce a revert branch through a server-side Git mirror.

    One long-lived mirror per repository lives under ``mirror_root``. The
    mirror is only ever fast-forwarded from the remote and reset to the base
    branch before a revert, so it holds no state that a replay depends on: an
    empty ``mirror_root`` reproduces the same revert branch.
    """

    def __init__(
        self,
        mirror_root: Path,
        *,
        git_binary: str = "git",
        token_provider=None,
        clone_base: str = "https://github.com",
        committer_name: str = "RepoMesh Delivery",
        committer_email: str = "delivery@repomesh.invalid",
    ) -> None:
        self._mirror_root = Path(mirror_root)
        self._git_binary = git_binary
        self._token_provider = token_provider
        self._clone_base = clone_base.rstrip("/")
        self._committer_name = committer_name
        self._committer_email = committer_email
        self._lock = asyncio.Lock()

    async def ensure(self, request: RevertBranchRequest) -> RevertBranch:
        self._validate(request)
        async with self._lock:
            return await self._ensure(request)

    async def _ensure(self, request: RevertBranchRequest) -> RevertBranch:
        mirror = await self._mirror(request.repository)
        environment = await self._environment(request.repository)
        published = await self._remote_sha(mirror, request.branch_name, environment=environment)
        if published is not None:
            # Replay: the revert commit was already published, so re-running
            # `git revert` would only produce a second commit with the same
            # tree under a different SHA.
            return RevertBranch(request.branch_name, published, created=False)

        remote_base = f"refs/remotes/origin/{request.base_branch}"
        await self._run(
            mirror,
            "fetch",
            "--no-tags",
            "--force",
            "origin",
            f"+refs/heads/{request.base_branch}:{remote_base}",
            environment=environment,
        )
        base_head = (await self._run(mirror, "rev-parse", remote_base)).strip().lower()
        await self._run(mirror, "checkout", "--force", "--detach", base_head)
        await self._run(mirror, "clean", "-xfdq")
        revert_arguments = await self._revert_arguments(mirror, request)
        try:
            await self._run(mirror, *self._identity(), *revert_arguments)
        except SCMConflict as error:
            raise RevertConflict(
                f"git revert of {request.merge_sha} conflicts with "
                f"{request.base_branch}: {error}"
            ) from error
        head = (await self._run(mirror, "rev-parse", "HEAD")).strip().lower()
        if head == base_head:
            raise SCMConflict("git revert produced no commit; the merge is already reverted")
        await self._run(
            mirror,
            "push",
            # A zero lease means "create only": if a concurrent rollback
            # published the branch first, the push is refused instead of
            # overwriting a revert someone else is already reviewing.
            f"--force-with-lease=refs/heads/{request.branch_name}:{_ZERO_SHA}",
            "origin",
            f"{head}:refs/heads/{request.branch_name}",
            environment=environment,
        )
        remote_after = await self._remote_sha(mirror, request.branch_name, environment=environment)
        if remote_after != head:
            raise SCMConflict("published revert branch does not match the local revert commit")
        return RevertBranch(request.branch_name, head, created=True)

    async def _revert_arguments(self, mirror: Path, request: RevertBranchRequest) -> list[str]:
        parents = (
            await self._run(mirror, "rev-list", "--parents", "-n", "1", request.merge_sha)
        ).split()
        arguments = ["revert", "--no-edit"]
        if len(parents) > 2:
            # A merge commit needs the first parent as the mainline; a squash
            # or rebase merge is an ordinary commit and must not carry -m.
            arguments += ["--mainline", "1"]
        arguments.append(request.merge_sha)
        return arguments

    async def _mirror(self, repository: RepositoryRef) -> Path:
        mirror = self._mirror_root / f"{repository.owner}__{repository.name}"
        mirror.mkdir(parents=True, exist_ok=True)
        if not (mirror / ".git").is_dir():
            await self._run(mirror, "init", "--quiet")
        url = f"{self._clone_base}/{repository.owner}/{repository.name}.git"
        await self._run(mirror, "config", "remote.origin.url", url)
        return mirror

    async def _remote_sha(
        self, mirror: Path, branch: str, *, environment: dict[str, str] | None
    ) -> str | None:
        output = await self._run(
            mirror,
            "ls-remote",
            "--heads",
            "origin",
            f"refs/heads/{branch}",
            environment=environment,
        )
        if not output.strip():
            return None
        return output.split()[0].lower()

    def _identity(self) -> tuple[str, ...]:
        return (
            "-c",
            f"user.name={self._committer_name}",
            "-c",
            f"user.email={self._committer_email}",
            "-c",
            "commit.gpgsign=false",
        )

    async def _environment(self, repository: RepositoryRef) -> dict[str, str] | None:
        if self._token_provider is None:
            return None
        supplied = self._token_provider(repository)
        token = (await supplied if inspect.isawaitable(supplied) else supplied).strip()
        if not token:
            raise SCMConflict("GitHub installation token is unavailable for the revert branch")
        return github_credential_environment(token)

    @staticmethod
    def _validate(request: RevertBranchRequest) -> None:
        if not _FULL_SHA.fullmatch(request.merge_sha.lower()):
            raise ValueError("merge_sha must be a full Git object id")
        if not is_safe_branch_name(request.branch_name):
            raise ValueError("branch_name is not a safe Git branch name")
        if not is_safe_branch_name(request.base_branch):
            raise ValueError("base_branch is not a safe Git branch name")

    async def _run(
        self,
        mirror: Path,
        *arguments: str,
        environment: dict[str, str] | None = None,
    ) -> str:
        process = await asyncio.create_subprocess_exec(
            self._git_binary,
            "-C",
            str(mirror),
            *arguments,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            detail = stderr.decode(errors="replace").strip()
            if "revert" in arguments:
                # Leave no half-applied revert behind: the next replay must
                # start from a clean base branch checkout.
                with suppress(OSError):
                    await self._abort(mirror)
            raise SCMConflict(detail or "git command failed in the revert mirror")
        return stdout.decode(errors="replace")

    async def _abort(self, mirror: Path) -> None:
        process = await asyncio.create_subprocess_exec(
            self._git_binary,
            "-C",
            str(mirror),
            "revert",
            "--abort",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.communicate()


class GitHubRevertDeliveryGateway:
    """GitHub implementation of ``RevertDeliveryGateway``.

    The gateway owns no state: every method re-derives its handles from the
    ChangeSet in the execution context and from GitHub itself, which is what
    makes the four recovery actions replay-safe.
    """

    def __init__(
        self,
        catalog: RepositoryCatalog,
        adapter: GitHubAdapter,
        reverter: GitRevertPublisher,
        *,
        base_branch: str = "main",
    ) -> None:
        self._catalog = catalog
        self._adapter = adapter
        self._reverter = reverter
        self._base_branch = base_branch

    async def close_pull_request(self, context: RecoveryExecutionContext) -> str:
        candidate = self._candidate(context)
        if candidate.pull_request_number is None:
            return f"repository {candidate.repository_id} never opened a pull request"
        repository = await self._repository_ref(candidate.repository_id)
        observation = await self._adapter.close_pull_request(
            repository,
            candidate.pull_request_number,
            idempotency_key=self._idempotency_key(context, "close"),
        )
        if observation.state is PullRequestState.MERGED:
            raise SCMConflict(
                f"pull request #{candidate.pull_request_number} merged remotely; "
                "it needs a revert instead of a close"
            )
        return (
            f"pull request #{observation.number} is {observation.state.value}; "
            f"candidate {candidate.commit_sha[:12]} keeps its evidence"
        )

    async def create_revert_pull_request(self, context: RecoveryExecutionContext) -> str:
        candidate = self._candidate(context)
        merge_sha = self._merge_sha(candidate)
        repository = await self._repository_ref(candidate.repository_id)
        branch = self._revert_branch(context.change_set.id, candidate)
        revert = await self._reverter.ensure(
            RevertBranchRequest(
                repository=repository,
                base_branch=self._base_branch,
                merge_sha=merge_sha,
                branch_name=branch,
            )
        )
        observation = await self._adapter.create_draft_pull_request(
            CreateDraftPullRequestCommand(
                repository=repository,
                head_branch=revert.branch_name,
                base_branch=self._base_branch,
                expected_head_sha=revert.head_sha,
                title=f"Revert: {context.change_set.title}",
                body=self._revert_body(context, candidate, merge_sha),
                idempotency_key=self._idempotency_key(context, "revert"),
                # Not a draft: a revert PR has to collect its own CI evidence
                # before the Saga is allowed to merge it.
                draft=False,
            )
        )
        published = "published" if revert.created else "reused"
        return (
            f"revert PR #{observation.number} for merge {merge_sha[:12]} "
            f"({published} branch {revert.branch_name} at {revert.head_sha[:12]})"
        )

    async def merge_revert_pull_request(self, context: RecoveryExecutionContext) -> str:
        candidate = self._candidate(context)
        merge_sha = self._merge_sha(candidate)
        repository = await self._repository_ref(candidate.repository_id)
        branch = self._revert_branch(context.change_set.id, candidate)
        observation = await self._revert_pull_request(repository, branch)
        if observation is None:
            raise SCMConflict(f"no revert pull request was opened from {branch}")
        if observation.state is PullRequestState.MERGED:
            return (
                f"revert PR #{observation.number} was already merged as "
                f"{(observation.merge_sha or '')[:12]}"
            )
        if observation.state is PullRequestState.CLOSED:
            raise SCMConflict(
                f"revert PR #{observation.number} was closed without merging the rollback"
            )
        if observation.mergeable is False:
            raise RevertConflict(
                f"revert PR #{observation.number} conflicts with {self._base_branch}"
            )
        if observation.draft:
            raise SCMConflict(f"revert PR #{observation.number} is still a draft")
        await self._require_revert_checks(repository, observation)
        result = await self._adapter.merge_pull_request(
            MergePullRequestCommand(
                repository=repository,
                number=observation.number,
                expected_head_sha=observation.head_sha,
                commit_title=f"Revert: {context.change_set.title}",
            )
        )
        return (
            f"revert PR #{observation.number} merged as {result.merge_sha[:12]}; "
            f"merge {merge_sha[:12]} is rolled back"
        )

    async def revalidate(self, context: RecoveryExecutionContext) -> str:
        """Re-read GitHub and confirm every planned rollback actually landed.

        The check is a pure read, so it can run on every replay. It only covers
        the repositories the enclosing recovery plan acted on, which keeps a
        repository-scoped plan from asserting anything about its siblings.
        """

        change_set = context.change_set
        scope = self._planned_repositories(context)
        lines: list[str] = []
        for candidate in sorted(change_set.repositories, key=lambda item: item.merge_order):
            if candidate.repository_id not in scope:
                continue
            repository = await self._repository_ref(candidate.repository_id)
            if candidate.merge_sha:
                branch = self._revert_branch(change_set.id, candidate)
                observation = await self._revert_pull_request(repository, branch)
                if observation is None or observation.state is not PullRequestState.MERGED:
                    raise SCMConflict(
                        f"repository {candidate.repository_id} still carries merge "
                        f"{candidate.merge_sha[:12]}; its revert PR is not merged"
                    )
                lines.append(
                    f"{candidate.repository_id}: reverted by PR #{observation.number}"
                )
                continue
            if candidate.pull_request_number is None:
                lines.append(f"{candidate.repository_id}: nothing was delivered")
                continue
            observation = await self._adapter.get_pull_request(
                repository, candidate.pull_request_number
            )
            if observation.state is PullRequestState.OPEN:
                raise SCMConflict(
                    f"pull request #{observation.number} is still open for repository "
                    f"{candidate.repository_id}"
                )
            lines.append(
                f"{candidate.repository_id}: PR #{observation.number} is {observation.state.value}"
            )
        if not lines:
            return "no delivered repository needed a rollback"
        return "; ".join(lines)

    async def _require_revert_checks(
        self, repository: RepositoryRef, observation: PullRequestObservation
    ) -> None:
        """Hold the merge until the revert PR has passed its own CI.

        A revert is a real commit on the base branch and can break the build
        just as the delivery it undoes did, so it earns its own evidence rather
        than inheriting the trust of the rollback decision.

        Scope: CI only. Review and governance approval are deliberately not
        gated here -- the human decision to roll back is taken *before* the Saga
        starts (the console submits a ROLLBACK_REQUIRED decision), so demanding
        a second approval on the revert PR would stall an already-approved
        rollback behind the same reviewers who asked for it.

        A check that has not reported yet is a wait, not a failure: it raises
        RecoveryWaiting so the action is retried, while a check that reported
        failure is an SCMConflict the Saga must not paper over.
        """

        head = observation.head_sha.lower()
        checks = {
            check.check_name: check
            for check in await self._adapter.list_check_runs(repository, head)
            if check.head_sha == head
        }
        failed = sorted(
            check.check_name for check in checks.values() if check.terminal and not check.passed
        )
        if failed:
            raise SCMConflict(
                f"revert PR #{observation.number} failed its own checks: {failed}"
            )
        required = await self._required_checks(repository)
        # Branch protection is the only thing that can tell "this repository has
        # no CI" apart from "CI has not posted a check run yet"; without a
        # required check to wait for, an empty result means the former.
        missing = sorted(name for name in required if name not in checks)
        if missing:
            raise RecoveryWaiting(
                f"revert PR #{observation.number} is missing required checks: {missing}"
            )
        running = sorted(check.check_name for check in checks.values() if not check.terminal)
        if running:
            raise RecoveryWaiting(
                f"revert PR #{observation.number} still has checks running: {running}"
            )

    async def _required_checks(self, repository: RepositoryRef) -> tuple[str, ...]:
        reader = getattr(self._adapter, "get_branch_protection", None)
        if reader is None:
            return ()
        try:
            protection = await reader(repository, self._base_branch)
        except SCMNotFound:
            # An unprotected base branch requires nothing of the revert.
            return ()
        return protection.required_checks

    async def _revert_pull_request(
        self, repository: RepositoryRef, branch: str
    ) -> PullRequestObservation | None:
        return await self._adapter.find_pull_request_by_head(repository, branch)

    async def _repository_ref(self, repository_id: UUID) -> RepositoryRef:
        profile = await self._catalog.get(repository_id)
        if profile is None:
            raise SCMNotFound(f"repository not in catalog: {repository_id}")
        return parse_repository_ref(profile.url)

    @staticmethod
    def _planned_repositories(context: RecoveryExecutionContext) -> set[UUID]:
        plan = next(
            (
                item
                for item in context.change_set.recovery_plans
                if any(action.id == context.action.id for action in item.actions)
            ),
            None,
        )
        if plan is None:
            return {item.repository_id for item in context.change_set.repositories}
        scoped = {
            action.repository_id for action in plan.actions if action.repository_id is not None
        }
        return scoped or {item.repository_id for item in context.change_set.repositories}

    @staticmethod
    def _candidate(context: RecoveryExecutionContext) -> RepositoryDeliveryView:
        repository_id = context.action.repository_id
        if repository_id is None:
            raise SCMConflict(
                f"recovery action {context.action.kind.value} is not bound to a repository"
            )
        return _candidate_of(context.change_set, repository_id)

    @staticmethod
    def _merge_sha(candidate: RepositoryDeliveryView) -> str:
        if not candidate.merge_sha:
            raise SCMConflict(
                f"repository {candidate.repository_id} has no merge commit to revert"
            )
        return candidate.merge_sha.lower()

    @staticmethod
    def _revert_branch(change_set_id: UUID, candidate: RepositoryDeliveryView) -> str:
        merge_sha = (candidate.merge_sha or "").lower()
        return (
            f"repomesh/revert/{str(change_set_id)[:8]}/"
            f"{str(candidate.repository_id)[:8]}-{merge_sha[:12]}"
        )

    @staticmethod
    def _idempotency_key(context: RecoveryExecutionContext, suffix: str) -> str:
        return (
            f"recovery:{context.change_set.id}:{context.action.repository_id}:{suffix}"
        )

    @staticmethod
    def _revert_body(
        context: RecoveryExecutionContext, candidate: RepositoryDeliveryView, merge_sha: str
    ) -> str:
        return "\n".join(
            [
                f"Automated RepoMesh rollback for ChangeSet `{context.change_set.id}`.",
                "",
                f"- reverted merge commit: `{merge_sha}`",
                f"- reverted candidate head: `{candidate.commit_sha}`",
                f"- recovery action: `{context.action.id}`",
                "",
                "Shared history is not force-reset; this revert must pass the same",
                "required checks and reviews as the delivery it rolls back.",
            ]
        )


def _candidate_of(change_set: ChangeSetView, repository_id: UUID) -> RepositoryDeliveryView:
    candidate = next(
        (item for item in change_set.repositories if item.repository_id == repository_id),
        None,
    )
    if candidate is None:
        raise SCMNotFound(f"repository not in ChangeSet: {repository_id}")
    return candidate
