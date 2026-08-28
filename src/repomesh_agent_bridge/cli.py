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
from .adapters.governed_task import RepoMeshGovernedTaskAdapter
from .adapters.leader_actions import RepoMeshLeaderActionAdapter
from .adapters.leader_session import LeaderCoordinationSession
from .adapters.matrix import MatrixRoomAdapter
from .adapters.memory import InertCodingSession
from .adapters.repomesh_binding import RepoMeshBindingAdapter
from .adapters.restricted_process import RestrictedProcessFactory
from .application import (
    RoomNativeAgent,
    StartupOutcome,
    _startup,
    resolve_env_credential,
)
from .contracts import (
    BridgeStartupError,
    EnrollmentInvalid,
    ExternalWorkerEnrollment,
    read_enrollment,
)
from .instance_lock import InstanceAlreadyRunning
from .ports import CodingSessionPort, RoomTransportError, WorkerBindingPort
from .runner_consumer import (
    GovernedRunConsumer,
    GovernedRuntime,
    build_runner_consumer,
    prepare_governed_codex_home,
    runner_state_root,
)
from .state import BridgeState
from .supervisor import LeaderRuntime

__all__ = ["EXIT_ALREADY_RUNNING", "EXIT_OK", "EXIT_STARTUP_REFUSED", "main"]

_logger = logging.getLogger(__name__)

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
GovernedRuntimeFactory = Callable[[ExternalWorkerEnrollment], GovernedRuntime]


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
    run.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="turn on governed execution: accept 'start task <id>' from a room and "
        "consume this worker's RepoMesh runner queue, executing leased tasks in "
        "worktrees under this existing directory. Workers only — a repository "
        "leader is never given a repository",
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
    governed_runtime_factory: GovernedRuntimeFactory | None = None,
) -> int:
    """Entry point. The three factories are the seams tests replace.

    Factories rather than the ports themselves, because every production adapter
    is built from the enrollment this function is about to read; factories rather
    than monkeypatched globals, because a test that reaches around the interface
    stops testing it. ``coding_session_factory`` lets a test drive the run path
    without a real CLI spawn — a session whose gate refuses (a missing binary,
    say) reaches ``main``'s exit mapping exactly as the real one would — and
    ``governed_runtime_factory`` does the same for the control plane and the
    Runner loop, neither of which a test may stand up for real.
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

        workspace_root = _governed_workspace_root(arguments, enrollment)
        # One factory for both halves of the process: the coding session's turns
        # and a governed run's driver spawn through the same containment
        # boundary, and two factories would be two Low-integrity stories to keep
        # true on one machine.
        process_factory = RestrictedProcessFactory()

        def build_session(enrolled: ExternalWorkerEnrollment) -> CodingSessionPort:
            if coding_session_factory is not None:
                return coding_session_factory(enrolled)
            return _build_coding_session(
                enrolled,
                inert=arguments.inert,
                state_dir=arguments.state_dir,
                process_factory=process_factory,
            )

        def build_governed(enrolled: ExternalWorkerEnrollment) -> GovernedRuntime | None:
            if workspace_root is None:
                return None
            if governed_runtime_factory is not None:
                return governed_runtime_factory(enrolled)
            return _build_governed_runtime(
                enrolled,
                workspace_root=workspace_root,
                state_dir=arguments.state_dir,
                process_factory=process_factory,
            )

        # Named rather than passed inline because the leader lane reads the same
        # session: one codex stack, two readings of its answer (B2-1), and a
        # second stack would be a second containment story on one machine.
        session = build_session(enrollment)
        agent = RoomNativeAgent(
            binding_port=binding_port,
            room_port=MatrixRoomAdapter(),
            coding_session=session,
            state_dir=arguments.state_dir,
            governed=build_governed(enrollment),
            leader=_build_leader_runtime(enrollment, session=session),
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
    """Read the enrollment file at whichever version it declares itself to be.

    Both versions are accepted because both are live: a deployed worker Bridge
    keeps its v1 document indefinitely, and a Repository Leader is only
    expressible in v2. Which one arrived is not a flag anybody passes — the
    document says so, and ``read_enrollment`` refuses one that says neither.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as unreadable:
        raise EnrollmentInvalid(f"cannot read enrollment file: {path}") from unreadable
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as malformed:
        raise EnrollmentInvalid(f"enrollment file is not valid JSON: {path}") from malformed
    return read_enrollment(payload)


