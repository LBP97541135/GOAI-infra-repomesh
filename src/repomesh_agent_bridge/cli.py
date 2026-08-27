"""``repomesh-agent-bridge run|check --enrollment <path>``.

``check`` is a facade, not a second implementation: it runs the same
package-private startup function ``run`` does, stops when that function returns,
and prints what it learned. It deliberately does **not** take the instance lock —
a diagnostic that cannot be run while the thing it diagnoses is alive would be
useless exactly when it is needed.

Nothing printed here is a secret. Credential references are reported by slot
name only: the locator itself is non-secret by contract but may still name a
private path or an internal keyring, and the slot name is the whole diagnostic
value anyway ("did the enrollment carry a repomesh credential at all").
"""

import argparse
import asyncio
import contextlib
import json
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from repomesh_runner.drivers.app_server import AppServerDriver
from repomesh_runner.profiles import get_profile

from .adapters.coding_session import CODEX_PROFILE_ID, DriverCodingSession, session_root
from .adapters.matrix import MatrixRoomAdapter
from .adapters.memory import InertCodingSession
from .adapters.repomesh_binding import RepoMeshBindingAdapter
from .adapters.restricted_process import RestrictedProcessFactory
from .application import RoomNativeAgent, StartupOutcome, _startup
from .contracts import BridgeStartupError, EnrollmentInvalid, ExternalWorkerEnrollment
from .instance_lock import InstanceAlreadyRunning
from .ports import CodingSessionPort, RoomTransportError, WorkerBindingPort

__all__ = ["EXIT_ALREADY_RUNNING", "EXIT_OK", "EXIT_STARTUP_REFUSED", "main"]

EXIT_OK = 0
EXIT_STARTUP_REFUSED = 2
"""Any startup refusal, either stage. Shares its value with argparse's own usage
error on purpose: both mean the invocation cannot produce a running Bridge, and
a supervisor that retries either one is retrying a decision, not an outage."""
EXIT_ALREADY_RUNNING = 3
"""Distinct because the correct supervisor response is distinct: nothing is
wrong, another instance already serves this worker."""

BindingPortFactory = Callable[[ExternalWorkerEnrollment], WorkerBindingPort]
CodingSessionFactory = Callable[[ExternalWorkerEnrollment], CodingSessionPort]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repomesh-agent-bridge",
        description="Serve one AgentTeams external worker from this machine.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    run = subcommands.add_parser(
        "run", help="validate, join the confirmed rooms, and answer mentions until cancelled"
    )
    run.add_argument("--enrollment", required=True, type=Path)
    run.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="where the per-worker instance lock lives (default: per-user state directory)",
    )
    run.add_argument(
        "--inert",
        action="store_true",
        help="serve the PR 3 stand-in that answers one honest note and spawns no CLI, "
        "instead of assembling the enrollment's real coding session",
    )
    check = subcommands.add_parser(
        "check", help="run both startup validation stages and exit; joins nothing"
    )
    check.add_argument("--enrollment", required=True, type=Path)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    binding_port_factory: BindingPortFactory | None = None,
    coding_session_factory: CodingSessionFactory | None = None,
) -> int:
    """Entry point. The two factories are the seams tests replace.

    Factories rather than the ports themselves, because both production adapters
    are built from the enrollment this function is about to read; factories
    rather than monkeypatched globals, because a test that reaches around the
    interface stops testing it. ``coding_session_factory`` lets a test drive the
    run path without a real CLI spawn — a session whose gate refuses (a missing
    binary, say) reaches ``main``'s exit mapping exactly as the real one would.
    """

    arguments = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    try:
        enrollment = _load_enrollment(arguments.enrollment)
        build_port = binding_port_factory or _default_binding_port
        binding_port = build_port(enrollment)
        if arguments.command == "check":
            outcome = asyncio.run(_startup(enrollment, binding_port))
            _report(outcome)
            return EXIT_OK

        def build_session(enrolled: ExternalWorkerEnrollment) -> CodingSessionPort:
            if coding_session_factory is not None:
                return coding_session_factory(enrolled)
            return _build_coding_session(
                enrolled, inert=arguments.inert, state_dir=arguments.state_dir
            )

        agent = RoomNativeAgent(
            binding_port=binding_port,
            room_port=MatrixRoomAdapter(),
            coding_session=build_session(enrollment),
            state_dir=arguments.state_dir,
        )
        # Ctrl-C is how an operator stops this process, so it is a normal
        # ending, not a failure: ``asyncio.run`` has already cancelled the agent
        # and let its unwind close the seams and release the lock.
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(agent.run(enrollment))
        return EXIT_OK
    except InstanceAlreadyRunning as running:
        print(f"error: {running}", file=sys.stderr)
        return EXIT_ALREADY_RUNNING
    except BridgeStartupError as refused:
        print(f"error: {refused}", file=sys.stderr)
        return EXIT_STARTUP_REFUSED
    except RoomTransportError as unreachable:
        # ``RoomTransportError`` lives on the port now (H-2); this frame is where
        # its two arrival paths meet. A failure out of ``start`` means the
        # instance never came up. A steady-state ``RoomRefused`` out of ``sync``
        # means the homeserver will no longer let this identity read its rooms,
        # so the supervisor ends the run and re-raises rather than backing off on
        # a decision — everything else it absorbs. Both mean this invocation
        # cannot serve, which is what exit 2 says; an operator gets one line, not
        # a traceback.
        print(f"error: {unreachable}", file=sys.stderr)
        return EXIT_STARTUP_REFUSED


