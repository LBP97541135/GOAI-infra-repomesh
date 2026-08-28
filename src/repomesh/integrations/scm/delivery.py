import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from repomesh.modules.delivery import DeliveryNotFound, DeliveryService, SCMCommandService
from repomesh.modules.delivery.conflicts import (
    DeliveryConflictKind,
    PostgresDeliveryConflictCaseStore,
)
from repomesh.modules.delivery.contracts import (
    ChangeSetView,
    CIObservationCommand,
    EnqueueSCMCommand,
    MergeObservationCommand,
    PullRequestObservationCommand,
    RecordMergeRequestedCommand,
    RepositoryDeliveryStatus,
    ReviewObservationCommand,
    ReviewState,
    SCMCommandKind,
)
from repomesh.modules.repository_intelligence.ports import RepositoryCatalog

from .contracts import (
    BranchPublisher,
    CreateDraftPullRequestCommand,
    MergePullRequestCommand,
    PublishBranchCommand,
    PullRequestObservation,
    PullRequestState,
    RepositoryRef,
    SCMAdapter,
    SCMConflict,
    SCMProvider,
    SCMReviewState,
)
from .github_events import (
    GitHubCIObservation,
    GitHubReviewObservation,
    validate_ci_observation,
)

logger = logging.getLogger(__name__)

_PUBLISHABLE_STATUSES = frozenset(
    {RepositoryDeliveryStatus.PENDING, RepositoryDeliveryStatus.PR_OPEN}
)
"""The only candidate states that may still receive a pull request number.

Not an arbitrary "non-terminal" set: it is exactly what ``RepositoryDelivery
.observe_pr`` accepts. Offering the domain a candidate it would refuse would
turn a stranded publish into a DeliveryConflict on every sweep.
"""

_UNDRAFTABLE_STATUSES = frozenset(
    {
        RepositoryDeliveryStatus.PR_OPEN,
        RepositoryDeliveryStatus.CI_PENDING,
        RepositoryDeliveryStatus.REVIEW_PENDING,
        RepositoryDeliveryStatus.READY_TO_MERGE,
    }
)
"""Candidate states in which promoting a draft pull request is still meaningful.

This used to be ``PR_OPEN`` alone, which quietly made undrafting depend on the
*order events arrived in* rather than on the candidate's actual state:
``observe_ci`` recomputes the status on every observation
(``RepositoryDelivery._readiness_status``), so the first CI fact moves a
candidate out of PR_OPEN forever. On the webhook path that was survivable by
luck -- a ``pull_request`` opened event usually arrives before the first
``check_run``, and undraft runs after every observation. On the reconciler's
polling path there is no luck to have: one sweep opens the PR *and* records
CI, so PR_OPEN never exists at any moment undraft could observe it.

CI_FAILED and REVIEW_CHANGES_REQUESTED are excluded on purpose. Those
candidates are on their way back to a Worker, and draft is the honest signal
for "not ready to be looked at"; the merge-request and compensation states
have nothing left to promote.
"""


@dataclass(frozen=True, slots=True)
class OpenChangeSetPullRequestCommand:
    change_set_id: UUID
    repository_id: UUID
    base_branch: str
    body: str
    draft: bool = False


@dataclass(frozen=True, slots=True)
class PublishChangeSetPullRequestCommand:
    change_set_id: UUID
    repository_id: UUID
    workspace: Path
    base_branch: str
    body: str
    expected_remote_sha: str | None = None
    draft: bool = False


