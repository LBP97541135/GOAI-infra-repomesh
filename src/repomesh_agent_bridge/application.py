"""The Bridge's one application interface.

``RoomNativeAgent.run(enrollment)`` is the whole external surface (ADR 0004
decision 4). There is no ``check`` method and no mode flag on ``run``: the CLI's
``check`` subcommand is a facade over the same package-private startup function,
because a diagnostic command that walked a *different* code path would be
diagnosing the wrong program. Nothing outside this package may import
:func:`_startup`.
"""

import asyncio
import contextlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .contracts import (
    CODING_PROFILES,
    BindingRefused,
    EnrollmentInvalid,
    ExternalWorkerEnrollment,
    WorkerBinding,
)
from .instance_lock import InstanceLock, instance_lock_path
from .ports import CodingSessionPort, RoomPort, WorkerBindingPort
from .runner_consumer import GovernedRuntime, RunnerConsumer
from .state import open_state, state_path
from .supervisor import RoomSupervisor

__all__ = ["CredentialResolver", "RoomNativeAgent", "StartupOutcome", "resolve_env_credential"]

_logger = logging.getLogger(__name__)

CredentialResolver = Callable[[str], str]
"""``resolve(ref) -> secret``. Injected, never a port: there is no variation to
absorb beyond the function itself, and a one-method port would be indirection
without variation."""


def resolve_env_credential(ref: str) -> str:
    """Resolve an ``env:NAME`` locator against the process environment.

    The only locator this tier understands. ``keyring:`` and ``file:`` are named
    in the contract as things a *resolver* may support, and adding them here
    before anything asks for them would be inventing a credential story; a ref
    this resolver cannot read is a stage-1 refusal, which is honest and cheap.

    The resolved value is returned and nothing else: it is never logged, never
    echoed to stdout, and never put in an exception message. Failures name the
    variable, which is not the secret.
    """

    scheme, separator, name = ref.partition(":")
    if not separator or scheme != "env" or not name:
        raise EnrollmentInvalid(
            f"credential locator scheme {scheme!r} is not supported; use 'env:NAME'"
        )
    value = os.environ.get(name, "")
    if not value:
        raise EnrollmentInvalid(f"credential environment variable is unset or empty: {name}")
    return value


@dataclass(frozen=True, slots=True)
class StartupOutcome:
    """What both stages together establish before anything else may happen."""

    enrollment: ExternalWorkerEnrollment
    binding: WorkerBinding
    confirmed_room_ids: tuple[str, ...]


def _validate_locally(
    enrollment: ExternalWorkerEnrollment,
    *,
    requires_credential: bool,
    resolve: CredentialResolver,
) -> str | None:
    """Stage 1: everything decidable without a socket, decided without one.

    ``ExternalWorkerEnrollment.from_wire`` has already applied the schema when
    the enrollment came from a file, but ``run`` accepts a constructed
    enrollment too, so the profile and the credential references are re-checked
    here. That is not belt-and-braces: this function, not the file reader, is
    what the interface promises runs before the network.
    """

    if enrollment.coding_profile not in CODING_PROFILES:
        raise EnrollmentInvalid(
            f"codingProfile must be one of {', '.join(CODING_PROFILES)}"
        )
    for name, ref in enrollment.credential_refs.items():
        if not ref.strip():
            raise EnrollmentInvalid(f"credentialRefs.{name} is an empty reference")
    if not requires_credential:
        return None
    if enrollment.credential_refs.repomesh is None:
        raise EnrollmentInvalid(
            "credentialRefs.repomesh is required: RepoMesh preflight is authenticated"
        )
    return resolve(enrollment.credential_refs.repomesh)


def _confirm(enrollment: ExternalWorkerEnrollment, binding: WorkerBinding) -> tuple[str, ...]:
    """Stage 2: hold the enrollment against what RepoMesh actually has on file.

    Every identity field must agree. ``teamName`` is treated exactly as strictly
    as the worker identity fields, which PR 1's final review left open: a Bridge
    whose enrollment names a different team than RepoMesh's projection is
    misconfigured in a way that will surface later as a room it should not be
    in, and preflight exists to catch precisely that class of drift. Refusing is
    reversible by editing one line of local configuration; accepting is not.

    The room allowlist is an intersection, with RepoMesh's list as the
    authority for ordering as well as membership. An empty intersection is a
    refusal rather than a Bridge that starts and then ignores every room: a
    process with nothing it may listen to is not a working member of anything,
    and "started successfully, does nothing" is the least debuggable outcome
    available.
    """

    for label, mine, theirs in (
        ("organizationId", str(enrollment.organization_id), str(binding.organization_id)),
        ("workerAgentId", str(enrollment.worker_agent_id), str(binding.worker_agent_id)),
        ("workerName", enrollment.worker_name, binding.worker_name),
        ("matrixUserId", enrollment.matrix_user_id, binding.matrix_user_id),
        ("teamName", enrollment.team_name, binding.team_name),
    ):
        if mine != theirs:
            raise BindingRefused(f"{label} disagrees with RepoMesh: {mine!r} != {theirs!r}")
    enrolled = set(enrollment.allowed_room_ids)
    confirmed = tuple(room for room in binding.allowed_room_ids if room in enrolled)
    if not confirmed:
        raise BindingRefused(
            "no room is confirmed by both the enrollment and RepoMesh"
        )
    return confirmed


