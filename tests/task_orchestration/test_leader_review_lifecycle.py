"""The second half of leader mode: a finished round becomes a review (PR 7).

``test_leader_mode_assignment`` pins the first half — the batch stops after the
leader task and parks. This pins what happens once the leader has planned and
its workers have finished: the platform does **not** settle the leader task for
itself. It snapshots the evidence, opens ``review_due``, tells the leader, and
waits.

That negative is the whole slice, and it is what the delivery gate rests on.
The gate has always required a SUCCEEDED leader task; in leader mode the only
thing that produces one is an approved review, so "nothing is delivered before
the leader approves" is inherited rather than implemented — and the test that
proves it inherits correctly is here.

The harness is ``test_plan_execution``'s, for the reason its neighbour gives:
reproducing a topology, a directory, an assigner and a task store would be a
second harness free to drift from the one every other batch-assignment test
uses. What this file adds is the two use cases under test and a real
``InMemoryLeaderAssignmentStore`` behind them.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from repomesh.modules.task_orchestration.application import (
    SubmitRepositoryPlan,
    SubmitRepositoryReview,
)
from repomesh.modules.task_orchestration.contracts import (
    LEADER_PROVENANCE_SOURCE,
    LeaderActionErrorCode,
    LeaderActionRefused,
    LeaderAssignmentPhase,
    LeaderProvenanceView,
    LeaderReviewFinding,
    LeaderReviewVerdict,
    LeaderWorkerTaskDraft,
    ReportTaskCommand,
    RepositoryPlanDecision,
    RepositoryReviewDecision,
    TaskOrigin,
    TaskStatus,
    TaskView,
)
from task_orchestration.test_plan_execution import Environment

TESTS = ("uv run pytest -q tests/pricing",)
TEST_PATHS = ("tests/pricing/",)

RUNNER_DOCUMENT = (
    '{"commitSha": "3f2a9d1b7c4e8f0a6b5d2c1e9f8a7b6c5d4e3f2a", '
    '"runId": "11111111-1111-4111-8111-111111111111", '
    '"changedFiles": ["src/pricing/quote.py"], '
    '"summary": "Added the currency field.", '
    '"testResults": [{"command": "uv run pytest -q tests/pricing", "exitCode": 0}]}'
)


class Reporter:
    """``TaskReportGateway`` over the harness's own task store."""

    def __init__(self, environment: Environment) -> None:
        self._environment = environment
        self.commands: list[ReportTaskCommand] = []

    async def report(self, command: ReportTaskCommand, *, idempotency_key: str) -> TaskView:
        self.commands.append(command)
        task = await self._environment.tasks.get(command.task_id)
        assert task is not None
        updated = task.report(command.status, command.summary)
        await self._environment.tasks.update(updated, expected_version=task.version)
        return updated.to_view()