def _default_binding_port(enrollment: ExternalWorkerEnrollment) -> WorkerBindingPort:
    del enrollment  # the adapter reads the endpoint per call, from the enrollment it is given
    return RepoMeshBindingAdapter()


def _governed_workspace_root(
    arguments: argparse.Namespace, enrollment: ExternalWorkerEnrollment
) -> Path | None:
    """Read ``--workspace-root``, or refuse an invocation that cannot honour it.

    A ``repository_leader`` enrollment is refused outright, and this is the
    first of AC-02's three layers of defence in depth: a leader decides and does
    not code, so a workspace is not something it may be given by accident, by a
    copied command line, or by an operator who reached for the flag out of
    habit. The other two layers are elsewhere and are not weaker for being
    later — the coordination session is handed text and never a repository
    (adjudication D-8), and RepoMesh's own worker-only checks still refuse a
    leader token that asks to start a coding task. This one is here because it
    is the cheapest place to say no: before a lock, before a socket, before any
    process exists.

    That a leader consumes no Runner queue then follows from the same refusal
    rather than from a second check: ``--workspace-root`` is the only thing that
    turns governed execution on at all, so an invocation that cannot carry the
    flag cannot reach the consumer either.

    ``--inert`` is the deliberate way to bring a Bridge up with a stand-in that
    codes nothing, so asking for it *and* for governed execution is asking for
    two incompatible things in one command line; guessing which one was meant
    would either start runs against a stand-in or silently ignore the flag an
    operator typed. A path that is not an existing directory is refused here
    rather than at the first lease, because the platform prepares worktrees
    *under* this directory and discovering it is missing an hour later means one
    task already dispatched to a worker that cannot run it.
    """

    root: Path | None = arguments.workspace_root
    if root is None:
        return None
    if enrollment.is_repository_leader:
        raise BridgeStartupError(
            "--workspace-root turns on governed execution, which is a worker's job; this "
            f"enrollment is a {enrollment.role} and a leader is never given a repository, "
            "not even a read-only one. Drop --workspace-root"
        )
    if arguments.inert:
        raise BridgeStartupError(
            "--workspace-root turns on governed execution and --inert turns off the "
            "coding session that would carry it out; choose one"
        )
    if not root.is_dir():
        raise BridgeStartupError(
            f"--workspace-root must name an existing directory: {root}"
        )
    return root


def _build_leader_runtime(
    enrollment: ExternalWorkerEnrollment, *, session: CodingSessionPort
) -> LeaderRuntime | None:
    """Assemble the Repository Leader lane, for the enrollments that have one.

    Built from the *role* and from nothing else: there is no flag that turns
    this on, because a leader has no other job. That is the mirror image of
    governed execution, which no leader may have at any price
    (:func:`_governed_workspace_root`), and between them the two roles get
    disjoint capabilities out of one composition root rather than out of an
    operator's command line (AC-02).

    ``credentialRefs.repomesh`` is required outright, as it is for governed
    execution and for the same reason: every leader action is authenticated as
    this member, and RepoMesh decides what the token's owner may do. Under
    adjudication D-6 the slot's historical name still says ``repomesh``; what it
    holds for a leader is the external *member* token.

    The lane needs the driver session's other reading, so a Bridge brought up
    with a stand-in has no leader lane and says so in the room the first time a
    notice arrives. That is the same answer ``--inert`` gives the conversation
    track — an honest limitation rather than a silent one — and it is preferred
    to refusing the invocation, because bringing a member up in its rooms before
    the CLI is installed is a legitimate step.
    """

    if not enrollment.is_repository_leader:
        return None
    reference = enrollment.credential_refs.repomesh
    if reference is None:
        raise BridgeStartupError(
            "a repository leader needs credentialRefs.repomesh: reading an assignment and "
            "submitting a plan or a verdict are all authenticated as this member"
        )
    if not isinstance(session, DriverCodingSession):
        _logger.warning(
            "this instance serves a %s with no real coding session, so it can hear its "
            "rooms but cannot plan or review",
            enrollment.role,
        )
        return None
    actions = RepoMeshLeaderActionAdapter(
        endpoint=enrollment.repomesh_endpoint,
        # Resolved per call, never held: the secret's lifetime is the request's,
        # exactly as it is for preflight, for Matrix and for the start action.
        credential=lambda: resolve_env_credential(reference),
    )
    return LeaderRuntime(
        actions=actions,
        session=LeaderCoordinationSession(session),
        close=actions.close,
    )


