"""Request bodies of the two leader-actions writes, as the frozen schemas declare them.

Shape lives here; judgement lives in the use cases. The split follows what the
two failures actually are: a body that is not the frozen document at all is a
malformed request and cannot be told apart from any other one, while a
well-formed plan that breaks an invariant is a *verdict* and carries the frozen
code that names which invariant.

That leaves malformed bodies answering FastAPI's 422 rather than the contract's
structured error, and the frozen error matrix has no row for 422 — a gap worth
naming rather than papering over. The alternative would be to map a missing
``taskDag`` onto one of the 409 plan codes, which would tell a leader its DAG is
invalid when it did not send one, and would put a code under a status the frozen
matrix does not give it. The conservative reading is that the matrix enumerates
the verdicts this surface renders, not every way an HTTP request can be wrong.

``extra="forbid"`` everywhere, because every frozen schema here sets
``additionalProperties: false``: a field the contract does not declare is
refused rather than dropped, so a Bridge that invented one learns it did.

``camelCase`` on the wire and ``snake_case`` in Python, spelled once per field
with ``alias``. ``populate_by_name`` is deliberately *not* set — the wire name
is the only accepted spelling, so a body using Python names is refused like any
other undeclared field.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from repomesh.modules.task_orchestration.contracts import (
    LEADER_PROVENANCE_SOURCE,
    PLAN_DECISION_SCHEMA_VERSION,
    REVIEW_DECISION_SCHEMA_VERSION,
    LeaderProvenanceView,
    LeaderReviewFinding,
    LeaderReviewVerdict,
    LeaderWorkerTaskDraft,
    RepositoryPlanDecision,
    RepositoryReviewDecision,
)


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProvenanceBody(_Frozen):
    #: Deliberately a plain string rather than a ``Literal`` of the one legal
    #: value: provenance that is present but wrong is a *verdict*
    #: (``plan_invalid_provenance``, 409), and typing it as a literal would
    #: make the framework answer 422 for the case the contract froze a code for.
    source: str = Field(min_length=1, max_length=200)
    session_thread_id: Annotated[str, Field(alias="sessionThreadId", min_length=1, max_length=200)]
    turn_id: Annotated[
        str | None, Field(alias="turnId", min_length=1, max_length=200)
    ] = None

    def to_view(self) -> LeaderProvenanceView:
        return LeaderProvenanceView(
            source=self.source,
            session_thread_id=self.session_thread_id,
            turn_id=self.turn_id,
        )


class EngineeringSpecBody(_Frozen):
    summary: str = Field(min_length=1, max_length=500)
    markdown: str = Field(min_length=1)


class DagNodeBody(_Frozen):
    node_id: Annotated[str, Field(alias="nodeId", min_length=1, max_length=100)]


class DagEdgeBody(_Frozen):
    # ``from`` is a Python keyword, which is exactly what aliases are for.
    from_node: Annotated[str, Field(alias="from", min_length=1, max_length=100)]
    to_node: Annotated[str, Field(alias="to", min_length=1, max_length=100)]


class TaskDagBody(_Frozen):
    nodes: list[DagNodeBody] = Field(min_length=1)
    edges: list[DagEdgeBody]


class WorkerTaskDraftBody(_Frozen):
    node_id: Annotated[str, Field(alias="nodeId", min_length=1, max_length=100)]
    assignee_worker_agent_id: Annotated[UUID, Field(alias="assigneeWorkerAgentId")]
    title: str = Field(min_length=1, max_length=200)
    instruction: str = Field(min_length=1)
    allowed_paths: Annotated[list[str], Field(alias="allowedPaths", min_length=1)]
    tests: list[str]


class PlanDecisionBody(_Frozen):
    """``repository-plan-decision.v1``.

    The leader task id is deliberately absent: it lives in the URL path and is
    the idempotency key, and a body that repeated it would create a second
    place for the two to disagree.
    """

    schema_version: Annotated[
        Literal[PLAN_DECISION_SCHEMA_VERSION], Field(alias="schemaVersion")
    ]
    engineering_spec: Annotated[EngineeringSpecBody, Field(alias="engineeringSpec")]
    task_dag: Annotated[TaskDagBody, Field(alias="taskDag")]
    worker_tasks: Annotated[list[WorkerTaskDraftBody], Field(alias="workerTasks", min_length=1)]
    provenance: ProvenanceBody

    def to_decision(self) -> RepositoryPlanDecision:
        return RepositoryPlanDecision(
            engineering_spec_summary=self.engineering_spec.summary,
            engineering_spec_markdown=self.engineering_spec.markdown,
            nodes=tuple(node.node_id for node in self.task_dag.nodes),
            edges=tuple((edge.from_node, edge.to_node) for edge in self.task_dag.edges),
            worker_tasks=tuple(
                LeaderWorkerTaskDraft(
                    node_id=draft.node_id,
                    assignee_worker_agent_id=draft.assignee_worker_agent_id,
                    title=draft.title,
                    instruction=draft.instruction,
                    allowed_paths=tuple(draft.allowed_paths),
                    tests=tuple(draft.tests),
                )
                for draft in self.worker_tasks
            ),
            provenance=self.provenance.to_view(),
            raw=canonical(self),
        )


class ReviewFindingBody(_Frozen):
    worker_task_id: Annotated[UUID, Field(alias="workerTaskId")]
    note: str = Field(min_length=1, max_length=2000)
    rework_instruction: Annotated[
        str | None, Field(alias="reworkInstruction", min_length=1)
    ] = None


class ReviewDecisionBody(_Frozen):
    """``repository-review-decision.v1``."""

    schema_version: Annotated[
        Literal[REVIEW_DECISION_SCHEMA_VERSION], Field(alias="schemaVersion")
    ]
    verdict: LeaderReviewVerdict
    summary: str = Field(min_length=1, max_length=4000)
    findings: list[ReviewFindingBody]
    provenance: ProvenanceBody

    def to_decision(self) -> RepositoryReviewDecision:
        return RepositoryReviewDecision(
            verdict=self.verdict,
            summary=self.summary,
            findings=tuple(
                LeaderReviewFinding(
                    worker_task_id=finding.worker_task_id,
                    note=finding.note,
                    rework_instruction=finding.rework_instruction,
                )
                for finding in self.findings
            ),
            provenance=self.provenance.to_view(),
            raw=canonical(self),
        )


def canonical(body: BaseModel) -> dict[str, object]:
    """The submitted document, in its wire spelling, with absent fields absent.

    This is what gets fingerprinted and persisted as the leader's product, so
    two properties matter. It must be *stable*: the same submission has to
    produce the same document, which ``by_alias`` and ``exclude_none`` give —
    an optional field the leader omitted stays omitted rather than becoming an
    explicit null that a later identical submission might spell differently.
    And it must be the *leader's* document rather than a rewrite of it: every
    value here is the submitted value, only the object key order is normalised,
    which JSON does not carry meaning in.
    """

    return body.model_dump(mode="json", by_alias=True, exclude_none=True)


__all__ = [
    "LEADER_PROVENANCE_SOURCE",
    "PlanDecisionBody",
    "ReviewDecisionBody",
    "canonical",
]
