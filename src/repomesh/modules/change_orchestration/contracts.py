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
    """The task orchestration plane is not configured; materialization refused.

    A published refusal, not an internal one: every caller of ``materialize``
    translates it into the same 503, and it lives here so they can name it
    without importing this module's application layer.
    """


class RoundNotRecorded(RuntimeError):
    """A plan was started but its snapshot could not be told about it.

    ``execution_plan_id`` is not decoration. It is the only place the round is
    written down: the read model keys a delivery's ``plan_version``,
    ``created_at`` and ``updated_at`` off it, and §8's "this issue has already
    been materialised" 409 is nothing but ``current_draft`` finding the column
    still NULL. A materialize that started a plan and then failed to record it
    returns 200 over a draft that still looks untouched, so the next attempt —
    a reloaded panel, a second operator — starts a *second* execution plan for
    the same round.

    That failure mode is not hypothetical. The snapshot block used to swallow
    every exception into a log line, and it has already hidden one bug that way
    (``dict()`` over a slotted dataclass raised ``TypeError`` for every plan
    that carried a contract; see the regression in
    ``tests/test_plan_execution_bridge.py``). Leniency there is only defensible
    while nothing has been started; once a plan exists, silence is the bug.

    Raised instead of swallowed because the round is now repairable: the failed
    materialization receipt lends its prefix to the next attempt, whose
    ``start_plan`` recognises the plan it already wrote and returns it without
    reassigning anything, so the retry gets a second run at the link. The tasks
    that were created are *not* undone — this says "not recorded", not "not
    started".
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
    "RoundNotRecorded",
    "StartedExecutionPlan",
]
