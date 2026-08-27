"""The Bridge's one application interface.

``RoomNativeAgent.run(enrollment)`` is the whole external surface (ADR 0004
decision 4). There is no ``check`` method and no mode flag on ``run``: the CLI's
``check`` subcommand is a facade over the same package-private startup function,
because a diagnostic command that walked a *different* code path would be
diagnosing the wrong program. Nothing outside this package may import
:func:`_startup`.
"""

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
    ) -> None:
        self._binding_port = binding_port
        self._room_port = room_port
        self._coding_session = coding_session
        self._state_dir = state_dir
        self._resolve_credential = resolve_credential

    async def run(self, enrollment: ExternalWorkerEnrollment) -> None:
        """Validate, claim the worker, preflight, join, and serve until cancelled.

        The order is the contract: local validation, then the instance lock,
        then RepoMesh preflight, then local state, then Matrix — and only then
        does the process serve anything. Nothing after ``await`` here is
        reachable without every earlier step having succeeded, which is what
        makes "no CLI is spawned before preflight" a structural property rather
        than a promise.

        The state file is opened between preflight and Matrix, and both
        neighbours matter. Opening it earlier would have a refused preflight
        leave a database behind for a worker that never started; opening it
        later would have the supervisor's first act — draining intents a crash
        stranded — happen after new messages had already been taken on.

        Cancellation unwinds the stack in reverse: the supervisor stops without
        committing the batch it was holding, the coding session closes, the room
        port closes, the state file closes, the lock is released, and
        ``CancelledError`` propagates so the caller learns the loop ended. The
        supervisor starts no background tasks, so there is nothing else to wait
        for.
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
                "bridge ready: worker=%s profile=%s rooms=%d",
                enrollment.worker_name,
                enrollment.coding_profile,
                len(outcome.confirmed_room_ids),
            )
            await RoomSupervisor(
                enrollment=enrollment,
                confirmed_room_ids=outcome.confirmed_room_ids,
                room_port=self._room_port,
                coding_session=self._coding_session,
                state=state,
            ).serve()
