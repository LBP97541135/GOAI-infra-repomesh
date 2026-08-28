"""The Bridge as its own worker's Runner consumer (ADR 0004, PR 5 J-11..J-14).

A governed run reaches this machine the same way it reaches a containerised
worker — RepoMesh leases it over ``runner-tasks/next`` and reads the outcome back
over ``runner-events`` — and it is executed by the *same* code a containerised
worker executes it with. Nothing here re-implements a Runner: this module is a
composition root that assembles ``repomesh_runner``'s task source, event sink,
ledger, driver executor and serve loop, wraps two of them thinly, and hands the
result to :class:`~repomesh_agent_bridge.application.RoomNativeAgent`.

**One consumer, in this process.** The Bridge holds the worker's identity and its
worker-scoped credential, so a second ``repomesh-runner`` pointed at the same
worker would be a competitor for the same lease: two processes, one queue, and
whichever polled first would run work the other was told about. The Bridge is
therefore the consumer, and the loop lives beside the room loop rather than in a
sibling process.

Three thin wrappers, and each exists for a reason the Runner could not carry:

* :class:`GovernedDriver` puts an environment on the driver request.
  ``DriverExecutor`` builds one with an *empty* environment, and the Bridge's
  restricted factory never merges ``os.environ`` — deliberately, so nothing of
  the operator's leaks — so a governed codex would spawn with no ``SystemRoot``,
  no ``PATH`` and no ``CODEX_HOME`` and die before it said anything. The
  environment is the very one the conversation track builds
  (:func:`~repomesh_agent_bridge.adapters.coding_session.session_environment`),
  not a copy of it.
* :class:`NarratingExecutor` puts the run back into the conversation that asked
  for it. RepoMesh knows the task, the worker and the run and knows nothing about
  Matrix; the anchor written when the room woke the run up is the only thing that
  ties the two together, so a run with no anchor is executed in silence and its
  truth travels — as it always does — through the event sink.
* Both are wrappers rather than edits: ``src/repomesh_runner`` is composed and
  never modified, which is what keeps one execution semantics for containerised
  and operator-hosted workers alike.

**The room never learns what the model said.** A governed run's terminal message
is built from structured evidence — how many files changed, the short commit sha,
how the test commands exited, how many tool actions were asked for and denied.
``RunnerExecutionResult.summary`` reaches a room only for a status that is *not*
success, and only when it is the machine's own words about a gate that closed
(``changed_path_denied: ...``, ``test_command_failed: ...``); when the field
holds anything else — a CLI's diagnostics, git's stderr — the room gets a
pointer at the log instead, because the frozen contract bans unsanitized output
from a room whatever its length. A successful run carrying the model's prose
would read as the model certifying its own work, which is exactly the four-layer
false-green this line exists to kill.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from repomesh_runner.contracts import (
    RunnerExecutionResult,
    RunnerResultStatus,
    RunnerTask,
    TestCommandResult,
)
from repomesh_runner.drivers.app_server import AppServerDriver
from repomesh_runner.drivers.base import (
    DriverEvent,
    DriverEventKind,
    DriverFamily,
    DriverObserver,
    DriverRequest,
    DriverResult,
    PermissionDecision,
    PermissionPolicy,
    ProtocolDriver,
)
from repomesh_runner.drivers.supervision import ProcessFactory, resolve_binary
from repomesh_runner.engine import RunnerEventSink, RunnerExecutor
from repomesh_runner.event_sink import HttpEventSink
from repomesh_runner.executor import DriverExecutor
from repomesh_runner.main import Shutdown
from repomesh_runner.main import serve as serve_runner
from repomesh_runner.profiles import CliProfile
from repomesh_runner.state_store import TaskLedger
from repomesh_runner.task_source import HttpLongPollTaskSource, TaskSource

from .adapters.coding_session import (
    CODEX_PROFILE_ID,
    executable_path,
    session_environment,
)
from .adapters.restricted_process import prepare_session_dirs, set_low_integrity
from .contracts import RoomObservation, SessionNotReady
from .instance_lock import default_state_dir
from .outbox import RUN_LANE, Outbox, observation_id
from .ports import GovernedTaskPort
from .state import BridgeState, RunAnchor

__all__ = [
    "CODEX_TOOL_VOCABULARY",
    "EVENT_SINK_PATH",
    "GOVERNED_CODEX_CONFIG",
    "RUN_CRASHED_BODY",
    "RUN_STARTED_BODY",
    "TASK_SOURCE_PATH",
    "TERMINAL_KINDS",
    "TEST_COMPLETED_BODY",
    "GovernedDriver",
    "GovernedRunConsumer",
    "GovernedRuntime",
    "NarratingExecutor",
    "RunnerConsumer",
    "ToolActionTally",
    "WorkspaceNotWritable",
    "WorkspacePreparer",
    "build_runner_consumer",
    "governed_environment",
    "runner_state_root",
]

_logger = logging.getLogger(__name__)

TASK_SOURCE_PATH = "/api/v1/runtime/runner-tasks/next"
EVENT_SINK_PATH = "/api/v1/runtime/runner-events"
"""Where RepoMesh serves the execution plane, under the enrollment's endpoint.