class Round:
    """One leader-mode round, from parked assignment to verdict."""

    def __init__(self, environment: Environment, leader_task_id: UUID) -> None:
        self.environment = environment
        self.leader_task_id = leader_task_id
        self.reporter = Reporter(environment)
        self.advanced: list[UUID] = []
        lane = environment.leader_lane
        assert lane is not None
        self.assignments = lane.assignments
        self.collaboration = lane.collaboration
        self.plan_submitter = SubmitRepositoryPlan(
            lane.assignments,
            environment.tasks,
            environment.directory,
            lane.modes,
            environment.assigner,
            spec_author=environment.spec_author,
        )
        self.review_submitter = SubmitRepositoryReview(
            lane.assignments,
            environment.tasks,
            environment.directory,
            lane.modes,
            environment.assigner,
            self.reporter,
            spec_author=environment.spec_author,
            on_leader_task_terminal=self._advance,
        )

    async def _advance(self, leader_task_id: UUID) -> None:
        self.advanced.append(leader_task_id)
        await self.environment.advancer.on_task_terminal(leader_task_id)

    @property
    def assignment(self):
        return self.assignments.assignments[self.leader_task_id]

    async def submit_plan(
        self, *, nodes: int = 2, allowed_paths: tuple[str, ...] | None = None
    ):
        assignment = self.assignment
        worker_agent_id = assignment.worker_roster[0].worker_agent_id
        drafts = tuple(
            LeaderWorkerTaskDraft(
                node_id=f"step-{index}",
                assignee_worker_agent_id=worker_agent_id,
                title=f"Step {index}",
                instruction=f"Do step {index}.",
                allowed_paths=allowed_paths or assignment.safety_envelope.allowed_path_roots,
                tests=assignment.safety_envelope.test_commands,
            )
            for index in range(nodes)
        )
        decision = RepositoryPlanDecision(
            engineering_spec_summary="Two steps.",
            engineering_spec_markdown="# Plan\nStep 0 then step 1.",
            nodes=tuple(draft.node_id for draft in drafts),
            edges=tuple(
                (drafts[index].node_id, drafts[index + 1].node_id)
                for index in range(len(drafts) - 1)
            ),
            worker_tasks=drafts,
            provenance=LeaderProvenanceView(
                source=LEADER_PROVENANCE_SOURCE, session_thread_id="thread-1"
            ),
            raw={"marker": f"plan-{nodes}"},
        )
        return await self.plan_submitter.execute(
            self.leader_task_id, decision, caller_agent_id=self.environment.leader_ids[0]
        )

    async def finish_workers(self, status: TaskStatus = TaskStatus.SUCCEEDED) -> list[UUID]:
        """Report every open worker task, then let the platform react to each."""

        children = await self.environment.tasks.list_by_parent(self.leader_task_id)
        open_children = [child for child in children if child.status is TaskStatus.ASSIGNED]
        for child in open_children:
            await self.environment.finish(child.id, status, RUNNER_DOCUMENT)
            await self.environment.advancer.on_task_terminal(child.id)
        return [child.id for child in open_children]

    async def review(
        self,
        verdict: LeaderReviewVerdict,
        *,
        findings: tuple[LeaderReviewFinding, ...] = (),
        summary: str = "Reviewed against the round's evidence.",
        marker: str = "review-1",
    ):
        decision = RepositoryReviewDecision(
            verdict=verdict,
            summary=summary,
            findings=findings,
            provenance=LeaderProvenanceView(
                source=LEADER_PROVENANCE_SOURCE, session_thread_id="thread-2"
            ),
            raw={"marker": marker},
        )
        return await self.review_submitter.execute(
            self.leader_task_id, decision, caller_agent_id=self.environment.leader_ids[0]
        )


async def start_round(**environment_kwargs) -> Round:
    environment = Environment(leader_mode_repositories=(0,), **environment_kwargs)
    plan = environment.plan(((0,),), tests={0: TESTS}, test_paths={0: TEST_PATHS})
    await environment.advancer.start(plan, idempotency_key="round-1")
    leader_task_id = await environment.leader_task_id(
        (await environment.plans.list_all())[0].id, 0, 0
    )
    return Round(environment, leader_task_id)


# ---------------------------------------------------------------------------
# Plan acceptance drives the ordinary dispatch machinery
# ---------------------------------------------------------------------------


async def test_no_worker_task_exists_until_the_leader_plans() -> None:
    """Acceptance 1's negative half, restated where the round really runs."""

    round_ = await start_round()

    assert await round_.environment.tasks.list_by_parent(round_.leader_task_id) == ()
    assert round_.assignment.phase is LeaderAssignmentPhase.PLANNING


async def test_an_accepted_plan_dispatches_through_the_formal_path() -> None:
    """The worker tasks are assigned and permitted exactly as server mode's are."""

    round_ = await start_round()

    receipt = await round_.submit_plan()

    children = await round_.environment.tasks.list_by_parent(round_.leader_task_id)
    assert [child.id for child in children] == list(receipt.worker_task_ids)
    # The assigner is the harness's own recorder, so this is the same delivery
    # (task package + room message) a server-planned task gets.
    assert [command.assignee_agent_id for command, _ in round_.environment.assigner.commands][
        1:
    ] == [round_.environment.worker_ids[0]] * 2
    # An execution permit per worker task, carrying the leader's own bounds.
    assert len(round_.environment.recorded_specifications) == 2
    assert round_.assignment.phase is LeaderAssignmentPhase.EXECUTING


# ---------------------------------------------------------------------------
# The round finishing opens a review rather than settling the leader task
# ---------------------------------------------------------------------------


async def test_finished_workers_open_review_instead_of_rolling_up() -> None:
    round_ = await start_round()
    await round_.submit_plan()

    await round_.finish_workers()

    leader_task = await round_.environment.tasks.get(round_.leader_task_id)
    assert leader_task is not None
    # The negative that the whole slice is about.
    assert leader_task.status is TaskStatus.ASSIGNED
    assert round_.assignment.phase is LeaderAssignmentPhase.REVIEW_DUE


