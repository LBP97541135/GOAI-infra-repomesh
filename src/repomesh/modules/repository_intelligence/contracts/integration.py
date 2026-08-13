"""Integration-phase contracts shared across module boundaries.

These types travel across the ``repository_intelligence`` →
``change_orchestration`` boundary, so they live in this module's
``contracts`` package (AGENTS.md: cross-module imports may target only
``repomesh.modules.<producer>.contracts``).

The unified dependency graph (:mod:`contracts.graph`) is the single source
of truth: execution batches, contracts and task dependencies are all
*projections* of its confirmed edges. ``normalize_plan`` rebuilds an
:class:`IntegratedPlan` so its operational fields always equal the graph
projections.
"""

from __future__ import annotations

from dataclasses import dataclass

from repomesh.modules.repository_intelligence.contracts.graph import (
    GraphEdge,
    GraphNode,
    PlanGraph,
    project_batches,
)


@dataclass(frozen=True, slots=True)
class ContractSpec:
    """A cross-repository interface agreement.

    The *producer* repo changes an API; the *consumer* repo depends on it.
    """

    producer: str
    consumer: str
    interface: str
    agreement: str


@dataclass(frozen=True, slots=True)
class TaskNode:
    """A single task in the execution DAG."""

    repository: str
    instruction: str
    depends_on: tuple[str, ...] = ()
    parallelizable_with: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    """Verification commands the Worker must run before reporting this task.

    They travel down to the Task Specification the Worker executes under and
    become the Runner's ``test_commands``.  The integration LLM does not emit
    them yet, so the caller supplies them when materialising a plan.
    """


@dataclass(frozen=True, slots=True)
class IntegratedPlan:
    """The complete integrated project plan."""

    engineering_spec: str
    contracts: list[ContractSpec]
    task_dag: list[TaskNode]
    execution_batches: list[list[str]]  # topologically sorted batches
    graph: PlanGraph | None = None
    """Plan-layer dependency graph (single source of truth).

    Set by :meth:`PlanIntegrationService.integrate`. Plans constructed
    without a graph (manual bridge) are backfilled at materialise time by
    :func:`plan_to_graph` + :func:`normalize_plan`.
    """

    def to_dict(self) -> dict:
        """Serialise to a plain dict for JSON output."""

        return {
            "engineering_spec": self.engineering_spec,
            "contracts": [
                {
                    "producer": c.producer,
                    "consumer": c.consumer,
                    "interface": c.interface,
                    "agreement": c.agreement,
                }
                for c in self.contracts
            ],
            "task_dag": [
                {
                    "repository": t.repository,
                    "instruction": t.instruction,
                    "depends_on": list(t.depends_on),
                    "parallelizable_with": list(t.parallelizable_with),
                    "tests": list(t.tests),
                }
                for t in self.task_dag
            ],
            "execution_batches": [list(b) for b in self.execution_batches],
        }


def tm_order_edges(
    batches: list[list[str]], node_names: set[str]
) -> list[GraphEdge]:
    """Derive ``source="tm"`` edges from an explicitly approved batch order.

    The approved execution order is itself a planning decision: consecutive
    batches imply serialization, so an edge is returned from each repo in an
    earlier batch to each repo in the next batch. Callers merge these into
    their edge set; the standard conflict rule applies (facts win), so this
    helper never produces an edge that would contradict an existing one.
    """

    order_edges: list[GraphEdge] = []
    if len(batches) > 1:
        # Batches and batches[1:] are slices of the same list — the second is
        # always one shorter, so strict=True cannot hold here by design.
        for earlier_batch, later_batch in zip(batches, batches[1:]):  # noqa: B905
            for earlier in earlier_batch:
                for later in later_batch:
                    if (
                        earlier == later
                        or earlier not in node_names
                        or later not in node_names
                    ):
                        continue
                    order_edges.append(
                        GraphEdge(
                            from_=earlier, to=later, status="confirmed", source="tm"
                        )
                    )
    return order_edges


def integration_method(graph: PlanGraph) -> str:
    """Classify how the plan-layer graph was produced.

    ``graph_assisted`` when scan-derived edges participate (``source="scan"``);
    ``llm_only`` otherwise (LLM backfill or manual bridge, sources in
    ``{"llm", "tm"}``).
    """

    if any(e.source == "scan" for e in graph.edges):
        return "graph_assisted"
    return "llm_only"


