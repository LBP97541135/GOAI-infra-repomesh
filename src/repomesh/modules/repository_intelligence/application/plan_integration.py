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
from dataclasses import dataclass
from typing import Protocol

from repomesh.modules.repository_intelligence.application.confirmation import (
    ConfirmationResult,
    ConfirmationSummary,
    RepositoryPlan,
)

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output data structures
# ---------------------------------------------------------------------------


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


@dataclass(frozen=True, slots=True)
class IntegratedPlan:
    """The complete integrated project plan."""

    engineering_spec: str
    contracts: list[ContractSpec]
    task_dag: list[TaskNode]
    execution_batches: list[list[str]]  # topologically sorted batches

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
                }
                for t in self.task_dag
            ],
            "execution_batches": [list(b) for b in self.execution_batches],
        }


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
        return _fallback_plan(repo_names)

    # Parse contracts
    contracts: list[ContractSpec] = []
    for c in data.get("contracts", []):
        producer = c.get("producer", "")
        consumer = c.get("consumer", "")
        if not producer or not consumer:
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

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def integrate(
        self,
        requirement: str,
        summary: ConfirmationSummary,
    ) -> IntegratedPlan:
        """Integrate confirmed repository plans into a complete project plan.

        Args:
            requirement: The original feature requirement text.
            summary: The confirmation summary from the Team Manager phase.

        Returns:
            An :class:`IntegratedPlan` with engineering spec, contracts,
            and task DAG.
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
        messages = _build_integration_prompt(requirement, all_results)
        raw = self._llm.chat(messages, temperature=0.1)

        return _parse_integrated_plan(raw, repo_names)
