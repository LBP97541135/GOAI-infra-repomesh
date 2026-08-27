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

from .adapters.memory import InertCodingSession, InertRoomPort
from .adapters.repomesh_binding import RepoMeshBindingAdapter
from .application import RoomNativeAgent, StartupOutcome, _startup
from .contracts import BridgeStartupError, EnrollmentInvalid, ExternalWorkerEnrollment
from .instance_lock import InstanceAlreadyRunning
from .ports import WorkerBindingPort

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repomesh-agent-bridge",
        description="Serve one AgentTeams external worker from this machine.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    run = subcommands.add_parser("run", help="validate, join, and stay up until cancelled")
    run.add_argument("--enrollment", required=True, type=Path)
    run.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="where the per-worker instance lock lives (default: per-user state directory)",
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
) -> int:
    """Entry point. ``binding_port_factory`` is the seam tests replace.

    A factory rather than a port, because the production adapter is built from
    the enrollment that this function is about to read; a factory rather than a
    monkeypatched global, because a test that reaches around the interface stops
    testing it.
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
        agent = RoomNativeAgent(
            binding_port=binding_port,
            room_port=InertRoomPort(),
            coding_session=InertCodingSession(),
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
