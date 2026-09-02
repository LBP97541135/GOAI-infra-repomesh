"""The gate materialize asks before it hands anybody work (AC-03, AC-04).

Three sources have to agree before a member is called ready, and each of them
can say something the other two do not: the directory knows who the member is,
the AgentTeams control plane knows whether RepoMesh runs its body, and the
lease knows whether the body it does not run is running *now*. Every case below
is a disagreement between two of them, so all three are driven together.

Time is injected the way ``test_readiness`` injects it. A lease turns stale at
a known instant, and a test that has to sleep through forty-five seconds cannot
observe that instant at all.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from integrations.agentteams.fakes import StubDirectory

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.agent_runtime.application.readiness import (
    ExternalMemberReadinessStore,
    ReadinessReportKind,
    ReportExternalMemberReadinessCommand,
    RequireExternalMembersReady,
)
from repomesh.modules.agent_runtime.contracts import ExternalMemberRole
from repomesh.modules.agent_runtime.ports.agent_team import (
    WorkerControlPlaneUnavailable,
    WorkerRuntimeRef,
)

ORGANIZATION = uuid4()
TTL_SECONDS = 45

#: The clock and store builders are spelled again here rather than imported
#: from ``test_readiness``: ``tests/agent_runtime`` is not a package, so a
#: sibling import would depend on pytest's rootdir insertion order.


class FakeClock:
    """A clock the tests move by hand, in the shape the store injects."""

    def __init__(self) -> None:
        self.now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def build_store() -> tuple[ExternalMemberReadinessStore, FakeClock]:
    clock = FakeClock()
    return ExternalMemberReadinessStore(ttl_seconds=TTL_SECONDS, now=clock), clock


def principal(role: AgentRole = AgentRole.WORKER) -> AgentPrincipalView:
    """One directory row, named the way the AgentTeams projection names it."""

    agent_id = uuid4()
    return AgentPrincipalView(
        id=agent_id,
        organization_id=ORGANIZATION,
        role=role,
        leader_agent_id=None,
        repository_id=uuid4(),
        responsibility_paths=(),
        agentteams_resource_name=f"repomesh-worker-{agent_id.hex}",
        status=AgentPrincipalStatus.ACTIVE,
    )


class StubControlPlane:
    """``WorkerBindingReader``'s one read, and the one field the gate looks at.

    ``containerManaged`` is the whole difference between the two fleets this
    file drives: a managed one, whose bodies the controller runs and whose
    liveness the gate never claims, and an external one, where every body is an
    operator's own CLI and every member has to hold a lease.

    ``unprovisioned`` names workers the controller has never seen, which is a
    third answer and not a variant of either.
    """

    def __init__(
        self, *, managed: Iterable[str] = (), unprovisioned: Iterable[str] = ()
    ) -> None:
        self._managed = frozenset(managed)
        self._unprovisioned = frozenset(unprovisioned)
        self.asked: list[str] = []

    async def get_worker(self, name: str) -> WorkerRuntimeRef | None:
        self.asked.append(name)
        if name in self._unprovisioned:
            return None
        return WorkerRuntimeRef(
            name=name, phase="Running", container_managed=name in self._managed
        )


def build_gate(
    *principals: AgentPrincipalView,
    control_plane: StubControlPlane | None = None,
    store: ExternalMemberReadinessStore | None = None,
) -> RequireExternalMembersReady:
    return RequireExternalMembersReady(
        StubDirectory(*principals),
        control_plane if control_plane is not None else StubControlPlane(),
        store if store is not None else build_store()[0],
    )


async def report_startup(
    store: ExternalMemberReadinessStore, member: AgentPrincipalView, *, instance: UUID | None = None
) -> None:
    """The member's Bridge saying it is up, in the role the directory holds."""

    leader = member.role is AgentRole.REPOSITORY_LEADER
    await store.startup(
        ReportExternalMemberReadinessCommand(
            member_agent_id=member.id,
            instance_id=instance or uuid4(),
            kind=ReadinessReportKind.STARTUP,
            role=(
                ExternalMemberRole.REPOSITORY_LEADER if leader else ExternalMemberRole.WORKER
            ),
            leader_lane=leader,
            governed_lane=not leader,
            workspace_root=None if leader else ".repomesh-workspaces",
        )
    )


def statuses(facts) -> dict[UUID, str]:
    return {fact.agent_id: fact.status for fact in facts}


# ---------------------------------------------------------------------------
# A member that never reported
# ---------------------------------------------------------------------------


async def test_a_member_that_never_reported_is_offline_and_says_which_absence() -> None:
    """"No readiness report" and "the lease ran out" are different next actions.

    The first means the CLI was never launched; the second means it was and has
    stopped answering. Only the first is fixed by starting the process, which
    is the whole of AC-03's remedy, so the reason has to distinguish them.
    """

    member = principal()
    gate = build_gate(member)

    (fact,) = await gate.check([member.id])

    assert fact.agent_id == member.id
    assert fact.role == "worker"
    assert fact.status == "offline"
    assert fact.reason == "no readiness report"


async def test_a_repository_leader_is_reported_in_its_own_role() -> None:
    """The role comes from the directory, never from the lease the member wrote.

    A panel offering "start this member" has to name what to start, and a
    leader's Bridge and a worker's are launched differently (ADR 0004). Reading
    it off the report would let a member decide how it is described.
    """

    leader = principal(AgentRole.REPOSITORY_LEADER)
    gate = build_gate(leader)

    (fact,) = await gate.check([leader.id])

    assert fact.role == "repository_leader"


