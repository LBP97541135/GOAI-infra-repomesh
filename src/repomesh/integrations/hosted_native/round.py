"""One hosted-native attempt, end to end (spec §4.2 M1).

``HostedNativeRound`` is the application service the task orchestrator, the
shared-directory observer and the recovery loop call with three verbs:
``open`` a construction attempt for a task's current assignment generation,
``observe`` an event the observer already deduplicated, ``expire`` an attempt
whose budget ran out or whose worker went away. It sits beside
``integrations.runner.worker_execution`` and mirrors its side-effect order —
assignment, reservation (lease = attempt budget), task start, package, room
notice — and its rule that any failure after the reservation exists blocks the
task and leaves no half-open attempt.

Fencing reuses the assignment generation and the reservation version (D-9);
the round never invents a second timer or a second set of ids. Everything a
caller sees is in ``contracts.py``; the room prose is in ``messages.py``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from repomesh.integrations.agentteams.task_package import load_helper_script
from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalReader,
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.agent_runtime.contracts import (
    WorkerExecutionReservation,
    WorkerExecutionReservationPort,
)
from repomesh.modules.agent_runtime.execution_reservation import (
    WorkerExecutionReservationConflict,
)
from repomesh.modules.collaboration.contracts import (
    CollaborationGateway,
    CollaborationMessageKind,
    SendCollaborationMessageCommand,
)
from repomesh.modules.project.contracts import (
    ProjectAgentTopologyView,
    ProjectTopologyReader,
    RepositoryTeamView,
)
from repomesh.modules.task_orchestration.assignment import PostgresTaskAssignmentStore
from repomesh.modules.task_orchestration.contracts import (
    PackageInputs,
    PathPolicy,
    ReviewInputs,
    TaskAssignmentPublisher,
    TaskExecutionStateGateway,
    TaskReader,
    TaskView,
)

from . import messages
from .contracts import (
    WORKER_SIDE_PHASES,
    AttemptPhase,
    BaseBundleSource,
    CandidateForVerification,
    CandidateVerificationLauncher,
    ConstructionPolicy,
    ConstructionPolicySource,
    EventKind,
    HostedNativeAttempt,
    HostedNativeAttemptStore,
    HostedNativeEvent,
    ReviewVerdict,
    RoundOpened,
    RoundOutcome,
    RoundTransition,
    SharedTaskDirectoryReader,
    SharedTaskEvent,
    SubmitStatus,
    SubmittedResult,
    utcnow,
    verdict_for,
)

_logger = logging.getLogger(__name__)

ATTEMPT_PAYLOAD_SCHEMA = "repomesh.hosted-native.attempt/v1"
"""``task_payload`` schema the round binds to the reservation: what recovery's
expired-lease scan reads to tell a hosted-native attempt from a runner dispatch."""

CANDIDATE_FILES: tuple[str, str, str, str] = (
    "candidate/candidate.bundle",
    "candidate/candidate.diff",
    "candidate/changes.json",
    "candidate/evidence.json",
)
"""The four files helper ``bundle`` writes; a ``SUCCESS`` without all four is invalid."""

CONTROL_FILE = "base/package.json"

Escalate = Callable[[TaskView, str], Awaitable[None]]
Recover = Callable[[HostedNativeAttempt, str], Awaitable[None]]


class HostedNativeRoundError(RuntimeError):
    """``open`` could not produce an attempt; the task has been blocked with the reason."""


class _CandidateInvalid(ValueError):
    """The worker's ``candidate/`` files are not the ones this attempt should have written."""


@dataclass(frozen=True, slots=True)
class _Candidate:
    bundle: bytes
    diff: str
    changes_json: str
    evidence_json: str
    head_sha: str