No ``workerAgentId`` query parameter accompanies either one. The Bridge presents
its ``credentialRefs.repomesh`` token, which RepoMesh scopes to the worker it was
issued for (WO-B), so the lease is narrowed on the server rather than requested
by the client — a client-supplied worker id would be a claim the server would
then have to check, and the credential already answers it.
"""

_NODE_BINARIES: tuple[str, ...] = ("node",)
"""codex ships here as an npm launcher that shells out to ``node``; both
directories have to be on the child's ``PATH``. Spelled again rather than
imported from the session adapter because it is one string, not a policy."""

RUN_STARTED_BODY = "The governed run is executing on this machine."
TEST_COMPLETED_BODY = "The task's test commands finished."
RUN_CRASHED_BODY = "The governed run could not be carried out on this machine."
"""The three canned lines. Only the terminal message carries evidence; these say
where the run got to and nothing a stranger in the room could act on."""

TERMINAL_KINDS: Mapping[RunnerResultStatus, str] = {
    RunnerResultStatus.SUCCEEDED: "run_completed",
    RunnerResultStatus.FAILED: "run_failed",
    RunnerResultStatus.INTERRUPTED: "run_interrupted",
    RunnerResultStatus.INPUT_REQUIRED: "blocked",
}
"""Runner status to ``room-observation.v1`` kind. Total over the status enum on
purpose: a status with no kind would be a run the room silently never hears the
end of, which is the one outcome worse than a bad word for it."""

STARTED_ORDINAL = 1
TEST_ORDINAL = 2
TERMINAL_ORDINAL = 3
"""Positions in the run lane of the trigger that woke this run up.

Zero belongs to the supervisor's ``run_accepted`` receipt, so a run's whole story
is one ordered sequence under one name. The positions are fixed rather than
counted because the messages arrive minutes apart, possibly across a restart, and
naming them is what makes a replay land on the row it landed on last time.
"""

_QUOTED_REASONS = (
    "changed_path_denied:",
    "test_command_failed:",
    "context_verification_failed:",
)
"""The gate reasons a room may read verbatim.

Each is platform-authored end to end — the executor's fixed prefix over the
task's own allowlist, test command or context manifest — so quoting one hands
the person who asked exactly what closed the gate, in nobody else's words.
Everything else a non-successful ``summary`` can hold is a tool's raw output:
``commit_failed:`` embeds git's stderr, and a driver-level failure carries the
CLI's own diagnostics there (``_to_runner_result``). The frozen observation
contract bans unsanitized stdout/stderr from a room outright — bounded is still
banned — so those cross as a name or not at all, and the operator's log keeps
the words.
"""

_MAX_REASON_CHARS = 200
"""Even a quotable reason is bounded: a room line is a summary, not a report."""

_MAX_TARGET_CHARS = 300
"""How much of a tool's target one log line carries. Bounded because a patch body
is unbounded and a log that swallowed one would bury the decision beside it."""

WorkspacePreparer = Callable[[Path], object]
"""``prepare(path)`` — make the platform's worktree writable by the restricted
child, reporting whether it worked. Production passes
:func:`~repomesh_agent_bridge.adapters.restricted_process.set_low_integrity`,
whose boolean is now the run's precondition rather than a diagnostic: see
:meth:`NarratingExecutor._label_workspace`."""

CODEX_TOOL_VOCABULARY: Mapping[str, tuple[str, ...]] = {
    "edit": ("fileChange",),
    "test": ("commandExecution",),
}
"""RepoMesh's capability words → the tool names codex's approvals actually carry.

