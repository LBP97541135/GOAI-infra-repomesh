"""Plan execution bridge.

Consumes an :class:`IntegratedPlan` produced by :class:`PlanIntegrationService`
and materialises it into the *specification* and *task_orchestration* modules:

- Engineering Spec → ``SpecificationService.create(kind=ENGINEERING)``
- Each Contract   → ``SpecificationService.create(kind=CONTRACT)``
- The batched task DAG → one execution plan started through
  :class:`ExecutionPlanStarter`

Only the first batch is assigned when the plan starts: the execution plan owns
batch progression, so later batches are assigned by *task_orchestration* once
the Runner reports the previous batch as terminal.

Fail-closed: when no execution plane is configured (no Matrix messenger,
therefore no task orchestrator) ``materialize`` raises
:class:`ExecutionPlaneUnavailable` **before any side effect** instead of
silently creating specs-only output — a 200 response whose ``task_ids`` is
empty proved indistinguishable from success in live use.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from opentelemetry import trace

from repomesh.modules.project.checkpoint_fallback import TopologyAwareCheckpointFallback
from repomesh.modules.project.contracts import (
    ProjectCheckpoint,
    ProjectCheckpointGateway,
    ProjectTopologyReader,
)
from repomesh.modules.repository_intelligence.application.plan_integration import (
    IntegratedPlan,
    TaskNode,
)
from repomesh.modules.repository_intelligence.ports.catalog import RepositoryCatalog
from repomesh.modules.specification.contracts import (
    CreateSpecificationCommand,
    SpecificationKind,
    SpecificationView,
)
from repomesh.modules.task_orchestration.contracts import (
    PlannedRepositoryTaskView,
    SupersedeTaskCommand,
    TaskStatus,
    TaskView,
)
from repomesh.shared.workflow import WorkflowBlocked
from repomesh.telemetry import SpanAttributes, traced

from .contracts import (
    ExecutionPlaneUnavailable,
    MaterializationResult,
    ReplanResult,
    RoundNotRecorded,
)
from .ports import (
    ExecutionPlanStarter,
    HandoffDocGenerator,
    PlanSnapshotWriter,
    ProjectTaskReader,
    SpecificationCreator,
    TaskSupersederGateway,
)

if TYPE_CHECKING:
    from repomesh.modules.repository_intelligence.application.confirmation import (
        ConfirmationSummary,
    )
    from repomesh.modules.repository_intelligence.application.dependency_graph import (
        DependencyGraphService,
    )
    from repomesh.modules.repository_intelligence.application.plan_integration import (
        PlanIntegrationService,
    )

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate(text: str, limit: int = 60) -> str:
    """Return *text* truncated to *limit* chars with an ellipsis."""

    text = text.strip()
    return text[:limit] + "…" if len(text) > limit else text


def _jsonable(item: object) -> dict:
    """Serialise one plan element for the snapshot's JSON columns.

    ``ContractSpec`` and ``TaskNode`` are frozen slotted dataclasses with no
    ``to_dict`` — the previous ``dict(item)`` fallback raised ``TypeError`` on
    every one of them, and the enclosing ``except Exception`` turned that into
    a log line, so materialize wrote no snapshot at all while reporting
    success. ``asdict`` is the conversion those dataclasses actually support.
    """

    to_dict = getattr(item, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    if is_dataclass(item) and not isinstance(item, type):
        return asdict(item)
    return dict(item)  # type: ignore[call-overload]


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------


class PlanExecutionBridge:
    """Materialise an :class:`IntegratedPlan` into specs and tasks.

    Usage::

        bridge = PlanExecutionBridge(specs, plans, topologies, catalog)
        result = await bridge.materialize(
            plan=plan,
            requirement="fix notification email bug",
            project_id=project_uuid,
            leader_agent_id=leader_uuid,
            idempotency_prefix="tt-001",
        )
    """

    def __init__(
        self,
        specifications: SpecificationCreator,
        plans: ExecutionPlanStarter | None,
        topologies: ProjectTopologyReader,
        catalog: RepositoryCatalog,
        snapshot_store: PlanSnapshotWriter | None = None,
        superseder: TaskSupersederGateway | None = None,
        task_reader: ProjectTaskReader | None = None,
        handoff_docs: HandoffDocGenerator | None = None,
        checkpoints: ProjectCheckpointGateway | None = None,
    ) -> None:
        self._specs = specifications
        self._plans = plans
        self._topologies = topologies
        self._catalog = catalog
        self._snapshots = snapshot_store
        self._superseder = superseder
        self._task_reader = task_reader
        self._handoff_docs = handoff_docs
        self._checkpoints = checkpoints or TopologyAwareCheckpointFallback(topologies)

    @traced("planning.materialize")
    async def materialize(
        self,
        plan: IntegratedPlan,
        requirement: str,
        project_id: UUID,
        leader_agent_id: UUID,
        *,
        idempotency_prefix: str,
        repo_details: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> MaterializationResult:
        """Create specs and tasks from *plan*.

        Args:
            plan: The integrated plan from PlanIntegrationService.
            requirement: Original requirement text.
            project_id: Target project UUID.
            leader_agent_id: ORG_LEADER agent UUID.
            idempotency_prefix: Unique prefix for idempotency keys
                (e.g. ``"tt-001"``).
            repo_details: Optional per-repository adjustment plan from the
                confirmation phase (keyed by repository name) used to enrich
                the handoff documents.  Plain ``dict`` values only.

        Returns:
            :class:`MaterializationResult` with created specs and tasks.

        Raises:
            ExecutionPlaneUnavailable: when no task orchestrator is configured.
                Raised before any spec is created so a refused materialization
                leaves no partial state behind.
            RoundNotRecorded: when a plan was started but the snapshot could
                not be made to name it. The opposite bargain to the one above:
                the side effects stand, and the failure is reported so the
                round is not silently lost.
        """

        # --- 0. Fail closed before any side effect ----------------------------
        if self._plans is None:
            raise ExecutionPlaneUnavailable(
                "task orchestration plane is not configured (no assignment "
                "gateway — is the Matrix messenger set up?); refusing to "
                "materialize a plan whose tasks cannot be assigned"
            )

        # --- 1. Load topology --------------------------------------------------
        topology = await self._topologies.get_view(project_id)
        if topology is None:
            raise ValueError(f"Project topology not found: {project_id}")

        org_id = topology.organization_id

        scope_payload = json.dumps(
                {
                    "repositories": sorted({task.repository for task in plan.task_dag}),
                    "contracts": sorted(
                        (item.producer, item.consumer, item.interface)
                        for item in plan.contracts
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        gate = await self._checkpoints.evaluate(
            project_id,
            ProjectCheckpoint.REPOSITORY_SCOPE,
            f"sha256:{hashlib.sha256(scope_payload).hexdigest()}",
        )
        if not gate.allowed:
            raise WorkflowBlocked(gate.reason)

        # --- 1b. Build name → UUID mappings -----------------------------------
        # catalog has RepositoryProfile(name, id) — gives us name → repository_id
        # topology has RepositoryTeamView(repository_id, leader_agent_id)
        profiles = await self._catalog.list()
        name_to_repo_id = {p.name: p.id for p in profiles}
        repo_id_to_team = {t.repository_id: t for t in topology.repository_teams}

        # --- 2. Create Engineering Spec ---------------------------------------
        eng_spec = await self._specs.create(
            CreateSpecificationCommand(
                organization_id=org_id,
                project_id=project_id,
                kind=SpecificationKind.ENGINEERING,
                created_by_agent_id=leader_agent_id,
                title=_truncate(requirement, 80),
                goal=plan.engineering_spec or requirement,
                acceptance=self._derive_acceptance(plan),
                scope=tuple(t.repository for t in plan.task_dag),
                dependencies=(),
                interface_changes=tuple(
                    f"{c.producer}→{c.consumer}: {c.interface}" for c in plan.contracts
                ),
            ),
            idempotency_key=f"{idempotency_prefix}-eng-spec",
        )
        _logger.info("Created Engineering Spec %s", eng_spec.id)

        # --- 3. Create Contract Specs -----------------------------------------
        contract_specs: list[SpecificationView] = []
        for i, contract in enumerate(plan.contracts):
            cs = await self._specs.create(
                CreateSpecificationCommand(
                    organization_id=org_id,
                    project_id=project_id,
                    kind=SpecificationKind.CONTRACT,
                    created_by_agent_id=leader_agent_id,
                    title=f"{contract.producer} → {contract.consumer}: {contract.interface}",
                    goal=contract.agreement,
                    acceptance=(
                        f"{contract.consumer} must adapt to {contract.producer}'s "
                        f"changes on {contract.interface}",
                    ),
                    scope=(contract.producer, contract.consumer),
                    dependencies=(),
                    interface_changes=(contract.interface,),
                ),
                idempotency_key=f"{idempotency_prefix}-contract-{i}",
            )
            contract_specs.append(cs)
            _logger.info(
                "Created Contract Spec %s for %s→%s",
                cs.id, contract.producer, contract.consumer,
            )

        # --- 4. Start the execution plan --------------------------------------
        tasks_created: list[TaskView] = []
        skipped: list[str] = []
        plan_id: UUID | None = None

        batches = self._plan_batches(
            plan,
            name_to_repo_id=name_to_repo_id,
            teamed_repository_ids=set(repo_id_to_team),
            skipped=skipped,
            verification=self._verification_commands(profiles),
            verification_paths=self._verification_paths(profiles),
        )
        if batches:
            started = await self._plans.start_plan(
                organization_id=org_id,
                project_id=project_id,
                created_by_agent_id=leader_agent_id,
                batches=batches,
                idempotency_key=f"{idempotency_prefix}-plan",
            )
            plan_id = started.plan.id
            tasks_created.extend(started.tasks)
            _logger.info(
                "Started execution plan %s with %d batch(es), current batch %d",
                started.plan.id,
                len(started.plan.batches),
                started.plan.current_batch_index,
            )
        else:
            _logger.info("No executable repository in the plan, nothing to schedule")

        span = trace.get_current_span()
        span.set_attribute(SpanAttributes.PROJECT_ID, str(project_id))
        span.set_attribute(
            "repomesh.materialize.contract_spec_count", len(contract_specs)
        )
        span.set_attribute("repomesh.materialize.task_count", len(tasks_created))
        span.set_attribute("repomesh.materialize.skipped_repos", list(skipped))

        # --- 5. Record the plan snapshot (if store configured) ---------------
        #
        # Contract v0.4 §2.4 / Q3 case (b): when the round already has a draft
        # snapshot — which it does whenever the issue was created through the
        # console, and whenever the discovery chain ran — materialize fills
        # that row in and consumes it rather than writing a second one.
        #
        # The alternative was to keep writing a fresh version here. That works,
        # but it makes every console round cost two versions (draft v1 holding
        # the discovery archive, execution v2 holding the plan) and pushes
        # `rounds[].plan_version` to start at 2, with the round's own discovery
        # evidence sitting on a different row from the plan it produced.
        # Reusing the draft keeps v1 meaning "the first round" and keeps the
        # archive and the plan on one row.
        #
        # The script path is untouched: with no draft — nothing created the
        # issue through intake — this still allocates the next version and
        # inserts, which is what a replan-style second materialize does too.
        plan_version: int | None = None
        if self._snapshots is not None:
            try:
                # Inside the try, deliberately: a snapshot problem must not
                # undo tasks that have already been created. That leniency is
                # also what hid the serialisation bug for the life of this
                # feature, so it now ends where a plan begins — see the
                # `plan_id is not None` re-raise below.
                contracts_payload = [_jsonable(c) for c in plan.contracts]
                task_dag_payload = [_jsonable(t) for t in plan.task_dag]
                batches_payload = [list(b) for b in plan.execution_batches]
                draft = await self._snapshots.current_draft(project_id)
                if draft is not None:
                    plan_version = draft.plan_version
                    await self._snapshots.set_integration(
                        draft.id,
                        engineering_spec=plan.engineering_spec or requirement,
                        contracts=contracts_payload,
                        task_dag=task_dag_payload,
                        execution_batches=batches_payload,
                        integration_method="llm_only",
                    )
                    if plan_id is not None:
                        # Only an actual execution plan consumes the draft. With
                        # nothing schedulable the row stays open, because a
                        # snapshot that claims to have been executed when no
                        # plan started is the dishonesty this column exists to
                        # avoid.
                        await self._snapshots.link_execution_plan(draft.id, plan_id)
                else:
                    plan_version = await self._snapshots.next_version(project_id)
                    await self._snapshots.save(
                        project_id=project_id,
                        plan_version=plan_version,
                        engineering_spec=plan.engineering_spec or requirement,
                        contracts=contracts_payload,
                        task_dag=task_dag_payload,
                        execution_batches=batches_payload,
                        graph_edges=[],
                        created_by_agent_id=leader_agent_id,
                        execution_plan_id=plan_id,
                        requirement_text=requirement,
                        integration_method="llm_only",
                    )
            except Exception as error:
                # Loud about which project lost its snapshot: the previous
                # message named neither the project nor the round, so the one
                # line this bug ever produced was indistinguishable from noise.
                _logger.warning(
                    "Failed to record the plan snapshot for project %s (v%s); "
                    "the plan executed but the DAG panel has nothing to read",
                    project_id,
                    plan_version,
                    exc_info=True,
                )
                if plan_id is not None:
                    # A started plan that no snapshot names is not a degraded
                    # panel, it is a round the server has lost track of: the
                    # draft still reads as unconsumed, so §8's "already
                    # materialised" 409 cannot fire and the next attempt starts
                    # a second execution plan against the same repositories.
                    # Answering 200 there is the one outcome nobody can repair,
                    # because nothing is left that says a repair is due.
                    #
                    # Failing instead is repairable, and only because of the
                    # replay machinery this sits on: the receipt records the
                    # failure and lends its prefix onward, `start_plan` finds
                    # the plan it already wrote and hands it back without
                    # reassigning anyone, and the link is simply attempted
                    # again. The tasks stay; only the verdict changes.
                    raise RoundNotRecorded(
                        f"execution plan {plan_id} was started for project "
                        f"{project_id} but its plan snapshot could not be "
                        f"updated ({error}); the work is running and the round "
                        "is not on record — materialize again to finish "
                        "recording it"
                    ) from error

        # --- 5b. Generate handoff documents (if store configured) ------------
        # One PENDING document per repository so the repository owner can
        # manually approve or reject the proposed adjustment before it lands.
        handoff_doc_ids: list[UUID] = []
        if self._handoff_docs is not None:
            try:
                docs = await self._handoff_docs.generate_for_plan(
                    project_id=project_id,
                    plan_version=plan_version if plan_version is not None else 1,
                    plan=plan,
                    requirement=requirement,
                    created_by_agent_id=leader_agent_id,
                    details=repo_details,
                )
                handoff_doc_ids = [doc.id for doc in docs]
                _logger.info(
                    "Generated %d handoff document(s) for plan v%d",
                    len(docs),
                    plan_version,
                )
            except Exception:
                _logger.warning(
                    "Failed to generate handoff documents", exc_info=True
                )

        return MaterializationResult(
            engineering_spec=eng_spec,
            contract_specs=contract_specs,
            tasks=tasks_created,
            skipped_repos=skipped,
            plan_id=plan_id,
            handoff_doc_ids=handoff_doc_ids,
        )

    @traced("planning.replan")
    async def replan(
        self,
        *,
        project_id: UUID,
        leader_agent_id: UUID,
        feedback: str,
        change_source_repo: str,
        plan_version: int,
        requirement: str,
        idempotency_prefix: str,
        all_repos: list[str],
        integration_service: PlanIntegrationService | None = None,
        confirmation_summary: ConfirmationSummary | None = None,
        graph: DependencyGraphService | None = None,
    ) -> ReplanResult:
        """Partially replan after a BLOCKED task reports an upstream change.

        Flow:

        1. **Impact analysis** — use the dependency graph's reverse edges to
           find every repository that depends on *change_source_repo*. The
           affected set always contains the change source itself.
        2. **Local re-integration** (optional) — when an *integration_service*
           is supplied, re-run plan integration scoped to the affected repos
           with a stability constraint appended to the requirement. This is a
           best-effort step; the framework still works without it.
        3. **Version migration** — supersede the old tasks of the affected
           repositories so the Runner can interrupt them, then (when a plan
           starter is configured) start a new execution plan for the new batch.
        4. **Notification** — record which Team Managers must be interrupted.
           The actual collaboration push is performed by the API layer, which
           owns the :class:`CollaborationGateway`; the bridge only reports the
           affected repos so callers know who to notify.

        Args:
            project_id: Target project UUID.
            leader_agent_id: ORG_LEADER agent UUID authorising the replan.
            feedback: BLOCKED task feedback explaining the upstream change.
            change_source_repo: Repository whose change triggered the replan.
            plan_version: Current plan version being superseded.
            requirement: Original requirement text (feedback is appended).
            idempotency_prefix: Unique prefix for idempotency keys.
            all_repos: Every repository in the current plan (for scoping).
            integration_service: Optional service for local re-integration.
            confirmation_summary: Optional prior confirmation to reuse.
            graph: Dependency graph for impact analysis.

        Returns:
            :class:`ReplanResult` with superseded/new tasks and affected repos.

        Raises:
            ExecutionPlaneUnavailable: when no superseder is configured. Raised
                before any side effect so a refused replan leaves no state.
        """

        # --- 0. Fail closed before any side effect ----------------------------
        if self._superseder is None:
            raise ExecutionPlaneUnavailable(
                "task orchestration plane is not configured (no superseder "
                "gateway — is the Matrix messenger set up?); refusing to "
                "replan when tasks cannot be superseded"
            )

        # --- 1. Impact analysis ------------------------------------------------
        affected_repos = self._compute_affected_repos(
            change_source_repo=change_source_repo,
            all_repos=all_repos,
            graph=graph,
        )
        _logger.info(
            "replan: change_source=%s affected_repos=%s",
            change_source_repo,
            affected_repos,
        )

        # --- 2. Local re-integration (optional, best effort) ------------------
        new_plan: IntegratedPlan | None = None
        if integration_service is not None and confirmation_summary is not None:
            try:
                new_plan = self._local_replan(
                    integration_service=integration_service,
                    confirmation_summary=confirmation_summary,
                    requirement=requirement,
                    feedback=feedback,
                    affected_repos=affected_repos,
                )
            except Exception:
                _logger.warning(
                    "replan: local re-integration failed, "
                    "falling back to cancel-only",
                    exc_info=True,
                )
                new_plan = None

        # --- 3. Version migration: supersede old tasks of affected repos -------
        new_plan_version = plan_version + 1
        superseded_tasks = await self._supersede_affected_tasks(
            project_id=project_id,
            affected_repos=affected_repos,
            feedback=feedback,
            idempotency_prefix=idempotency_prefix,
        )

        # --- 3b. Start a new execution plan for the affected repos ------------
        new_tasks: list[TaskView] = []
        if new_plan is not None and self._plans is not None:
            new_tasks = await self._start_replan_batch(
                new_plan=new_plan,
                affected_repos=affected_repos,
                project_id=project_id,
                leader_agent_id=leader_agent_id,
                idempotency_prefix=idempotency_prefix,
                new_plan_version=new_plan_version,
            )

        # --- 3c. Regenerate handoff documents for the affected repos ----------
        # A replan produces a new plan version: the previous documents of the
        # affected repositories are superseded and fresh PENDING documents are
        # generated from the new plan so repository owners re-approve the
        # adjusted proposal. When no new plan was produced (cancel-only
        # fallback), the stale documents are still superseded.
        handoff_doc_ids: list[UUID] = []
        if self._handoff_docs is not None and affected_repos:
            try:
                if new_plan is None:
                    await self._handoff_docs.supersede_for_repos(
                        project_id=project_id,
                        repositories=affected_repos,
                        superseded_by_version=new_plan_version,
                    )
                else:
                    docs = await self._handoff_docs.generate_for_plan(
                        project_id=project_id,
                        plan_version=new_plan_version,
                        plan=new_plan,
                        requirement=requirement,
                        created_by_agent_id=leader_agent_id,
                        repositories=affected_repos,
                        details=self._handoff_details_from_summary(
                            confirmation_summary, affected_repos
                        ),
                    )
                    handoff_doc_ids = [doc.id for doc in docs]
                _logger.info(
                    "replan: handoff documents for %s regenerated @ v%d",
                    affected_repos,
                    new_plan_version,
                )
            except Exception:
                _logger.warning(
                    "replan: failed to regenerate handoff documents",
                    exc_info=True,
                )

        # --- 4. Notification: record who needs to be interrupted --------------
        # The bridge does not own a CollaborationGateway; the API layer reads
        # ``affected_repos`` from the result and pushes the interrupt notices.
        if superseded_tasks:
            _logger.info(
                "replan: %d task(s) superseded; Team Managers for %s must be "
                "notified of the interruption",
                len(superseded_tasks),
                affected_repos,
            )

        span = trace.get_current_span()
        span.set_attribute(SpanAttributes.PROJECT_ID, str(project_id))
        span.set_attribute("repomesh.replan.new_plan_version", new_plan_version)
        span.set_attribute(
            "repomesh.replan.superseded_task_count", len(superseded_tasks)
        )
        span.set_attribute("repomesh.replan.new_task_count", len(new_tasks))
        span.set_attribute("repomesh.replan.affected_repos", list(affected_repos))

        feedback_summary = _truncate(feedback, limit=200)
        return ReplanResult(
            new_plan_version=new_plan_version,
            superseded_tasks=superseded_tasks,
            new_tasks=new_tasks,
            affected_repos=list(affected_repos),
            feedback_summary=feedback_summary,
            handoff_doc_ids=handoff_doc_ids,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_affected_repos(
        *,
        change_source_repo: str,
        all_repos: list[str],
        graph: DependencyGraphService | None,
    ) -> list[str]:
        """Return the affected repository set for a change source.

        The set is ``{change_source_repo}`` plus every consumer that depends
        on it (reverse dependencies). When no graph is available, the set
        collapses to the change source itself — the minimal safe assumption.
        """

        known = set(all_repos)
        affected: set[str] = set()
        if change_source_repo in known or not known:
            affected.add(change_source_repo)

        if graph is None:
            return sorted(affected)

        for edge in graph.reverse_dependencies(change_source_repo):
            # ``edge.consumer`` depends on the change source → it is affected.
            if not known or edge.consumer in known:
                affected.add(edge.consumer)
        return sorted(affected)

    @staticmethod
    def _local_replan(
        *,
        integration_service: PlanIntegrationService,
        confirmation_summary: ConfirmationSummary,
        requirement: str,
        feedback: str,
        affected_repos: list[str],
    ) -> IntegratedPlan:
        """Re-run plan integration scoped to the affected repositories.

        The feedback is appended to the requirement with a stability
        constraint so the LLM keeps the unaffected repositories stable.
        """

        scoped_requirement = (
            f"{requirement}\n\n"
            f"Replan feedback (scope: {', '.join(affected_repos)}): {feedback}\n"
            "Stability constraint: repositories outside the affected set must "
            "not change their public contracts."
        )
        return integration_service.integrate(scoped_requirement, confirmation_summary)

    @staticmethod
    def _handoff_details_from_summary(
        confirmation_summary: ConfirmationSummary | None,
        affected_repos: list[str],
    ) -> Mapping[str, Mapping[str, Any]] | None:
        """Project per-repository adjustment plans onto the affected repos.

        The confirmation phase already produced one :class:`RepositoryPlan`
        per repository; replan handoff documents reuse that adjustment plan
        so repository owners approve the same proposal that was confirmed.
        """

        if confirmation_summary is None:
            return None
        details: dict[str, Mapping[str, Any]] = {}
        for result in (
            confirmation_summary.required + confirmation_summary.maybe
        ):
            if result.repository not in affected_repos or result.plan is None:
                continue
            details[result.repository] = {
                "summary": result.plan_summary,
                "changed_apis": tuple(result.plan.changed_apis),
                "changed_modules": tuple(result.plan.changed_modules),
                "risk": result.plan.risk,
            }
        return details or None

    async def _supersede_affected_tasks(
        self,
        *,
        project_id: UUID,
        affected_repos: list[str],
        feedback: str,
        idempotency_prefix: str,
    ) -> list[TaskView]:
        """Supersede the active tasks of the affected repositories.

        Tasks are discovered through the catalog (name → repository id) and
        the task store is not directly accessible from the bridge, so this
        helper relies on the caller having already identified the tasks via
        the snapshot/execution plan. In the current framework stage it
        records intent and returns an empty list when no tasks can be
        resolved here; the API layer passes concrete task ids when available.
        """

        if self._task_reader is None or self._superseder is None:
            raise ExecutionPlaneUnavailable(
                "task orchestration plane cannot enumerate project tasks; "
                "refusing a replan that would leave old tasks active"
            )

        profiles = await self._catalog.list()
        affected_repository_ids = {
            profile.id for profile in profiles if profile.name in set(affected_repos)
        }
        reason = f"plan replan requested: {_truncate(feedback, limit=200)}"
        expected_summary = f"SUPERSEDED: {reason}"
        candidates = (
            task
            for task in await self._task_reader.list_project_tasks(project_id)
            if task.repository_id in affected_repository_ids
            and (
                task.status
                in {TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED}
                or (
                    task.status is TaskStatus.SUPERSEDED
                    and task.result_summary == expected_summary
                )
            )
        )
        superseded: list[TaskView] = []
        for task in candidates:
            superseded.append(
                await self._superseder.supersede(
                    SupersedeTaskCommand(task_id=task.id, reason=reason),
                    idempotency_key=f"{idempotency_prefix}:supersede:{task.id}",
                )
            )
        _logger.info(
            "replan: superseded %d task(s) for repos=%s project=%s",
            len(superseded),
            affected_repos,
            project_id,
        )
        return superseded

    async def _start_replan_batch(
        self,
        *,
        new_plan: IntegratedPlan,
        affected_repos: list[str],
        project_id: UUID,
        leader_agent_id: UUID,
        idempotency_prefix: str,
        new_plan_version: int,
    ) -> list[TaskView]:
        """Start a new execution plan batch for the locally re-planned repos."""

        topology = await self._topologies.get_view(project_id)
        if topology is None:
            _logger.warning("replan: topology not found for %s", project_id)
            return []

        org_id = topology.organization_id
        profiles = await self._catalog.list()
        name_to_repo_id = {p.name: p.id for p in profiles}
        repo_id_to_team = {t.repository_id: t for t in topology.repository_teams}

        skipped: list[str] = []
        batches = self._plan_batches(
            new_plan,
            name_to_repo_id=name_to_repo_id,
            teamed_repository_ids=set(repo_id_to_team),
            skipped=skipped,
            verification=self._verification_commands(profiles),
            verification_paths=self._verification_paths(profiles),
        )
        # Keep only batches that touch an affected repository.
        affected_set = set(affected_repos)
        scoped_batches = tuple(
            batch
            for batch in batches
            if any(self._repo_name_for_task(planned, profiles) in affected_set
                   for planned in batch)
        )
        if not scoped_batches or self._plans is None:
            return []

        started = await self._plans.start_plan(
            organization_id=org_id,
            project_id=project_id,
            created_by_agent_id=leader_agent_id,
            batches=scoped_batches,
            idempotency_key=f"{idempotency_prefix}-replan-v{new_plan_version}",
        )
        _logger.info(
            "replan: started new execution plan %s with %d batch(es)",
            started.plan.id,
            len(started.plan.batches),
        )
        return list(started.tasks)

    @staticmethod
    def _repo_name_for_task(
        planned: PlannedRepositoryTaskView,
        profiles: list,
    ) -> str:
        """Resolve a planned task's repository id back to its name."""

        for profile in profiles:
            if profile.id == planned.repository_id:
                return profile.name
        return ""

    # ------------------------------------------------------------------
    # Private helpers (materialize)
    # ------------------------------------------------------------------

    def _plan_batches(
        self,
        plan: IntegratedPlan,
        *,
        name_to_repo_id: dict[str, UUID],
        teamed_repository_ids: set[UUID],
        skipped: list[str],
        verification: dict[str, tuple[str, ...]] | None = None,
        verification_paths: dict[str, tuple[str, ...]] | None = None,
    ) -> tuple[tuple[PlannedRepositoryTaskView, ...], ...]:
        """Translate the batched task DAG into planned repository tasks.

        Repositories the platform cannot execute (unknown to the catalog, or
        without a repository team in the project topology) are logged and
        collected in *skipped* instead of entering the execution plan: an
        execution plan must only contain assignable work.

        ``verification`` is the catalog's answer to defect A-19: how each
        repository is checked. ``TaskNode.tests`` states that the integration
        LLM does not emit verification commands and "the caller supplies them
        when materialising a plan" — the script era's caller did, and the
        console's supplied nothing, so every console round dispatched
        ``testCommands: []`` and the Runner verified nothing under a green tick.

        It is applied *here* rather than on the plan the console reads back,
        and that placement is load-bearing: ``materialize`` writes the plan it
        was handed into the draft's ``task_dag``, and that column is what §8's
        retry fingerprints. A plan mutated before that write fingerprints
        differently on the second attempt, so a retry under a new key would
        stop inheriting the failed attempt's prefix and fork the round — the
        exact failure A-5 exists to prevent. Injecting at the last translation
        keeps the snapshot the LLM's and the execution plan verified.

        A node that states its own tests keeps them, so the request body of
        ``/bridge/materialize`` still outranks the catalog and the catalog only
        fills what nobody stated.
        """

        batches: list[tuple[PlannedRepositoryTaskView, ...]] = []
        for batch_index, batch in enumerate(plan.execution_batches):
            planned: list[PlannedRepositoryTaskView] = []
            for repo_name in batch:
                task_node = self._find_task(plan, repo_name)
                if task_node is None:
                    _logger.warning("No task node for %s, skipping", repo_name)
                    skipped.append(repo_name)
                    continue

                repo_id = name_to_repo_id.get(repo_name)
                if repo_id is None:
                    _logger.warning(
                        "Repository %s not found in catalog, skipping task", repo_name
                    )
                    skipped.append(repo_name)
                    continue

                if repo_id not in teamed_repository_ids:
                    _logger.warning(
                        "Repository %s (id=%s) has no team in topology, skipping",
                        repo_name,
                        repo_id,
                    )
                    skipped.append(repo_name)
                    continue

                planned.append(
                    PlannedRepositoryTaskView(
                        repository_id=repo_id,
                        title=f"Implement changes for {repo_name}",
                        instruction=task_node.instruction
                        or f"Implement changes for {repo_name}",
                        acceptance=self._derive_task_acceptance(task_node),
                        leader_task_id=None,
                        tests=task_node.tests or (verification or {}).get(repo_name, ()),
                        # Added to the Worker's allowed paths downstream, never
                        # substituted for them (defect A-21).
                        test_paths=(verification_paths or {}).get(repo_name, ()),
                    )
                )
                _logger.info("Planned repository task for %s (batch %d)", repo_name, batch_index)
            if planned:
                batches.append(tuple(planned))
        return tuple(batches)

    @staticmethod
    def _verification_commands(profiles) -> dict[str, tuple[str, ...]]:
        """Catalog verification commands by repository name (defect A-19)."""

        return PlanExecutionBridge._by_name(profiles, "test_commands", "test commands")

    @staticmethod
    def _verification_paths(profiles) -> dict[str, tuple[str, ...]]:
        """Catalog test paths by repository name (defect A-21).

        Where a repository keeps the files its verification commands read. They
        are added to a Worker's allowed paths so the agent may write the test
        its own command will look for — the contradiction that voided a live
        run on ``changed_path_denied: tests/test_discount.py``.
        """

        return PlanExecutionBridge._by_name(profiles, "test_paths", "test paths")

    @staticmethod
    def _by_name(profiles, attribute: str, label: str) -> dict[str, tuple[str, ...]]:
        """One catalog list per repository name, refusing to guess on collisions.

        ``repositories.name`` carries no unique constraint — two owners' ``api``
        are both legitimate rows — so two rows of one name that disagree resolve
        to nothing rather than to whichever came last. For commands, running
        another repository's is worse than running none: it fails as
        *verification*, the one signal delivery trusts. For paths it is worse
        still — it would hand a Worker write permission somewhere on the
        strength of a name collision.
        """

        found: dict[str, tuple[str, ...]] = {}
        ambiguous: set[str] = set()
        for profile in profiles:
            declared = tuple(getattr(profile, attribute, ()) or ())
            if not declared:
                continue
            if profile.name in found and found[profile.name] != declared:
                ambiguous.add(profile.name)
            found.setdefault(profile.name, declared)
        for name in ambiguous:
            _logger.warning(
                "repository name %s has conflicting catalog %s; tasks for it will carry none",
                name,
                label,
            )
            found.pop(name, None)
        return found

    @staticmethod
    def _find_task(plan: IntegratedPlan, repo_name: str) -> TaskNode | None:
        for t in plan.task_dag:
            if t.repository == repo_name:
                return t
        return None

    @staticmethod
    def _derive_acceptance(plan: IntegratedPlan) -> tuple[str, ...]:
        """Derive acceptance criteria from the plan."""

        criteria: list[str] = []
        for t in plan.task_dag:
            if t.instruction:
                criteria.append(f"{t.repository}: {t.instruction}")
        if not criteria:
            criteria.append("All changes compile and tests pass.")
        return tuple(criteria)

    @staticmethod
    def _derive_task_acceptance(task_node: TaskNode) -> tuple[str, ...]:
        """Derive acceptance criteria for a single task."""

        criteria = ["Code compiles without errors.", "Existing tests pass."]
        if task_node.instruction:
            criteria.append(task_node.instruction)
        return tuple(criteria)
