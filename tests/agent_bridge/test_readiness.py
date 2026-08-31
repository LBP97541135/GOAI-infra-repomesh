"""Short-lease readiness: what the process does, and what goes on the wire.

The first half goes through ``RoomNativeAgent.run`` for ``test_application``'s
reason: the claims are about the *startup order* and the *shape of the running
process* — that no member is announced ready before the platform can see it,
and that renewal is a peer of the room loop rather than something bolted onto
it — and a test that called the reporter directly would be verifying a
different claim. The second half is the HTTP adapter against
``httpx.MockTransport``, where the claims are about a translation table and
driving it through ``run`` would prove nothing extra at the cost of a fixture.

Two facts recur in the first half:

* "the platform was told" is the double's ``calls`` list, in order. Kinds
  rather than counters, because the interesting failures are orderings: a
  ``renew`` before a ``startup``, a ``shutdown`` that never arrives.
* renew periods here are milliseconds. The real lease is forty-five seconds and
  the period is a third of it; what these tests are about is the loop's shape,
  not the lease's duration, so the double answers in a fraction of a second and
  nothing sleeps through a real one.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from uuid import UUID

import httpx
import pytest

from repomesh_agent_bridge.adapters.memory import (
    InertCodingSession,
    InMemoryRoomPort,
    InMemoryWorkerBindingPort,
    MemoryReadinessReporter,
)
from repomesh_agent_bridge.adapters.readiness import RepoMeshReadinessReporter
from repomesh_agent_bridge.application import RoomNativeAgent
from repomesh_agent_bridge.contracts import (
    READINESS_SCHEMA_VERSION,
    BridgeStartupError,
    ExternalWorkerEnrollment,
    WorkerBinding,
)
from repomesh_agent_bridge.ports import ReadinessRejected

from .conftest import (
    REPOMESH_ENDPOINT,
    REPOMESH_TOKEN_REF,
    REPOMESH_TOKEN_VALUE,
    WORKER_AGENT_ID,
)

INSTANCE_ID = UUID("00000000-0000-0000-0000-0000000000e1")
"""This process, as the platform names it. Distinct from the worker's id on
purpose: a restart is a new instance of the same member."""

RENEW_AFTER = 0.01
"""The period the double hands back, in seconds. Small enough that a test which
waits for three renewals waits for thirty milliseconds."""


def _agent(
    binding: WorkerBinding,
    *,
    tmp_path: object,
    room: InMemoryRoomPort,
    readiness: MemoryReadinessReporter,
    session: InertCodingSession | None = None,
) -> RoomNativeAgent:
    return RoomNativeAgent(
        binding_port=InMemoryWorkerBindingPort(binding),
        room_port=room,
        coding_session=session or InertCodingSession(),
        readiness=readiness,
        state_dir=tmp_path,
    )


async def _until(condition: Callable[[], bool], *, timeout: float = 5) -> None:
    """Wait for something the renewal loop does, or fail the test in bounded time.

    Polled rather than signalled, because the signal would have to be a second
    interface on the double that only tests use. Bounded on purpose: a
    regression that stopped renewing would otherwise hang the suite, which
    reports nothing.
    """

    async def poll() -> None:
        while not condition():
            await asyncio.sleep(RENEW_AFTER / 2)

    await asyncio.wait_for(poll(), timeout=timeout)


async def test_a_member_the_platform_cannot_see_is_never_announced_ready(
    enrollment: ExternalWorkerEnrollment,
    binding: WorkerBinding,
    tmp_path,
    matrix_token: str,
    caplog,
) -> None:
    """FR-04's first half, stated as the line that was not printed.

    ``bridge ready`` is what an operator reads to learn this member is serving,
    and materialize refuses a member whose lease the platform never saw. A
    Bridge that printed the line anyway would have told the operator the
    opposite of what the control plane is about to say, so the report is
    blocking and its failure is a startup refusal like every other one.
    """

    caplog.set_level(logging.INFO)
    reporter = MemoryReadinessReporter(
        startup_failure=BridgeStartupError("RepoMesh refused the readiness report with 403")
    )
    room = InMemoryRoomPort()

    with pytest.raises(BridgeStartupError):
        await _agent(binding, tmp_path=tmp_path, room=room, readiness=reporter).run(enrollment)

    assert reporter.calls == ["startup"], "and nothing renewed a lease that was never taken"
    assert "bridge ready" not in caplog.text
    assert room.closed, "the exit stack unwound the seams the report was made behind"


async def test_a_reported_member_is_announced_ready_and_renews_through_a_failure(
    enrollment: ExternalWorkerEnrollment,
    binding: WorkerBinding,
    tmp_path,
    matrix_token: str,
    caplog,
) -> None:
    """FR-04's second half: the lease is kept alive, and one bad answer is not fatal.

    The renewal loop is a peer of the room loop, so a member that is serving
    keeps saying so without anything else in the process having to notice. A
    single failed renewal is the ordinary weather of a control plane behind a
    laptop's network — the lease outlives it by two thirds of the TTL — and a
    process that restarted or died on one would turn a blip into an outage the
    materialize gate can see.
    """

    caplog.set_level(logging.INFO)
    reporter = MemoryReadinessReporter(
        RuntimeError("RepoMesh answered 503"), renew_after_seconds=RENEW_AFTER
    )
    room = InMemoryRoomPort()
    task = asyncio.create_task(
        _agent(binding, tmp_path=tmp_path, room=room, readiness=reporter).run(enrollment)
    )

    await _until(lambda: reporter.calls.count("renew") >= 3)

    assert reporter.calls[0] == "startup", "the first report is made before anything is served"
    assert "bridge ready" in caplog.text
    assert not task.done(), "a failed renewal is tolerated, not escalated"
    assert "readiness renewal failed" in caplog.text, (
        "tolerated is not the same as silent: an operator watching a member that later "
        "goes stale needs the failures that led there to be in the log"
    )

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_the_loop_renews_on_the_period_the_server_last_named(
    enrollment: ExternalWorkerEnrollment,
    binding: WorkerBinding,
    tmp_path,
    matrix_token: str,
) -> None:
    """The period is the server's, and it is re-read rather than remembered.

    Every answer carries one, so a deployment that retunes its TTL retunes the
    fleet without restarting anything. A loop that kept the period its startup
    report returned would look identical until the day that number changed, and
    then would renew at the old rate against a shorter lease — so the claim is
    checked the only way it can be: the server names a far longer period at the
    first renewal, and the loop is then observed *not* renewing again.
    """

    reporter = MemoryReadinessReporter(renew_after_seconds=RENEW_AFTER, retuned_to=60)
    room = InMemoryRoomPort()
    task = asyncio.create_task(
        _agent(binding, tmp_path=tmp_path, room=room, readiness=reporter).run(enrollment)
    )

    await _until(lambda: "renew" in reporter.calls)
    # Four of the original periods. Had the loop kept the startup answer it
    # would have renewed at least three more times by now.
    await asyncio.sleep(RENEW_AFTER * 4)

    assert reporter.calls.count("renew") == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_superseded_instance_stops_instead_of_renewing_against_its_replacement(
    enrollment: ExternalWorkerEnrollment,
    binding: WorkerBinding,
    tmp_path,
    matrix_token: str,
) -> None:
    """The one readiness answer a Bridge acts on rather than reports.

    ``stale_instance`` means a newer process owns this member. Two Bridges
    renewing the same lease is the state where the platform's answer depends on
    which one reported last, so the older one ends — which is why this refusal
    is the renewal loop's single exception to "tolerate and wait".
    """

    reporter = MemoryReadinessReporter(
        ReadinessRejected("a newer instance holds this member"),
        renew_after_seconds=RENEW_AFTER,
    )
    room = InMemoryRoomPort()

    with pytest.raises(ReadinessRejected):
        await asyncio.wait_for(
            _agent(binding, tmp_path=tmp_path, room=room, readiness=reporter).run(enrollment),
            timeout=5,
        )

    assert room.closed, "the whole instance unwound, not just the loop that was refused"
    assert reporter.calls == ["startup", "renew", "shutdown"], (
        "the goodbye is still attempted; the replacement's lease is not this process's "
        "to reason about, and the server refuses a stale one anyway"
    )


async def test_a_clean_stop_says_goodbye_exactly_once(
    enrollment: ExternalWorkerEnrollment,
    binding: WorkerBinding,
    tmp_path,
    matrix_token: str,
) -> None:
    """FR-08: the goodbye is a courtesy that shortens the gap, never the mechanism.

    An operator stopping a Bridge should not leave the console showing a ready
    member for the rest of the TTL, so the first thing the exit stack does is
    say so. It is reported once, because the callback is registered once and
    against the one startup report that succeeded.
    """

    reporter = MemoryReadinessReporter()
    room = InMemoryRoomPort()
    task = asyncio.create_task(
        _agent(binding, tmp_path=tmp_path, room=room, readiness=reporter).run(enrollment)
    )
    await asyncio.wait_for(room.ready.wait(), timeout=2)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert reporter.calls == ["startup", "shutdown"]


async def test_a_goodbye_that_fails_does_not_turn_a_clean_stop_into_a_crash(
    enrollment: ExternalWorkerEnrollment,
    binding: WorkerBinding,
    tmp_path,
    matrix_token: str,
) -> None:
    """The other half of FR-08: the courtesy is never the mechanism.

    A process that was killed sends no goodbye at all and the platform copes,
    because the lease expires on its own. So a goodbye that could not be
    delivered has cost nothing that was not already accounted for — and letting
    it end the unwind would lose the seams the exit stack is in the middle of
    closing properly, over a message whose only job was to save the console
    forty-five seconds.
    """

    reporter = MemoryReadinessReporter(
        shutdown_failure=BridgeStartupError("RepoMesh could not be reached")
    )
    room = InMemoryRoomPort()
    session = InertCodingSession()
    task = asyncio.create_task(
        _agent(
            binding, tmp_path=tmp_path, room=room, readiness=reporter, session=session
        ).run(enrollment)
    )
    await asyncio.wait_for(room.ready.wait(), timeout=2)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert reporter.calls == ["startup", "shutdown"]
    assert room.closed and session.closed, "the unwind carried on past the refused goodbye"


# ---------------------------------------------------------------------------
# The wire: what the reporter sends, and what it makes of the answers
# ---------------------------------------------------------------------------


READINESS_URL = (
    f"https://repomesh.example.org/api/v1/runtime/v1/external-members/{WORKER_AGENT_ID}/readiness"
)


def _reporter(*answers: httpx.Response) -> tuple[RepoMeshReadinessReporter, list[httpx.Request]]:
    """A reporter over a scripted homeserver-shaped control plane.

    No server is started, for ``test_repomesh_binding``'s reason: what this
    adapter does is a translation table, and a translation table is fully
    determined by the response it is handed.
    """

    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return answers[min(len(requests) - 1, len(answers) - 1)]

    return (
        RepoMeshReadinessReporter(
            endpoint=REPOMESH_ENDPOINT,
            member_agent_id=UUID(WORKER_AGENT_ID),
            resolve_credential=lambda ref: REPOMESH_TOKEN_VALUE,
            credential_ref=REPOMESH_TOKEN_REF,
            instance_id=INSTANCE_ID,
            role="worker",
            leader_lane=False,
            governed_lane=True,
            workspace_root="C:/work/pricing",
            transport=httpx.MockTransport(handle),
        ),
        requests,
    )


async def test_a_report_is_the_frozen_document_under_the_members_own_credential() -> None:
    """The body is the whole claim, so every field of it is pinned here.

    ``kind`` is the only thing that varies between the three calls; everything
    else was settled when the process was assembled, which is what makes the
    report checkable against the directory on the other side.
    """

    reporter, requests = _reporter(
        httpx.Response(200, json={"renewAfterSeconds": 15, "status": "ready"})
    )

    assert await reporter.report_startup() == 15

    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == READINESS_URL
    assert request.headers["Authorization"] == f"Bearer {REPOMESH_TOKEN_VALUE}"
    assert json.loads(request.content) == {
        "schema": READINESS_SCHEMA_VERSION,
        "instanceId": str(INSTANCE_ID),
        "kind": "startup",
        "role": "worker",
        "leaderLane": False,
        "governedLane": True,
        "workspaceRoot": "C:/work/pricing",
    }


async def test_the_stale_instance_refusal_is_the_one_a_bridge_acts_on() -> None:
    """Matched on the server's code, never on its sentence.

    The endpoint puts a machine-readable ``code`` on exactly this refusal
    because it is the only one a client is expected to change its behaviour
    over, and a client that recognised it by prose would come to do that by
    accident.
    """

    reporter, _ = _reporter(
        httpx.Response(
            409,
            json={"detail": {"code": "stale_instance", "message": "a newer instance reported"}},
        )
    )

    with pytest.raises(ReadinessRejected):
        await reporter.report_renew()


async def test_every_other_refusal_is_an_ordinary_startup_error() -> None:
    """The same status, a different shape, and deliberately a different answer.

    This endpoint answers 409 for two things and only wraps one of them in an
    object: a report whose lanes disagree with the directory arrives as a plain
    sentence and does *not* mean this instance has been replaced, so treating
    every 409 as a takeover would stop a Bridge that should have kept serving
    and told its operator what was actually wrong.
    """

    reporter, _ = _reporter(
        httpx.Response(409, json={"detail": "governedLane disagrees with this member's enrolment"})
    )

    with pytest.raises(BridgeStartupError) as refused:
        await reporter.report_startup()

    assert not isinstance(refused.value, ReadinessRejected)


async def test_a_409_nobody_can_vouch_for_is_a_refusal_rather_than_a_takeover() -> None:
    """A proxy answering 409 with HTML names no instance, so it is not one.

    The shape read is the discrimination, and it has to survive a body that is
    not JSON at all: reading ``detail`` off one would raise out of the *first*
    branch, before the ordinary refusal below it could be reached, and hand the
    caller a ``JSONDecodeError`` where the port's vocabulary was promised.
    """

    reporter, _ = _reporter(httpx.Response(409, text="<html>gateway conflict</html>"))

    with pytest.raises(BridgeStartupError) as refused:
        await reporter.report_renew()

    assert not isinstance(refused.value, ReadinessRejected), (
        "an unreadable 409 is not evidence that anything replaced this instance"
    )


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(httpx.Response(200, text="<html>sign in to the wifi</html>"), id="not-json"),
        pytest.param(httpx.Response(200, json={"status": "ready"}), id="no-renew-period"),
        pytest.param(httpx.Response(200, json=["ready"]), id="not-an-object"),
    ],
)
async def test_an_answer_that_is_not_a_receipt_reaches_the_clis_exit_mapping(
    answer: httpx.Response,
) -> None:
    """A 2xx this process cannot read is not a receipt, and no retry makes it one.

    The captive portal is the case that matters: it answers 200 with a login
    page, and an operator running ``repomesh-agent-bridge run`` on hotel wifi
    should get the one line ``main`` promises rather than a traceback out of the
    JSON decoder. Asserting the *family* is what pins that — ``BridgeStartupError``
    is precisely what the CLI maps onto exit 2.
    """

    reporter, _ = _reporter(answer)

    with pytest.raises(BridgeStartupError):
        await reporter.report_startup()