async def _startup(
    enrollment: ExternalWorkerEnrollment,
    binding_port: WorkerBindingPort,
    *,
    resolve_credential: CredentialResolver = resolve_env_credential,
    after_local_validation: Callable[[], None] | None = None,
) -> StartupOutcome:
    """Run both startup stages in the one order the contract allows.

    Package-private and shared by ``run`` and the CLI's ``check``. The hook runs
    between the stages because that is where the instance lock belongs: a broken
    enrollment must not take the lock, and a legitimate second instance must
    fail before it spends a network round-trip discovering what it could have
    known locally. ``check`` passes no hook — it is a diagnostic and has to work
    while a ``run`` is live.
    """

    credential = _validate_locally(
        enrollment,
        requires_credential=binding_port.requires_credential,
        resolve=resolve_credential,
    )
    if after_local_validation is not None:
        after_local_validation()
    binding = await binding_port.fetch_binding(enrollment, credential=credential)
    return StartupOutcome(
        enrollment=enrollment,
        binding=binding,
        confirmed_room_ids=_confirm(enrollment, binding),
    )


class RoomNativeAgent:
    """One AgentTeams external worker, served by one local process.

    Composition happens outside: the three seams and the credential resolver
    arrive through the constructor, so the same agent is exercised in tests with
    in-memory doubles and in production with the HTTP, Matrix and CLI adapters.
    """

    def __init__(
        self,
        *,
        binding_port: WorkerBindingPort,
        room_port: RoomPort,
        coding_session: CodingSessionPort,
        state_dir: Path | None = None,
        resolve_credential: CredentialResolver = resolve_env_credential,
        governed: GovernedRuntime | None = None,
    ) -> None:
        self._binding_port = binding_port
        self._room_port = room_port
        self._coding_session = coding_session
        self._state_dir = state_dir
        self._resolve_credential = resolve_credential
        self._governed = governed
        """Governed execution, or ``None`` for a conversation-only instance.

        One value rather than two seams because the wake-up port and the run
        consumer are not independently useful: an instance that could accept
        ``start task <id>`` but never execute it would hand the room a receipt
        for work nothing on this machine will do (J-10).
        """

    async def run(self, enrollment: ExternalWorkerEnrollment) -> None:
        """Validate, claim the worker, preflight, gate the runtime, and serve.

        The order is the contract: local validation, then the instance lock,
        then RepoMesh preflight, then the coding runtime's own readiness gate,
        then local state, then Matrix — and only then does the process serve
        anything. Nothing after ``await`` here is reachable without every
        earlier step having succeeded, which is what makes "no CLI is spawned
        before preflight" a structural property rather than a promise.

        The two middle steps each sit between the only two neighbours they can.
        ``ensure_ready`` is after preflight because a worker RepoMesh will not
        bind has no business probing a CLI, and before the state file because a
        runtime that is turned away must not leave a database behind for a
        worker that never served a turn — the same argument that puts the lock
        after stage 1. The state file is then opened before Matrix, because the
        supervisor's first act is draining intents a crash stranded, and that
        has to happen before new messages are taken on.

        A refused gate therefore unwinds through a stack holding only the lock:
        no state file exists, ``RoomPort.start`` was never called, the claim is
        handed straight back, and the refusal reaches the CLI as the same
        ``BridgeStartupError`` family every other startup refusal uses. Closing
        the session is not owed here — ``ensure_ready`` reaps whatever it probed
        with before it raises, which is why it is registered afterwards.

        Cancellation unwinds the stack in reverse: the supervisor stops without
        committing the batch it was holding, the coding session closes, the room
        port closes, the state file closes, the lock is released, and
        ``CancelledError`` propagates so the caller learns the loop ended. A
        ``RoomRefused`` out of the steady-state sync leaves the same way and for
        the same reason — the supervisor ends the run rather than retrying a
        decision — so the shutdown path has one shape, not two.

        What serves at the end is one of two arrangements, and the unwind above
        is the same for both. Conversation-only, the supervisor is simply
        awaited and this process has no other task. With governed execution the
        supervisor and the Runner consumer are two peers in one
        ``asyncio.TaskGroup``: the room loop answers mentions and drains, the
        consumer leases and executes, and neither is the other's parent. They are
        siblings rather than one hosting the other because either failing means
        this instance cannot do its job — a homeserver that revoked the token and
        a control plane that will not lease are both reasons to stop — and a
        group is the arrangement in which one failure cancels the other and the
        exit stack still unwinds once, in order.
        """

        lock = InstanceLock(instance_lock_path(enrollment.worker_agent_id, self._state_dir))
        async with contextlib.AsyncExitStack() as stack:
            # Registered before the claim is taken: release() is a no-op when
            # this instance never held it, and registering afterwards would
            # leave a stage-2 refusal holding the lock for the process lifetime.
            stack.callback(lock.release)
            outcome = await _startup(
                enrollment,
                self._binding_port,
                resolve_credential=self._resolve_credential,
                after_local_validation=lock.acquire,
            )
            # The startup gate on the coding runtime. Deliberately *before* the
            # state file: a Bridge that cannot code must not be the reason a
            # room's backlog gets written off as read, and the cheapest place to
            # discover that is while this process still owns nothing but a lock.
            await self._coding_session.ensure_ready()
            state = open_state(
                state_path(enrollment.worker_agent_id, self._state_dir),
                worker_agent_id=enrollment.worker_agent_id,
            )
            # Registered first of the three, so it is closed last: the ports
            # write through this file, and a file closed out from under them
            # would turn a clean shutdown into a broken one.
            stack.callback(state.close)
            stack.push_async_callback(self._room_port.close)
            stack.push_async_callback(self._coding_session.close)
            await self._room_port.start(
                homeserver_url=enrollment.matrix_homeserver_url,
                user_id=enrollment.matrix_user_id,
                room_ids=outcome.confirmed_room_ids,
                # Resolved here and handed over per call: the secret's lifetime
                # is this call's, not the process's, exactly as with preflight's
                # credential. Resolution is deliberately not part of stage 1 —
                # requiring a Matrix token would make ``check`` useless as the
                # thing an operator runs *before* the credentials are in place.
                access_token=self._resolve_credential(enrollment.credential_refs.matrix),
            )
            _logger.info(
                "bridge ready: worker=%s profile=%s rooms=%d governed=%s",
                enrollment.worker_name,
                enrollment.coding_profile,
                len(outcome.confirmed_room_ids),
                "off" if self._governed is None else "on",
            )
            supervisor = RoomSupervisor(
                enrollment=enrollment,
                confirmed_room_ids=outcome.confirmed_room_ids,
                room_port=self._room_port,
                coding_session=self._coding_session,
                state=state,
                governed_task=None if self._governed is None else self._governed.task_port,
            )
            if self._governed is None:
                await supervisor.serve()
            else:
                await _serve_both(supervisor, self._governed.build_consumer(state))


