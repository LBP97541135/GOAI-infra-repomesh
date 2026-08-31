"""Is this external member's Bridge alive right now, and may it say so?

An external member's body runs on an operator's own machine (ADR 0004), which
is the one place RepoMesh cannot see. Everything else about such a member is a
durable fact — it is provisioned, it is bound, it owns these rooms — and none of
those facts answer the only question a dispatcher has: *is the process running*.
This module is that answer, and it is deliberately the weakest kind of fact in
the codebase: a lease that expires unless somebody keeps saying so.

Three decisions shape the whole module.

*The lease lives in memory and nowhere else.* Forty-five seconds is shorter than
any deployment event, so a row that survived a restart would be a claim about a
process that RepoMesh has not heard from since — exactly the false green the
lease exists to prevent. Losing the table on restart costs one renew period of
fail-closed refusals and then heals itself, because :meth:`renew` inserts. That
is a better failure than a stale row, and it is why there is no migration here.

*Status is derived at read time, never written.* Nothing sweeps the table and
nothing schedules an expiry: a row plus a clock is enough to say ``ready``,
``stale`` or ``offline``, so there is no background task whose failure would
leave a member green forever. ``stale`` is not a third kind of green — only
``ready`` passes the gate — it exists so the console can tell an operator the
difference between "late" and "gone".

*One process may hold one member's lease.* The reporter names its
``instance_id``, and a ``renew`` or ``shutdown`` from any other one is refused
rather than applied. Startup is the exception and takes over unconditionally:
the host-side instance lock already prevents a genuine double-run, so a startup
from an unfamiliar instance is a newer process, not an impostor — while a
*renew* from one is the opposite, an old process that has not noticed it was
replaced (AC-05). Applying it would extend the lease of something nobody is
listening to.

The report use case sits in front of the store and is the only writer. It checks
that the reporter is a principal RepoMesh knows, that the role it claims is the
role on file, and that the capabilities it reports are the ones its role is
allowed to have — a leader that reports a code workspace, or a worker that
reports no governed lane, is not the process this principal is on file as. All
of it happens before anything is written, so a refused report leaves no row.

:class:`RequireExternalMembersReady` is the reader that gives the lease its
point. The console's status board merely renders it; this is what materialization
asks before it turns a plan into somebody's task, and the only place an answer
of "no" stops something from happening.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from repomesh.modules.agent_directory.contracts import AgentPrincipalReader, AgentPrincipalView
from repomesh.modules.agent_runtime.application.external_worker import MEMBER_ROLES
from repomesh.modules.agent_runtime.contracts import ExternalMemberRole, UnknownExternalWorker
from repomesh.modules.agent_runtime.ports.agent_team import (
    WorkerBindingReader,
    WorkerControlPlaneUnavailable,
)


class ReadinessRefused(RuntimeError):
    """The reporter exists, and what it reported is not a readiness it may hold.

    One type for every capability refusal — a role that disagrees with the
    directory, a leader carrying a workspace, a worker outside the governed
    lane, an Organization Leader reporting at all — because the answer to the
    caller is the same in every case: no lease. Only the message differs.
    """


class StaleInstance(RuntimeError):
    """A process that no longer holds this member's lease tried to write it.

    Separate from :class:`ReadinessRefused` on purpose, though both are 409s.
    This is the only refusal a Bridge *acts* on rather than logs: it means the
    member has been taken over by a newer process, so the right response is to
    stop renewing and exit, and a client should not have to match on prose to
    decide that.
    """


class ExternalMemberReadinessStatus(StrEnum):
    """What the lease says about a member at the instant it is read."""

    READY = "ready"
    STALE = "stale"
    OFFLINE = "offline"


class ReadinessReportKind(StrEnum):
    """The three things a Bridge has to say about its own lifetime."""

    STARTUP = "startup"
    RENEW = "renew"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True, slots=True)
class ReportExternalMemberReadinessCommand:
    """One readiness report, as the member states it about itself.

    Every field except ``member_agent_id`` is the reporter's claim, which is
    why the use case checks rather than stores most of them. ``workspace_root``
    is the reporter's own machine's path and is kept only long enough to be
    compared with this deployment's setting; it is never answered back on any
    endpoint.
    """

    member_agent_id: UUID
    instance_id: UUID
    kind: ReadinessReportKind
    role: ExternalMemberRole
    leader_lane: bool
    governed_lane: bool
    workspace_root: str | None


@dataclass(frozen=True, slots=True)
class ExternalMemberReadinessLease:
    """One member's row: who reported, what it can do, and until when."""

    instance_id: UUID
    role: ExternalMemberRole
    leader_lane: bool
    governed_lane: bool
    workspace_root: str | None
    reported_at: datetime
    expires_at: datetime
    stopped_at: datetime | None


