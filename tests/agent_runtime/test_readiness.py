"""The readiness lease store and the report use case, driven by a hand-run clock.

Two subjects in one file because they are two halves of one rule: the store
decides what a lease *is* — when it slides, when it is taken over, when it goes
stale — and the use case decides who is allowed to write one at all. Splitting
them would leave "a refused report writes nothing" untestable in either half.

Time is injected rather than slept through. Every status boundary in this file
is asserted at the exact instant it turns, which a real clock cannot do at all
for a 45 second lease and cannot do reliably for a shorter one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from integrations.agentteams.fakes import StubDirectory

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.agent_runtime.application.readiness import (
    ExternalMemberReadinessStatus,
    ExternalMemberReadinessStore,
    ReadinessRefused,
    ReadinessReportKind,
    ReportExternalMemberReadiness,
    ReportExternalMemberReadinessCommand,
    StaleInstance,
)
from repomesh.modules.agent_runtime.contracts import ExternalMemberRole, UnknownExternalWorker

TTL_SECONDS = 45
WORKSPACE_ROOT = Path(".repomesh-workspaces")
START = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


class FakeClock:
    """A clock the tests move by hand, in the shape the store injects."""

    def __init__(self, start: datetime = START) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def build_store(clock: FakeClock | None = None) -> tuple[ExternalMemberReadinessStore, FakeClock]:
    clock = FakeClock() if clock is None else clock
    return ExternalMemberReadinessStore(ttl_seconds=TTL_SECONDS, now=clock), clock


def command(
    member_agent_id: UUID,
    *,
    instance_id: UUID,
    kind: ReadinessReportKind = ReadinessReportKind.STARTUP,
    role: ExternalMemberRole = ExternalMemberRole.WORKER,
    leader_lane: bool = False,
    governed_lane: bool = True,
    workspace_root: str | None = str(WORKSPACE_ROOT),
) -> ReportExternalMemberReadinessCommand:
    """A well-formed worker report; every test names only what it is about."""

    return ReportExternalMemberReadinessCommand(
        member_agent_id=member_agent_id,
        instance_id=instance_id,
        kind=kind,
        role=role,
        leader_lane=leader_lane,
        governed_lane=governed_lane,
        workspace_root=workspace_root,
    )


def leader_command(
    member_agent_id: UUID, *, instance_id: UUID, **overrides
) -> ReportExternalMemberReadinessCommand:
    """The leader's mirror of :func:`command`: lane on, no governed lane, no workspace."""

    return command(
        member_agent_id,
        instance_id=instance_id,
        role=ExternalMemberRole.REPOSITORY_LEADER,
        **{"leader_lane": True, "governed_lane": False, "workspace_root": None, **overrides},
    )


# ---------------------------------------------------------------------------
# The store: what a lease is
# ---------------------------------------------------------------------------


async def test_a_startup_report_writes_the_lease() -> None:
    store, clock = build_store()
    member, instance = uuid4(), uuid4()

    await store.startup(command(member, instance_id=instance))

    (view,) = await store.snapshot()
    assert view.member_agent_id == member
    assert view.status is ExternalMemberReadinessStatus.READY
    assert view.role is ExternalMemberRole.WORKER
    assert view.governed_lane is True
    assert view.reported_at == clock.now
    assert view.expires_at == clock.now + timedelta(seconds=TTL_SECONDS)
    assert view.stopped_at is None


async def test_a_startup_report_takes_over_from_a_stopped_instance() -> None:
    """A newer process is always the truth: the host-side lock forbids two.

    The interesting half is ``stopped_at``. A member that reported shutdown and
    then started again would otherwise stay offline forever behind a lease that
    is being renewed, which is the shape of a member nobody can dispatch to.
    """

    store, _ = build_store()
    member, first, second = uuid4(), uuid4(), uuid4()
    await store.startup(command(member, instance_id=first))
    await store.shutdown(member, instance_id=first)

    await store.startup(command(member, instance_id=second))

    (view,) = await store.snapshot()
    assert view.status is ExternalMemberReadinessStatus.READY
    assert view.stopped_at is None


