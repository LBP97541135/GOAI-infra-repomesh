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
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any, Protocol
from uuid import UUID

from opentelemetry import trace

from repomesh.modules.project.contracts import (
    ProjectCheckpoint,
    ProjectCheckpointGateway,
    ProjectTopologyReader,
    TopologyAwareCheckpointFallback,
)
from repomesh.modules.repository_intelligence.contracts import (
    IntegratedPlan,
    PlanDiff,
    PlanGraph,
    TaskNode,
    diff_plan_graphs,
    integration_method,
    normalize_plan,
    plan_to_graph,
)
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
    ReplanMode,
    ReplanResult,
)
from .ports import (
    ExecutionPlanStarter,
    HandoffDocGenerator,
    PlanSnapshotReader,
    PlanSnapshotWriter,
    ProjectTaskReader,
    SpecificationCreator,
    TaskSupersederGateway,
)

_logger = logging.getLogger(__name__)


class RepositoryCatalogReader(Protocol):
    async def list(self) -> Sequence[Any]: ...


class WorldDependencyEdge(Protocol):
    consumer: str


class WorldDependencyGraph(Protocol):
    def reverse_dependencies(self, repo_name: str) -> Sequence[WorldDependencyEdge]: ...


class ReplanIntegrator(Protocol):
    def integrate(self, requirement: str, confirmation_summary: Any) -> IntegratedPlan: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate(text: str, limit: int = 60) -> str:
    """Return *text* truncated to *limit* chars with an ellipsis."""

    text = text.strip()
    return text[:limit] + "…" if len(text) > limit else text


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
        catalog: RepositoryCatalogReader,
        snapshot_store: PlanSnapshotWriter | None = None,
        snapshot_reader: PlanSnapshotReader | None = None,
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
        self._snapshot_reader = snapshot_reader
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
        """

        # --- 0. Fail closed before any side effect ----------------------------
        if self._plans is None:
            raise ExecutionPlaneUnavailable(
                "task orchestration plane is not configured (no assignment "
                "gateway — is the Matrix messenger set up?); refusing to "
                "materialize a plan whose tasks cannot be assigned"
            )

        # --- 0b. Unify on the plan-layer graph --------------------------------
        # The graph is the single source of truth: execution batches, contracts
        # and task dependencies are projections of its confirmed edges. Plans
        # arriving without a graph (manual bridge / legacy callers) are
        # backfilled from their fields. Everything below therefore operates on
        # graph-consistent values.
        graph = plan.graph or plan_to_graph(plan)
        plan = normalize_plan(plan, graph)

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

        # --- 5. Save plan snapshot (if store configured) ---------------------
        plan_version: int | None = None
        if self._snapshots is not None:
            try:
                plan_version = await self._snapshots.next_version(project_id)
                # The row owns the real plan_version; align the graph with it.
                snapshot_graph = graph.model_copy(update={"plan_version": plan_version})
                saved = await self._snapshots.save(
                    project_id=project_id,
                    plan_version=plan_version,
                    engineering_spec=plan.engineering_spec or requirement,
                    contracts=[
                        asdict(c) for c in plan.contracts
                    ],
                    task_dag=[
                        asdict(t) for t in plan.task_dag
                    ],
                    execution_batches=[list(b) for b in plan.execution_batches],
                    graph_edges=[
                        e.model_dump(by_alias=True) for e in snapshot_graph.edges
                    ],
                    created_by_agent_id=leader_agent_id,
                    execution_plan_id=plan_id,
                    requirement_text=requirement,
                    integration_method=integration_method(snapshot_graph),
                )
                plan_snapshot_id = getattr(saved, "id", None)
                if plan_snapshot_id is not None:
                    _logger.info(
                        "Saved materialized plan snapshot %s for project %s",
                        plan_snapshot_id,
                        project_id,
                    )
            except Exception:
                _logger.exception("Failed to save plan snapshot")
                raise

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
                _logger.exception("Failed to generate handoff documents")
                raise

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
        integration_service: ReplanIntegrator | None = None,
        confirmation_summary: Any | None = None,
        graph: WorldDependencyGraph | None = None,
        mode: ReplanMode = "commit",
    ) -> ReplanResult:
        """Partially replan after a BLOCKED task reports an upstream change.

        Flow:

        1. **Impact analysis** — the affected set is derived from the
           *plan-layer* graph of the latest immutable snapshot (its confirmed
           edges), so interruption is exact: candidate scan edges never widen
           the set. When no snapshot exists yet, the world-layer dependency
           graph is used as a fallback; without either the set collapses to
           the change source itself. The affected set always contains the
           change source.
        2. **Local re-integration** (optional) — when an *integration_service*
           is supplied, re-run plan integration scoped to the affected repos
           with a stability constraint appended to the requirement. This is a
           best-effort step; the framework still works without it.
        3. **Execution** — ``commit`` supersedes the old tasks of the affected
           repositories so the Runner can interrupt them, starts a new
           execution plan for the new batch (when a plan starter is
           configured), mints the new plan version from the snapshot store
           (``next_version``) and persists a new immutable snapshot carrying
           the full plan-layer graph. ``preview`` performs **no side
           effects**: it reports the same change footprint plus the graph diff
           so callers can approve the change before committing.
        4. **Notification** — record which Team Managers must be interrupted.
           The actual collaboration push is performed by the API layer, which
           owns the :class:`CollaborationGateway`; the bridge only reports the
           affected repos so callers know who to notify.

        Args:
            project_id: Target project UUID.
            leader_agent_id: ORG_LEADER agent UUID authorising the replan.
            feedback: BLOCKED task feedback explaining the upstream change.
            change_source_repo: Repository whose change triggered the replan.
            plan_version: Current plan version being superseded. Only used as
                the fallback when no snapshot store is configured.
            requirement: Original requirement text (feedback is appended).
            idempotency_prefix: Unique prefix for idempotency keys.
            all_repos: Every repository in the current plan (for scoping).
            integration_service: Optional service for local re-integration.
            confirmation_summary: Optional prior confirmation to reuse.
            graph: World-layer dependency graph, used only when no plan-layer
                snapshot is available for impact analysis.
            mode: ``commit`` executes the full replan (default, preserves
                pre-PR-4 behaviour); ``preview`` computes impact analysis and
                the graph diff without any side effect.

        Returns:
            :class:`ReplanResult` with superseded/new tasks, affected repos,
            the persisted snapshot id (``plan_id``) and the graph diff
            (``diff``). Preview returns empty task lists and ``plan_id=None``.

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

        # --- 0b. Load the latest plan-layer snapshot (best effort) ------------
        plan_graph: PlanGraph | None = None
        if self._snapshot_reader is not None:
            try:
                plan_graph = await self._snapshot_reader.get_latest_graph(
                    project_id
                )
            except Exception:
                _logger.warning(
                    "replan: failed to load latest plan snapshot for %s, "
                    "falling back to world-layer impact analysis",
                    project_id,
                    exc_info=True,
                )

        # --- 1. Impact analysis ------------------------------------------------
        affected_repos = self._compute_affected_repos(
            change_source_repo=change_source_repo,
            all_repos=all_repos,
            plan_graph=plan_graph,
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

        # --- 3. Version minting (read-only) -----------------------------------
        # The snapshot store is the single source of truth for plan versions
        # when configured; the caller-supplied version is only a fallback for
        # deployments without snapshot persistence. ``next_version`` only
        # reads the latest row, so preview may report the version a commit
        # would mint without any side effect.
        if self._snapshots is not None:
            new_plan_version = await self._snapshots.next_version(project_id)
        else:
            new_plan_version = plan_version + 1

        if mode == "preview":
            # --- preview: zero side effects ------------------------------------
            # No supersede, no plan start, no snapshot write, no handoff
            # regeneration — only the change footprint and the graph diff the
            # commit would apply, so callers can approve before committing.
            superseded_tasks: list[TaskView] = []
            new_tasks: list[TaskView] = []
            handoff_doc_ids: list[UUID] = []
            plan_id: UUID | None = None
            preview_graph: PlanGraph | None = None
            if new_plan is not None and new_plan.graph is not None:
                preview_graph = new_plan.graph.model_copy(
                    update={"plan_version": new_plan_version}
                )
            diff = diff_plan_graphs(plan_graph, preview_graph)
            _logger.info(
                "replan: preview for %s at v%d (no side effects)",
                project_id,
                new_plan_version,
            )
        else:
            # --- commit: execute the replan ------------------------------------
            superseded_tasks = await self._supersede_affected_tasks(
                project_id=project_id,
                affected_repos=affected_repos,
                feedback=feedback,
                idempotency_prefix=idempotency_prefix,
            )

            # --- 3b. Start a new execution plan for the affected repos --------
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

            # --- 3b2. Persist the re-planned version as an immutable snapshot --
            # A replan with a new plan mints a fresh snapshot carrying the full
            # plan-layer graph (same invariant as materialise: read graph ≡
            # projection columns). Cancel-only replans leave the last snapshot
            # in place — there is no new graph to record.
            plan_id: UUID | None = None
            diff: PlanDiff | None = None
            if new_plan is not None and self._snapshots is not None:
                try:
                    snapshot_graph = new_plan.graph.model_copy(
                        update={"plan_version": new_plan_version}
                    )
                    saved = await self._snapshots.save(
                        project_id=project_id,
                        plan_version=new_plan_version,
                        engineering_spec=new_plan.engineering_spec,
                        contracts=[asdict(c) for c in new_plan.contracts],
                        task_dag=[asdict(t) for t in new_plan.task_dag],
                        execution_batches=[
                            list(b) for b in new_plan.execution_batches
                        ],
                        graph_edges=[
                            e.model_dump(by_alias=True)
                            for e in snapshot_graph.edges
                        ],
                        created_by_agent_id=leader_agent_id,
                        requirement_text=requirement,
                        integration_method=integration_method(snapshot_graph),
                    )
                    plan_id = saved.id
                    diff = diff_plan_graphs(plan_graph, snapshot_graph)
                except Exception:
                    _logger.warning(
                        "replan: failed to save plan snapshot v%d for %s",
                        new_plan_version,
                        project_id,
                        exc_info=True,
                    )

            # --- 3c. Regenerate handoff documents for the affected repos ------
            # A replan produces a new plan version: the previous documents of
            # the affected repositories are superseded and fresh PENDING
            # documents are generated from the new plan so repository owners
            # re-approve the adjusted proposal. When no new plan was produced
            # (cancel-only fallback), the stale documents are still superseded.
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
            plan_id=plan_id,
            diff=diff,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_affected_repos(
        *,
        change_source_repo: str,
        all_repos: list[str],
        plan_graph: PlanGraph | None = None,
        graph: WorldDependencyGraph | None = None,
    ) -> list[str]:
        """Return the affected repository set for a change source.

        The set is ``{change_source_repo}`` plus every consumer that depends
        on it. The plan-layer snapshot's **confirmed** edges are
        authoritative: candidate edges never widen the interruption set. When
        no plan-layer graph is available, the world-layer dependency graph is
        used as a fallback; without either the set collapses to the change
        source itself — the minimal safe assumption.
        """

        known = set(all_repos)
        affected: set[str] = set()
        if change_source_repo in known or not known:
            affected.add(change_source_repo)

        if plan_graph is not None:
            for edge in plan_graph.edges:
                # ``edge.from_`` is the producer; its consumers (``edge.to``)
                # are affected when the producer changes.
                if (
                    edge.status == "confirmed"
                    and edge.from_ == change_source_repo
                    and (not known or edge.to in known)
                ):
                    affected.add(edge.to)
            return sorted(affected)

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
        integration_service: ReplanIntegrator,
        confirmation_summary: Any,
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
        confirmation_summary: Any | None,
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
    ) -> tuple[tuple[PlannedRepositoryTaskView, ...], ...]:
        """Translate the batched task DAG into planned repository tasks.

        Repositories the platform cannot execute (unknown to the catalog, or
        without a repository team in the project topology) are logged and
        collected in *skipped* instead of entering the execution plan: an
        execution plan must only contain assignable work.
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
                        tests=task_node.tests,
                    )
                )
                _logger.info("Planned repository task for %s (batch %d)", repo_name, batch_index)
            if planned:
                batches.append(tuple(planned))
        return tuple(batches)

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
