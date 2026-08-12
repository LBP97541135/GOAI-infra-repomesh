"""Public result contracts for cross-module change orchestration."""

from dataclasses import dataclass, field
from uuid import UUID

from repomesh.modules.specification.contracts import SpecificationView
from repomesh.modules.task_orchestration.contracts import ExecutionPlanView, TaskView


class ExecutionPlaneUnavailable(RuntimeError):
    """The task orchestration plane is not configured; materialization refused.

    A published refusal, not an internal one: every caller of ``materialize``
    translates it into the same 503, and it lives here so they can name it
    without importing this module's application layer.
    """


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
    """Tasks and repository handoffs affected by a new plan version."""

    new_plan_version: int
    superseded_tasks: list[TaskView] = field(default_factory=list)
    new_tasks: list[TaskView] = field(default_factory=list)
    affected_repos: list[str] = field(default_factory=list)
    feedback_summary: str = ""
    handoff_doc_ids: list[UUID] = field(default_factory=list)


__all__ = [
    "ExecutionPlaneUnavailable",
    "MaterializationResult",
    "ReplanResult",
    "StartedExecutionPlan",
]