def plan_to_graph(plan: IntegratedPlan) -> PlanGraph:
    """Backfill a plan-layer graph from plan fields alone.

    Used when a plan arrives without a graph (manual bridge / legacy
    callers). Edges are derived from:

    1. **Task DAG dependencies** — authoritative dependency facts, entered
       as ``confirmed`` llm edges.
    2. **Contracts** — contract ⇒ serialization must hold, so contract pairs
       become ``confirmed`` llm edges (upgrading an existing dependency edge
       with ``interface``/``agreement`` instead of dropping it as a
       duplicate).
    3. **Explicit batch ordering** — the approved execution order is itself
       a planning decision (``source="tm"``): consecutive batches imply
       serialization, so a confirmed edge is added from each repo in an
       earlier batch to each repo in the next batch. Dependency facts win on
       conflict — an edge is only added when the pair is not already
       connected in either direction, so a contradictory manual order can
       never override a real dependency.

    ``plan_version`` is a placeholder (1); the snapshot row owns the real
    version.
    """

    nodes = [
        GraphNode(
            repository=t.repository,
            instruction=t.instruction or None,
            tests=list(t.tests),
        )
        for t in plan.task_dag
    ]
    # Dict-merge so contracts upgrade existing dependency edges with their
    # interface/agreement instead of being dropped as duplicates.
    edges: dict[tuple[str, str], GraphEdge] = {}
    for task in plan.task_dag:
        for dep in task.depends_on:
            edges.setdefault(
                (dep, task.repository),
                GraphEdge(from_=dep, to=task.repository, status="confirmed", source="llm"),
            )
    for contract in plan.contracts:
        key = (contract.producer, contract.consumer)
        existing = edges.get(key)
        if existing is not None:
            edges[key] = existing.model_copy(
                update={
                    "interface": contract.interface or existing.interface,
                    "agreement": contract.agreement or existing.agreement,
                }
            )
        else:
            edges[key] = GraphEdge(
                from_=contract.producer,
                to=contract.consumer,
                status="confirmed",
                source="llm",
                interface=contract.interface,
                agreement=contract.agreement,
            )

    # Preserve an explicitly approved (manual-bridge) batch order.
    node_names = {n.repository for n in nodes}
    for order_edge in tm_order_edges(plan.execution_batches, node_names):
        key = (order_edge.from_, order_edge.to)
        if key not in edges and (order_edge.to, order_edge.from_) not in edges:
            edges[key] = order_edge  # dependency facts win on conflict

    return PlanGraph(plan_version=1, nodes=nodes, edges=list(edges.values()))


def normalize_plan(plan: IntegratedPlan, graph: PlanGraph) -> IntegratedPlan:
    """Rebuild *plan* so its operational fields equal the graph projections.

    Idempotent for plans whose fields already match their graph. The graph is
    the single source of truth: execution batches come from confirmed edges
    only, contracts from edge metadata, and ``depends_on`` from the graph
    (LLM-discovered dependencies are edges and therefore included).

    LLM-only metadata that the graph does not model yet (``instruction``,
    ``parallelizable_with``, ``tests``) is carried over from the original
    plan, keyed by repository.
    """

    llm_by_repo = {t.repository: t for t in plan.task_dag}

    task_dag: list[TaskNode] = []
    for node_view in graph.task_dag:
        llm_node = llm_by_repo.get(node_view.repository)
        task_dag.append(
            TaskNode(
                repository=node_view.repository,
                instruction=node_view.instruction
                or (llm_node.instruction if llm_node is not None else ""),
                depends_on=tuple(node_view.depends_on),
                parallelizable_with=(
                    llm_node.parallelizable_with if llm_node is not None else ()
                ),
                tests=llm_node.tests if llm_node is not None else (),
            )
        )

    contracts = [
        ContractSpec(
            producer=contract_view.producer,
            consumer=contract_view.consumer,
            interface=contract_view.interface,
            agreement=contract_view.agreement or "",
        )
        for contract_view in graph.contracts
    ]

    return IntegratedPlan(
        engineering_spec=plan.engineering_spec,
        contracts=contracts,
        task_dag=task_dag,
        execution_batches=[list(b) for b in project_batches(graph.nodes, graph.edges)],
        graph=graph,
    )