async def test_a_renew_without_a_row_inserts_one() -> None:
    """How a backend restart heals itself: the lease table is in memory.

    Refusing a renew nobody has a row for would leave every member blocked
    until it happened to restart. Inserting means the whole roster is back
    within one renew period.
    """

    store, clock = build_store()
    member, instance = uuid4(), uuid4()

    await store.renew(command(member, instance_id=instance, kind=ReadinessReportKind.RENEW))

    (view,) = await store.snapshot()
    assert view.status is ExternalMemberReadinessStatus.READY
    assert view.expires_at == clock.now + timedelta(seconds=TTL_SECONDS)


async def test_a_renew_from_the_same_instance_slides_the_expiry() -> None:
    store, clock = build_store()
    member, instance = uuid4(), uuid4()
    await store.startup(command(member, instance_id=instance))
    clock.advance(TTL_SECONDS - 1)

    await store.renew(command(member, instance_id=instance, kind=ReadinessReportKind.RENEW))

    (view,) = await store.snapshot()
    assert view.expires_at == clock.now + timedelta(seconds=TTL_SECONDS)
    assert view.reported_at == clock.now


async def test_a_renew_after_shutdown_slides_the_expiry_but_stays_offline() -> None:
    """The row a renew writes is answered as it reads, not as it was asked for.

    A renew landing after the same instance's shutdown is a timer that fired
    during teardown. It is not refused, and it does not revive the member — only
    a startup does that — so the view the store answers must already say
    ``offline``, or the reporter and the console are told two different things.
    """

    store, clock = build_store()
    member, instance = uuid4(), uuid4()
    await store.startup(command(member, instance_id=instance))
    await store.shutdown(member, instance_id=instance)
    clock.advance(1)

    applied = await store.renew(
        command(member, instance_id=instance, kind=ReadinessReportKind.RENEW)
    )

    assert applied.status is ExternalMemberReadinessStatus.OFFLINE
    assert applied.expires_at == clock.now + timedelta(seconds=TTL_SECONDS)
    assert applied.stopped_at is not None


async def test_a_renew_from_another_instance_is_refused() -> None:
    """AC-05: the process that was replaced must not overwrite the one that replaced it.

    The clock moves first, so a lease the ghost had slid would be visible: the
    expiry is asserted to be the one the live instance wrote, not a later one.
    """

    store, clock = build_store()
    member, live, ghost = uuid4(), uuid4(), uuid4()
    await store.startup(command(member, instance_id=live))
    written_at = clock.now
    clock.advance(10)

    with pytest.raises(StaleInstance):
        await store.renew(command(member, instance_id=ghost, kind=ReadinessReportKind.RENEW))

    (view,) = await store.snapshot()
    assert view.expires_at == written_at + timedelta(seconds=TTL_SECONDS)


async def test_a_shutdown_report_stops_the_lease_before_it_expires() -> None:
    store, clock = build_store()
    member, instance = uuid4(), uuid4()
    await store.startup(command(member, instance_id=instance))

    await store.shutdown(member, instance_id=instance)

    (view,) = await store.snapshot()
    assert view.status is ExternalMemberReadinessStatus.OFFLINE
    assert view.stopped_at == clock.now
    assert clock.now < view.expires_at


async def test_a_shutdown_report_for_a_member_with_no_lease_is_a_no_op() -> None:
    store, clock = build_store()

    ended_at = await store.shutdown(uuid4(), instance_id=uuid4())

    assert ended_at == clock.now
    assert await store.snapshot() == ()


async def test_a_shutdown_report_from_another_instance_is_refused() -> None:
    store, _ = build_store()
    member, live, ghost = uuid4(), uuid4(), uuid4()
    await store.startup(command(member, instance_id=live))

    with pytest.raises(StaleInstance):
        await store.shutdown(member, instance_id=ghost)

    (view,) = await store.snapshot()
    assert view.status is ExternalMemberReadinessStatus.READY


