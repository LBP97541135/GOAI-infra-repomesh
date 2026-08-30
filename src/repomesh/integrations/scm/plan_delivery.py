"""Turn a completed execution plan into governed SCM delivery operations."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from repomesh.modules.delivery import DeliveryService, delivery_change_set_key
from repomesh.modules.delivery.contracts import (
    AppendCandidatesCommand,
    DeliveryTraceability,
    PrepareChangeSetCommand,
    RecordCandidateTraceabilityCommand,
    RepositoryCandidateInput,
    RepositoryDeliveryView,
    render_delivery_pull_request_body,
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
    PlannedRepositoryTaskView,
    TaskStatus,
)
from repomesh.modules.task_orchestration.domain import Task
from repomesh.modules.task_orchestration.ports import TaskStore

from .delivery import ChangeSetSCMCoordinator, PublishChangeSetPullRequestCommand

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PlanDeliveryPolicy:
    base_branch: str = "main"
    required_checks: tuple[str, ...] = ()
    required_approvals: int = 1
    add_label: bool = False


@dataclass(frozen=True, slots=True)
class _WorkerProvenance:
    """The two traceability ids a delivered candidate does not persist.

    ``RepositoryCandidateInput`` keeps run and worker out on purpose (ruling
    W-C3-D3: no migration, no change to the persisted candidate), so the
    finalizer carries them beside the candidates it just built rather than
    through the delivery contract.
    """

    run_id: UUID | None
    worker_agent_id: UUID

    @classmethod
    def of(cls, worker: Task) -> _WorkerProvenance:
        evidence = worker.to_view().evidence
        return cls(
            run_id=evidence.run_id if evidence is not None else None,
            worker_agent_id=worker.assignee_agent_id,
        )


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
        policy_resolver: Callable[[UUID, UUID | None], Awaitable[PlanDeliveryPolicy]] | None = None,
    ) -> None:
        self._delivery = delivery
        self._coordinator = coordinator
        self._tasks = tasks
        self._policy = policy
        self._policy_resolver = policy_resolver
        self._validation = validation
        self._checkpoints = checkpoints
        self._contracts = contracts

    async def handle(self, plan: ExecutionPlanView) -> None:
        self._policy = await self._resolve_policy(plan.organization_id)
        candidates, workspaces, tests, provenance = await self._candidates(plan)
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
        await self._record_traceability(change_set.id, candidates)
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
                        plan, change_set.id, candidate, provenance, batch_index=None
                    ),
                    # Draft by design: consumers of a contract stay hidden
                    # until the producer merges, then undraft_when_allowed
                    # promotes the PR on the next 15s replay cycle.
                    draft=True,
                )
            )
        await self._backfill_sibling_links(
            change_set.id, plan, provenance, batch_index=None
        )

    async def handle_batch(self, plan: ExecutionPlanView) -> None:
        """Deliver the plan's current batch (batch-by-batch delivery).

        The first successful batch creates the plan's ChangeSet and the later
        batches append their candidates to the same ChangeSet, so merge order
        is preserved across the whole plan. Re-invocation for an already
        delivered batch is idempotent: existing pull requests are skipped and
        ``append_candidates`` ignores repositories already present.
        """
        self._policy = await self._resolve_policy(plan.organization_id)
        batch_index = plan.current_batch_index
        candidates, workspaces, tests, provenance = await self._candidates_for_batch(
            plan, batch_index
        )
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
        await self._record_traceability(change_set.id, candidates)
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
                        plan, change_set.id, candidate, provenance, batch_index=batch_index
                    ),
                    # Draft by design: consumers of a contract stay hidden
                    # until the producer merges, then undraft_when_allowed
                    # promotes the PR on the next 15s replay cycle.
                    draft=True,
                )
            )
        await self._backfill_sibling_links(
            change_set.id,
            plan,
            await self._delivered_provenance(plan, batch_index),
            batch_index=batch_index,
        )

    async def _resolve_policy(self, organization_id: UUID) -> PlanDeliveryPolicy:
        if self._policy_resolver is None:
            return self._policy
        return await self._policy_resolver(organization_id, None)

    async def _candidates_for_batch(
        self, plan: ExecutionPlanView, batch_index: int
    ) -> tuple[
        list[RepositoryCandidateInput],
        dict[UUID, Path],
        list[ValidationTestInput],
        dict[UUID, _WorkerProvenance],
    ]:
        candidates: list[RepositoryCandidateInput] = []
        workspaces: dict[UUID, Path] = {}
        tests: list[ValidationTestInput] = []
        provenance: dict[UUID, _WorkerProvenance] = {}
        earlier_repositories = [
            item.repository_id
            for batch in plan.batches[:batch_index]
            for item in batch
        ]
        for planned in plan.batches[batch_index]:
            if planned.leader_task_id is None:
                continue
            worker = await self._successful_worker(planned)
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
            worker_provenance = _WorkerProvenance.of(worker)
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
                    plan_id=plan.id,
                    run_id=worker_provenance.run_id,
                    worker_agent_id=worker_provenance.worker_agent_id,
                )
            )
            workspaces[planned.repository_id] = workspace
            provenance[planned.repository_id] = worker_provenance
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
        return candidates, workspaces, tests, provenance

    async def _successful_worker(self, planned: PlannedRepositoryTaskView) -> Task:
        workers = await self._tasks.list_by_parent(planned.leader_task_id)
        succeeded = [task for task in workers if task.status is TaskStatus.SUCCEEDED]
        if len(succeeded) != 1:
            raise BatchDeliveryRefused(
                f"repository {planned.repository_id} has no unique successful candidate",
                repository_id=planned.repository_id,
            )
        return succeeded[0]

    async def _delivered_provenance(
        self, plan: ExecutionPlanView, batch_index: int
    ) -> dict[UUID, _WorkerProvenance]:
        """Provenance for every batch delivered so far, not only the current one.

        ``_backfill_sibling_links`` rewrites the description of every published
        pull request in the ChangeSet, and the earlier batches' run and worker
        ids are not in this invocation's candidate set. Rewriting without them
        would strip two traceability lines off a pull request that was opened
        carrying them.
        """

        provenance: dict[UUID, _WorkerProvenance] = {}
        for batch in plan.batches[: batch_index + 1]:
            for planned in batch:
                if planned.leader_task_id is None:
                    continue
                provenance[planned.repository_id] = _WorkerProvenance.of(
                    await self._successful_worker(planned)
                )
        return provenance

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
        provenance: dict[UUID, _WorkerProvenance],
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
            item
            for item in sorted(current.repositories, key=lambda item: item.merge_order)
            if item.pull_request_number is not None
        ]
        if not published:
            return
        sibling_section = (
            self._sibling_links(
                current,
                [(item.repository_id, item.pull_request_number) for item in published],
            )
            if len(published) > 1
            else ""
        )
        for candidate in published:
            repository_id = candidate.repository_id
            body = self._pull_request_body(
                plan, change_set_id, candidate, provenance, batch_index=batch_index
            )
            await self._coordinator.update_pull_request_description(
                change_set_id, repository_id, body + sibling_section
            )
            if self._policy.add_label:
                await self._coordinator.add_change_set_label(
                    change_set_id, repository_id
                )

    async def _record_traceability(
        self, change_set_id: UUID, candidates: list[RepositoryCandidateInput]
    ) -> None:
        """Persist the owning application's complete chain before SCM replay."""

        for candidate in candidates:
            if candidate.plan_id is None or candidate.worker_agent_id is None:
                continue
            await self._delivery.record_candidate_traceability(
                RecordCandidateTraceabilityCommand(
                    change_set_id=change_set_id,
                    repository_id=candidate.repository_id,
                    task_id=candidate.task_id,
                    commit_sha=candidate.commit_sha,
                    plan_id=candidate.plan_id,
                    run_id=candidate.run_id,
                    worker_agent_id=candidate.worker_agent_id,
                )
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
        change_set_id: UUID,
        candidate: RepositoryDeliveryView,
        provenance: dict[UUID, _WorkerProvenance],
        *,
        batch_index: int | None,
    ) -> str:
        """The full traceability chain, assembled here because only here is it whole.

        Ruling D-9 places the assembly in the owning application: the plan
        supplies the Issue and plan ids, the ChangeSet candidate the repository,
        task, branch and commit, and ``provenance`` the run and worker the SCM
        layer is not allowed to go looking for.

        ``provenance`` is looked up rather than indexed because a replay of an
        already-delivered batch (which ``handle_batch`` promises is idempotent)
        can back-fill a repository belonging to a *later* batch than the index
        it was handed. Two lines thinner is the right answer there; refusing the
        replay outright is not.
        """

        worker = provenance.get(candidate.repository_id)
        traceability = DeliveryTraceability(
            issue_id=plan.project_id,
            change_set_id=change_set_id,
            repository_id=candidate.repository_id,
            task_id=candidate.task_id,
            branch_name=candidate.branch_name,
            commit_sha=candidate.commit_sha,
            plan_id=plan.id,
            run_id=worker.run_id if worker is not None else None,
            worker_agent_id=worker.worker_agent_id if worker is not None else None,
        )
        batch_line = (
            f"execution order: batch {batch_index + 1}"
            if batch_index is not None
            else "execution order: full plan"
        )
        return render_delivery_pull_request_body(
            traceability,
            headline=f"Automated RepoMesh delivery for execution plan `{plan.id}`.",
            context=(batch_line,),
            notes=("The candidate commits passed their frozen Task Spec commands.",),
        )

    async def _candidates(
        self, plan: ExecutionPlanView
    ) -> tuple[
        list[RepositoryCandidateInput],
        dict[UUID, Path],
        list[ValidationTestInput],
        dict[UUID, _WorkerProvenance],
    ]:
        candidates: list[RepositoryCandidateInput] = []
        workspaces: dict[UUID, Path] = {}
        tests: list[ValidationTestInput] = []
        provenance: dict[UUID, _WorkerProvenance] = {}
        for batch_index in range(len(plan.batches)):
            batch_candidates, batch_workspaces, batch_tests, batch_provenance = (
                await self._candidates_for_batch(plan, batch_index)
            )
            candidates.extend(batch_candidates)
            workspaces.update(batch_workspaces)
            tests.extend(batch_tests)
            provenance.update(batch_provenance)
        return candidates, workspaces, tests, provenance

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