async def _serve_both(supervisor: RoomSupervisor, consumer: RunnerConsumer) -> None:
    """Run the room loop and the Runner loop until one of them ends.

    A task group is what makes "either failure cancels the other" structural: the
    alternative — one loop awaiting the other, or a bare ``create_task`` — leaves
    a way for the Bridge to keep syncing a room while it has silently stopped
    executing the runs that room asked for, which is the worst of the available
    failures because it looks alive.

    The group reports through an ``ExceptionGroup``, and the composition root
    above it does not: the CLI maps ``RoomTransportError`` and the startup
    refusals onto exit codes by type, and a group is neither. One failure is the
    overwhelmingly common case — the second loop is *cancelled*, not failed, so
    it contributes nothing — so a single-leaf group is unwrapped back to the
    exception the caller's vocabulary is written in. A genuine double failure is
    left as a group, because collapsing that would mean choosing which of two
    real reasons to report.
    """

    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(supervisor.serve())
            group.create_task(consumer.serve())
    except BaseExceptionGroup as failures:
        raise _single_failure(failures) from None


def _single_failure(failures: BaseExceptionGroup) -> BaseException:
    """The one exception inside a group, however deeply nested, or the group."""

    if len(failures.exceptions) != 1:
        return failures
    inner = failures.exceptions[0]
    return _single_failure(inner) if isinstance(inner, BaseExceptionGroup) else inner