Two vocabularies meet at this boundary and neither side can be moved. RepoMesh
grants capabilities in its own words — ``read``, ``edit``, ``test`` plus the MCP
operation ids a role's servers expose — and puts them in the task's
``allowed_tools``. codex asks for approval under the *item type* of the thing it
wants to do, and its app-server surface has exactly two:
``commandExecution`` and ``fileChange``. ``AllowlistPermissionPolicy`` compares
one against the other by set membership, so on a real governed run every single
approval falls through to "tool not in allowlist" and is denied — measured live
on 2026-08-28, where four consecutive codex requests were refused, nothing was
written and the run failed on its own untouched tests.

The translation is **additive and codex-only**: the granted words stay in the
list, so nothing that was permitted stops being permitted, and a task for any
other adapter passes through untouched.

Why this is a vocabulary bridge and not a grant of new power:

*   The path allowlist still decides. ``AllowlistPermissionPolicy`` consults
    ``denied_paths`` and then ``allowed_paths`` *before* it ever looks at the
    tool name, so a ``fileChange`` aimed outside the grant is refused at the
    path rule and never reaches the name rule this mapping affects.
*   The three hard gates are the server's and are untouched: a changed path off
    the allowlist voids the run before the tests, a failing test command fails
    it and commits nothing, and a commit happens only on a success that changed
    something.
*   The real boundary was never this callback. It is the Low-integrity token the
    child runs on and the single Low-labelled directory it can write; the
    protocol callback is the second, cooperative line the contract already
    describes it as.