async def test_the_round_stays_executing_while_any_worker_is_still_open() -> None:
    round_ = await start_round()
    await round_.submit_plan()
    children = await round_.environment.tasks.list_by_parent(round_.leader_task_id)

    await round_.environment.finish(children[0].id, TaskStatus.SUCCEEDED, RUNNER_DOCUMENT)
    await round_.environment.advancer.on_task_terminal(children[0].id)

    assert round_.assignment.phase is LeaderAssignmentPhase.EXECUTING
    assert round_.assignment.review_evidence is None


async def test_the_evidence_snapshot_carries_every_worker_task() -> None:
    round_ = await start_round()
    await round_.submit_plan()

    worker_task_ids = await round_.finish_workers()

    evidence = round_.assignment.review_evidence
    assert evidence is not None
    assert evidence.review_revision == 1
    assert [item.worker_task_id for item in evidence.worker_evidence] == worker_task_ids
    for item in evidence.worker_evidence:
        assert item.status is TaskStatus.SUCCEEDED
        assert item.commit_sha == "3f2a9d1b7c4e8f0a6b5d2c1e9f8a7b6c5d4e3f2a"
        assert item.run_id == UUID("11111111-1111-4111-8111-111111111111")
        assert item.changed_files == ("src/pricing/quote.py",)
        assert [result.exit_code for result in item.test_results] == [0]
        # Nothing in this system computes a diff stat, so none is invented.
        assert item.diff_stat is None


async def test_the_snapshot_is_not_rewritten_by_a_later_event() -> None:
    """Adjudication A-6: a verdict must be attributable to what was shown."""

    round_ = await start_round()
    await round_.submit_plan()
    await round_.finish_workers()
    frozen = round_.assignment.review_evidence

    children = await round_.environment.tasks.list_by_parent(round_.leader_task_id)
    await round_.environment.advancer.on_task_terminal(children[0].id)

    assert round_.assignment.review_evidence == frozen


async def test_the_leader_is_told_its_round_is_waiting() -> None:
    round_ = await start_round()
    await round_.submit_plan()

    await round_.finish_workers()

    subjects = [command.subject for command, _ in round_.collaboration.sent]
    assert any("awaiting your review" in subject for subject in subjects)
    review_notice = next(
        command for command, _ in round_.collaboration.sent if "review" in command.subject
    )
    assert review_notice.recipient_agent_id == round_.environment.leader_ids[0]


async def test_the_review_notice_is_sent_once_per_round() -> None:
    round_ = await start_round()
    await round_.submit_plan()
    await round_.finish_workers()
    children = await round_.environment.tasks.list_by_parent(round_.leader_task_id)

    await round_.environment.advancer.on_task_terminal(children[0].id)

    keys = [key for _, key in round_.collaboration.sent]
    assert len(keys) == len(set(keys))


async def test_a_blocked_worker_still_opens_the_review() -> None:
    """``blocked`` is resting, not running: something must decide, and that is the leader."""

    round_ = await start_round()
    await round_.submit_plan()

    await round_.finish_workers(TaskStatus.BLOCKED)

    assert round_.assignment.phase is LeaderAssignmentPhase.REVIEW_DUE
    evidence = round_.assignment.review_evidence
    assert evidence is not None
    assert {item.status for item in evidence.worker_evidence} == {TaskStatus.BLOCKED}


# ---------------------------------------------------------------------------
# The three verdicts, through the real advance path
# ---------------------------------------------------------------------------


async def test_approve_settles_the_leader_task_and_advances_the_plan() -> None:
    round_ = await start_round()
    await round_.submit_plan()
    await round_.finish_workers()

    receipt = await round_.review(LeaderReviewVerdict.APPROVE, summary="All green.")

    leader_task = await round_.environment.tasks.get(round_.leader_task_id)
    assert leader_task is not None
    assert leader_task.status is TaskStatus.SUCCEEDED
    # The leader's own words became the roll-up body.
    assert leader_task.result_summary == "All green."
    assert receipt.leader_task_status == "succeeded"
    assert round_.advanced == [round_.leader_task_id]
    assert round_.assignment.phase is LeaderAssignmentPhase.CLOSED


async def test_the_delivery_gate_takes_no_candidate_before_the_leader_approves() -> None:
    """The gate is inherited, not implemented — so this is what proves it inherits.

    ``AdvanceExecutionPlan`` only reaches its batch-delivery hook once every
    leader task of the batch is SUCCEEDED. In leader mode that cannot happen
    before a verdict, so a finished-but-unreviewed round must leave the hook
    untouched, and an approved one must reach it.
    """

    delivered: list[UUID] = []

    async def deliver(plan) -> None:
        delivered.append(plan.id)

    round_ = await start_round(on_batch_deliver=deliver)
    await round_.submit_plan()
    await round_.finish_workers()

    assert delivered == []

    await round_.review(LeaderReviewVerdict.APPROVE)

    assert delivered != []


