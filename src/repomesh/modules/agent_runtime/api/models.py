from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from repomesh.modules.agent_runtime.application.readiness import (
    ReadinessReportKind,
    ReportExternalMemberReadinessCommand,
)
from repomesh.modules.agent_runtime.contracts import ExternalMemberRole

MockScenarioName = Literal[
    "success",
    "test_failed",
    "failed",
    "timeout",
    "cancelled",
    "interrupted",
    "question_required",
]


class CodingRunCreate(BaseModel):
    task_id: UUID
    repository_url: HttpUrl
    instruction: str = Field(min_length=1, max_length=20_000)
    base_revision: str = Field(default="main", min_length=1, max_length=200)
    scenario: MockScenarioName = "success"


class RunEventView(BaseModel):
    type: str
    message: str


class CodingRunView(BaseModel):
    run_id: UUID
    status: str
    adapter: str
    summary: str
    changed_files: tuple[str, ...]
    test_command: str | None
    events: tuple[RunEventView, ...]


class WorkerTaskStartCreate(BaseModel):
    task_id: UUID
    worker_agent_id: UUID
    adapter_id: str = Field(min_length=1, max_length=100)
    base_revision: str = Field(default="main", min_length=1, max_length=200)
    task_features: frozenset[str] = frozenset()


class ExternalWorkerProvisionRequest(BaseModel):
    """A body with no fields, and that is the point.

    The whole request is the path id: which principal becomes external. Every
    other fact about the resulting worker is owned by somebody who is not the
    caller — the resource name by the agent directory, the runtime, model and
    skills by the projection the ordinary project path already uses, and
    ``containerManaged`` by the controller's own answer — so there is nothing
    here to fill in.

    ``extra="forbid"`` makes a body that states one of those a 422 rather than
    a silent drop. Ignoring it would be worse than refusing: an operator who
    wrote ``{"containerManaged": true}`` and read 200 would believe they had
    asked for something, and what they would have got is the opposite.

    Declared optional at the route, so both an absent body and ``{}`` are
    accepted; there is no difference between them to preserve.
    """

    model_config = ConfigDict(extra="forbid")


EXTERNAL_MEMBER_READINESS_SCHEMA = "repomesh.agent-bridge.readiness.v1"
"""Wire version of a readiness report.

A new document family rather than a widening of any frozen one: readiness is a
fact about a *process*, which no binding or enrollment document was ever able to
carry, and the versions that are frozen must keep meaning what they meant.

It lives beside the model that pins it rather than in ``contracts`` with the
binding versions, because this string has exactly one reader — the body model
below. A Bridge holds its own copy on the other side of the wire, which is the
point of a schema field at all.
"""


class ExternalMemberReadinessReport(BaseModel):
    """A member's report about its own process, as the v1 document declares it.

    ``camelCase`` on the wire and ``snake_case`` in Python, spelled once per
    field with ``alias``; ``populate_by_name`` is deliberately not set, and
    ``extra="forbid"`` refuses a field this family does not declare rather than
    dropping it — both for ``leader_action_models``' reasons.

    The ``schema`` field is a ``Literal``, so a report from a later family is a
    422 rather than a lease. That is the one place a framework rejection is the
    right answer on this route: every other refusal here is a verdict about a
    member RepoMesh understood, while a foreign document is a request this
    endpoint cannot read at all.

    ``role``, ``leaderLane``, ``governedLane`` and ``workspaceRoot`` are the
    reporter's claims about what it started as, and the use case behind this
    checks every one of them against the directory and this deployment's own
    settings. They are in the body rather than derived because the point of the
    report is the disagreement it makes visible.
    """

    model_config = ConfigDict(extra="forbid")

    readiness_schema: Annotated[
        Literal[EXTERNAL_MEMBER_READINESS_SCHEMA], Field(alias="schema")
    ]
    instance_id: Annotated[UUID, Field(alias="instanceId")]
    kind: ReadinessReportKind
    role: ExternalMemberRole
    leader_lane: Annotated[bool, Field(alias="leaderLane")]
    governed_lane: Annotated[bool, Field(alias="governedLane")]
    workspace_root: Annotated[str | None, Field(alias="workspaceRoot")]

    def to_command(self, member_agent_id: UUID) -> ReportExternalMemberReadinessCommand:
        """The member is the path's, never the body's.

        The credential behind the request has already been checked against the
        path id, so taking the subject from the body as well would be a second,
        weaker answer to "who is reporting".
        """

        return ReportExternalMemberReadinessCommand(
            member_agent_id=member_agent_id,
            instance_id=self.instance_id,
            kind=self.kind,
            role=self.role,
            leader_lane=self.leader_lane,
            governed_lane=self.governed_lane,
            workspace_root=self.workspace_root,
        )


class WorkerTaskStartView(BaseModel):
    task_id: UUID
    run_id: UUID
    status: str
    workspace_id: str
    workspace_path: str
    base_sha: str