def _build_governed_runtime(
    enrollment: ExternalWorkerEnrollment,
    *,
    workspace_root: Path,
    state_dir: Path | None,
    process_factory: RestrictedProcessFactory,
) -> GovernedRuntime:
    """Assemble both halves of governed execution (J-10, J-11).

    The profile check is the session path's, for the session path's reason: only
    ``codex`` has a real adapter in this build, and a Bridge that leased a task
    it has no CLI for would fail every run after telling the room it had started
    one. It is repeated here rather than left to ``_build_coding_session``
    because a test may substitute the session and must not thereby acquire a
    governed runtime the real assembly would have refused.

    ``credentialRefs.repomesh`` is required outright. It is the credential the
    lease, the events and the start action are all authenticated with, and
    RepoMesh scopes the lease to the worker that token names, so an enrollment
    without one cannot execute anything — whatever the preflight port thought it
    needed.
    """

    if enrollment.coding_profile != CODEX_PROFILE_ID:
        raise BridgeStartupError(
            f"this build has no coding adapter for profile {enrollment.coding_profile!r}, so "
            "it cannot execute governed runs; drop --workspace-root to serve conversation only"
        )
    reference = enrollment.credential_refs.repomesh
    if reference is None:
        raise BridgeStartupError(
            "governed execution needs credentialRefs.repomesh: the lease, the run events "
            "and the start action are all authenticated as this worker"
        )
    # Before ``RoomNativeAgent`` exists, and therefore before the conversation
    # track's readiness gate starts a codex that reads this file. The repair used
    # to run after that gate, which meant a configuration codex refused took the
    # gate down and took the repair with it — a Bridge locked out by the one file
    # it knows how to fix.
    prepare_governed_codex_home(session_root(enrollment.worker_agent_id, state_dir))
    return GovernedRuntime(
        task_port=RepoMeshGovernedTaskAdapter(
            endpoint=enrollment.repomesh_endpoint,
            # Resolved per call, never held: the secret's lifetime is the
            # request's, exactly as it is for preflight and for Matrix.
            credential=lambda: resolve_env_credential(reference),
            adapter_id=enrollment.coding_profile,
        ),
        build_consumer=lambda state: _build_consumer(
            state,
            enrollment,
            workspace_root=workspace_root,
            state_dir=state_dir,
            process_factory=process_factory,
            control_token=resolve_env_credential(reference),
        ),
    )


def _build_consumer(
    state: BridgeState,
    enrollment: ExternalWorkerEnrollment,
    *,
    workspace_root: Path,
    state_dir: Path | None,
    process_factory: RestrictedProcessFactory,
    control_token: str,
) -> GovernedRunConsumer:
    """The Runner consumer, built against the state file ``run`` has just opened."""

    return build_runner_consumer(
        state,
        worker_agent_id=enrollment.worker_agent_id,
        worker_name=enrollment.worker_name,
        endpoint=enrollment.repomesh_endpoint,
        control_token=control_token,
        workspace_root=workspace_root,
        session_dir=session_root(enrollment.worker_agent_id, state_dir),
        ledger_dir=runner_state_root(enrollment.worker_agent_id, state_dir),
        process_factory=process_factory,
    )


def _build_coding_session(
    enrollment: ExternalWorkerEnrollment,
    *,
    inert: bool,
    state_dir: Path | None,
    process_factory: RestrictedProcessFactory,
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
    ``RoomNativeAgent.run``. The factory arrives from the caller because a
    governed instance launches its runs through the same one.
    """

    if inert:
        return InertCodingSession(worker_name=enrollment.worker_name)
    if enrollment.coding_profile != CODEX_PROFILE_ID:
        raise BridgeStartupError(
            f"this build has no coding adapter for profile {enrollment.coding_profile!r}; "
            "only 'codex' can serve a real session today. Re-run with --inert to bring the "
            "Bridge up with an honest stand-in instead"
        )
    return DriverCodingSession(
        AppServerDriver(process_factory),
        process_factory,
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
        # Both sides, because the two agreeing is what stage 2 established and
        # a report that showed only one would hide the check it just ran.
        f"role: {enrollment.role} (RepoMesh confirms: {binding.role})",
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
        "governed execution: not wired (run --workspace-root turns it on)",
    ]
    print("\n".join(lines))