# ---------------------------------------------------------------------------
# A whole fleet, up and down
# ---------------------------------------------------------------------------


async def test_a_fleet_that_all_reported_is_all_ready() -> None:
    store, _clock = build_store()
    members = [principal(AgentRole.REPOSITORY_LEADER)] + [principal() for _ in range(5)]
    for member in members:
        await report_startup(store, member)
    gate = build_gate(*members, store=store)

    facts = await gate.check([member.id for member in members])

    assert len(facts) == 6
    assert set(statuses(facts).values()) == {"ready"}


async def test_a_fleet_that_all_stopped_is_all_refused() -> None:
    """Six members that said goodbye: six facts, and not one of them green.

    Asserted over the whole set rather than over one member, because the gate's
    failure mode is a member it forgot to look at — which a single-member test
    passes happily.
    """

    store, _clock = build_store()
    members = [principal() for _ in range(6)]
    instances = [uuid4() for _ in members]
    for member, instance in zip(members, instances, strict=True):
        await report_startup(store, member, instance=instance)
    for member, instance in zip(members, instances, strict=True):
        await store.shutdown(member.id, instance_id=instance)
    gate = build_gate(*members, store=store)

    facts = await gate.check([member.id for member in members])

    assert len(facts) == 6
    assert set(statuses(facts).values()) == {"offline"}


async def test_one_stopped_member_of_six_is_the_only_one_the_gate_names() -> None:
    """The refusal has to be actionable, which means it must not over-report.

    A gate that answered "the fleet is not ready" would send an operator round
    six machines. Five of these are up and one is not, and the answer says so.
    """

    store, _clock = build_store()
    members = [principal() for _ in range(6)]
    stopped, instance = members[3], uuid4()
    for member in members:
        await report_startup(store, member, instance=instance if member is stopped else None)
    await store.shutdown(stopped.id, instance_id=instance)
    gate = build_gate(*members, store=store)

    facts = await gate.check([member.id for member in members])

    not_ready = [fact for fact in facts if fact.status != "ready"]
    assert [fact.agent_id for fact in not_ready] == [stopped.id]
    assert len(facts) == 6


async def test_a_lease_that_has_expired_does_not_pass_as_ready() -> None:
    """``stale`` is not a third kind of green — only ``ready`` opens the gate.

    Observed at the instant the lease turns rather than after it, because the
    boundary is the assertion: a test that advanced two TTLs would pass against
    a gate that treated ``stale`` as ready right up until it went offline.
    """

    store, clock = build_store()
    member = principal()
    await report_startup(store, member)
    gate = build_gate(member, store=store)

    clock.advance(TTL_SECONDS)

    (fact,) = await gate.check([member.id])
    assert fact.status == "stale"
    assert fact.reason


# ---------------------------------------------------------------------------
# Who the gate is not about (AC-04)
# ---------------------------------------------------------------------------


async def test_a_managed_member_is_left_out_of_the_answer_entirely() -> None:
    """Omitted, not reported ready — the difference is who owns the claim.

    The controller runs a managed member's container and restarts it; its
    liveness is the controller's fact, and a green one invented here would be
    this gate's opinion about a process it cannot see. Absence says nothing,
    which is the only honest thing to say.
    """

    managed, external = principal(), principal()
    control_plane = StubControlPlane(managed=[managed.agentteams_resource_name])
    gate = build_gate(managed, external, control_plane=control_plane)

    facts = await gate.check([managed.id, external.id])

    assert [fact.agent_id for fact in facts] == [external.id]
    # Both were asked about: the omission is the controller's answer, not a
    # member the gate skipped before looking.
    assert control_plane.asked == [
        managed.agentteams_resource_name,
        external.agentteams_resource_name,
    ]


async def test_a_member_the_controller_has_never_seen_is_not_this_gates_business() -> None:
    """No worker document is no confirmation of ``containerManaged: false``.

    The same stance ``ResolveExternalMemberBinding`` takes: a member is external
    only when the controller says so, and everything else — managed, unknown,
    unprovisioned — is a member whose readiness this gate does not report on.
    Treating an absent document as external would refuse every round in a
    deployment that has simply not projected its topology yet.
    """

    unprovisioned, external = principal(), principal()
    control_plane = StubControlPlane(
        unprovisioned=[unprovisioned.agentteams_resource_name]
    )
    gate = build_gate(unprovisioned, external, control_plane=control_plane)

    facts = await gate.check([unprovisioned.id, external.id])

    assert [fact.agent_id for fact in facts] == [external.id]


# ---------------------------------------------------------------------------
# The deployment that cannot answer at all
# ---------------------------------------------------------------------------


async def test_an_unconfigured_control_plane_refuses_rather_than_admitting_everyone() -> None:
    """Fail closed, exactly as the binding endpoints' 503 does.

    Without a control plane there is no way to tell an external member from a
    managed one, so the empty tuple that would let the round through is
    indistinguishable from "this fleet is all managed" — and one of those two
    readings starts work nobody will pick up.
    """

    member = principal()
    gate = RequireExternalMembersReady(StubDirectory(member), None, build_store()[0])

    with pytest.raises(WorkerControlPlaneUnavailable):
        await gate.check([member.id])


async def test_an_empty_member_set_asks_nobody_anything() -> None:
    """A plan whose repositories have no teams yet is not a refusal.

    And it must not become one in a deployment without a control plane either:
    there is nothing to confirm, so there is nothing the missing controller
    would have been asked.
    """

    gate = RequireExternalMembersReady(StubDirectory(), None, build_store()[0])

    assert await gate.check([]) == ()