async def test_a_lease_walks_ready_then_stale_then_offline() -> None:
    """The status table, asserted on both sides of each boundary.

    ``stale`` is a full TTL wide and exists to tell an operator the difference
    between "this member is late" and "this member is gone". Only ``ready``
    ever passes the gate, so the two later states differ in what the console
    says, not in what work is dispatched.
    """

    store, clock = build_store()
    member = uuid4()
    await store.startup(command(member, instance_id=uuid4()))

    async def status() -> ExternalMemberReadinessStatus:
        (view,) = await store.snapshot()
        return view.status

    clock.advance(TTL_SECONDS - 1)
    assert await status() is ExternalMemberReadinessStatus.READY
    clock.advance(1)
    assert await status() is ExternalMemberReadinessStatus.STALE
    clock.advance(TTL_SECONDS - 1)
    assert await status() is ExternalMemberReadinessStatus.STALE
    clock.advance(1)
    assert await status() is ExternalMemberReadinessStatus.OFFLINE


async def test_the_snapshot_is_ordered_by_member() -> None:
    """The console renders the snapshot as a table, so its order is the store's."""

    store, _ = build_store()
    members = [UUID(int=3), UUID(int=1), UUID(int=2)]
    for member in members:
        await store.startup(command(member, instance_id=uuid4()))

    assert [view.member_agent_id for view in await store.snapshot()] == sorted(members)


# ---------------------------------------------------------------------------
# The use case: who may write one
# ---------------------------------------------------------------------------


def principal(
    member_agent_id: UUID, *, role: AgentRole = AgentRole.WORKER
) -> AgentPrincipalView:
    return AgentPrincipalView(
        id=member_agent_id,
        organization_id=uuid4(),
        role=role,
        leader_agent_id=None,
        repository_id=None,
        responsibility_paths=(),
        agentteams_resource_name=f"readiness-{role.value}",
        status=AgentPrincipalStatus.ACTIVE,
    )


def build_use_case(
    *principals: AgentPrincipalView,
) -> tuple[ReportExternalMemberReadiness, ExternalMemberReadinessStore]:
    store, _ = build_store()
    return (
        ReportExternalMemberReadiness(
            StubDirectory(*principals), store, workspace_root=WORKSPACE_ROOT
        ),
        store,
    )


async def test_a_worker_startup_is_accepted_and_carries_the_renew_hint() -> None:
    member = uuid4()
    report, store = build_use_case(principal(member))

    receipt = await report.execute(command(member, instance_id=uuid4()))

    assert receipt.member_agent_id == member
    assert receipt.status is ExternalMemberReadinessStatus.READY
    assert receipt.renew_after_seconds == TTL_SECONDS // 3
    assert (await store.snapshot())[0].status is ExternalMemberReadinessStatus.READY


async def test_a_leader_startup_is_accepted() -> None:
    member = uuid4()
    report, store = build_use_case(principal(member, role=AgentRole.REPOSITORY_LEADER))

    receipt = await report.execute(leader_command(member, instance_id=uuid4()))

    assert receipt.status is ExternalMemberReadinessStatus.READY
    assert (await store.snapshot())[0].role is ExternalMemberRole.REPOSITORY_LEADER


async def test_a_shutdown_receipt_says_offline() -> None:
    member = uuid4()
    report, _ = build_use_case(principal(member))
    instance = uuid4()
    await report.execute(command(member, instance_id=instance))

    receipt = await report.execute(
        command(member, instance_id=instance, kind=ReadinessReportKind.SHUTDOWN)
    )

    assert receipt.status is ExternalMemberReadinessStatus.OFFLINE


async def test_a_member_the_directory_does_not_hold_is_unknown() -> None:
    member = uuid4()
    report, store = build_use_case()

    with pytest.raises(UnknownExternalWorker):
        await report.execute(command(member, instance_id=uuid4()))

    assert await store.snapshot() == ()


