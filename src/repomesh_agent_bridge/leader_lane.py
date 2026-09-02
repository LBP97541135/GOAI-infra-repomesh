"""The leader lane's decisions, as functions of their inputs and nothing else.

Three jobs live here and none of them does any I/O, which is the point: what a
notice means, what a coordination session is told, and whether what came back is
a decision are all questions with one right answer given the same bytes, and
they are much easier to hold to that standard when nothing around them can fail.
The session adapter next door does the spawning; the port does the talking.

**Nothing rendered here names a place on disk (adjudication D-8).** A Repository
Leader never receives a repository workspace — not even a read-only one — so its
coordination session is given text and structured facts and nothing else. That
is a property of the frozen assignment package first, which has no field for a
path, and of :func:`render_fact_package` second, which builds only from that
package. ``tests/agent_bridge/test_leader_lane.py`` holds the rendering against
the machine's own session directories to keep it that way.

**The leader's session does not write its own provenance.** The model is asked
for the decision and for nothing else; ``schemaVersion`` and ``provenance`` are
attached here, from the session thread the driver actually reported. A document
that arrives carrying either one is refused rather than overwritten — a model
that names its own session thread is making the one claim in the whole contract
it is not in a position to make (frozen invariant 5), and quietly replacing it
would leave the refusal untested and the claim unearned.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from uuid import UUID

from .contracts import (
    PLAN_DECISION_SCHEMA_VERSION,
    REVIEW_DECISION_SCHEMA_VERSION,
    DecisionProvenance,
    LeaderDocumentInvalid,
    RepositoryAssignmentPackage,
    RepositoryPlanDecision,
    RepositoryReviewDecision,
)

__all__ = [
    "NOTICE_ACTIONS",
    "PLAN_NOTICE",
    "REVIEW_NOTICE",
    "LeaderNotice",
    "assemble_plan_decision",
    "assemble_review_decision",
    "extract_json_object",
    "parse_leader_assignment_notice",
    "parse_leader_notice",
    "render_fact_package",
    "render_plan_instructions",
    "render_review_instructions",
]

_ASSIGNMENT_ROUTE = re.compile(
    r"/api/v1/agent-actions/leader/assignments/"
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)

PLAN_NOTICE = "plan"
REVIEW_NOTICE = "review"
NOTICE_ACTIONS: tuple[str, ...] = (PLAN_NOTICE, REVIEW_NOTICE)
"""The two things RepoMesh wakes a Repository Leader up for.