async def test_escalate_blocks_without_failing_the_plan() -> None:
    """AC-07: a leader that cannot finish is not a round that failed."""

    round_ = await start_round()
    await round_.submit_plan()
    await round_.finish_workers()

    receipt = await round_.review(LeaderReviewVerdict.ESCALATE, summary="Needs a human.")

    leader_task = await round_.environment.tasks.get(round_.leader_task_id)
    assert leader_task is not None
    assert leader_task.status is TaskStatus.BLOCKED
    assert receipt.leader_task_status == "blocked"
    assert round_.advanced == []


async def test_request_rework_reopens_the_round_with_new_work() -> None:
    round_ = await start_round()
    await round_.submit_plan()
    worker_task_ids = await round_.finish_workers()
    before = await round_.environment.tasks.get(worker_task_ids[0])

    receipt = await round_.review(
        LeaderReviewVerdict.REQUEST_REWORK,
        findings=(
            LeaderReviewFinding(
                worker_task_id=worker_task_ids[0],
                note="The rule table is duplicated.",
                rework_instruction="Read the table from the contract instead.",
            ),
        ),
    )

    assert len(receipt.rework_task_ids) == 1
    created = await round_.environment.tasks.get(receipt.rework_task_ids[0])
    assert created is not None
    assert created.origin is TaskOrigin.REWORK
    assert created.status is TaskStatus.ASSIGNED
    # The reviewed task is untouched, so the evidence stays as it was judged.
    assert await round_.environment.tasks.get(worker_task_ids[0]) == before
    assert round_.assignment.phase is LeaderAssignmentPhase.EXECUTING
    assert round_.assignment.review_revision == 2


async def test_a_second_round_snapshots_its_own_evidence() -> None:
    round_ = await start_round()
    await round_.submit_plan()
    worker_task_ids = await round_.finish_workers()
    await round_.review(
        LeaderReviewVerdict.REQUEST_REWORK,
        findings=(
            LeaderReviewFinding(
                worker_task_id=worker_task_ids[0],
                note="Duplicated.",
                rework_instruction="Read it from the contract.",
            ),
        ),
    )

    await round_.finish_workers()

    evidence = round_.assignment.review_evidence
    assert evidence is not None
    assert evidence.review_revision == 2
    # Every task under the leader is in the new snapshot, repair included.
    assert len(evidence.worker_evidence) == 3
    assert round_.assignment.phase is LeaderAssignmentPhase.REVIEW_DUE


async def test_the_second_rounds_verdict_has_its_own_idempotency() -> None:
    round_ = await start_round()
    await round_.submit_plan()
    worker_task_ids = await round_.finish_workers()
    first = await round_.review(
        LeaderReviewVerdict.REQUEST_REWORK,
        findings=(
            LeaderReviewFinding(
                worker_task_id=worker_task_ids[0],
                note="Duplicated.",
                rework_instruction="Read it from the contract.",
            ),
        ),
        marker="review-1",
    )
    await round_.finish_workers()

    second = await round_.review(LeaderReviewVerdict.APPROVE, marker="review-2")

    assert first.review_revision == 1
    assert second.review_revision == 2
    assert len(round_.assignment.accepted_reviews) == 2


# ---------------------------------------------------------------------------
# Server mode is untouched
# ---------------------------------------------------------------------------


async def test_server_mode_still_rolls_up_automatically() -> None:
    """The comparison the whole fork rests on: no assignment row, no new path."""

    environment = Environment(leader_mode_repositories=())
    plan = environment.plan(((0,),), tests={0: TESTS}, test_paths={0: TEST_PATHS})
    await environment.advancer.start(plan, idempotency_key="server-round")
    leader_task_id = await environment.leader_task_id(
        (await environment.plans.list_all())[0].id, 0, 0
    )
    worker = await environment.worker_task_of(leader_task_id)

    await environment.finish(worker.id, TaskStatus.SUCCEEDED, RUNNER_DOCUMENT)
    await environment.advancer.on_task_terminal(worker.id)

    leader_task = await environment.tasks.get(leader_task_id)
    assert leader_task is not None
    assert leader_task.status is TaskStatus.SUCCEEDED