async def test_a_report_that_claims_the_other_role_is_refused() -> None:
    """The directory is the truth; the report is a claim, as in v2 preflight."""

    member = uuid4()
    report, store = build_use_case(principal(member, role=AgentRole.REPOSITORY_LEADER))

    with pytest.raises(ReadinessRefused):
        await report.execute(command(member, instance_id=uuid4()))

    assert await store.snapshot() == ()


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"leader_lane": False}, id="no leader lane"),
        pytest.param({"governed_lane": True}, id="governed lane"),
        pytest.param({"workspace_root": str(WORKSPACE_ROOT)}, id="a code workspace"),
    ],
)
async def test_a_leader_that_reports_a_worker_capability_is_refused(overrides: dict) -> None:
    """A Repository Leader decides; it does not run code, and says so.

    Each of the three is a different lie about the same process: one that never
    entered the leader lane, one that also entered the governed lane, and one
    that opened a workspace. Any of them means the Bridge is not the thing this
    principal is on file as.
    """

    member = uuid4()
    report, store = build_use_case(principal(member, role=AgentRole.REPOSITORY_LEADER))

    with pytest.raises(ReadinessRefused):
        await report.execute(leader_command(member, instance_id=uuid4(), **overrides))

    assert await store.snapshot() == ()


async def test_a_worker_outside_the_governed_lane_is_refused() -> None:
    member = uuid4()
    report, store = build_use_case(principal(member))

    with pytest.raises(ReadinessRefused):
        await report.execute(command(member, instance_id=uuid4(), governed_lane=False))

    assert await store.snapshot() == ()


@pytest.mark.parametrize(
    "reported",
    [pytest.param(None, id="none"), pytest.param("somewhere-else", id="another root")],
)
async def test_a_worker_under_another_workspace_root_is_refused(reported: str | None) -> None:
    member = uuid4()
    report, store = build_use_case(principal(member))

    with pytest.raises(ReadinessRefused):
        await report.execute(command(member, instance_id=uuid4(), workspace_root=reported))

    assert await store.snapshot() == ()


async def test_a_worker_root_spelled_differently_is_the_same_root() -> None:
    """Compared as paths, because the two sides spell it in two languages.

    The reporter's value comes back through PowerShell and a ``.env`` file,
    which disagree with this deployment's setting about separators and trailing
    slashes while naming the same directory. A string comparison would refuse
    every Windows Bridge.
    """

    member = uuid4()
    report, store = build_use_case(principal(member))

    receipt = await report.execute(
        command(member, instance_id=uuid4(), workspace_root=f"./{WORKSPACE_ROOT}/")
    )

    assert receipt.status is ExternalMemberReadinessStatus.READY
    assert len(await store.snapshot()) == 1


async def test_an_organization_leader_may_not_report_readiness() -> None:
    """D-11 again: it stays on the AgentTeams Manager, so it has no Bridge."""

    member = uuid4()
    report, store = build_use_case(principal(member, role=AgentRole.ORGANIZATION_LEADER))

    with pytest.raises(ReadinessRefused):
        await report.execute(command(member, instance_id=uuid4()))

    assert await store.snapshot() == ()


async def test_a_refused_renew_leaves_the_lease_with_its_owner() -> None:
    """The fence, reached through the use case rather than the store.

    Ownership is asserted by exercising it: after the ghost is refused, the
    instance that holds the lease renews it without a fight.
    """

    member = uuid4()
    report, _ = build_use_case(principal(member))
    live = uuid4()
    await report.execute(command(member, instance_id=live))

    with pytest.raises(StaleInstance):
        await report.execute(command(member, instance_id=uuid4(), kind=ReadinessReportKind.RENEW))

    receipt = await report.execute(
        command(member, instance_id=live, kind=ReadinessReportKind.RENEW)
    )
    assert receipt.status is ExternalMemberReadinessStatus.READY