@dataclass(frozen=True, slots=True)
class ExternalMemberReadinessView:
    """One row as the console reads it: the row's facts plus the derived status.

    Deliberately without ``instance_id`` and ``workspace_root``. The first is a
    process identity that only the fence uses, and the second is a path on the
    operator's own machine — neither is something a console renders, and a
    readiness list is not a place to publish either.
    """

    member_agent_id: UUID
    status: ExternalMemberReadinessStatus
    role: ExternalMemberRole
    leader_lane: bool
    governed_lane: bool
    reported_at: datetime
    expires_at: datetime
    stopped_at: datetime | None

    def to_wire(self) -> dict[str, object]:
        return {
            "agentId": str(self.member_agent_id),
            "status": self.status.value,
            "role": self.role.value,
            "leaderLane": self.leader_lane,
            "governedLane": self.governed_lane,
            "reportedAt": self.reported_at.isoformat(),
            "expiresAt": self.expires_at.isoformat(),
            "stoppedAt": None if self.stopped_at is None else self.stopped_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ExternalMemberReadinessReceipt:
    """What a reporter is told back: where its lease stands and when to return.

    ``renew_after_seconds`` is the reason this is a document rather than a 204.
    The renew period is the server's to choose — it is derived from the TTL the
    server holds — so a deployment that retunes the lease retunes every Bridge
    with it, and no client has a period compiled in.
    """

    member_agent_id: UUID
    status: ExternalMemberReadinessStatus
    expires_at: datetime
    renew_after_seconds: int

    def to_wire(self) -> dict[str, object]:
        return {
            "agentId": str(self.member_agent_id),
            "status": self.status.value,
            "expiresAt": self.expires_at.isoformat(),
            "renewAfterSeconds": self.renew_after_seconds,
        }


class ExternalMemberReadinessStore:
    """The lease table: one row per member, guarded by a lock, held in memory.

    The lock is what makes a read-modify-write of one row atomic across the
    concurrent requests a Bridge fleet produces — two members renewing at once
    is the normal case, and the fence below is a read followed by a write.

    The clock is injected because every rule in here is a statement about time,
    and a test that has to sleep through a forty-five second lease cannot make
    one at the instant it turns.
    """

    def __init__(
        self,
        *,
        ttl_seconds: int,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._renew_after_seconds = ttl_seconds // 3
        self._now = now
        self._leases: dict[UUID, ExternalMemberReadinessLease] = {}
        self._lock = asyncio.Lock()

    @property
    def renew_after_seconds(self) -> int:
        """How long a reporter should wait before renewing.

        A third of the TTL, so a Bridge may miss two reports in a row — a
        network blip, a paused laptop — before the lease it holds runs out.
        """

        return self._renew_after_seconds

    async def startup(
        self, report: ReportExternalMemberReadinessCommand
    ) -> ExternalMemberReadinessView:
        """Write the row, whoever held it before, and answer how it now reads.

        Unconditional takeover, and ``stopped_at`` is cleared with it: a member
        that reported shutdown and then started again would otherwise stay
        offline behind a lease it is faithfully renewing.

        The answer is the *derived* view rather than the raw row, so that what a
        reporter is told and what the console reads are one derivation against
        one reading of the clock, computed here under the lock. Two spellings of
        "what state is this member in" is exactly how the two halves of the
        contract come to disagree.
        """

        async with self._lock:
            return self._write(report, now=self._now(), stopped_at=None)

    async def renew(
        self, report: ReportExternalMemberReadinessCommand
    ) -> ExternalMemberReadinessView:
        """Slide the expiry, or refuse a process that no longer owns the row.

        A missing row is inserted rather than refused. That is what makes a
        backend restart heal itself within one renew period: the table is
        deliberately in memory, so "I have never heard of you" is far more
        often RepoMesh's own amnesia than a Bridge's mistake.

        A renew that lands *after* this instance's own shutdown — a timer that
        fired during teardown — slides the expiry and stays offline, and the
        view says so. It is not a refusal (nothing is wrong: the lease is still
        this instance's) and it is not a revival either, because only a startup
        says a member is back.
        """

        async with self._lock:
            now = self._now()
            held = self._leases.get(report.member_agent_id)
            if held is None:
                return self._write(report, now=now, stopped_at=None)
            self._assert_owns(held, report.instance_id, member=report.member_agent_id)
            # ``stopped_at`` is carried rather than cleared: a renew says the
            # lease is still held, and only a startup says the member is back.
            return self._write(report, now=now, stopped_at=held.stopped_at)

    async def shutdown(self, member_agent_id: UUID, *, instance_id: UUID) -> datetime:
        """Mark the lease stopped, and answer the instant it ended.

        A member with no row is a no-op that answers the same instant: it holds
        no lease now, which is exactly what it was reporting.
        """

        async with self._lock:
            now = self._now()
            held = self._leases.get(member_agent_id)
            if held is None:
                return now
            self._assert_owns(held, instance_id, member=member_agent_id)
            self._leases[member_agent_id] = replace(held, stopped_at=now)
            return now

    async def snapshot(self) -> tuple[ExternalMemberReadinessView, ...]:
        """Every lease, with its status derived against one reading of the clock.

        Ordered by member so the console's table has a stable order that does
        not depend on which Bridge reported first.
        """

        async with self._lock:
            now = self._now()
            return tuple(
                self._view(member_agent_id, self._leases[member_agent_id], now)
                for member_agent_id in sorted(self._leases)
            )

    def _write(
        self,
        report: ReportExternalMemberReadinessCommand,
        *,
        now: datetime,
        stopped_at: datetime | None,
    ) -> ExternalMemberReadinessView:
        """Store the row and read it straight back, both against the same instant."""

        lease = ExternalMemberReadinessLease(
            instance_id=report.instance_id,
            role=report.role,
            leader_lane=report.leader_lane,
            governed_lane=report.governed_lane,
            workspace_root=report.workspace_root,
            reported_at=now,
            expires_at=now + self._ttl,
            stopped_at=stopped_at,
        )
        self._leases[report.member_agent_id] = lease
        return self._view(report.member_agent_id, lease, now)

    def _assert_owns(
        self, held: ExternalMemberReadinessLease, instance_id: UUID, *, member: UUID
    ) -> None:
        if held.instance_id != instance_id:
            raise StaleInstance(
                f"the readiness lease for {member} is held by another instance"
            )

    def _view(
        self, member_agent_id: UUID, lease: ExternalMemberReadinessLease, now: datetime
    ) -> ExternalMemberReadinessView:
        return ExternalMemberReadinessView(
            member_agent_id=member_agent_id,
            status=self._status(lease, now),
            role=lease.role,
            leader_lane=lease.leader_lane,
            governed_lane=lease.governed_lane,
            reported_at=lease.reported_at,
            expires_at=lease.expires_at,
            stopped_at=lease.stopped_at,
        )

    def _status(
        self, lease: ExternalMemberReadinessLease, now: datetime
    ) -> ExternalMemberReadinessStatus:
        """A row and a clock, in the order the states actually happen.

        A member that said goodbye is offline whatever its expiry says: it told
        us it was going, and that is better evidence than a lease it stopped
        renewing.
        """

        if lease.stopped_at is not None:
            return ExternalMemberReadinessStatus.OFFLINE
        if now < lease.expires_at:
            return ExternalMemberReadinessStatus.READY
        if now < lease.expires_at + self._ttl:
            return ExternalMemberReadinessStatus.STALE
        return ExternalMemberReadinessStatus.OFFLINE


class ReportExternalMemberReadiness:
    """The only writer of the lease table, and the whole of its admission policy.

    Everything is checked before anything is written, so a refused report leaves
    the table exactly as it found it. That matters more here than it looks:
    a half-applied report would be a member the gate lets through on the
    strength of facts RepoMesh refused.

    ``workspace_root`` is a value rather than a dependency, injected at the
    composition edge like the store's TTL. No application-layer module in this
    codebase reads settings, and this one is not going to be the first: what it
    needs is one path to compare against, not the deployment's configuration.
    """

    def __init__(
        self,
        directory: AgentPrincipalReader,
        store: ExternalMemberReadinessStore,
        *,
        workspace_root: Path,
    ) -> None:
        self._directory = directory
        self._store = store
        self._workspace_root = workspace_root

    async def execute(
        self, report: ReportExternalMemberReadinessCommand
    ) -> ExternalMemberReadinessReceipt:
        principal = await self._directory.get_view(report.member_agent_id)
        if principal is None:
            raise UnknownExternalWorker(
                f"agent principal does not exist: {report.member_agent_id}"
            )
        self._assert_reports_its_own_shape(principal, report)

        if report.kind is ReadinessReportKind.SHUTDOWN:
            ended_at = await self._store.shutdown(
                report.member_agent_id, instance_id=report.instance_id
            )
            return self._receipt(
                report, ExternalMemberReadinessStatus.OFFLINE, expires_at=ended_at
            )
        # The reporter is told the status its row *derives to* once the report
        # has been applied, never the status its report asked for. The two part
        # company for a renew that lands after this instance's own shutdown, and
        # a receipt saying ``ready`` there would contradict the read model the
        # console is polling about the same member at the same moment.
        applied = await (
            self._store.startup(report)
            if report.kind is ReadinessReportKind.STARTUP
            else self._store.renew(report)
        )
        return self._receipt(report, applied.status, expires_at=applied.expires_at)

    def _assert_reports_its_own_shape(
        self, principal: AgentPrincipalView, report: ReportExternalMemberReadinessCommand
    ) -> None:
        """The role RepoMesh holds, and the capabilities that role is allowed.

        The role is confirmed rather than echoed, for v2 preflight's reason: a
        member that reports as the other role is not the process this principal
        is on file as, and the whole value of a self-report is that the server
        checks it.

        No message names a path — neither this deployment's workspace root nor
        the one the reporter sent. The first is the operator's own filesystem
        and does not belong in an API body; the second is caller-controlled text
        that would end up in an operator's log.
        """

        # The same mapping the binding paths join through: which principals may
        # be served by a Bridge at all is one fact, and only the refusal differs.
        role = MEMBER_ROLES.get(principal.role)
        if role is None:
            raise ReadinessRefused(
                f"agent {principal.id} is a {principal.role.value}, which stays on the "
                "AgentTeams Manager and reports no readiness"
            )
        if report.role is not role:
            raise ReadinessRefused(
                f"agent {principal.id} is a {role.value} on file, "
                f"but the report claims {report.role.value}"
            )
        if role is ExternalMemberRole.REPOSITORY_LEADER:
            if not report.leader_lane or report.governed_lane:
                raise ReadinessRefused(
                    "a repository leader reports the leader lane and not the governed lane"
                )
            if report.workspace_root is not None:
                raise ReadinessRefused("a repository leader runs no code and holds no workspace")
            return
        if not report.governed_lane:
            raise ReadinessRefused("a worker reports the governed lane")
        # Compared as paths, not as strings: the reporter's value arrives from
        # a PowerShell launch and a ``.env`` file, which disagree with this
        # setting about separators and trailing slashes while naming the same
        # directory. ``None`` is the absent case rather than a special one — a
        # worker that reports no workspace root has not matched this one.
        if (
            report.workspace_root is None
            or Path(report.workspace_root) != self._workspace_root
        ):
            raise ReadinessRefused("a worker's workspace root is not the one this deployment runs")

    def _receipt(
        self,
        report: ReportExternalMemberReadinessCommand,
        status: ExternalMemberReadinessStatus,
        *,
        expires_at: datetime,
    ) -> ExternalMemberReadinessReceipt:
        return ExternalMemberReadinessReceipt(
            member_agent_id=report.member_agent_id,
            status=status,
            expires_at=expires_at,
            renew_after_seconds=self._store.renew_after_seconds,
        )


@dataclass(frozen=True, slots=True)
class ExternalMemberReadinessFact:
    """What the materialize gate is told about one external member.

    Plain strings rather than this module's enums, and that is the type's whole
    job: it crosses into ``repository_intelligence``, which declares the shape
    it consumes as a protocol and must not import this module to read four
    fields it passes through to a refusal body. Nothing is lost in the
    flattening — both values are their enums' own spelling, so a console reading
    this and a console polling the readiness list see one vocabulary.
    """

    agent_id: UUID
    role: str
    status: str
    reason: str


#: What a lease that exists means to the operator reading a refusal, one phrase
#: per derived status. A mapping rather than a branch each: ``reason`` is a
#: field every reported member carries, and the only thing that differs between
#: the statuses is the sentence.
_LEASE_REASONS: dict[ExternalMemberReadinessStatus, str] = {
    ExternalMemberReadinessStatus.READY: "the readiness lease is current",
    ExternalMemberReadinessStatus.STALE: "the readiness lease expired without a renewal",
    ExternalMemberReadinessStatus.OFFLINE: "the member stopped reporting readiness",
}

#: The one member state that is not a lease at all, and the only one whose
#: remedy is simply to launch the CLI.
_NO_REPORT = "no readiness report"


class RequireExternalMembersReady:
    """May this round hand out work? Three sources, joined per member (AC-03).

    The directory says who the member is, the AgentTeams control plane says
    whether RepoMesh runs its body, and the lease says whether the body RepoMesh
    does *not* run is running. Only the third is a live fact, and it is
    worthless without the second: a lease is absent both for a managed member
    that never reports one and for an external member whose CLI is down, and
    those two are opposite verdicts.

    So ``containerManaged`` decides who is in the answer at all, and it decides
    it the way :class:`ResolveExternalMemberBinding` does — a confirmed
    ``False`` and nothing else. A managed member, an unknown one, a member the
    controller has no document for: none of them is a member whose liveness this
    class claims to know, so none of them appears (AC-04). Absence says nothing,
    which is the only honest thing to say about a process somebody else runs.

    A member id with no directory principal is not defended against. The ids
    arrive from a project topology's own teams or from the directory's own
    listing, so a missing principal is a broken invariant rather than a state
    worth a message.
    """

    def __init__(
        self,
        directory: AgentPrincipalReader,
        control_plane: WorkerBindingReader | None,
        store: ExternalMemberReadinessStore,
    ) -> None:
        self._directory = directory
        self._control_plane = control_plane
        self._store = store

    async def check(
        self, member_ids: Sequence[UUID]
    ) -> tuple[ExternalMemberReadinessFact, ...]:
        """One fact per external member, in the order they were asked about.

        The whole table is read once, before the per-member loop, so every fact
        in one answer derives from a single reading of the clock. Asking the
        store per member would let a refusal say a member was ready and the one
        after it stale on the strength of the microseconds between two reads.

        An unconfigured control plane is a refusal rather than an empty answer,
        for the reason the binding endpoints give theirs: without it there is no
        way to tell a managed fleet from an external one that is entirely down,
        and one of those two readings starts work nobody will pick up. In
        practice it is unreachable from materialization — the runtime projection
        runs first and already needs the controller — and it is spelled anyway
        because "unreachable" is a claim about today's call order.
        """

        if not member_ids:
            return ()
        if self._control_plane is None:
            raise WorkerControlPlaneUnavailable(
                "the AgentTeams control plane is not configured, so no member can be "
                "confirmed as an external one"
            )

        leases = {view.member_agent_id: view for view in await self._store.snapshot()}
        facts = []
        for member_id in member_ids:
            principal = await self._directory.get_view(member_id)
            worker = await self._control_plane.get_worker(principal.agentteams_resource_name)
            if worker is None or worker.container_managed is not False:
                continue
            facts.append(
                self._fact(member_id, MEMBER_ROLES[principal.role], leases.get(member_id))
            )
        return tuple(facts)

    @staticmethod
    def _fact(
        member_id: UUID,
        role: ExternalMemberRole,
        lease: ExternalMemberReadinessView | None,
    ) -> ExternalMemberReadinessFact:
        """The member's lease as a fact, or its absence as ``offline``.

        The role is the directory's, never the lease's. A refusal that offers
        "start this member" has to name what to start, and a leader's Bridge and
        a worker's are launched differently — reading it off the report would
        let a member that has not reported at all go unnamed, and one that has
        decide how it is described.
        """

        if lease is None:
            return ExternalMemberReadinessFact(
                agent_id=member_id,
                role=role.value,
                status=ExternalMemberReadinessStatus.OFFLINE.value,
                reason=_NO_REPORT,
            )
        return ExternalMemberReadinessFact(
            agent_id=member_id,
            role=role.value,
            status=lease.status.value,
            reason=_LEASE_REASONS[lease.status],
        )