def _load_enrollment(path: Path) -> ExternalWorkerEnrollment:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as unreadable:
        raise EnrollmentInvalid(f"cannot read enrollment file: {path}") from unreadable
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as malformed:
        raise EnrollmentInvalid(f"enrollment file is not valid JSON: {path}") from malformed
    return ExternalWorkerEnrollment.from_wire(payload)


def _default_binding_port(enrollment: ExternalWorkerEnrollment) -> WorkerBindingPort:
    del enrollment  # the adapter reads the endpoint per call, from the enrollment it is given
    return RepoMeshBindingAdapter()


def _build_coding_session(
    enrollment: ExternalWorkerEnrollment, *, inert: bool, state_dir: Path | None
) -> CodingSessionPort:
    """Assemble the session ``run`` serves with (H-6).

    The default is the product: the enrollment's coding profile, behind a real
    CLI and the restricted process factory. ``--inert`` keeps PR 3's honest
    stand-in for an operator bringing a Bridge up in a room before the CLI is
    installed. A profile this build has no real adapter for is a startup refusal,
    not a silent downgrade to inert: a Bridge that looks like it can code and
    cannot is exactly the outcome the readiness gate exists to prevent, and
    ``--inert`` names the deliberate way to ask for the stand-in.

    Nothing here spawns. The restricted factory and the driver are only wired;
    the first process is the one ``ensure_ready`` starts, after preflight, inside
    ``RoomNativeAgent.run``.
    """

    if inert:
        return InertCodingSession(worker_name=enrollment.worker_name)
    if enrollment.coding_profile != CODEX_PROFILE_ID:
        raise BridgeStartupError(
            f"this build has no coding adapter for profile {enrollment.coding_profile!r}; "
            "only 'codex' can serve a real session today. Re-run with --inert to bring the "
            "Bridge up with an honest stand-in instead"
        )
    factory = RestrictedProcessFactory()
    return DriverCodingSession(
        AppServerDriver(factory),
        factory,
        session_dir=session_root(enrollment.worker_agent_id, state_dir),
        worker_name=enrollment.worker_name,
        profile=get_profile(CODEX_PROFILE_ID),
    )


def _report(outcome: StartupOutcome) -> None:
    enrollment = outcome.enrollment
    binding = outcome.binding
    lines = [
        "enrollment: valid",
        f"worker: {enrollment.worker_name} ({enrollment.worker_agent_id})",
        f"team: {binding.team_name}",
        f"organization: {binding.organization_id}",
        f"matrix: {enrollment.matrix_user_id} via {enrollment.matrix_homeserver_url}",
        f"profile: {enrollment.coding_profile}",
        f"credentialRefs: {', '.join(enrollment.credential_refs.names())}",
        f"preflight: {enrollment.repomesh_endpoint} confirmed the binding",
        f"containerManaged: {str(binding.container_managed).lower()}",
        f"confirmed rooms: {len(outcome.confirmed_room_ids)}",
        *(f"  {room_id}" for room_id in outcome.confirmed_room_ids),
        "matrix sync: not started (check joins nothing)",
        "coding session: not spawned (check spawns nothing)",
    ]
    print("\n".join(lines))