class HostedNativeRound:
    def __init__(
        self,
        *,
        tasks: TaskReader,
        directory: AgentPrincipalReader,
        topologies: ProjectTopologyReader,
        assignments: PostgresTaskAssignmentStore,
        reservations: WorkerExecutionReservationPort,
        states: TaskExecutionStateGateway,
        publisher: TaskAssignmentPublisher,
        collaboration: CollaborationGateway,
        attempts: HostedNativeAttemptStore,
        reader: SharedTaskDirectoryReader,
        bundles: BaseBundleSource,
        policies: ConstructionPolicySource,
        verification: CandidateVerificationLauncher,
        escalate: Escalate | None = None,
        recover: Recover | None = None,
        attempt_budget_seconds: int = 2700,
        review_budget_seconds: int = 900,
        lease_owner: str = "hosted-native-round",
        clock: Callable[[], datetime] = utcnow,
        helper_script: bytes | None = None,
    ) -> None:
        self._tasks = tasks
        self._directory = directory
        self._topologies = topologies
        self._assignments = assignments
        self._reservations = reservations
        self._states = states
        self._publisher = publisher
        self._collaboration = collaboration
        self._attempts = attempts
        self._reader = reader
        self._bundles = bundles
        self._policies = policies
        self._verification = verification
        self._escalate = escalate
        self._recover = recover
        self._attempt_budget_seconds = attempt_budget_seconds
        self._review_budget_seconds = review_budget_seconds
        self._lease_owner = lease_owner
        self._clock = clock
        self._helper_script = helper_script if helper_script is not None else load_helper_script()

    # ------------------------------------------------------------------ open

    async def open(
        self, task_id: UUID, *, idempotency_key: str, revision_note: str | None = None
    ) -> RoundOpened:
        """Open a construction attempt for the task's current assignment generation.

        Idempotent on ``task_id + generation``: an open attempt of the current
        generation is returned untouched. ``revision_note`` is the Leader's
        reasons when a ``REVISION`` re-opens the same generation; it is appended
        to the instruction the new spec renders.
        """

        task = await self._tasks.get_view(task_id)
        if task is None:
            raise HostedNativeRoundError(f"task not found: {task_id}")
        worker = await self._directory.get_view(task.assignee_agent_id)
        if (
            worker is None
            or worker.role is not AgentRole.WORKER
            or worker.status is not AgentPrincipalStatus.ACTIVE
        ):
            raise HostedNativeRoundError("task assignee is not an active Worker")
        _topology, team = await self._team_for(task, worker.id)
        if team is None or not team.room_id:
            raise await self._block_open(
                task, worker, "Worker team room is not ready for a hosted-native attempt"
            )

        assignment = await self._assignments.ensure_initial(task.id)
        existing = await self._attempts.get_open_for_task(task.id)
        if existing is not None:
            if existing.generation == assignment.generation:
                return RoundOpened(existing, created=False)
            # An attempt of an older generation still open: nothing it writes
            # may count any more (D-9), and the store allows one open attempt
            # per task, so it is fenced before the new generation's is added.
            # Its reservation goes with it: the store binds a task's active
            # execution to one worker, and the new generation may be another's.
            stale = await self._fence(existing, "generation_advanced", self._clock())
            await self._release_reservation(stale, "generation_advanced")

        try:
            reserved = await self._reservations.reserve(
                organization_id=task.organization_id,
                project_id=task.project_id,
                repository_id=task.repository_id,
                task_id=task.id,
                worker_agent_id=worker.id,
                lease_owner=self._lease_owner,
                lease_seconds=self._attempt_budget_seconds,
                assignment_attempt_id=assignment.id,
                assignment_generation=assignment.generation,
            )
        except WorkerExecutionReservationConflict as error:
            raise await self._block_open(task, worker, _describe(error)) from error

        reservation = reserved.reservation
        attempt: HostedNativeAttempt | None = None
        try:
            if reserved.created:
                await self._assignments.bind_execution(
                    task.id,
                    expected_generation=assignment.generation,
                    execution_id=reservation.id,
                )
            elif reservation.assignment_attempt_id == assignment.id:
                # A revision re-open or a retry after a crash: the reservation
                # of this very assignment is still alive, so the lease is
                # renewed to a full budget rather than a second one created.
                reservation = await self._reservations.renew(
                    reservation.id,
                    lease_owner=self._lease_owner,
                    fencing_version=reservation.version,
                    lease_seconds=self._attempt_budget_seconds,
                )
            else:
                raise WorkerExecutionReservationConflict(
                    "active execution reservation belongs to another assignment attempt"
                )
            await self._states.start(task.id, agent_id=worker.id)
            bundle = await self._bundles.build(task.repository_id)
            policy = await self._policies.resolve(task.id, worker_agent_id=worker.id)
            attempt_id = uuid4()  # a new directory every time (D-8)
            package = PackageInputs(
                kind="construction",
                attempt_id=attempt_id,
                generation=assignment.generation,
                budget_seconds=self._attempt_budget_seconds,
                base_sha=bundle.base_sha,
                helper_script=self._helper_script,
                policy=policy.policy,
                test_commands=policy.test_commands,
                base_bundle=bundle.bundle,
            )
            view = (
                task
                if revision_note is None
                else replace(
                    task,
                    instruction=messages.revision_instruction(task.instruction, revision_note),
                )
            )
            key = f"{idempotency_key}:g{assignment.generation}:{attempt_id}"
            published = await self._publisher.publish(
                view,
                team_name=team.agentteams_team_name,
                room_id=team.room_id,
                assignee_resource_name=worker.agentteams_resource_name,
                idempotency_key=key,
                package=package,
            )
            reservation = await self._reservations.bind_payload(
                reservation.id,
                {
                    "schema": ATTEMPT_PAYLOAD_SCHEMA,
                    "attemptId": str(attempt_id),
                    "taskId": str(task.id),
                    "generation": assignment.generation,
                    "packageDir": published.task_path,
                    "budgetSeconds": self._attempt_budget_seconds,
                },
                lease_owner=self._lease_owner,
                fencing_version=reservation.version,
            )
            now = self._clock()
            attempt = HostedNativeAttempt(
                id=attempt_id,
                task_id=task.id,
                worker_agent_id=worker.id,
                leader_agent_id=team.leader_agent_id,
                team_name=team.agentteams_team_name,
                room_id=team.room_id,
                assignment_attempt_id=assignment.id,
                generation=assignment.generation,
                execution_id=reservation.id,
                phase=AttemptPhase.NOTIFIED,
                package_dir=published.task_path,
                base_sha=bundle.base_sha,
                budget_until=now + timedelta(seconds=self._attempt_budget_seconds),
                notified_at=now,
                created_at=now,
                updated_at=now,
            )
            await self._attempts.add(attempt)
            await self._collaboration.send(
                SendCollaborationMessageCommand(
                    organization_id=task.organization_id,
                    project_id=task.project_id,
                    repository_id=task.repository_id,
                    task_id=task.id,
                    sender_agent_id=task.assigned_by_agent_id,
                    recipient_agent_id=worker.id,
                    kind=CollaborationMessageKind.TASK_ASSIGNMENT,
                    subject=task.title,
                    body=messages.construction_notice(
                        attempt_id=attempt_id,
                        package_dir=published.task_path,
                        title=task.title,
                        budget_seconds=self._attempt_budget_seconds,
                    ),
                    correlation_id=task.id,
                ),
                idempotency_key=f"{key}:notice",
            )
        except Exception as error:
            raise await self._abandon_open(task, worker, reservation, attempt, error) from error
        _logger.info(
            "hosted-native attempt %s opened for task %s generation %s (%s)",
            attempt.id,
            task.id,
            assignment.generation,
            published.task_path,
        )
        return RoundOpened(attempt, created=True)

    async def _abandon_open(
        self,
        task: TaskView,
        worker: AgentPrincipalView,
        reservation: WorkerExecutionReservation,
        attempt: HostedNativeAttempt | None,
        error: Exception,
    ) -> HostedNativeRoundError:
        """Undo a partly opened attempt (``worker_execution.py`` ``_fail_reservation``)."""

        detail = _describe(error)
        try:
            await self._reservations.fail_preparation(
                reservation.id,
                detail,
                lease_owner=self._lease_owner,
                fencing_version=reservation.version,
            )
        except WorkerExecutionReservationConflict as conflict:
            _logger.warning(
                "reservation %s could not be failed after an open failure: %s",
                reservation.id,
                conflict,
            )
        if attempt is not None:
            await self._fence(attempt, "open_failed", self._clock())
        return await self._block_open(task, worker, detail)

    async def _block_open(
        self, task: TaskView, worker: AgentPrincipalView, detail: str
    ) -> HostedNativeRoundError:
        summary = f"Hosted-native attempt could not be opened: {detail}"
        try:
            await self._states.block(task.id, agent_id=worker.id, summary=summary)
        except Exception as block_error:  # the original failure must surface
            summary += f"; task block failed: {_describe(block_error)}"
        _logger.info("hosted-native open refused for task %s: %s", task.id, summary)
        return HostedNativeRoundError(summary)

    # --------------------------------------------------------------- observe

    async def observe(self, event: SharedTaskEvent) -> RoundTransition:
        """Apply one deduplicated shared-directory event to its attempt."""

        attempt = await self._attempts.get(event.attempt_id)
        if attempt is None:
            return _ignored(event.attempt_id, None, "unknown_attempt")
        now = self._clock()
        if not attempt.is_open:
            await self._record(attempt, EventKind.FENCED, _fence_marker(event), event, now)
            return _ignored(attempt.id, attempt.phase, "attempt_terminal")
        active = await self._assignments.active(attempt.task_id)
        if (
            active is None
            or active.generation != attempt.generation
            or active.id != attempt.assignment_attempt_id
        ):
            fenced = await self._fence(attempt, "generation_advanced", now)
            await self._record(fenced, EventKind.FENCED, _fence_marker(event), event, now)
            await self._release_reservation(fenced, "generation_advanced")
            return _ignored(attempt.id, fenced.phase, "fenced_generation")

        if event.kind is EventKind.ACKNOWLEDGED:
            return await self._acknowledged(attempt, event)
        if event.kind is EventKind.SUBMITTED:
            return await self._submitted(attempt, event, now)
        if event.kind is EventKind.REVIEW_SUBMITTED:
            return await self._review_submitted(attempt, event, now)
        return _ignored(attempt.id, attempt.phase, "unsupported_event")

    async def _acknowledged(
        self, attempt: HostedNativeAttempt, event: SharedTaskEvent
    ) -> RoundTransition:
        if attempt.phase is not AttemptPhase.NOTIFIED:
            return _ignored(attempt.id, attempt.phase, "phase_mismatch")
        updated = attempt.with_phase(
            AttemptPhase.ACKNOWLEDGED, at=self._clock(), acknowledged_at=event.observed_at
        )
        await self._attempts.save(updated)
        _logger.info("hosted-native attempt %s acknowledged", attempt.id)
        return _applied(updated)

    async def _submitted(
        self, attempt: HostedNativeAttempt, event: SharedTaskEvent, now: datetime
    ) -> RoundTransition:
        if attempt.phase not in WORKER_SIDE_PHASES:
            return _ignored(attempt.id, attempt.phase, "phase_mismatch")
        result = event.result
        if result is None:
            return _ignored(attempt.id, attempt.phase, "missing_result")
        submitted = {"submitted_at": event.observed_at, "submit_status": result.status}
        if result.status in (SubmitStatus.SUCCESS, SubmitStatus.SUCCESS_WITH_NOTES):
            return await self._candidate_submitted(attempt, result, now, submitted)
        task = await self._required_task(attempt)
        if result.status is SubmitStatus.BLOCKED:
            blocked = await self._block_attempt(
                attempt,
                task,
                now,
                summary=f"Worker reported BLOCKED: {result.summary}",
                **submitted,
            )
            await self._hand_to_recovery(blocked, "worker_blocked")
            return _applied(blocked)
        blocked = await self._block_attempt(
            attempt,
            task,
            now,
            summary=f"Worker asked for clarification (REVISION_NEEDED): {result.summary}",
            **submitted,
        )
        await self._hand_to_human(task, "worker_needs_revision")
        return _applied(blocked)

    async def _candidate_submitted(
        self,
        attempt: HostedNativeAttempt,
        result: SubmittedResult,
        now: datetime,
        submitted: dict[str, object],
    ) -> RoundTransition:
        task = await self._required_task(attempt)
        try:
            candidate = await self._read_candidate(attempt)
        except _CandidateInvalid as error:
            return await self._candidate_invalid(attempt, task, now, str(error), submitted)
        leader = await self._directory.get_view(attempt.leader_agent_id)
        topology, team = await self._team_for(task, attempt.worker_agent_id)
        if leader is None or topology is None or team is None or not team.leader_room_id:
            blocked = await self._block_attempt(
                attempt,
                task,
                now,
                summary="Candidate submitted but the Team Leader's review room is not ready",
                **submitted,
            )
            await self._hand_to_human(task, "leader_room_missing")
            return _applied(blocked)

        policy = await self._frozen_policy(attempt)
        review_id = uuid4()
        package = PackageInputs(
            kind="review",
            attempt_id=review_id,
            generation=attempt.generation,
            budget_seconds=self._review_budget_seconds,
            base_sha=attempt.base_sha,
            helper_script=self._helper_script,
            policy=policy.policy,
            test_commands=policy.test_commands,
            review=ReviewInputs(
                review_of=attempt.id,
                head_sha=candidate.head_sha,
                candidate_diff=candidate.diff,
                changes_json=candidate.changes_json,
                evidence_json=candidate.evidence_json,
            ),
        )
        published = await self._publisher.publish(
            task,
            team_name=team.agentteams_team_name,
            room_id=team.leader_room_id,
            assignee_resource_name=leader.agentteams_resource_name,
            idempotency_key=f"review:{attempt.id}",
            package=package,
        )
        # The collaboration route reaches the Leader's own room only when the
        # organization leader is one end of the message (``_route``); the
        # platform speaks to the Leader as that principal.
        await self._collaboration.send(
            SendCollaborationMessageCommand(
                organization_id=task.organization_id,
                project_id=task.project_id,
                repository_id=task.repository_id,
                task_id=task.id,
                sender_agent_id=topology.organization_leader_id,
                recipient_agent_id=leader.id,
                kind=CollaborationMessageKind.TASK_ASSIGNMENT,
                subject=f"Review candidate {candidate.head_sha[:8]}: {task.title}",
                body=messages.review_notice(
                    review_id=review_id,
                    attempt_id=attempt.id,
                    package_dir=published.task_path,
                    head_sha=candidate.head_sha,
                    title=task.title,
                    budget_seconds=self._review_budget_seconds,
                ),
                correlation_id=task.id,
            ),
            idempotency_key=f"review:{attempt.id}:notice",
        )
        updated = attempt.with_phase(
            AttemptPhase.REVIEW_PENDING,
            at=now,
            review_dir=published.task_path,
            review_budget_until=now + timedelta(seconds=self._review_budget_seconds),
            **submitted,
        )
        await self._attempts.save(updated)
        _logger.info(
            "hosted-native attempt %s submitted %s; review %s published for leader %s",
            attempt.id,
            result.status,
            review_id,
            leader.id,
        )
        return _applied(updated)

    async def _candidate_invalid(
        self,
        attempt: HostedNativeAttempt,
        task: TaskView,
        now: datetime,
        why: str,
        submitted: dict[str, object],
    ) -> RoundTransition:
        failed = attempt.with_phase(
            AttemptPhase.FAILED,
            at=now,
            fenced_at=now,
            fence_reason=f"candidate_invalid: {why}",
            **submitted,
        )
        await self._attempts.save(failed)
        await self._states.block(
            task.id,
            agent_id=attempt.worker_agent_id,
            summary=f"Hosted-native candidate rejected: {why}",
        )
        _logger.info("hosted-native attempt %s failed: candidate invalid (%s)", attempt.id, why)
        await self._hand_to_recovery(failed, "candidate_invalid")
        return _applied(failed)

    async def _review_submitted(
        self, attempt: HostedNativeAttempt, event: SharedTaskEvent, now: datetime
    ) -> RoundTransition:
        if attempt.phase is not AttemptPhase.REVIEW_PENDING:
            return _ignored(attempt.id, attempt.phase, "phase_mismatch")
        result = event.result
        if result is None:
            return _ignored(attempt.id, attempt.phase, "missing_result")
        verdict = verdict_for(result.status)
        stated = _stated_verdict(result.summary)
        if stated is not None and stated is not verdict:
            # review.md: the status wins; the disagreement stays on the record.
            await self._attempts.record_event(
                HostedNativeEvent(
                    id=uuid4(),
                    attempt_id=attempt.id,
                    kind=EventKind.REVIEW_SUBMITTED,
                    marker=f"{event.marker}:disagreement",
                    payload={
                        "status": result.status.value,
                        "verdict": verdict.value,
                        "stated_verdict": stated.value,
                        "summary": result.summary,
                    },
                    observed_at=event.observed_at,
                    applied_at=now,
                )
            )
        task = await self._required_task(attempt)
        if verdict is ReviewVerdict.ACCEPT:
            return await self._review_accepted(attempt, task, now)
        if verdict is ReviewVerdict.REVISION:
            return await self._review_revision(attempt, result, now)
        blocked = await self._block_attempt(
            attempt,
            task,
            now,
            summary=f"Leader review BLOCKED: {result.summary}",
            review_verdict=ReviewVerdict.BLOCKED,
        )
        await self._hand_to_human(task, "leader_blocked")
        return _applied(blocked)

    async def _review_accepted(
        self, attempt: HostedNativeAttempt, task: TaskView, now: datetime
    ) -> RoundTransition:
        try:
            candidate = await self._read_candidate(attempt)
        except _CandidateInvalid as error:
            return await self._candidate_invalid(attempt, task, now, str(error), {})
        policy = await self._frozen_policy(attempt)
        run_id = await self._verification.launch(
            CandidateForVerification(
                attempt_id=attempt.id,
                task_id=task.id,
                repository_id=task.repository_id,
                base_sha=attempt.base_sha,
                head_sha=candidate.head_sha,
                candidate_bundle=candidate.bundle,
                changes_json=candidate.changes_json,
                evidence_json=candidate.evidence_json,
                policy=policy.policy,
                test_commands=policy.test_commands,
            ),
            attempt=attempt,
        )
        updated = attempt.with_phase(
            AttemptPhase.VERIFYING,
            at=now,
            verification_run_id=run_id,
            review_verdict=ReviewVerdict.ACCEPT,
        )
        await self._attempts.save(updated)
        _logger.info(
            "hosted-native attempt %s accepted by leader; verification run %s", attempt.id, run_id
        )
        return _applied(updated)

    async def _review_revision(
        self, attempt: HostedNativeAttempt, result: SubmittedResult, now: datetime
    ) -> RoundTransition:
        fenced = await self._fence(
            attempt, "leader_revision", now, review_verdict=ReviewVerdict.REVISION
        )
        _logger.info("hosted-native attempt %s fenced: leader asked for a revision", attempt.id)
        try:
            opened = await self.open(
                attempt.task_id,
                idempotency_key=f"revision:{attempt.id}",
                revision_note=result.summary,
            )
        except HostedNativeRoundError as error:
            # ``open`` has already blocked the task; the observation itself applied.
            return RoundTransition(
                attempt.id,
                RoundOutcome.APPLIED,
                fenced.phase,
                reason=f"leader_revision:reopen_failed:{error}",
            )
        return RoundTransition(
            attempt.id,
            RoundOutcome.APPLIED,
            opened.attempt.phase,
            reason="leader_revision:reopened",
            next_attempt_id=opened.attempt.id,
        )

    # ---------------------------------------------------------------- expire

    async def expire(self, attempt_id: UUID, *, reason: str) -> RoundTransition:
        """Fence an open attempt the recovery loop gave up on.

        A worker-side or verifying attempt is left to the recovery decision
        (reassign or escalate, D-12) and the task is never blocked here. An
        attempt waiting on the Leader is different (D-13): a review that ran
        out of budget is not skipped — the task is blocked and a human
        checkpoint opens, because ``ACCEPT`` is the only door into verification.
        """

        attempt = await self._attempts.get(attempt_id)
        if attempt is None:
            return _ignored(attempt_id, None, "unknown_attempt")
        if not attempt.is_open:
            return _ignored(attempt.id, attempt.phase, "attempt_terminal")
        now = self._clock()
        fenced = await self._fence(attempt, reason, now)
        await self._attempts.record_event(
            HostedNativeEvent(
                id=uuid4(),
                attempt_id=attempt.id,
                kind=EventKind.EXPIRED,
                marker=f"{reason}:{now.isoformat()}",
                payload={"reason": reason, "phase_before": attempt.phase.value},
                observed_at=now,
                applied_at=now,
            )
        )
        await self._release_reservation(fenced, reason)
        _logger.info("hosted-native attempt %s expired: %s", attempt.id, reason)
        if attempt.phase is AttemptPhase.REVIEW_PENDING:
            task = await self._required_task(attempt)
            await self._states.block(
                task.id,
                agent_id=attempt.worker_agent_id,
                summary=f"Leader review of candidate did not finish: {reason}",
            )
            await self._hand_to_human(task, reason)
        else:
            await self._hand_to_recovery(fenced, reason)
        return _applied(fenced)

    async def _release_reservation(self, attempt: HostedNativeAttempt, reason: str) -> None:
        """Fail the reservation a fenced attempt was opened under, if it is still ours.

        An already-expired lease raises a conflict and is recovery's to reap;
        a reservation another owner has since taken is likewise left alone.
        """

        reservation = await self._reservations.get(attempt.execution_id)
        if reservation is None:
            return
        try:
            await self._reservations.fail_preparation(
                reservation.id,
                reason,
                lease_owner=self._lease_owner,
                fencing_version=reservation.version,
            )
        except WorkerExecutionReservationConflict:
            _logger.info(
                "reservation %s of attempt %s left to recovery", reservation.id, attempt.id
            )

    # --------------------------------------------------------------- helpers

    async def _team_for(
        self, task: TaskView, worker_id: UUID
    ) -> tuple[ProjectAgentTopologyView | None, RepositoryTeamView | None]:
        topology = await self._topologies.get_view(task.project_id)
        if topology is None:
            return None, None
        team = next(
            (
                item
                for item in topology.repository_teams
                if item.repository_id == task.repository_id and worker_id in item.worker_agent_ids
            ),
            None,
        )
        return topology, team

    async def _required_task(self, attempt: HostedNativeAttempt) -> TaskView:
        task = await self._tasks.get_view(attempt.task_id)
        if task is None:
            raise HostedNativeRoundError(f"task not found: {attempt.task_id}")
        return task

    async def _read_candidate(self, attempt: HostedNativeAttempt) -> _Candidate:
        files: dict[str, bytes] = {}
        for name in CANDIDATE_FILES:
            data = await self._reader.read(attempt.team_name, str(attempt.id), name)
            if data is None:
                raise _CandidateInvalid(f"missing {name}")
            files[name] = data
        try:
            changes = json.loads(files["candidate/changes.json"])
            evidence = json.loads(files["candidate/evidence.json"])
        except ValueError as error:
            raise _CandidateInvalid(f"candidate JSON does not parse: {error}") from error
        if not isinstance(changes, dict) or not isinstance(evidence, dict):
            raise _CandidateInvalid("candidate JSON is not an object")
        attempt_ids = {changes.get("attempt_id"), evidence.get("attempt_id")}
        if attempt_ids != {str(attempt.id)}:
            raise _CandidateInvalid(
                f"attempt_id mismatch: candidate names {sorted(map(str, attempt_ids))}, "
                f"attempt is {attempt.id}"
            )
        base_shas = {changes.get("base_sha"), evidence.get("base_sha")}
        if base_shas != {attempt.base_sha}:
            raise _CandidateInvalid(
                f"base_sha mismatch: candidate names {sorted(map(str, base_shas))}, "
                f"attempt was pinned at {attempt.base_sha}"
            )
        head_sha = changes.get("head_sha")
        if not isinstance(head_sha, str) or not head_sha.strip():
            raise _CandidateInvalid("changes.json has no head_sha")
        if evidence.get("head_sha") != head_sha:
            raise _CandidateInvalid("head_sha differs between changes.json and evidence.json")
        return _Candidate(
            bundle=files["candidate/candidate.bundle"],
            diff=files["candidate/candidate.diff"].decode("utf-8", errors="replace"),
            changes_json=files["candidate/changes.json"].decode("utf-8"),
            evidence_json=files["candidate/evidence.json"].decode("utf-8"),
            head_sha=head_sha,
        )

    async def _frozen_policy(self, attempt: HostedNativeAttempt) -> ConstructionPolicy:
        """The policy and test commands this attempt was told (``base/package.json``).

        ``base/`` is platform-written and never pushed back, so it is the record
        of what the worker saw; the review and the verifier judge against that,
        not against whatever the sources say now. Only when the file is missing
        or unreadable — a platform fault — does the round resolve afresh.
        """

        data = await self._reader.read(attempt.team_name, str(attempt.id), CONTROL_FILE)
        if data is not None:
            try:
                control = json.loads(data)
                return ConstructionPolicy(
                    policy=PathPolicy(
                        allowed_paths=tuple(control["allowed_paths"]),
                        denied_paths=tuple(control["denied_paths"]),
                    ),
                    test_commands=tuple(control["test_commands"]),
                )
            except (ValueError, KeyError, TypeError) as error:
                _logger.warning(
                    "attempt %s %s is not the control file the publisher writes (%s); "
                    "resolving the policy afresh",
                    attempt.id,
                    CONTROL_FILE,
                    error,
                )
        return await self._policies.resolve(
            attempt.task_id, worker_agent_id=attempt.worker_agent_id
        )

    async def _fence(
        self, attempt: HostedNativeAttempt, reason: str, now: datetime, **changes: object
    ) -> HostedNativeAttempt:
        fenced = attempt.with_phase(
            AttemptPhase.FENCED, at=now, fenced_at=now, fence_reason=reason, **changes
        )
        await self._attempts.save(fenced)
        return fenced

    async def _block_attempt(
        self,
        attempt: HostedNativeAttempt,
        task: TaskView,
        now: datetime,
        *,
        summary: str,
        **changes: object,
    ) -> HostedNativeAttempt:
        blocked = attempt.with_phase(AttemptPhase.BLOCKED, at=now, **changes)
        await self._attempts.save(blocked)
        await self._states.block(task.id, agent_id=attempt.worker_agent_id, summary=summary)
        _logger.info("hosted-native attempt %s blocked: %s", attempt.id, summary)
        return blocked

    async def _record(
        self,
        attempt: HostedNativeAttempt,
        kind: EventKind,
        marker: str,
        event: SharedTaskEvent,
        now: datetime,
    ) -> None:
        await self._attempts.record_event(
            HostedNativeEvent(
                id=uuid4(),
                attempt_id=attempt.id,
                kind=kind,
                marker=marker,
                payload={
                    "event_kind": event.kind.value,
                    "event_marker": event.marker,
                    "phase": attempt.phase.value,
                    "fence_reason": attempt.fence_reason,
                },
                observed_at=event.observed_at,
                applied_at=now,
            )
        )

    async def _hand_to_recovery(self, attempt: HostedNativeAttempt, reason: str) -> None:
        if self._recover is not None:
            await self._recover(attempt, reason)

    async def _hand_to_human(self, task: TaskView, reason: str) -> None:
        if self._escalate is not None:
            await self._escalate(task, reason)


def _describe(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def _fence_marker(event: SharedTaskEvent) -> str:
    return f"{event.kind.value}:{event.marker}"


def _stated_verdict(summary: str) -> ReviewVerdict | None:
    """The ``VERDICT: X`` the Leader wrote as the summary's first line, if any."""

    first = summary.strip().splitlines()[0].strip() if summary.strip() else ""
    if not first.upper().startswith("VERDICT:"):
        return None
    word = first.split(":", 1)[1].strip().split()
    if not word:
        return None
    try:
        return ReviewVerdict(word[0].upper().strip(".,;"))
    except ValueError:
        return None


def _applied(attempt: HostedNativeAttempt) -> RoundTransition:
    return RoundTransition(attempt.id, RoundOutcome.APPLIED, attempt.phase)


def _ignored(attempt_id: UUID, phase: AttemptPhase | None, reason: str) -> RoundTransition:
    return RoundTransition(attempt_id, RoundOutcome.IGNORED, phase, reason=reason)


__all__ = [
    "ATTEMPT_PAYLOAD_SCHEMA",
    "CANDIDATE_FILES",
    "HostedNativeRound",
    "HostedNativeRoundError",
]
