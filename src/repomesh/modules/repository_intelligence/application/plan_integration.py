"""Plan integration service.

The Project Manager (总 Manager) collects structured RepositoryPlans from
all confirmed Team Managers and integrates them into a single coherent
project-level plan consisting of:

- An Engineering Spec (project-level description)
- A list of Contracts (cross-repository interface agreements)
- A Task DAG (execution order with dependencies)

MVP implementation: pure LLM inference with structured output.
Future: LLM + call_graph for deterministic dependency inference.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Protocol

from opentelemetry import trace

from repomesh.modules.repository_intelligence.application.confirmation import (
    ConfirmationResult,
    ConfirmationSummary,
    RepositoryPlan,
)
from repomesh.modules.repository_intelligence.application.dependency_graph import (
    DependencyGraphService,
    GraphEdge,
)
from repomesh.telemetry import traced

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output data structures
# ---------------------------------------------------------------------------
# Defined in ``contracts.integration`` so cross-module consumers (change
# orchestration) import them through the module boundary; re-exported here
# for backward compatibility with in-module imports.

from repomesh.modules.repository_intelligence.contracts import (  # noqa: E402, I001
    ContractSpec,
    GraphEdge as PlanGraphEdge,
    GraphNode,
    IntegratedPlan,
    PlanGraph,
    TaskNode,
    normalize_plan,
    plan_to_graph,
)

__all__ = ["ContractSpec", "IntegratedPlan", "PlanIntegrationService", "TaskNode"]


# ---------------------------------------------------------------------------
# LLM protocol
# ---------------------------------------------------------------------------


class LLMClient(Protocol):
    """Minimal protocol for an LLM chat client."""

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str: ...


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _format_plans(plans: list[tuple[str, str, RepositoryPlan]]) -> str:
    """Format repository plans for the LLM prompt."""

    lines: list[str] = []
    for repo, status, plan in plans:
        lines.append(f"### {repo} ({status})")
        lines.append(f"  summary: {plan.summary if hasattr(plan, 'summary') else ''}")
        if plan.changed_apis:
            lines.append(f"  changed_apis: {', '.join(plan.changed_apis)}")
        if plan.changed_modules:
            lines.append(f"  changed_modules: {', '.join(plan.changed_modules)}")
        if plan.depends_on:
            lines.append(f"  depends_on: {', '.join(plan.depends_on)}")
        if plan.impacts:
            lines.append(f"  impacts: {', '.join(plan.impacts)}")
        lines.append(f"  risk: {plan.risk}")
        lines.append("")
    return "\n".join(lines)


def _build_integration_prompt(
    requirement: str,
    results: list[ConfirmationResult],
) -> list[dict[str, str]]:
    """Build the prompt for the Plan Integration LLM call."""

    # Collect structured plans
    plan_lines: list[str] = []
    for r in results:
        if r.status == "EXCLUDED" or r.plan is None:
            continue
        plan_lines.append(f"### {r.repository} ({r.status})")
        plan_lines.append(f"  summary: {r.plan_summary}")
        if r.plan.changed_apis:
            plan_lines.append(f"  changed_apis: {', '.join(r.plan.changed_apis)}")
        if r.plan.changed_modules:
            plan_lines.append(f"  changed_modules: {', '.join(r.plan.changed_modules)}")
        if r.plan.depends_on:
            plan_lines.append(f"  depends_on: {', '.join(r.plan.depends_on)}")
        if r.plan.impacts:
            plan_lines.append(f"  impacts: {', '.join(r.plan.impacts)}")
        plan_lines.append(f"  risk: {r.plan.risk}")
        plan_lines.append("")

    plans_text = "\n".join(plan_lines)
    repo_names = [r.repository for r in results if r.status != "EXCLUDED"]

    system = (
        "You are the Project Manager (总 Manager) responsible for integrating "
        "individual repository change plans into a coherent project-level plan.\n\n"
        "You will receive:\n"
        "- The original requirement\n"
        "- Structured change plans from each repository's Team Manager\n\n"
        "You must produce:\n"
        "1. **Engineering Spec**: A clear project-level description of the complete "
        "change, including the overall approach, key decisions, and acceptance criteria.\n"
        "2. **Contracts**: For each pair of repositories where one repo's API changes "
        "affect another repo, define an interface agreement. Only include pairs where "
        "there is a genuine dependency, not just because they are in the same project.\n"
        "3. **Task DAG**: Define the execution order. Repositories with no dependency "
        "on each other can be executed in parallel. A repo that depends on another's "
        "API change must wait for the producer to finish first.\n\n"
        "Return ONLY a JSON object (no markdown fences, no extra text):\n"
        "{\n"
        '  "engineering_spec": "detailed project-level plan",\n'
        '  "contracts": [\n'
        '    {"producer": "repo A", "consumer": "repo B", '
        '"interface": "API name", "agreement": "what is agreed"}\n'
        "  ],\n"
        '  "task_dag": [\n'
        '    {"repository": "repo name", "instruction": "what to change", '
        '"depends_on": ["repos that must finish first"], '
        '"parallelizable_with": ["repos that can run in parallel"]}\n'
        "  ]\n"
        "}"
    )

    user = (
        f"## Requirement\n\n{requirement}\n\n"
        f"## Repository Plans\n\n{plans_text}\n\n"
        f"## Confirmed Repositories\n\n{', '.join(repo_names)}\n\n"
        f"## Task\n\n"
        f"Integrate these plans into a complete project-level plan. "
        f"Identify cross-repository contracts and define execution order."
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _build_graph_assisted_prompt(
    requirement: str,
    results: list[ConfirmationResult],
    edges: list[GraphEdge],
    batches: list[list[str]],
    cyclic_repos: list[str],
) -> list[dict[str, str]]:
    """Build prompt for graph-assisted integration.

    The graph provides deterministic edges and batches.
    The LLM only writes: Engineering Spec, Contract details, Task instructions.
    """

    plan_lines: list[str] = []
    for r in results:
        if r.status == "EXCLUDED" or r.plan is None:
            continue
        plan_lines.append(f"### {r.repository} ({r.status})")
        plan_lines.append(f"  summary: {r.plan_summary}")
        if r.plan.changed_apis:
            plan_lines.append(f"  changed_apis: {', '.join(r.plan.changed_apis)}")
        if r.plan.changed_modules:
            plan_lines.append(f"  changed_modules: {', '.join(r.plan.changed_modules)}")
        plan_lines.append(f"  risk: {r.plan.risk}")
        plan_lines.append("")

    plans_text = "\n".join(plan_lines)

    edge_lines: list[str] = []
    for e in edges:
        tag = "confirmed" if e.confidence == "confirmed" else "possible"
        edge_lines.append(f"  {e.consumer} → {e.producer} ({tag})")
    edges_text = "\n".join(edge_lines) if edge_lines else "  (no edges found)"

    batch_lines: list[str] = []
    for i, batch in enumerate(batches, 1):
        batch_lines.append(f"  Batch {i}: {', '.join(batch)}")
    batches_text = "\n".join(batch_lines)

    cycle_warning = ""
    if cyclic_repos:
        cycle_warning = (
            f"\n**WARNING**: Circular dependency detected among: "
            f"{', '.join(cyclic_repos)}. They are placed in the same batch "
            f"and may need coordinated changes.\n"
        )

    system = (
        "You are the Project Manager integrating repository "
        "change plans into a project-level plan.\n\n"
        "The dependency graph has ALREADY determined:\n"
        "- Which repositories depend on which (edges below)\n"
        "- The execution order (batches below)\n\n"
        "You ONLY need to write:\n"
        "1. **Engineering Spec**: project-level description of the change.\n"
        "2. **Contracts**: For each edge that genuinely needs a contract "
        "(based on the specific APIs being changed), write the interface "
        "agreement. You may skip edges where the dependency is unrelated.\n"
        "3. **Task DAG instructions**: For each repo, write a concrete "
        "instruction describing what to change.\n\n"
        "Return ONLY a JSON object (no markdown fences):\n"
        "{\n"
        '  "engineering_spec": "detailed project-level plan",\n'
        '  "contracts": [\n'
        '    {"producer": "repo A", "consumer": "repo B", '
        '"interface": "API name", "agreement": "what is agreed"}\n'
        "  ],\n"
        '  "task_dag": [\n'
        '    {"repository": "repo name", "instruction": "what to change", '
        '"depends_on": [], "parallelizable_with": []}\n'
        "  ]\n"
        "}"
    )

    user = (
        f"## Requirement\n\n{requirement}\n\n"
        f"## Repository Plans\n\n{plans_text}\n\n"
        f"## Graph-Derived Dependencies (authoritative)\n\n{edges_text}\n\n"
        f"## Execution Batches (authoritative)\n\n{batches_text}\n"
        f"{cycle_warning}\n"
        f"## Task\n\n"
        f"Write the Engineering Spec, Contract details for edges that need "
        f"them, and per-repo instructions."
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _extract_json_object(text: str) -> str:
    """Extract the outermost ``{...}`` block from *text*."""

    fence = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
    m = fence.search(text)
    if m:
        text = m.group(1).strip()

    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError("Unterminated JSON object")


def _parse_integrated_plan(raw: str, repo_names: list[str]) -> IntegratedPlan:
    """Parse the LLM response into an :class:`IntegratedPlan`."""

    try:
        json_text = _extract_json_object(raw)
        data = json.loads(json_text)
    except (json.JSONDecodeError, ValueError):
        _logger.warning("Failed to parse integrated plan, using fallback")
        # A fallback DAG is a silent quality downgrade; make it visible on the
        # planning.integration span instead of only in the log stream.
        trace.get_current_span().set_attribute("repomesh.plan_fallback", True)
        return _fallback_plan(repo_names)

    # Parse contracts (filter out any that reference repos not in the confirmed list)
    valid_set = set(repo_names)
    contracts: list[ContractSpec] = []
    for c in data.get("contracts", []):
        producer = c.get("producer", "")
        consumer = c.get("consumer", "")
        if not producer or not consumer:
            continue
        # Deterministic filter: only keep contracts between confirmed repos
        if producer not in valid_set or consumer not in valid_set:
            _logger.info(
                "Dropping contract %s -> %s (not in confirmed repos)", producer, consumer,
            )
            continue
        contracts.append(
            ContractSpec(
                producer=producer,
                consumer=consumer,
                interface=c.get("interface", ""),
                agreement=c.get("agreement", ""),
            )
        )

    # Parse task DAG
    task_dag: list[TaskNode] = []
    repo_set = set(repo_names)
    for t in data.get("task_dag", []):
        repo = t.get("repository", "")
        if not repo:
            continue
        deps = tuple(d for d in t.get("depends_on", []) if d in repo_set)
        parallel = tuple(p for p in t.get("parallelizable_with", []) if p in repo_set)
        task_dag.append(
            TaskNode(
                repository=repo,
                instruction=t.get("instruction", ""),
                depends_on=deps,
                parallelizable_with=parallel,
            )
        )

    # Ensure all repos are in the DAG
    existing = {t.repository for t in task_dag}
    for repo in repo_names:
        if repo not in existing:
            task_dag.append(TaskNode(repository=repo, instruction=""))

    # Compute execution batches via topological sort
    batches = _topological_batches(task_dag)

    return IntegratedPlan(
        engineering_spec=data.get("engineering_spec", ""),
        contracts=contracts,
        task_dag=task_dag,
        execution_batches=batches,
    )


def _topological_batches(dag: list[TaskNode]) -> list[list[str]]:
    """Group tasks into execution batches using Kahn's algorithm.

    Returns a list of batches where each batch can run in parallel,
    and batches must execute in order.
    """

    # Build dependency map
    deps: dict[str, set[str]] = {}
    for t in dag:
        deps[t.repository] = set(d for d in t.depends_on if d in {n.repository for n in dag})

    batches: list[list[str]] = []
    remaining = set(deps.keys())

    while remaining:
        # Find all repos whose deps are fully satisfied
        ready = {r for r in remaining if not deps[r]}
        if not ready:
            # Circular dependency — break by picking lowest-name
            _logger.warning("Circular dependency detected in DAG, breaking cycle")
            ready = {min(remaining)}

        batches.append(sorted(ready))
        remaining -= ready
        for r in remaining:
            deps[r] -= ready

    return batches


def _fallback_plan(repo_names: list[str]) -> IntegratedPlan:
    """Generate a minimal fallback plan when LLM parsing fails."""

    return IntegratedPlan(
        engineering_spec="Failed to generate engineering spec.",
        contracts=[],
        task_dag=[TaskNode(repository=r, instruction="") for r in repo_names],
        execution_batches=[repo_names] if repo_names else [],
    )


# ---------------------------------------------------------------------------
# Graph merge (single source of truth)
# ---------------------------------------------------------------------------


def _build_plan_graph(
    *,
    repo_names: list[str],
    scan_edges: list[GraphEdge],
    llm_plan: IntegratedPlan,
) -> PlanGraph:
    """Merge the world layer and LLM semantics into the plan-layer graph.

    - Scan ``confirmed`` edges become plan ``confirmed`` edges (source=scan).
    - Scan ``possible`` edges become plan ``candidate`` edges (discovery
      only — they never participate in topology).
    - LLM contracts attach ``interface``/``agreement`` to existing edges and
      promote possible edges to confirmed; brand-new pairs become confirmed
      llm edges (contract ⇒ serialization must hold).
    - LLM ``depends_on`` not already present become confirmed llm edges, so
      LLM-discovered dependencies enter the execution batches.

    ``plan_version`` is a placeholder (1); the snapshot row owns the real
    version.
    """

    nodes = {repo: GraphNode(repository=repo) for repo in repo_names}
    for task in llm_plan.task_dag:
        node = nodes.get(task.repository)
        if node is not None and task.instruction:
            nodes[task.repository] = node.model_copy(
                update={"instruction": task.instruction}
            )

    edges: dict[tuple[str, str], PlanGraphEdge] = {}
    for scan_edge in scan_edges:
        edges[(scan_edge.producer, scan_edge.consumer)] = PlanGraphEdge(
            from_=scan_edge.producer,
            to=scan_edge.consumer,
            status="confirmed" if scan_edge.confidence == "confirmed" else "candidate",
            source="scan",
        )
    for contract in llm_plan.contracts:
        key = (contract.producer, contract.consumer)
        existing = edges.get(key)
        if existing is not None:
            edges[key] = existing.model_copy(
                update={
                    "status": "confirmed",
                    "interface": contract.interface or existing.interface,
                    "agreement": contract.agreement or existing.agreement,
                }
            )
        else:
            edges[key] = PlanGraphEdge(
                from_=contract.producer,
                to=contract.consumer,
                status="confirmed",
                source="llm",
                interface=contract.interface,
                agreement=contract.agreement,
            )
    for task in llm_plan.task_dag:
        for dep in task.depends_on:
            key = (dep, task.repository)
            if key not in edges:
                edges[key] = PlanGraphEdge(
                    from_=dep, to=task.repository, status="confirmed", source="llm"
                )

    return PlanGraph(
        plan_version=1,
        nodes=list(nodes.values()),
        edges=list(edges.values()),
    )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class PlanIntegrationService:
    """Integrates per-repository plans into a project-level plan.

    Usage::

        service = PlanIntegrationService(llm_client)
        plan = service.integrate(requirement, confirmation_summary)
        print(plan.engineering_spec)
        print(plan.execution_batches)
    """

    def __init__(
        self,
        llm_client: LLMClient,
        graph: DependencyGraphService | None = None,
    ) -> None:
        self._llm = llm_client
        self._graph = graph

    @traced("planning.integration")
    def integrate(
        self,
        requirement: str,
        summary: ConfirmationSummary,
    ) -> IntegratedPlan:
        """Integrate confirmed repository plans into a complete project plan.

        When a DependencyGraphService is available, the graph provides
        deterministic edges and topological ordering — the LLM only writes
        semantic content. When no graph is available, falls back to pure LLM.
        """

        all_results = summary.required + summary.maybe
        if not all_results:
            return IntegratedPlan(
                engineering_spec="No repositories confirmed.",
                contracts=[],
                task_dag=[],
                execution_batches=[],
            )

        repo_names = [r.repository for r in all_results if r.status != "EXCLUDED"]

        if self._graph is not None:
            plan = self._integrate_with_graph(requirement, all_results, repo_names)
        else:
            messages = _build_integration_prompt(requirement, all_results)
            raw = self._llm.chat(messages, temperature=0.1)
            llm_plan = _parse_integrated_plan(raw, repo_names)
            # Single source of truth even without a scan graph: backfill the
            # plan-layer graph from the LLM output and normalise to its
            # projections (contract ⇒ serialization stays guaranteed).
            plan = normalize_plan(llm_plan, plan_to_graph(llm_plan))

        span = trace.get_current_span()
        span.set_attribute("repomesh.integration.task_count", len(plan.task_dag))
        span.set_attribute("repomesh.integration.contract_count", len(plan.contracts))
        span.set_attribute("repomesh.integration.batch_count", len(plan.execution_batches))
        return plan

    # ------------------------------------------------------------------
    # Graph-assisted integration
    # ------------------------------------------------------------------

    def _integrate_with_graph(
        self,
        requirement: str,
        results: list[ConfirmationResult],
        repo_names: list[str],
    ) -> IntegratedPlan:
        """Use graph for structure, LLM for semantic content."""

        edges = self._graph.edges_in(repo_names)  # type: ignore[union-attr]
        topo = self._graph.topological_batches(repo_names)  # type: ignore[union-attr]
        batches = topo.batches

        messages = _build_graph_assisted_prompt(
            requirement, results, edges, batches, topo.cyclic_repos
        )
        raw = self._llm.chat(messages, temperature=0.1)
        llm_plan = _parse_integrated_plan(raw, repo_names)

        # The plan-layer graph becomes the single source of truth: scan
        # edges (possible ⇒ candidate) plus LLM semantics written back
        # (contracts → edge metadata, new depends_on → confirmed llm edges).
        # Execution batches therefore contain LLM-discovered dependencies and
        # never contain possible edges.
        graph = _build_plan_graph(
            repo_names=repo_names,
            scan_edges=edges,
            llm_plan=llm_plan,
        )
        return normalize_plan(llm_plan, graph)