What it *does* widen, stated plainly rather than buried: a grant carrying
``test`` now approves any ``commandExecution``, not only the task's own test
commands, because the policy reads string leaves and cannot see a path inside a
shell command string. The mitigation is the containment above, not this list.
"""


class WorkspaceNotWritable(RuntimeError):
    """The platform's worktree could not be made writable by the restricted child.

    Raised instead of executing, which is the whole point: an unlabelled worktree
    produces a run that reads its repository, changes nothing, fails its own
    untested tests and blames them — the failure mode measured live on
    2026-08-28, where the room was told ``test_command_failed`` about a workspace
    the agent had never been able to touch.

    It travels the failure path the Runner already has. ``ExecuteRunnerTask``
    turns any exception out of the executor into a ``runner.failed`` event for
    the control plane, and ``NarratingExecutor`` tells the room the run could not
    be carried out — existing words, existing ordinal, no new vocabulary in a
    frozen schema.

    The message names no path: it is copied into a control-plane event, and the
    machine that holds the workspace is the one with the log.
    """


def _translated(task: RunnerTask) -> RunnerTask:
    """Add the codex names for the capabilities this task was already granted.

    See :data:`CODEX_TOOL_VOCABULARY` for why this is a translation rather than a
    widening of the grant. Three things keep it narrow:

    *   it runs only for the codex adapter, so no other runtime's task is touched;
    *   it only ever appends, and only for a capability the grant already carries;
    *   an empty ``allowed_tools`` is left empty, because in this policy an empty
        allowlist means "no tool-name rule at all" and adding names to it would
        turn a task nobody scoped into one scoped to exactly two verbs.
    """

    permissions = task.permissions
    if task.adapter_id != CODEX_PROFILE_ID or not permissions.allowed_tools:
        return task
    granted = list(permissions.allowed_tools)
    for capability, aliases in CODEX_TOOL_VOCABULARY.items():
        if capability not in permissions.allowed_tools:
            continue
        # ``RunnerPermissions`` rejects duplicates outright, so a task that
        # already names the codex word (a future RepoMesh that learned it, or a
        # replay of a translated task) must not have it added twice.
        granted.extend(alias for alias in aliases if alias not in granted)
    if len(granted) == len(permissions.allowed_tools):
        return task
    return dataclasses.replace(
        task,
        permissions=dataclasses.replace(permissions, allowed_tools=tuple(granted)),
    )


class _LoggingPermissionPolicy:
    """The task's own policy, with every decision written to the operator's log.

    A wrapper rather than a change to the policy: ``DriverExecutor`` builds the
    real one from ``task.permissions`` and hands it over on the ``DriverRequest``,
    so the Bridge can decorate it on the way to the driver without owning the
    rules or restating them.

    This log line is the only place a denial is explained. The room gets a count
    (``4 tool action(s), 4 denied``) because a room is not a transcript, and the
    driver's ``PERMISSION_REQUEST`` event carries the tool name but not what the
    tool was pointed at — so before this wrapper, finding out *why* a governed run
    did nothing meant reading codex's own rollout files. The tool input is
    summarised and bounded here: it is a machine-local diagnostic, which is where
    the frozen contract has always said the words belong.

    No run id is written, because the serve loop runs one task at a time and has
    already logged ``accepted task run=… attempt=…`` above these lines. Carrying
    one would mean a second piece of per-execution mutable state shared between
    this wrapper and the executor, to restate something the line above already
    says.
    """

    def __init__(self, policy: PermissionPolicy) -> None:
        self._policy = policy

    def decide(self, tool_name: str, tool_input: Mapping[str, object]) -> PermissionDecision:
        decision = self._policy.decide(tool_name, tool_input)
        _logger.info(
            "permission %s: %s on %s", decision.value, tool_name, _target_summary(tool_input)
        )
        return decision


def _target_summary(tool_input: Mapping[str, object]) -> str:
    """A short, bounded rendering of what a tool was pointed at.

    Whatever string leaves the input has, in order, joined and cut. Deliberately
    not a parser: codex puts a command under ``command``, a patch under
    ``changes`` and neither shape is promised, so a summary that guessed at keys
    would go blank on the day the shape changed — which is the day the log is
    needed. Never rendered into a room.
    """

    leaves = [value for value in _string_leaves(tool_input) if value.strip()]
    if not leaves:
        return "(no target)"
    rendered = " ".join(leaves)
    return rendered[:_MAX_TARGET_CHARS] if len(rendered) > _MAX_TARGET_CHARS else rendered


def _string_leaves(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _string_leaves(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _string_leaves(item)


class RunnerConsumer(Protocol):
    """The half of governed execution that runs work, as ``run`` sees it.

    A protocol rather than a class so the application module depends on the shape
    and not on the HTTP composition below — which is what lets a lifecycle test
    substitute a consumer that crashes without standing up a control plane.
    """

    async def serve(self) -> None:
        """Lease and execute governed tasks until cancelled."""
        ...


@dataclass(frozen=True, slots=True)
class GovernedRuntime:
    """The two halves of governed execution, which only ever arrive together.

    A Bridge that accepted ``start task <id>`` and then never executed the run it
    started would be lying to the room in the most expensive way available: the
    person who asked has a receipt, RepoMesh has a lease nobody will take, and
    nothing anywhere says so. So the wake-up port and the consumer are one
    switch — ``--workspace-root`` — and one value.
    """

    task_port: GovernedTaskPort
    build_consumer: Callable[[BridgeState], RunnerConsumer]
    """Deferred because the consumer narrates into the Bridge's state file, and
    that file is opened inside ``run`` — after preflight and after the coding
    runtime's gate. Handing over a builder keeps the composition root's ordering
    intact instead of forcing the state file open early to satisfy a constructor.
    """


def runner_state_root(worker_agent_id: UUID, state_dir: Path | None = None) -> Path:
    """Where this worker's Runner ledger lives.

    Beside the state file, the instance lock and the session directories, under
    the same ``state_dir`` and the same worker dimension: the ledger is the local
    half of at-most-once execution for *this* worker, and a ledger shared between
    two workers would let one worker's finished key suppress the other's task.
    """

    return (state_dir or default_state_dir()) / "runner" / str(worker_agent_id)


GOVERNED_CODEX_CONFIG = """\
# Written by repomesh-agent-bridge when governed execution is turned on.
# sandbox_mode: codex's own filesystem sandbox is a nested restriction this
#   process cannot create. The Bridge already launches codex on a Low-integrity
#   token, and a Low-integrity process cannot build the restricted token codex's
#   Windows sandbox helper needs, so every apply_patch died in the helper before
#   it reached an approval ("windows sandbox failed: os error 5", measured
#   2026-08-28). Containment here is the restricted token and the single
#   Low-labelled worktree, not a second sandbox inside it.
# approval_policy: which is why this must stay a policy that asks. The protocol
#   approval callback is where every governed decision is made and where the
#   conversation track's deny-all is enforced, so a policy that stopped asking
#   would turn both tracks into "do it and report", which is the one outcome
#   neither track may have. codex 0.149.1 removed "untrusted" and accepts only
#   read-only, workspace-write or danger-full-access for the sandbox; the first
#   two are the ones that build the helper this process cannot build.
sandbox_mode = "danger-full-access"
approval_policy = "on-request"
"""
"""codex's own configuration for a Bridge that executes governed runs.