They are spelled as the last segment of the route each notice carries, because
that is exactly what tells them apart on the wire: the two messages differ in
their prose and in one path segment, and only the second is a contract.
"""

_JSON_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n?```", re.DOTALL)


@dataclass(frozen=True, slots=True)
class LeaderNotice:
    """A notice RepoMesh addressed to one Repository Leader."""

    task_id: UUID
    action: str
    """``plan`` | ``review`` — one of :data:`NOTICE_ACTIONS`."""


def parse_leader_notice(body: str) -> LeaderNotice | None:
    """What a message tells this leader to do, or ``None`` for anything else.

    RepoMesh sends a leader exactly two messages that are not conversation, and
    both are a *second* message beside something else: the planning notice sits
    beside the parked assignment, and the review notice beside a round whose
    workers have all finished (``task_orchestration/application.py``). Each
    carries the *route*, because neither a leader task nor a finished round says
    on its own that this team decides leader-side or where the decision surface
    is. This reads those two messages and nothing else.

    It keys on the routes rather than on the prose. The URL is the contract and
    the sentences around it are not: a reworded notice must not stop waking the
    lane, and a message that happens to mention a leader task without naming a
    decision endpoint must not start one. Two refusals follow from the same
    reasoning, and both are declines rather than guesses — a body naming several
    different leader tasks, and a body naming both endpoints, because there is
    no honest way to pick one of either.

    Recognising a notice is a *wake-up* and never an authorisation. Whether the
    task exists, whether this member is its assignee and what phase it is really
    in are all answered by RepoMesh when the lane goes and asks; nothing decided
    here is trusted by anything that follows.
    """

    task_ids: set[UUID] = set()
    actions: set[str] = set()
    for match in _ASSIGNMENT_ROUTE.finditer(body):
        task_ids.add(UUID(match.group(1)))
        if (action := _action_after(body, match.end())) is not None:
            actions.add(action)
    if len(task_ids) != 1 or len(actions) != 1:
        return None
    return LeaderNotice(task_ids.pop(), actions.pop())


def parse_leader_assignment_notice(body: str) -> UUID | None:
    """The leader task a "submit your repository plan" notice is about, or None.

    The planning half of :func:`parse_leader_notice`, kept as its own name
    because "is this the notice that starts a plan" is a question worth asking
    on its own — and because reading a review notice as a planning one would be
    the worst available answer: a leader would plan a round it was asked to
    judge.
    """

    notice = parse_leader_notice(body)
    return notice.task_id if notice is not None and notice.action == PLAN_NOTICE else None


def _action_after(body: str, end: int) -> str | None:
    """Which decision endpoint a matched route names, if it names one at all.

    A notice quotes both the read and the write, and only the write says what
    the leader is being woken for; the bare ``GET`` line is deliberately not an
    action.
    """

    tail = body[end:]
    for action in NOTICE_ACTIONS:
        if tail.startswith(f"/{action}"):
            return action
    return None


def render_fact_package(package: RepositoryAssignmentPackage) -> str:
    """The assignment package as the text a coordination session is given.

    Everything the server offers, in the three groups the contract separates it
    into and labelled as such: facts the leader plans, bounds it is clamped to,
    and hints it may ignore. The labels are load-bearing — a leader that could
    not tell the decomposition hint from the safety envelope would treat a
    suggestion as a requirement, which is precisely the "server writes the
    leader's product" failure the whole surface exists to avoid.

    Ids are included because the plan has to name a worker by id, and paths are
    included because the plan has to declare ``allowedPaths`` under the roots.
    Both are repository-relative contract data. There is no absolute path here
    and there is nowhere for one to come from: the package has no such field.
    """

    lines = [
        "# Repository assignment",
        "",
        f"Leader task: {package.leader_task_id}",
        f"Phase: {package.phase}",
        f"Repository: {package.repository_id}",
        "",
        "## The task (facts, not suggestions)",
        "",
        f"Title: {package.repository_task.title}",
        "",
        package.repository_task.instruction,
        "",
        f"Acceptance: {package.repository_task.acceptance or '(none stated)'}",
        "",
        "## Worker roster — every assignee must be one of these",
        "",
    ]
    for entry in package.worker_roster:
        paths = ", ".join(entry.responsibility_paths) or "(none declared)"
        lines.append(f"- {entry.worker_name} — id {entry.worker_agent_id} — owns {paths}")
    envelope = package.safety_envelope
    lines += [
        "",
        "## Safety envelope — hard bounds your plan is validated against",
        "",
        "Every allowedPaths entry of every worker task must fall under one of:",
        *(f"  - {root}" for root in envelope.allowed_path_roots),
        "",
        "Every worker task's tests must include all of:",
        *(f"  - {command}" for command in envelope.test_commands),
    ]
    if envelope.test_paths:
        lines += ["", "Tests live under: " + ", ".join(envelope.test_paths)]
    advisory = package.advisory_context
    lines += ["", "## Advisory context — hints. Not authoritative. Ignore any of it.", ""]
    if advisory.discovery_evidence:
        lines += [f"Discovery: {advisory.discovery_evidence}", ""]
    if advisory.dependency_edges:
        lines.append("Cross-repository dependencies (downstream depends on upstream):")
        lines += [
            f"  - {edge.downstream_repository_id} depends on {edge.upstream_repository_id}"
            for edge in advisory.dependency_edges
        ]
        lines.append("")
    if advisory.decomposition_hint:
        lines += [f"Decomposition hint: {advisory.decomposition_hint}", ""]
    if evidence := package.review_evidence:
        lines += [
            f"## Worker evidence — review round {evidence.review_revision}",
            "",
            "This is the whole record your verdict may be based on.",
            "",
        ]
        for entry in evidence.worker_evidence:
            lines += [
                f"### Worker task {entry.worker_task_id}",
                f"- status: {entry.status}",
                f"- run: {entry.run_id or '(none)'}",
                f"- commit: {entry.commit_sha or '(none)'}",
                f"- changed files: {', '.join(entry.changed_files) or '(none)'}",
            ]
            if entry.diff_stat:
                lines.append(f"- diffstat: {entry.diff_stat}")
            for result in entry.test_results:
                lines.append(f"- test `{result.command}` exited {result.exit_code}")
            if entry.summary:
                lines.append(f"- worker's own summary: {entry.summary}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


_OUTPUT_RULES = (
    "Answer with exactly one JSON object and nothing else — no prose before or after, "
    "no explanation. A fenced ```json block is fine. Do not include a schemaVersion or "
    "provenance field; those are attached for you. Do not attempt to read or write any "
    "file: you have no repository here, by design."
)


def render_plan_instructions(package: RepositoryAssignmentPackage) -> str:
    """What to ask a coordination session for, in the planning phase."""

    return "\n\n".join(
        (
            render_fact_package(package),
            "# Your job",
            "You are this repository's Repository Leader. Produce the engineering plan: an "
            "Engineering Spec, a task DAG, and one worker task per DAG node. You are not "
            "writing code and you have no repository checkout — you are deciding what the "
            "workers will do.",
            "Requirements: every DAG node is named by exactly one worker task and vice "
            "versa; edges point from a prerequisite to what depends on it; the graph is "
            "acyclic; every assignee is on the roster above; every allowedPaths entry is "
            "under an envelope root; every worker task's tests include every envelope test "
            "command. Every worker task must explicitly include databaseChange; use "
            "declared=true, required=false when the task has no database impact.",
            "# Output shape",
            json.dumps(
                {
                    "engineeringSpec": {"summary": "<one line>", "markdown": "<the spec>"},
                    "taskDag": {
                        "nodes": [{"nodeId": "<id>"}],
                        "edges": [{"from": "<id>", "to": "<id>"}],
                    },
                    "workerTasks": [
                        {
                            "nodeId": "<id>",
                            "assigneeWorkerAgentId": "<roster uuid>",
                            "title": "<title>",
                            "instruction": "<what to do>",
                            "allowedPaths": ["<under an envelope root>"],
                            "tests": ["<every envelope test command>"],
                            "databaseChange": {
                                "declared": True,
                                "required": False,
                                "changeKinds": [],
                                "affectedTables": [],
                                "migrationRequired": False,
                                "backfillRequired": False,
                                "requiredChecks": [],
                            },
                        }
                    ],
                },
                indent=2,
            ),
            _OUTPUT_RULES,
        )
    )


def render_review_instructions(package: RepositoryAssignmentPackage) -> str:
    """What to ask a coordination session for, in the review_due phase."""

    return "\n\n".join(
        (
            render_fact_package(package),
            "# Your job",
            "You are this repository's Repository Leader. Review the evidence above and "
            "return a verdict. You have no repository checkout: the evidence is the whole "
            "record, and a verdict must rest on it rather than on anything you would like "
            "to go and look at.",
            "Verdicts: `approve` when the work meets the acceptance criteria; "
            "`request_rework` when it does not and can be fixed — every problem needs a "
            "finding carrying a reworkInstruction, which becomes a new worker task; "
            "`escalate` when this cannot be resolved at repository level. Every finding "
            "must name a workerTaskId from the evidence above.",
            "# Output shape",
            json.dumps(
                {
                    "verdict": "approve | request_rework | escalate",
                    "summary": "<the roll-up a human reads>",
                    "findings": [
                        {
                            "workerTaskId": "<from the evidence>",
                            "note": "<what you found>",
                            "reworkInstruction": "<only on findings that demand rework>",
                        }
                    ],
                },
                indent=2,
            ),
            _OUTPUT_RULES,
        )
    )


def extract_json_object(text: str) -> object:
    """The one JSON object in a session's answer, or a refusal.

    A fenced block is unwrapped because the fence is markdown packaging rather
    than part of the document — the same kind of decoding as reading the body of
    an HTTP response. Everything past that is strict: two fences are ambiguous
    rather than "probably the first one", and text that is not JSON is refused
    as it stands. Nothing here repairs, truncates or re-quotes anything; a
    document that needs repairing is a document the model did not produce.
    """

    fenced = _JSON_FENCE.findall(text)
    if len(fenced) > 1:
        raise LeaderDocumentInvalid(
            "the session's answer carries more than one fenced block, so which one is the "
            "decision is ambiguous"
        )
    candidate = (fenced[0] if fenced else text).strip()
    if not candidate:
        raise LeaderDocumentInvalid("the session's answer is empty")
    try:
        return json.loads(candidate)
    except ValueError as unreadable:
        raise LeaderDocumentInvalid(
            f"the session's answer is not JSON: {unreadable}"
        ) from unreadable


def assemble_plan_decision(
    raw: object, provenance: DecisionProvenance
) -> RepositoryPlanDecision:
    """Hold a session's plan against the freeze, with true provenance attached."""

    return RepositoryPlanDecision.from_wire(
        _with_provenance(raw, provenance, PLAN_DECISION_SCHEMA_VERSION, "plan")
    )


