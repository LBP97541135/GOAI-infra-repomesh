"""Turn a completed execution plan into governed SCM delivery operations."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from repomesh.modules.delivery import DeliveryService, delivery_change_set_key
from repomesh.modules.delivery.contracts import (
    AppendCandidatesCommand,
    PrepareChangeSetCommand,
    RepositoryCandidateInput,
)
from repomesh.modules.delivery.ports import ContractCatalogPort
from repomesh.modules.project.checkpoint_control import ProjectCheckpointService
from repomesh.modules.project.contracts import ProjectCheckpoint
from repomesh.modules.review_validation import (
    CreateValidationSnapshotCommand,
    ValidationSnapshotService,
    ValidationTestInput,
)
from repomesh.modules.task_orchestration.contracts import (
    BatchDeliveryRefused,
    ExecutionPlanView,
    TaskStatus,
)
from repomesh.modules.task_orchestration.ports import TaskStore

from .delivery import ChangeSetSCMCoordinator, PublishChangeSetPullRequestCommand

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PlanDeliveryPolicy:
    base_branch: str = "main"
    required_checks: tuple[str, ...] = ()
    required_approvals: int = 1
    add_label: bool = False


class PlanDeliveryFinalizer:
    """Create one idempotent ChangeSet and publish every completed candidate."""

    def __init__(
        self,
        delivery: DeliveryService,
        coordinator: ChangeSetSCMCoordinator,
        tasks: TaskStore,
        policy: PlanDeliveryPolicy,
        validation: ValidationSnapshotService | None = None,
        checkpoints: ProjectCheckpointService | None = None,
        contracts: ContractCatalogPort | None = None,
    ) -> None:
        self._delivery = delivery
        self._coordinator = coordinator
        self._tasks = tasks
        self._policy = policy
        self._validation = validation
        self._checkpoints = checkpoints
        self._contracts = contracts

    async def handle(self, plan: ExecutionPlanView) -> None:
        candidates, workspaces, tests = await self._candidates(plan)
        if not candidates:
            return
        if self._checkpoints is not None:
            validation_payload = json.dumps(
                {
                    "heads": sorted(
                        (str(item.repository_id), item.commit_sha) for item in candidates
                    ),
                    "tests": [
                        {
                            "repository_id": str(item.repository_id),
                            "command": item.command,
                            "exit_code": item.exit_code,
                        }
                        for item in tests
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            validation_gate = await self._checkpoints.evaluate(
                plan.project_id,
                ProjectCheckpoint.VALIDATION,
                f"sha256:{hashlib.sha256(validation_payload).hexdigest()}",
            )
            if not validation_gate.allowed:
                raise ValueError(validation_gate.reason)
            delivery_gate = await self._checkpoints.evaluate(
                plan.project_id,
                ProjectCheckpoint.DELIVERY,
                f"execution-plan:{plan.id}:v{plan.version}",
            )
            if not delivery_gate.allowed:
                raise ValueError(delivery_gate.reason)
        idempotency_key = delivery_change_set_key(plan.id)
        existing = await self._delivery.get_by_idempotency_key(idempotency_key)
        validation_snapshot_id = (
            existing.validation_snapshot_id if existing is not None else None
        )
        if existing is None and self._validation is not None:
            snapshot = await self._validation.create(
                CreateValidationSnapshotCommand(
                    organization_id=plan.organization_id,
                    project_id=plan.project_id,
                    specification_version_id=None,
                    candidate_heads={item.repository_id: item.commit_sha for item in candidates},
                    tests=tuple(tests),
                    environment={
                        "runner_protocol": "v1",
                        "execution_plan": str(plan.id),
                    },
                )
            )
            validation_snapshot_id = snapshot.id
        change_set = await self._delivery.prepare(
            PrepareChangeSetCommand(
                organization_id=plan.organization_id,
                project_id=plan.project_id,
                created_by_agent_id=plan.created_by_agent_id,
                title=f"RepoMesh delivery {str(plan.id)[:8]}",
                validation_snapshot_id=validation_snapshot_id,
                candidates=tuple(candidates),
            ),
            idempotency_key=idempotency_key,
        )
        for candidate in sorted(change_set.repositories, key=lambda item: item.merge_order):
            if candidate.pull_request_number is not None:
                continue
            await self._coordinator.publish_and_open_draft_pull_request(
                PublishChangeSetPullRequestCommand(
                    change_set_id=change_set.id,
                    repository_id=candidate.repository_id,
                    workspace=workspaces[candidate.repository_id],
                    base_branch=self._policy.base_branch,
                    body=self._pull_request_body(
                        plan, [candidate.repository_id], batch_index=None
                    ),
                    # Draft by design: consumers of a contract stay hidden
                    # until the producer merges, then undraft_when_allowed
                    # promotes the PR on the next 15s replay cycle.
                    draft=True,
                )
            )
        await self._backfill_sibling_links(change_set.id, plan, batch_index=None)

    async def handle_batch(self, plan: ExecutionPlanView) -> None:
        """Deliver the plan's current batch (batch-by-batch delivery).

        The first successful batch creates the plan's ChangeSet and the later
        batches append their candidates to the same ChangeSet, so merge order
        is preserved across the whole plan. Re-invocation for an already
        delivered batch is idempotent: existing pull requests are skipped and
        ``append_candidates`` ignores repositories already present.
        """
        batch_index = plan.current_batch_index
        candidates, workspaces, tests = await self._candidates_for_batch(plan, batch_index)
        if not candidates:
            return
        idempotency_key = f"execution-plan:{plan.id}:delivery"
        existing = await self._delivery.get_by_idempotency_key(idempotency_key)
        if existing is None:
            validation_snapshot_id = None
            if self._validation is not None:
                snapshot = await self._validation.create(
                    CreateValidationSnapshotCommand(
                        organization_id=plan.organization_id,
                        project_id=plan.project_id,
                        specification_version_id=None,
                        candidate_heads={
                            item.repository_id: item.commit_sha for item in candidates
                        },
                        tests=tuple(tests),
                        environment={
                            "runner_protocol": "v1",
                            "execution_plan": str(plan.id),
                        },
                    )
                )
                validation_snapshot_id = snapshot.id
            change_set = await self._delivery.prepare(
                PrepareChangeSetCommand(
                    organization_id=plan.organization_id,
                    project_id=plan.project_id,
                    created_by_agent_id=plan.created_by_agent_id,
                    title=f"RepoMesh delivery {str(plan.id)[:8]}",
                    validation_snapshot_id=validation_snapshot_id,
                    candidates=tuple(candidates),
                ),
                idempotency_key=idempotency_key,
            )
        else:
            change_set = await self._delivery.append_candidates(
                AppendCandidatesCommand(
                    change_set_id=existing.id,
                    candidates=tuple(candidates),
                ),
                idempotency_key=f"{idempotency_key}:b{batch_index}",
            )
        for candidate in sorted(change_set.repositories, key=lambda item: item.merge_order):
            if (
                candidate.pull_request_number is not None
                or candidate.repository_id not in workspaces
            ):
                continue
            await self._coordinator.publish_and_open_draft_pull_request(
                PublishChangeSetPullRequestCommand(
                    change_set_id=change_set.id,
                    repository_id=candidate.repository_id,
                    workspace=workspaces[candidate.repository_id],
                    base_branch=self._policy.base_branch,
                    body=self._pull_request_body(
                        plan, [candidate.repository_id], batch_index=batch_index
                    ),
                    # Draft by design: consumers of a contract stay hidden
                    # until the producer merges, then undraft_when_allowed
                    # promotes the PR on the next 15s replay cycle.
                    draft=True,
                )
            )
        await self._backfill_sibling_links(
            change_set.id, plan, batch_index=batch_index
        )

    async def _candidates_for_batch(
        self, plan: ExecutionPlanView, batch_index: int
    ) -> tuple[
        list[RepositoryCandidateInput],
        dict[UUID, Path],
        list[ValidationTestInput],
    ]:
        candidates: list[RepositoryCandidateInput] = []
        workspaces: dict[UUID, Path] = {}
        tests: list[ValidationTestInput] = []
        earlier_repositories = [
            item.repository_id
            for batch in plan.batches[:batch_index]
            for item in batch
        ]
        for planned in plan.batches[batch_index]:
            if planned.leader_task_id is None:
                continue
            workers = await self._tasks.list_by_parent(planned.leader_task_id)
            succeeded = [task for task in workers if task.status is TaskStatus.SUCCEEDED]
            if len(succeeded) != 1:
                raise BatchDeliveryRefused(
                    f"repository {planned.repository_id} has no unique successful candidate",
                    repository_id=planned.repository_id,
                )
            worker = succeeded[0]
            # Declared evidence, resolved by the producing module at projection
            # time. This used to re-parse ``result_summary`` here, which is free
            # text by contract, so delivery was reading a shape nobody promised
            # it -- and parsing it differently from the producer besides.
            evidence = worker.to_view().evidence
            if evidence is None:
                raise BatchDeliveryRefused(
                    f"repository {planned.repository_id} candidate carries no Runner evidence",
                    repository_id=planned.repository_id,
                    task_id=worker.id,
                )
            # ``commit_sha`` is nullable since A-18's fourth face let failed runs
            # keep their evidence. This path only ever sees SUCCEEDED workers, so
            # a null here means a run that reported success without committing --
            # not a shape to publish. It falls through to the ``_full_sha`` check
            # below and refuses there; the ``or ""`` exists so that check gets to
            # run at all instead of an AttributeError two lines earlier.
            commit_sha = (evidence.commit_sha or "").lower()
            base_sha = (evidence.base_sha or "").lower()
            workspace = Path(evidence.workspace_path or "")
            if not self._full_sha(commit_sha) or not self._full_sha(base_sha):
                raise BatchDeliveryRefused(
                    "Runner evidence has no frozen commit/base SHA",
                    repository_id=planned.repository_id,
                    task_id=worker.id,
                )
            if not evidence.workspace_path or not workspace.is_dir():
                raise BatchDeliveryRefused(
                    "Runner evidence workspace no longer exists",
                    repository_id=planned.repository_id,
                    task_id=worker.id,
                )
            branch = f"repomesh/{str(plan.id)[:8]}/{str(planned.repository_id)[:8]}"
            candidates.append(
                RepositoryCandidateInput(
                    repository_id=planned.repository_id,
                    task_id=worker.id,
                    commit_sha=commit_sha,
                    base_sha=base_sha,
                    branch_name=branch,
                    depends_on=tuple(earlier_repositories),
                    required_checks=self._policy.required_checks,
                    required_approvals=self._policy.required_approvals,
                )
            )
            workspaces[planned.repository_id] = workspace
            # The last undeclared read is gone (A-18): test results are now part
            # of TaskEvidenceView, so this reads the producer's parse like every
            # other field above instead of re-opening the free-text summary.
            #
            # The refusal stays exactly as strict, and since A-19 it is a
            # *named* refusal the advancer records on the round instead of a
            # ValueError that died in a log. ``_parse_evidence`` drops an entry
            # it cannot read, which is right for a display projection and wrong
            # here: delivery must not publish a candidate whose test evidence
            # was partly unreadable, so a dropped entry surfaces as a count
            # mismatch against the raw list and refuses.
            raw_test_count = len(self._evidence(worker.result_summary).get("testResults") or ())
            if not evidence.test_results:
                raise BatchDeliveryRefused(
                    "Runner evidence has no test results",
                    repository_id=planned.repository_id,
                    task_id=worker.id,
                )
            if len(evidence.test_results) != raw_test_count:
                raise BatchDeliveryRefused(
                    "Runner test evidence is malformed",
                    repository_id=planned.repository_id,
                    task_id=worker.id,
                )
            for result in evidence.test_results:
                tests.append(
                    ValidationTestInput(
                        repository_id=planned.repository_id,
                        command=result.command,
                        exit_code=result.exit_code,
                        summary=result.summary,
                    )
                )
            earlier_repositories.append(planned.repository_id)
        await self._check_contract_coverage(plan, earlier_repositories)
        return candidates, workspaces, tests

    async def _check_contract_coverage(
        self, plan: ExecutionPlanView, delivered_ids: list[UUID]
    ) -> None:
        """Primary path: a delivered producer's contract consumer must have a candidate.

        Consumers are part of the plan's batches (the graph reasoning stage
        emits adapter tasks), so they already enter the candidate set when
        their batch is delivered. A contract whose consumer has neither a
        planned task nor a delivered candidate is left to the merge gate's
        eighth check ("contract change is missing a consumer adapter
        candidate") to refuse the producer merge.
        """
        if self._contracts is None:
            return
        contracts = await self._contracts.contracts_for_project(plan.project_id)
        if not contracts:
            return
        planned_ids = {
            planned.repository_id
            for batch in plan.batches
            for planned in batch
            if planned.leader_task_id is not None
        }
        for contract in contracts:
            if contract.producer not in delivered_ids:
                continue
            if contract.consumer in planned_ids or contract.consumer in delivered_ids:
                continue
            _logger.warning(
                "contract %s->%s has no consumer adapter candidate for plan %s",
                contract.producer,
                contract.consumer,
                plan.id,
            )

    async def _backfill_sibling_links(
        self,
        change_set_id: UUID,
        plan: ExecutionPlanView,
        *,
        batch_index: int | None,
    ) -> None:
        """Append the sibling PR list to every published description.

        Each publish run rewrites the descriptions so that a ChangeSet's PRs
        link to each other. The update is skipped for single-repository
        ChangeSets and is idempotent per repository (same body content).
        """

        current = await self._delivery.get(change_set_id)
        published = [
            (item.repository_id, item.pull_request_number)
            for item in sorted(current.repositories, key=lambda item: item.merge_order)
            if item.pull_request_number is not None
        ]
        if len(published) < 2:
            return
        sibling_section = self._sibling_links(current, published)
        for repository_id, _ in published:
            body = self._pull_request_body(
                plan, [repository_id], batch_index=batch_index
            )
            await self._coordinator.update_pull_request_description(
                change_set_id, repository_id, body + sibling_section
            )
            if self._policy.add_label:
                await self._coordinator.add_change_set_label(
                    change_set_id, repository_id
                )

    @staticmethod
    def _sibling_links(change_set, published: list[tuple[UUID, int | None]]) -> str:
        status_by_repository = {
            item.repository_id: item.status.value
            for item in change_set.repositories
        }
        links = [
            (
                f"- `{str(repository_id)[:8]}`: "
                f"PR #{number} ({status_by_repository.get(repository_id, 'unknown')})"
            )
            for repository_id, number in published
            if number is not None
        ]
        return "\n\n## Sibling PRs in this ChangeSet\n\n" + "\n".join(links)

    @staticmethod
    def _pull_request_body(
        plan: ExecutionPlanView,
        repository_ids: list[UUID],
        *,
        batch_index: int | None,
    ) -> str:
        """Standard PR body: change_id, execution order and batch scope."""
        batch_line = (
            f"- execution order: batch {batch_index + 1}"
            if batch_index is not None
            else "- execution order: full plan"
        )
        return "\n".join(
            [
                f"Automated RepoMesh delivery for execution plan `{plan.id}`.",
                "",
                f"- change_id: `{plan.id}`",
                batch_line,
                "- repositories: "
                + ", ".join(f"`{repository_id}`" for repository_id in repository_ids),
                "",
                "The candidate commits passed their frozen Task Spec commands.",
            ]
        )

    async def _candidates(
        self, plan: ExecutionPlanView
    ) -> tuple[
        list[RepositoryCandidateInput],
        dict[UUID, Path],
        list[ValidationTestInput],
    ]:
        candidates: list[RepositoryCandidateInput] = []
        workspaces: dict[UUID, Path] = {}
        tests: list[ValidationTestInput] = []
        for batch_index in range(len(plan.batches)):
            batch_candidates, batch_workspaces, batch_tests = (
                await self._candidates_for_batch(plan, batch_index)
            )
            candidates.extend(batch_candidates)
            workspaces.update(batch_workspaces)
            tests.extend(batch_tests)
        return candidates, workspaces, tests

    @staticmethod
    def _evidence(raw: str | None) -> dict[str, object]:
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("task result is not structured Runner evidence") from error
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _full_sha(value: str) -> bool:
        return len(value) == 40 and all(character in "0123456789abcdef" for character in value)