async def test_a_leader_mode_lane_does_not_change_a_server_mode_team() -> None:
    """Two repositories, one adopted: the other keeps rolling up by itself."""

    environment = Environment(repository_count=2, leader_mode_repositories=(1,))
    plan = environment.plan(((0, 1),), tests={0: TESTS}, test_paths={0: TEST_PATHS})
    await environment.advancer.start(plan, idempotency_key="mixed-round")
    plan_id = (await environment.plans.list_all())[0].id
    server_leader = await environment.leader_task_id(plan_id, 0, 0)
    worker = await environment.worker_task_of(server_leader)

    await environment.finish(worker.id, TaskStatus.SUCCEEDED, RUNNER_DOCUMENT)
    await environment.advancer.on_task_terminal(worker.id)

    settled = await environment.tasks.get(server_leader)
    assert settled is not None
    assert settled.status is TaskStatus.SUCCEEDED


# ---------------------------------------------------------------------------
# AC-02: what a leader still cannot do
# ---------------------------------------------------------------------------


async def test_a_plan_cannot_assign_work_outside_the_team() -> None:
    round_ = await start_round()
    assignment = round_.assignment
    decision = RepositoryPlanDecision(
        engineering_spec_summary="One step.",
        engineering_spec_markdown="# Plan",
        nodes=("only",),
        edges=(),
        worker_tasks=(
            LeaderWorkerTaskDraft(
                node_id="only",
                assignee_worker_agent_id=uuid4(),
                title="Somebody else's work",
                instruction="Do it.",
                allowed_paths=assignment.safety_envelope.allowed_path_roots,
                tests=assignment.safety_envelope.test_commands,
            ),
        ),
        provenance=LeaderProvenanceView(
            source=LEADER_PROVENANCE_SOURCE, session_thread_id="thread-1"
        ),
        raw={"marker": "outsider"},
    )

    with pytest.raises(LeaderActionRefused) as refused:
        await round_.plan_submitter.execute(
            round_.leader_task_id, decision, caller_agent_id=round_.environment.leader_ids[0]
        )

    assert refused.value.code is LeaderActionErrorCode.PLAN_INVALID_ASSIGNEE
    assert await round_.environment.tasks.list_by_parent(round_.leader_task_id) == ()


async def test_a_plan_cannot_widen_the_envelope_it_was_given() -> None:
    round_ = await start_round()
    assignment = round_.assignment
    decision = RepositoryPlanDecision(
        engineering_spec_summary="One step.",
        engineering_spec_markdown="# Plan",
        nodes=("only",),
        edges=(),
        worker_tasks=(
            LeaderWorkerTaskDraft(
                node_id="only",
                assignee_worker_agent_id=assignment.worker_roster[0].worker_agent_id,
                title="Reach further",
                instruction="Do it.",
                allowed_paths=("/etc/", *assignment.safety_envelope.allowed_path_roots),
                tests=assignment.safety_envelope.test_commands,
            ),
        ),
        provenance=LeaderProvenanceView(
            source=LEADER_PROVENANCE_SOURCE, session_thread_id="thread-1"
        ),
        raw={"marker": "wide"},
    )

    with pytest.raises(LeaderActionRefused) as refused:
        await round_.plan_submitter.execute(
            round_.leader_task_id, decision, caller_agent_id=round_.environment.leader_ids[0]
        )

    assert refused.value.code is LeaderActionErrorCode.PLAN_INVALID_ALLOWED_PATHS


async def test_the_envelope_the_clamp_uses_is_the_stored_one() -> None:
    """Not re-derived: a roster that moved while the leader planned must not bite it.

    The stored envelope is narrowed underneath the assignment after it was
    handed out. A clamp that re-derived from the directory would now refuse a
    plan that was inside its bounds when it was written; the stored one is what
    the leader was given, so it is what the plan is judged against.
    """

    round_ = await start_round()
    stored = round_.assignment
    handed_out = stored.safety_envelope.allowed_path_roots
    round_.assignments.assignments[round_.leader_task_id] = replace(
        stored,
        safety_envelope=replace(
            stored.safety_envelope, allowed_path_roots=("src/pricing/deep/",)
        ),
    )

    # The plan is written against the envelope the leader was handed; the
    # stored one has since been narrowed, and the clamp uses the stored one.
    with pytest.raises(LeaderActionRefused) as refused:
        await round_.submit_plan(allowed_paths=handed_out)

    assert refused.value.code is LeaderActionErrorCode.PLAN_INVALID_ALLOWED_PATHS
    # And a plan inside the narrowed bounds is accepted, so the refusal above
    # is about the envelope rather than about anything else in the submission.
    assert (await round_.submit_plan(allowed_paths=("src/pricing/deep/",))).plan_revision == 1