Written into the session's ``CODEX_HOME`` — which the conversation track shares,
because the two tracks are deliberately one codex identity on one machine with
one set of rollout files. That sharing is safe in exactly one direction and it is
worth being precise about which: the file removes codex's *internal* sandbox and
keeps its *approval* requests, and the conversation track's guarantee has never
rested on the former. Its policy denies every request it is asked about, so as
long as codex keeps asking, a room turn still executes nothing.

That "as long as" is a property of codex, not of this file, so it is checked
rather than assumed: a governed run logs every decision it makes (see
:class:`_LoggingPermissionPolicy`), and a build where the requests stop arriving
is a build where this file must not ship.

Only written when governed execution is on. A Bridge running conversation-only
(no ``--workspace-root``) never calls this path and its codex-home keeps whatever
defaults codex ships with.
"""


def governed_environment(session_dir: Path) -> dict[str, str]:
    """The six-key environment a governed codex is launched with (J-12).

    Exactly what the conversation track builds, from the same function and the
    same session directories: a governed run and a room turn are the same CLI on
    the same machine for the same worker, so they share ``CODEX_HOME`` (and with
    it the rollout files a resume needs) and the same writable scratch.

    The binaries are resolved here rather than passed in because this runs after
    the coding runtime's startup gate has already found them; a build where they
    are missing is a Bridge that has no business starting, which is what the
    refusal says.
    """

    node_binary = resolve_binary(_NODE_BINARIES)
    cli_binary = resolve_binary((CODEX_PROFILE_ID,))
    if node_binary is None or cli_binary is None:
        raise SessionNotReady(
            "governed execution needs both codex and Node.js on PATH; install them and "
            "start the Bridge again"
        )
    dirs = prepare_session_dirs(session_dir)
    _write_governed_codex_config(dirs.codex_home)
    return session_environment(dirs, executable_path(node_binary, cli_binary))


def _write_governed_codex_config(codex_home: Path) -> None:
    """Put :data:`GOVERNED_CODEX_CONFIG` in place, rewriting only on a change.

    Rewriting unconditionally would be simpler and worse: codex-home carries the
    Low label so that the CLI can maintain its own state there (PR 4 §7.4), which
    means a Low-integrity process can edit this file, and a Bridge that rewrote it
    every start would erase the evidence that something had. Comparing first
    turns that into a log line.
    """

    config = codex_home / "config.toml"
    try:
        current = config.read_text(encoding="utf-8")
    except OSError:
        current = None
    if current == GOVERNED_CODEX_CONFIG:
        return
    if current is not None:
        _logger.warning("codex config in %s differs from the governed one; rewriting", codex_home)
    config.write_text(GOVERNED_CODEX_CONFIG, encoding="utf-8")


@dataclass(slots=True)
class ToolActionTally:
    """How many tool actions one execution asked for, and how many were refused.

    Counts and nothing else. A tool name, its arguments and its output are the
    model's working, and a governance record that quoted them would be putting a
    transcript in a room; a count still answers the only question the room has —
    was this answer produced with the commands it wanted, or without them.
    """

    requested: int = 0
    denied: int = 0

    def reset(self) -> None:
        self.requested = 0
        self.denied = 0

    def observe(self, event: DriverEvent) -> None:
        """Fold one driver event in. Never raises: observers are told not to."""

        if event.kind is DriverEventKind.TOOL_USE:
            self.requested += 1
        # The decision is a fixed vocabulary word the driver puts on the event,
        # not content: reading it is what makes "denied" a fact rather than an
        # assumption, because a governed run's policy comes from the task's
        # permissions and may well have said yes.
        elif (
            event.kind is DriverEventKind.PERMISSION_REQUEST
            and event.payload.get("decision") == PermissionDecision.DENY.value
        ):
            self.denied += 1

    def phrase(self) -> str | None:
        """``N tool action(s), M denied``, or ``None`` when nothing was asked for.

        A denial with no tool action behind it still counts as something asked
        for: a run whose every request was refused is precisely the one a room
        must not read as a run that simply chose to do nothing.
        """

        if not (self.requested or self.denied):
            return None
        return f"{self.requested} tool action(s), {self.denied} denied"


class GovernedDriver:
    """A ``ProtocolDriver`` that supplies the environment, counts tool events and
    logs each permission decision.

    All three jobs belong to the same wrapper because all three are
    per-execution and the driver boundary is where an execution begins.
    ``family`` delegates, so the executor's driver lookup is unchanged.

    The decision log is put on here rather than anywhere else because this is the
    only place the Bridge holds the ``DriverRequest``, and the request is what
    carries the policy ``DriverExecutor`` built.
    """

    def __init__(
        self,
        driver: ProtocolDriver,
        *,
        environment: Mapping[str, str],
        tally: ToolActionTally,
    ) -> None:
        self._driver = driver
        self._environment = dict(environment)
        self._tally = tally

    @property
    def family(self) -> DriverFamily:
        return self._driver.family

    async def execute(
        self,
        request: DriverRequest,
        profile: CliProfile,
        observer: DriverObserver,
    ) -> DriverResult:
        def counting(event: DriverEvent) -> None:
            self._tally.observe(event)
            observer(event)

        return await self._driver.execute(
            dataclasses.replace(
                request,
                environment=self._environment,
                permission_policy=_LoggingPermissionPolicy(request.permission_policy),
            ),
            profile,
            counting,
        )


class NarratingExecutor:
    """Executes a leased task, and tells the room that asked for it what happened.

    Wraps a real ``DriverExecutor``: the governance gates — a changed path off
    the allowlist fails the run before the tests and before any commit, a failing
    test command fails it and commits nothing, a commit happens only on a success
    that changed something — are the Runner's and are *reused*, never restated
    here. What this class adds is a second audience.

    A task whose ``run_id`` has no anchor is executed in silence. That is not a
    gap: a run RepoMesh dispatched on its own was never asked for in a room, so
    there is no thread to narrate into, and the structured truth about it travels
    through the event sink either way.
    """

    def __init__(
        self,
        executor: RunnerExecutor,
        *,
        state: BridgeState,
        outbox: Outbox,
        worker_agent_id: UUID,
        worker_name: str,
        tally: ToolActionTally,
        prepare_workspace: WorkspacePreparer,
    ) -> None:
        self._executor = executor
        self._state = state
        self._outbox = outbox
        self._worker_agent_id = worker_agent_id
        self._worker_name = worker_name
        self._tally = tally
        self._prepare_workspace = prepare_workspace

    async def execute(self, task: RunnerTask) -> RunnerExecutionResult:
        # The serve loop runs one task at a time and never concurrently, which is
        # what makes a single tally on this executor a per-execution counter
        # rather than a shared accumulator. Reset here rather than in the driver
        # so a task that never reaches a driver cannot report the last one's
        # numbers.
        self._tally.reset()
        # Translated once, here, so everything downstream — the policy
        # ``DriverExecutor`` builds, the evidence gates, the narration — sees one
        # task. The room-facing ids and the workspace are untouched by it.
        task = _translated(task)
        anchor = self._state.anchor_for_run(task.run_id)
        if anchor is None:
            _logger.info("run %s was not started from a room; executing it quietly", task.run_id)
            self._label_workspace(task)
            return await self._executor.execute(task)

        self._observe(anchor, task, STARTED_ORDINAL, "run_started", RUN_STARTED_BODY)
        try:
            # Inside the try, and before the executor: a workspace the child
            # cannot write is a run that cannot happen, and saying so here is
            # what stops it being reported an hour later as a test failure.
            self._label_workspace(task)
            result = await self._executor.execute(task)
        except BaseException:
            # The room is told the run did not happen; the exception carries on
            # to the serve loop, whose witness decides what the ledger records.
            self._observe(anchor, task, TERMINAL_ORDINAL, "run_failed", RUN_CRASHED_BODY)
            raise
        self._observe_tests(anchor, task, result)
        self._observe(
            anchor,
            task,
            TERMINAL_ORDINAL,
            TERMINAL_KINDS[result.status],
            _terminal_body(result, self._tally),
            changed_files=result.changed_files or None,
            commit_sha=result.commit_sha,
        )
        return result

    def _label_workspace(self, task: RunnerTask) -> None:
        """Make the platform's worktree writable by the restricted child (J-13).

        The Bridge launches every CLI on a Low-integrity token, and the worktree
        RepoMesh prepared carries the default Medium label, so without this the
        agent can read its task's repository and change nothing in it — a run
        that fails for a reason nothing in its evidence explains.

        The cost is the same one the codex-home labelling already pays and is
        recorded in the PR 4 handoff (§7.4): a Low label is not addressed to this
        child, so *any* Low-integrity process on this machine may write the
        worktree while it is labelled. The alternative on offer is not a tighter
        label but no governed execution at all.

        **A failure here ends the run.** The label does not always stick, and the
        reason is an ownership fact nothing upstream checks: writing a mandatory
        label needs ``WRITE_OWNER`` on the directory, which an inherited
        ``Modify`` grant does not carry and which being the owner does not confer
        either. A workspace root outside the user's own profile therefore silently
        keeps its Medium label — measured live on 2026-08-28 under
        ``D:\\...\\workspaces``, where the run went ahead, the agent could not
        write a byte, and the room was told its tests had failed. The operator's
        fix is one command (``icacls <root> /grant <user>:(OI)(CI)F``); what this
        refusal buys is being told to run it.

        Only a task carrying a workspace assignment is labelled. A governed
        dispatch always carries one; a task without one runs in the executor's own
        transitional fallback directory, which this process created and already
        owns.
        """

        if task.workspace is None:
            return
        if not self._prepare_workspace(Path(task.workspace.path)):
            raise WorkspaceNotWritable(
                "the prepared worktree could not be labelled Low integrity, so the "
                "restricted agent process cannot write it; grant this machine's user "
                "full control of the runner workspace root and start the run again"
            )

    def _observe_tests(
        self, anchor: RunAnchor, task: RunnerTask, result: RunnerExecutionResult
    ) -> None:
        """One message about the task's own verification, when there was any.

        The *first failing* command is the one reported, because that is the one
        that decided the run; with everything green the first command stands for
        the set. The command and its exit code travel in the observation's own
        fields, which is where the frozen schema puts them.
        """

        if not result.test_results:
            return
        reported = _first_failure(result.test_results)
        self._observe(
            anchor,
            task,
            TEST_ORDINAL,
            "test_completed",
            TEST_COMPLETED_BODY,
            test_command=reported.command,
            test_exit_code=reported.exit_code,
        )

    def _observe(
        self,
        anchor: RunAnchor,
        task: RunnerTask,
        ordinal: int,
        kind: str,
        body: str,
        **detail: object,
    ) -> None:
        """Write one lifecycle message at its known position in the run lane.

        Nothing is sent here. The supervisor drains the outbox at the head of
        every round, so a message written by this loop reaches its room on the
        room loop's next pass — which is also what keeps a single Matrix client
        and a single transaction-id space in one place.

        Enqueueing the same position with the same message is the no-op a replay
        must be, so this method needs no memory of what it has already said.
        """

        self._outbox.enqueue_at(
            room_id=anchor.room_id,
            thread_root_id=anchor.thread_root_id,
            trigger_event_id=anchor.trigger_event_id,
            observation=RoomObservation(
                observation_id=observation_id(
                    self._worker_agent_id,
                    anchor.room_id,
                    anchor.trigger_event_id,
                    RUN_LANE,
                    ordinal,
                ),
                emitted_at=self._state.now(),
                worker_name=self._worker_name,
                room_id=anchor.room_id,
                kind=kind,
                body=body,
                task_id=task.task_id,
                run_id=task.run_id,
                **detail,  # type: ignore[arg-type]
            ),
            lane=RUN_LANE,
            ordinal=ordinal,
        )


def _first_failure(results: tuple[TestCommandResult, ...]) -> TestCommandResult:
    return next((entry for entry in results if entry.exit_code != 0), results[0])


def _terminal_body(result: RunnerExecutionResult, tally: ToolActionTally) -> str:
    """What a room is told a governed run produced, from evidence alone.

    Every clause is something the Runner *observed*: git's own account of what
    changed, the commit it made, the exit codes the task's own test commands
    returned, and how many tool actions the permission policy saw. A successful
    run adds nothing else — in particular not ``summary``, which on success is
    the model's closing message and would make this record read as the model
    certifying its own work.

    A run that did not succeed carries a reason, and only a room-safe one: the
    platform-authored gate reasons (``changed_path_denied: ...``,
    ``test_command_failed: ... (exit code 1)``) verbatim, because the person who
    asked cannot act without them; anything else in that field is a tool's raw
    output and is replaced by a pointer at the machine that holds it.
    """

    parts = [f"{len(result.changed_files)} file(s) changed"]
    if result.commit_sha:
        parts.append(f"commit {result.commit_sha[:12]}")
    if result.test_results:
        reported = _first_failure(result.test_results)
        parts.append(
            "tests passed"
            if reported.exit_code == 0
            else f"tests failed (exit {reported.exit_code})"
        )
    if (actions := tally.phrase()) is not None:
        parts.append(actions)
    evidence = ", ".join(parts)
    if result.status is RunnerResultStatus.SUCCEEDED:
        return f"The governed run finished. {evidence}."
    reason = _room_safe_reason(result.summary)
    return f"The governed run ended {result.status.value}. {evidence}. Reason: {reason}"


def _room_safe_reason(summary: str) -> str:
    reason = summary.strip()
    if reason.startswith(_QUOTED_REASONS):
        return reason[:_MAX_REASON_CHARS]
    if reason.startswith("commit_failed:"):
        return "commit_failed (git's own words are in the run record)"
    return "this machine's log has the details"


class GovernedRunConsumer:
    """One worker's Runner loop, hosted by the Bridge process.

    Holds the two HTTP seams so it can close them, and nothing else: the loop
    itself, the idempotency ledger and the execution semantics are
    ``repomesh_runner``'s.
    """

    def __init__(
        self,
        *,
        source: TaskSource,
        sink: RunnerEventSink,
        executor: RunnerExecutor,
        ledger: TaskLedger,
    ) -> None:
        self._source = source
        self._sink = sink
        self._executor = executor
        self._ledger = ledger
        self._shutdown = Shutdown()
        """Never set. The Runner's own process ends on SIGTERM and needs a
        cooperative flag; this loop ends when the Bridge is cancelled, and
        cancellation arrives as an exception through the poll it is waiting on.
        Installing signal handlers here would be a second way to stop a process
        that already has one, and the second way is the one that is forgotten.
        """

    async def serve(self) -> None:
        """Lease and execute until cancelled, then hand both clients back.

        The close is in a ``finally`` because the way this loop ends is
        cancellation: a task group tearing the Bridge down cancels this task
        wherever it is waiting, and two httpx clients left open would outlive the
        process's own shutdown path.
        """

        try:
            await serve_runner(
                source=self._source,
                sink=self._sink,
                executor=self._executor,
                ledger=self._ledger,
                shutdown=self._shutdown,
            )
        finally:
            await self._source.aclose()
            await self._sink.aclose()


def build_runner_consumer(
    state: BridgeState,
    *,
    worker_agent_id: UUID,
    worker_name: str,
    endpoint: str,
    control_token: str,
    workspace_root: Path,
    session_dir: Path,
    ledger_dir: Path,
    process_factory: ProcessFactory,
    prepare_workspace: WorkspacePreparer = set_low_integrity,
) -> GovernedRunConsumer:
    """Assemble the real consumer: HTTP in, the real driver chain, HTTP out.

    The only registered driver is the app-server one, because ``codex`` is the
    only profile this build has a real adapter for; a task naming anything else
    is refused by the executor rather than quietly run with the wrong CLI.

    Nothing about the task's permissions is touched on the way through:
    ``DriverExecutor`` builds its own ``AllowlistPermissionPolicy`` from
    ``task.permissions``, and the evidence gates behind it are the ones every
    other worker runs under.
    """

    tally = ToolActionTally()
    driver = GovernedDriver(
        AppServerDriver(process_factory),
        environment=governed_environment(session_dir),
        tally=tally,
    )
    return GovernedRunConsumer(
        source=HttpLongPollTaskSource(
            endpoint.rstrip("/") + TASK_SOURCE_PATH, control_token=control_token
        ),
        sink=HttpEventSink(endpoint.rstrip("/") + EVENT_SINK_PATH, control_token=control_token),
        executor=NarratingExecutor(
            DriverExecutor(
                drivers={DriverFamily.APP_SERVER: driver},
                workspace_root=workspace_root,
            ),
            state=state,
            outbox=Outbox(state, worker_agent_id=worker_agent_id),
            worker_agent_id=worker_agent_id,
            worker_name=worker_name,
            tally=tally,
            prepare_workspace=prepare_workspace,
        ),
        ledger=TaskLedger(ledger_dir),
    )
