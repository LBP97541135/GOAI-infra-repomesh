"""Public result contracts for cross-module change orchestration."""

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from repomesh.modules.repository_intelligence.contracts import PlanDiff
from repomesh.modules.specification.contracts import SpecificationView
from repomesh.modules.task_orchestration.contracts import ExecutionPlanView, TaskView

ReplanMode = Literal["preview", "commit"]
"""How a replan executes:

- ``preview`` — compute impact analysis and the graph diff only; no
  supersede, no new plan start, no snapshot write (zero side effects).
- ``commit`` — the full replan (impact analysis, supersede, re-integration,
  new immutable snapshot, handoff regeneration).
"""


class ExecutionPlaneUnavailable(RuntimeError):
    """The task orchestration plane is not configured; the workflow refused side effects."""


@dataclass(frozen=True, slots=True)
class StartedExecutionPlan:
    """Execution plan and initially released tasks."""

    plan: ExecutionPlanView
    tasks: tuple[TaskView, ...] = ()


@dataclass(frozen=True, slots=True)
class MaterializationResult:
    """Specifications, tasks and handoff records created from a change plan."""

    engineering_spec: SpecificationView
    contract_specs: list[SpecificationView] = field(default_factory=list)
    tasks: list[TaskView] = field(default_factory=list)
    skipped_repos: list[str] = field(default_factory=list)
    plan_id: UUID | None = None
    handoff_doc_ids: list[UUID] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ReplanResult:
    """Tasks and repository handoffs affected by a new plan version.

    ``diff`` carries the plan-layer graph diff between the previous snapshot
    and the re-planned version. It is populated on commit when a new plan was
    produced, and on preview (which performs no side effects). Cancel-only
    replans have no new graph to diff against, so ``diff`` is None.
    """

    new_plan_version: int
    superseded_tasks: list[TaskView] = field(default_factory=list)
    new_tasks: list[TaskView] = field(default_factory=list)
    affected_repos: list[str] = field(default_factory=list)
    feedback_summary: str = ""
    handoff_doc_ids: list[UUID] = field(default_factory=list)
    plan_id: UUID | None = None
    diff: PlanDiff | None = None


__all__ = [
    "ExecutionPlaneUnavailable",
    "MaterializationResult",
    "ReplanMode",
    "ReplanResult",
    "StartedExecutionPlan",
]