class ChangeSetSCMCoordinator:
    """Connects frozen ChangeSet candidates to an SCM adapter."""

    def __init__(
        self,
        delivery: DeliveryService,
        catalog: RepositoryCatalog,
        adapter: SCMAdapter | None,
        branch_publisher: BranchPublisher | None = None,
        command_service: SCMCommandService | None = None,
        base_branch: str = "main",
        conflict_cases: PostgresDeliveryConflictCaseStore | None = None,
        conflict_tasks=None,
        conflict_escalate: Callable[[object, str], Awaitable[None]] | None = None,
    ) -> None:
        self._delivery = delivery
        self._catalog = catalog
        self._adapter = adapter
        self._branch_publisher = branch_publisher
        self._command_service = command_service
        self._base_branch = base_branch.strip() or "main"
        self._conflict_cases = conflict_cases
        self._conflict_tasks = conflict_tasks
        self._conflict_escalate = conflict_escalate

    @property
    def can_mutate(self) -> bool:
        return self._adapter is not None

    async def publish_and_open_draft_pull_request(
        self, command: PublishChangeSetPullRequestCommand
    ) -> ChangeSetView:
        if self._branch_publisher is None:
            raise RuntimeError("branch publisher is not configured")
        change_set = await self._delivery.get(command.change_set_id)
        candidate = self._candidate(change_set, command.repository_id)
        profile = await self._catalog.get(command.repository_id)
        if profile is None:
            raise DeliveryNotFound(f"repository not in catalog: {command.repository_id}")
        await self._branch_publisher.publish(
            PublishBranchCommand(
                workspace=command.workspace,
                branch_name=candidate.branch_name,
                expected_head_sha=candidate.commit_sha,
                expected_remote_sha=command.expected_remote_sha,
                repository=parse_repository_ref(profile.url),
            )
        )
        return await self.open_draft_pull_request(
            OpenChangeSetPullRequestCommand(
                change_set_id=command.change_set_id,
                repository_id=command.repository_id,
                base_branch=command.base_branch,
                body=command.body,
                draft=command.draft,
            )
        )

    async def open_draft_pull_request(
        self, command: OpenChangeSetPullRequestCommand
    ) -> ChangeSetView:
        change_set = await self._delivery.get(command.change_set_id)
        candidate = self._candidate(change_set, command.repository_id)
        profile = await self._catalog.get(command.repository_id)
        if profile is None:
            raise DeliveryNotFound(f"repository not in catalog: {command.repository_id}")
        if self._adapter is None:
            raise RuntimeError("SCM adapter is not configured")
        protection_reader = getattr(self._adapter, "get_branch_protection", None)
        if protection_reader is not None:
            protection = await protection_reader(
                parse_repository_ref(profile.url), command.base_branch
            )
            # An unprotected base branch is a legitimate state, not a refusal.
            # This preflight only checks that a rule which *exists* is at least
            # as strict as the candidate demands -- it is belt to the merge
            # gate's braces. With no rule at all there is nothing to compare
            # against, and opening a draft PR is harmless: RepoMesh still
            # refuses to merge until it has itself observed every required
            # check passing and enough approvals (RepositoryDelivery
            # ._readiness_status), which no remote setting can relax.
            if protection.protected:
                missing = set(candidate.required_checks) - set(protection.required_checks)
                if missing:
                    raise SCMConflict(
                        f"base branch protection is missing required checks: {sorted(missing)}"
                    )
                if protection.required_approvals < candidate.required_approvals:
                    raise SCMConflict("base branch protection allows too few approvals")
                if candidate.required_approvals and not protection.dismisses_stale_reviews:
                    raise SCMConflict("base branch protection must dismiss stale reviews")
        observation = await self._adapter.create_draft_pull_request(
            CreateDraftPullRequestCommand(
                repository=parse_repository_ref(profile.url),
                head_branch=candidate.branch_name,
                base_branch=command.base_branch,
                expected_head_sha=candidate.commit_sha,
                title=change_set.title,
                body=command.body,
                idempotency_key=(f"changeset:{change_set.id}:repository:{candidate.repository_id}"),
                draft=command.draft,
            )
        )
        return await self._record_observation(change_set, candidate.repository_id, observation)

    async def reconcile_pull_request(
        self,
        change_set_id: UUID,
        repository_id: UUID,
        number: int,
    ) -> PullRequestObservation:
        profile = await self._catalog.get(repository_id)
        if profile is None:
            raise DeliveryNotFound(f"repository not in catalog: {repository_id}")
        if self._adapter is None:
            raise RuntimeError("SCM adapter is not configured")
        observation = await self._adapter.get_pull_request(
            parse_repository_ref(profile.url), number
        )
        change_set = await self._delivery.get(change_set_id)
        candidate = self._candidate(change_set, repository_id)
        if observation.head_sha != candidate.commit_sha.lower():
            raise ValueError("remote PR head SHA differs from the frozen ChangeSet candidate")
        return observation

    async def merge_when_allowed(self, change_set_id: UUID, repository_id: UUID) -> ChangeSetView:
        if self._adapter is None:
            raise RuntimeError("SCM adapter is not configured")
        gate = await self._delivery.evaluate_merge_gate(change_set_id, repository_id)
        if not gate.allowed:
            raise ValueError(f"merge gate denied: {'; '.join(gate.reasons)}")
        change_set = await self._delivery.get(change_set_id)
        candidate = self._candidate(change_set, repository_id)
        if candidate.pull_request_number is None:
            raise ValueError("delivery candidate has no pull request")
        profile = await self._catalog.get(repository_id)
        if profile is None:
            raise DeliveryNotFound(f"repository not in catalog: {repository_id}")
        observation = await self.reconcile_pull_request(
            change_set_id, repository_id, candidate.pull_request_number
        )
        if observation.state.value != "open":
            raise ValueError("pull request is no longer open")
        if observation.draft:
            # Draft is the designed early lifecycle of every delivery PR;
            # undraft_when_allowed promotes it once dependencies merged.
            return change_set
        if observation.mergeable is False:
            raise ValueError("pull request has conflicts")
        if self._command_service is not None:
            await self._command_service.enqueue(
                EnqueueSCMCommand(
                    change_set_id=change_set.id,
                    repository_id=candidate.repository_id,
                    kind=SCMCommandKind.MERGE_PULL_REQUEST,
                    idempotency_key=(
                        f"merge:{change_set.id}:{candidate.repository_id}:{candidate.commit_sha}"
                    ),
                    payload={
                        "pull_request_number": candidate.pull_request_number,
                        "expected_head_sha": candidate.commit_sha,
                        "commit_title": change_set.title,
                    },
                )
            )
            return change_set
        await self._adapter.merge_pull_request(
            MergePullRequestCommand(
                repository=parse_repository_ref(profile.url),
                number=candidate.pull_request_number,
                expected_head_sha=candidate.commit_sha,
                commit_title=change_set.title,
            )
        )
        return await self._delivery.record_merge_requested(
            RecordMergeRequestedCommand(
                change_set_id,
                repository_id,
                candidate.commit_sha,
            )
        )

    async def record_github_ci(
        self,
        change_set_id: UUID,
        repository_id: UUID,
        observation: GitHubCIObservation,
    ) -> ChangeSetView:
        if not observation.terminal:
            return await self._delivery.get(change_set_id)
        change_set = await self._delivery.get(change_set_id)
        candidate = self._candidate(change_set, repository_id)
        profile = await self._catalog.get(repository_id)
        if profile is None:
            raise DeliveryNotFound(f"repository not in catalog: {repository_id}")
        validate_ci_observation(
            observation,
            expected_repository=parse_repository_ref(profile.url),
            expected_head_sha=candidate.commit_sha,
        )
        return await self._delivery.observe_ci(
            CIObservationCommand(
                change_set_id=change_set_id,
                repository_id=repository_id,
                passed=observation.passed,
                check_run_id=observation.check_run_id,
                summary=observation.summary,
                check_name=observation.check_name,
            )
        )

    async def record_github_review(
        self,
        change_set_id: UUID,
        repository_id: UUID,
        observation: GitHubReviewObservation,
    ) -> ChangeSetView:
        change_set = await self._delivery.get(change_set_id)
        candidate = self._candidate(change_set, repository_id)
        profile = await self._catalog.get(repository_id)
        if profile is None:
            raise DeliveryNotFound(f"repository not in catalog: {repository_id}")
        if observation.repository != parse_repository_ref(profile.url):
            raise ValueError("review event belongs to another repository")
        if observation.head_sha != candidate.commit_sha.lower():
            raise ValueError("review event head SHA differs from the frozen candidate")
        return await self._delivery.observe_review(
            ReviewObservationCommand(
                change_set_id=change_set_id,
                repository_id=repository_id,
                review_id=observation.review_id,
                reviewer=observation.reviewer,
                state=observation.state,
                head_sha=observation.head_sha,
                summary=observation.summary,
            )
        )

    async def update_pull_request_description(
        self, change_set_id: UUID, repository_id: UUID, body: str
    ) -> ChangeSetView:
        """Rewrite a published PR description (used to back-fill sibling links)."""

        change_set = await self._delivery.get(change_set_id)
        candidate = self._candidate(change_set, repository_id)
        if candidate.pull_request_number is None:
            return change_set
        updater = getattr(self._adapter, "update_pull_request", None)
        if updater is None:
            return change_set
        profile = await self._catalog.get(repository_id)
        if profile is None:
            raise DeliveryNotFound(f"repository not in catalog: {repository_id}")
        await updater(
            parse_repository_ref(profile.url),
            candidate.pull_request_number,
            body=body,
            idempotency_key=(
                f"changeset:{change_set.id}:repository:{repository_id}:body"
            ),
        )
        return change_set

    async def add_change_set_label(
        self, change_set_id: UUID, repository_id: UUID
    ) -> ChangeSetView:
        """Tag a published PR with the change set id (optional, label-token only)."""

        change_set = await self._delivery.get(change_set_id)
        candidate = self._candidate(change_set, repository_id)
        if candidate.pull_request_number is None:
            return change_set
        label_adder = getattr(self._adapter, "add_label", None)
        if label_adder is None:
            return change_set
        profile = await self._catalog.get(repository_id)
        if profile is None:
            raise DeliveryNotFound(f"repository not in catalog: {repository_id}")
        await label_adder(
            parse_repository_ref(profile.url),
            candidate.pull_request_number,
            f"changeset/{str(change_set.id)[:8]}",
            idempotency_key=(
                f"changeset:{change_set.id}:repository:{repository_id}:label"
            ),
        )
        return change_set

    async def merge_ready_repositories(self, change_set_id: UUID) -> ChangeSetView:
        """Merge every currently eligible candidate in dependency order."""

        current = await self._delivery.get(change_set_id)
        for candidate in sorted(current.repositories, key=lambda item: item.merge_order):
            gate = await self._delivery.evaluate_merge_gate(change_set_id, candidate.repository_id)
            if gate.allowed:
                current = await self.merge_when_allowed(change_set_id, candidate.repository_id)
        return current

    async def undraft_when_allowed(self, change_set_id: UUID) -> ChangeSetView:
        """Promote draft PRs to ready-for-review once upstream dependencies merged.

        A candidate stays draft while any ``depends_on`` repository is not yet
        merged; once the upstream merge is observed, an
        ``UNDRAFT_PULL_REQUEST`` command is issued for every open draft PR.
        A candidate with no dependencies is eligible immediately -- ``all()``
        over an empty ``depends_on`` is vacuously true, which is exactly right:
        nothing upstream can be waited for.

        Commands are idempotent per PR (``undraft:<change set>:<repo>:<pr>``),
        and the direct ``ready_for_review`` path re-reads the PR and skips the
        PATCH when it is no longer draft, so repeated calls never duplicate.
        """

        change_set = await self._delivery.get(change_set_id)
        for candidate in sorted(change_set.repositories, key=lambda item: item.merge_order):
            if candidate.status not in _UNDRAFTABLE_STATUSES:
                continue
            if candidate.pull_request_number is None:
                continue
            if not all(
                self._candidate(change_set, dependency).status
                is RepositoryDeliveryStatus.MERGED
                for dependency in candidate.depends_on
            ):
                continue
            idempotency_key = (
                f"undraft:{change_set.id}:{candidate.repository_id}:"
                f"{candidate.pull_request_number}"
            )
            if self._command_service is not None:
                await self._command_service.enqueue(
                    EnqueueSCMCommand(
                        change_set_id=change_set.id,
                        repository_id=candidate.repository_id,
                        kind=SCMCommandKind.UNDRAFT_PULL_REQUEST,
                        idempotency_key=idempotency_key,
                        payload={
                            "pull_request_number": candidate.pull_request_number,
                            "expected_head_sha": candidate.commit_sha,
                        },
                    )
                )
                continue
            ready_for_review = getattr(self._adapter, "ready_for_review", None)
            if ready_for_review is None:
                continue
            profile = await self._catalog.get(candidate.repository_id)
            if profile is None:
                raise DeliveryNotFound(f"repository not in catalog: {candidate.repository_id}")
            await ready_for_review(
                parse_repository_ref(profile.url),
                candidate.pull_request_number,
                idempotency_key=idempotency_key,
            )
        return change_set

    async def complete_pending_publications(self, change_set_id: UUID) -> ChangeSetView:
        """Open the pull request for a candidate whose branch is already pushed.

        ``publish_and_open_draft_pull_request`` is two remote calls behind one
        name, and the second half can fail on its own: the branch reaches the
        remote and ``create_draft_pull_request`` dies. Nothing retried that.
        ``reconcile_and_merge`` skipped every PR-less candidate, and the plan
        advancer only re-fires on task-terminal or reconsider events that a
        finished delivery will never emit again -- so "branch pushed, PR
        failed" was stranded permanently, the same one-shot disease already
        fixed for dispatch, publish, mention and commands.

        This pass completes the *second* half only. When the remote branch is
        absent the first half failed too, and rebuilding it needs the Runner
        workspace that this process no longer holds; the candidate is then
        skipped with a log rather than half-repaired, leaving a task-level
        redispatch as the honest remedy.
        """

        if self._adapter is None:
            raise RuntimeError("SCM adapter is not configured")
        current = await self._delivery.get(change_set_id)
        for original in sorted(current.repositories, key=lambda item: item.merge_order):
            # Re-read per candidate: a concurrent sweep may have recorded the
            # number since this loop started, and the check below is what turns
            # that into a skip instead of a second create.
            current = await self._delivery.get(change_set_id)
            candidate = self._candidate(current, original.repository_id)
            context = {
                "change_set_id": str(change_set_id),
                "repository_id": str(candidate.repository_id),
                "branch_name": candidate.branch_name,
            }
            if (
                candidate.pull_request_number is not None
                or candidate.status not in _PUBLISHABLE_STATUSES
                or not candidate.branch_name.strip()
                or not candidate.commit_sha.strip()
            ):
                continue
            branch_reader = getattr(self._adapter, "get_branch_head", None)
            if branch_reader is None:
                logger.warning(
                    "SCM adapter cannot read a remote branch head; "
                    "leaving the unpublished delivery candidate alone",
                    extra=context,
                )
                continue
            repository = await self._repository_ref(candidate.repository_id)
            remote_head = await branch_reader(repository, candidate.branch_name)
            if remote_head is None:
                logger.info(
                    "delivery candidate branch is not on the remote; "
                    "the publish never happened and only a redispatch can rebuild it",
                    extra=context,
                )
                continue
            if remote_head.lower() != candidate.commit_sha.lower():
                # The branch exists but carries a commit RepoMesh did not
                # freeze. Opening a PR for it would publish a head nobody
                # validated, so this is reported and left alone -- not raised,
                # because a permanently drifted branch must not block the
                # other candidates' reconciliation on every sweep.
                logger.warning(
                    "delivery candidate branch head differs from the frozen commit; "
                    "not opening a pull request for it",
                    extra={**context, "commit_sha": candidate.commit_sha},
                )
                continue
            current = await self.open_draft_pull_request(
                OpenChangeSetPullRequestCommand(
                    change_set_id=change_set_id,
                    repository_id=candidate.repository_id,
                    base_branch=self._base_branch,
                    body=self._reconciled_pull_request_body(current, candidate),
                    # Draft for the same reason handle_batch uses it: the
                    # undraft cycle promotes the PR once dependencies merge.
                    draft=True,
                )
            )
            logger.info(
                "delivery reconciliation opened the pull request a failed publish left behind",
                extra=context,
            )
        return current

    async def reconcile_and_merge(self, change_set_id: UUID) -> ChangeSetView:
        """Recover remote SCM facts, then merge eligible repositories in order."""

        if self._adapter is None:
            raise RuntimeError("SCM adapter is not configured")
        # Convergence first: a candidate stranded between "branch pushed" and
        # "PR opened" has no pull request number, and every recovery step below
        # is keyed on one. Completing the publish here lets the very same sweep
        # go on to reconcile the PR it just opened.
        current = await self.complete_pending_publications(change_set_id)
        for original in sorted(current.repositories, key=lambda item: item.merge_order):
            current = await self._delivery.get(change_set_id)
            candidate = self._candidate(current, original.repository_id)
            if (
                candidate.status is RepositoryDeliveryStatus.MERGED
                or candidate.pull_request_number is None
            ):
                continue
            repository = await self._repository_ref(candidate.repository_id)
            pull_request = await self._adapter.get_pull_request(
                repository, candidate.pull_request_number
            )
            if pull_request.head_sha != candidate.commit_sha.lower():
                raise SCMConflict("remote PR head SHA differs from the frozen ChangeSet candidate")

            if self._conflict_cases is not None:
                active_case = await self._conflict_cases.active_for(
                    change_set_id, candidate.repository_id
                )
                if (
                    active_case is not None
                    and active_case.candidate_head_sha != candidate.commit_sha
                ):
                    await self._conflict_cases.resolve_for_revision(
                        change_set_id,
                        candidate.repository_id,
                        active_case.candidate_head_sha,
                    )

            conflict_kind = None
            detail = ""
            if pull_request.base_sha.lower() != candidate.base_sha.lower():
                conflict_kind = DeliveryConflictKind.BASE_DRIFT
                detail = "target branch moved after candidate validation"
            if pull_request.mergeable is False:
                conflict_kind = DeliveryConflictKind.CONTENT_CONFLICT
                detail = "SCM reports the pull request is not mergeable"
            if conflict_kind is not None and self._conflict_cases is not None:
                case = await self._conflict_cases.ensure(
                    change_set_id=change_set_id,
                    project_id=current.project_id,
                    repository_id=candidate.repository_id,
                    candidate_head_sha=candidate.commit_sha,
                    kind=conflict_kind,
                    expected_base_sha=candidate.base_sha,
                    observed_base_sha=pull_request.base_sha,
                    detail=detail,
                )
                if self._conflict_tasks is not None and case.repair_task_id is None:
                    try:
                        repair_task_id = await self._conflict_tasks.create(
                            current, candidate, case
                        )
                        await self._conflict_cases.set_repair_task(case.id, repair_task_id)
                    except ValueError as error:
                        if self._conflict_escalate is not None:
                            await self._conflict_escalate(case, str(error))
                        logger.warning(
                            "Delivery conflict could not be assigned case=%s error=%s",
                            case.id,
                            type(error).__name__,
                        )
                logger.warning(
                    "Delivery conflict detected change_set=%s repository=%s kind=%s",
                    change_set_id,
                    candidate.repository_id,
                    conflict_kind.value,
                )
                continue

            for check in await self._adapter.list_check_runs(repository, candidate.commit_sha):
                if not check.terminal or check.head_sha != candidate.commit_sha.lower():
                    continue
                current = await self._delivery.observe_ci(
                    CIObservationCommand(
                        change_set_id=change_set_id,
                        repository_id=candidate.repository_id,
                        passed=check.passed,
                        check_run_id=check.check_run_id,
                        summary=check.summary,
                        check_name=check.check_name,
                    )
                )

            review_states = {
                SCMReviewState.APPROVED: ReviewState.APPROVED,
                SCMReviewState.CHANGES_REQUESTED: ReviewState.CHANGES_REQUESTED,
                SCMReviewState.DISMISSED: ReviewState.DISMISSED,
            }
            latest_reviews = {
                review.reviewer: review
                for review in await self._adapter.list_pull_request_reviews(
                    repository, candidate.pull_request_number
                )
                if review.head_sha == candidate.commit_sha.lower()
            }
            for review in latest_reviews.values():
                current = await self._delivery.observe_review(
                    ReviewObservationCommand(
                        change_set_id=change_set_id,
                        repository_id=candidate.repository_id,
                        review_id=review.review_id,
                        reviewer=review.reviewer,
                        state=review_states[review.state],
                        head_sha=review.head_sha,
                        summary=review.summary,
                    )
                )

            candidate = self._candidate(current, candidate.repository_id)
            if pull_request.state is PullRequestState.MERGED:
                if not pull_request.merge_sha:
                    raise SCMConflict("merged pull request has no merge commit SHA")
                gate = await self._delivery.evaluate_merge_gate(
                    change_set_id, candidate.repository_id
                )
                if (
                    candidate.status is not RepositoryDeliveryStatus.MERGE_REQUESTED
                    and not gate.allowed
                ):
                    raise SCMConflict(
                        "remote pull request merged while RepoMesh merge gate was closed"
                    )
                current = await self._delivery.observe_merge(
                    MergeObservationCommand(
                        change_set_id,
                        candidate.repository_id,
                        pull_request.merge_sha,
                    )
                )
                continue
            if candidate.status is RepositoryDeliveryStatus.MERGE_REQUESTED:
                continue
            if (
                pull_request.state is not PullRequestState.OPEN
                or pull_request.draft
                or pull_request.mergeable is False
            ):
                continue
            gate = await self._delivery.evaluate_merge_gate(change_set_id, candidate.repository_id)
            if gate.allowed:
                current = await self.merge_when_allowed(change_set_id, candidate.repository_id)
        # Draft-ness is the sweep's third convergence duty, and it belongs
        # here rather than before the loop. ``undraft_when_allowed`` asks
        # whether every ``depends_on`` repository is MERGED, and the loop above
        # is precisely what discovers this sweep's merges -- running it first
        # would judge a candidate against dependency state one full interval
        # stale. Nothing is lost by promoting after the gate: this sweep's PR
        # observation was read while the PR was still draft, and in the wired
        # deployment the promotion travels through the SCM command queue
        # anyway, so no ordering inside one sweep could have merged it here.
        # The next sweep sees a non-draft PR and gates it.
        await self.undraft_when_allowed(change_set_id)
        return current

    @staticmethod
    def _reconciled_pull_request_body(change_set: ChangeSetView, candidate) -> str:
        """An honest minimal body for a pull request completed by reconciliation.

        ``PlanDeliveryFinalizer._pull_request_body`` writes the plan narrative
        -- change_id, batch position, repository list -- because it is holding
        the ``ExecutionPlanView``. This path is not, and cannot get one: the
        reconciler starts from ``DeliveryService.list_active()``, and a
        ``ChangeSetView`` carries no route back to its plan. The only link is
        the ChangeSet's idempotency key (``execution-plan:<plan>:delivery``),
        which the store writes but no port reads back for a change set id.
        So this body states the facts the reconciler actually verified against
        the remote, and says who wrote it, instead of reconstructing a
        narrative it cannot check.
        """

        return "\n".join(
            [
                "Automated RepoMesh delivery, completed by reconciliation.",
                "",
                f"- change_set: `{change_set.id}`",
                f"- repository: `{candidate.repository_id}`",
                f"- branch: `{candidate.branch_name}`",
                f"- commit: `{candidate.commit_sha}`",
                "",
                "The candidate branch was published but its pull request never opened.",
                "The delivery reconciler finished the publish; the originating execution",
                "plan is not reachable from a ChangeSet, so this description carries only",
                "what was verified against the remote.",
            ]
        )

    async def _repository_ref(self, repository_id: UUID) -> RepositoryRef:
        profile = await self._catalog.get(repository_id)
        if profile is None:
            raise DeliveryNotFound(f"repository not in catalog: {repository_id}")
        return parse_repository_ref(profile.url)

    @staticmethod
    def _candidate(change_set: ChangeSetView, repository_id: UUID):
        candidate = next(
            (item for item in change_set.repositories if item.repository_id == repository_id),
            None,
        )
        if candidate is None:
            raise DeliveryNotFound(f"repository not in ChangeSet: {repository_id}")
        return candidate

    async def _record_observation(
        self,
        change_set: ChangeSetView,
        repository_id: UUID,
        observation: PullRequestObservation,
    ) -> ChangeSetView:
        return await self._delivery.observe_pull_request(
            PullRequestObservationCommand(
                change_set_id=change_set.id,
                repository_id=repository_id,
                pull_request_number=observation.number,
                pull_request_url=observation.url,
                head_sha=observation.head_sha,
            )
        )


def parse_repository_ref(url: str) -> RepositoryRef:
    parsed = urlparse(url.strip())
    if (parsed.hostname or "").lower() != "github.com":
        raise ValueError("only github.com repository URLs are supported")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise ValueError("repository URL must identify one owner and repository")
    owner, name = parts
    if name.endswith(".git"):
        name = name[:-4]
    if not owner or not name:
        raise ValueError("repository owner and name are required")
    return RepositoryRef(SCMProvider.GITHUB, owner, name)