def assemble_review_decision(
    raw: object, provenance: DecisionProvenance
) -> RepositoryReviewDecision:
    """Hold a session's verdict against the freeze, with true provenance attached."""

    return RepositoryReviewDecision.from_wire(
        _with_provenance(raw, provenance, REVIEW_DECISION_SCHEMA_VERSION, "review")
    )


def _with_provenance(
    raw: object, provenance: DecisionProvenance, schema_version: str, what: str
) -> dict[str, object]:
    """Attach the two fields the model may not write, refusing one that did.

    ``schemaVersion`` is this build's to state and ``provenance`` names the
    session thread the driver actually reported. A model that supplied either
    would be asserting something it cannot know, and the provenance in
    particular is the one claim a server-authored plan could not make honestly —
    so a document carrying it is refused, not corrected. Overwriting would make
    the field meaningless and the refusal invisible.
    """

    if not isinstance(raw, dict):
        raise LeaderDocumentInvalid(f"the session's {what} is not a JSON object")
    for reserved in ("schemaVersion", "provenance"):
        if reserved in raw:
            raise LeaderDocumentInvalid(
                f"the session's {what} carries its own {reserved}, which it is not in a "
                "position to state"
            )
    return {"schemaVersion": schema_version, **raw, "provenance": provenance.to_wire()}
