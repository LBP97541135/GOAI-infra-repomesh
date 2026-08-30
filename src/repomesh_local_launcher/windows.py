"""The plane with this machine behind it.

Thin on purpose. Everything hard about starting a Bridge on Windows -- loading
``NAME=value`` credentials into the child's environment without echoing them,
giving a worker ``--workspace-root`` and a leader none, creating the process
without a console window, writing the PID file that is the only record of which
process serves which member -- is in ``scripts/start-local-cli.ps1`` and the
``bridge-e1`` scripts it wraps, where it has been run live. Reimplementing any
of it in Python would mean maintaining two answers to the same question and
having the operator's manual runs disagree with the Console's. So this module
shells out, and what it owns is only what a script invoked from a web page
cannot own: which members to invoke it for, and whether a PID may be believed.

Three facts about those scripts shape everything below.

*The start script refuses rather than skips.* ``start_members.ps1`` throws
"already has a live process; stop it first" when a member's PID file names a
live id, and ``$ErrorActionPreference = "Stop"`` means the throw abandons the
whole batch -- the members after it in the roster never start. So a second click
of "start" cannot simply re-run the wrapper: with one member already up it would
fail, and would fail *having started some of the others*. Idempotence (FR-02,
AC-01) is therefore ours: :meth:`WindowsMemberProcessPlane.start_all` observes
first and invokes the script once per member that is genuinely not running,
through ``-Only``. That also makes start and restart the same primitive.

*The stop script's identity check is coarser than ours.* It confirms a PID's
command line contains ``repomesh_agent_bridge`` before killing it, which is
already enough to save an unrelated Python process, but not enough to keep one
member's stale PID file from stopping another member's Bridge. FR-08 asks for
the finer check, so no kill is issued here for a member whose PID has not passed
:func:`is_member_process` -- ``stop_all`` names the members it has confirmed,
one at a time, and never lets the script decide on its own.

*The start script's guard is coarser still, and it is a wall.* Its "already
live" check is ``Get-Process -Id`` with no command line at all, so a PID file
naming a live process that is *not* this member's Bridge stops the launcher
dead: the finer check says the member is down, the coarser one throws rather
than start it, and the batch ends. Neither reporting it running nor killing that
PID is honest, so this module refuses instead and says which file to delete --
:class:`StalePidFileClaimed`, which the app answers as a 409.

*PID and log names come from the roster key.* ``<runtimeDir>/pids/<key>.pid``
and ``<runtimeDir>/logs/<key>.out.log``, written by the start script; this
module reads them and invents neither.

The roster is re-read on every call. It is a small file, the operator may edit
it between two clicks, and a cached copy would let the launcher answer for
members it is no longer configured to run.
"""

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .config import LauncherConfig
from .process import (
    MemberProcess,
    StalePidClaim,
    StalePidFileClaimed,
    UnknownMember,
    is_member_process,
)

__all__ = ["WindowsMemberProcessPlane"]

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
"""``src/repomesh_local_launcher/windows.py`` -> the checkout that owns the scripts.

The scripts are part of this repository, not of the operator's config: FR-09 is
that a caller cannot name a script path, and the launcher does not offer itself
one either.
"""

START_SCRIPT = REPOSITORY_ROOT / "scripts" / "start-local-cli.ps1"
STOP_SCRIPT = REPOSITORY_ROOT / "scripts" / "bridge-e1" / "stop_members.ps1"
POWERSHELL = ("powershell", "-NoProfile", "-File")


@dataclass(frozen=True, slots=True)
class RosterMember:
    """The three roster fields the launcher needs, out of the many it does not."""

    key: str
    agent_id: str
    role: str


@dataclass(frozen=True, slots=True)
class PidRecord:
    """What a member's PID file claims, before this machine is asked about it.

    Three states, because the file has three ways of not naming a process and
    only two of them are the same thing. ``pid`` set is the ordinary claim.
    ``pid`` unset with ``garbled`` false is "no claim yet" -- no file at all, or
    one still empty because ``Set-Content`` runs after ``Start-Process``.
    ``garbled`` is a file with something in it that is not a number, which is
    neither a claim nor a race: hand that member to the start script and its
    ``Get-Process -Id "abc"`` fails on the conversion, so the launcher stops it
    here and asks for the same thing it asks for any unusable PID file.
    """

    pid: int | None
    garbled: bool


@dataclass(frozen=True, slots=True)
class MemberObservation:
    """A member as this sweep found it: the public view, plus what blocks a start.

    ``stale`` is deliberately not part of :class:`MemberProcess`. The response
    shape is fixed at the process facts a page renders, and "there is a PID file
    in the way" is not one of them -- it is the reason a *start* refuses, and it
    is carried to the caller as a 409 rather than as a seventh column.
    """

    member: MemberProcess
    stale: StalePidClaim | None


class WindowsMemberProcessPlane:
    """The four operations, performed by PowerShell on this host."""

    def __init__(self, config: LauncherConfig) -> None:
        self._config = config

    def status(self) -> tuple[MemberProcess, ...]:
        return tuple(observation.member for observation in self._sweep())

    def start_all(self) -> tuple[MemberProcess, ...]:
        blocked = []
        for observation in self._sweep():
            if observation.stale is not None:
                blocked.append(observation.stale)
            elif not observation.member.running:
                self._start(observation.member.display_name)
        if blocked:
            raise StalePidFileClaimed(blocked)
        return self.status()

    def stop_all(self) -> tuple[MemberProcess, ...]:
        for member in self.status():
            if member.running:
                self._stop(member.display_name)
        return self.status()

    def restart(self, agent_id: str) -> tuple[MemberProcess, ...]:
        for observation in self._sweep():
            member = observation.member
            if member.agent_id == agent_id:
                if observation.stale is not None:
                    raise StalePidFileClaimed([observation.stale])
                if member.running:
                    self._stop(member.display_name)
                self._start(member.display_name)
                return self.status()
        raise UnknownMember(agent_id)

    def _sweep(self) -> tuple[MemberObservation, ...]:
        """Read every PID file, then ask this machine about all of them at once.

        One query per sweep rather than one per member. ``/v1/status`` is what
        the Console polls while members come up, and a PowerShell spawn per
        member per poll is a cost paid on every tick for an answer that is a
        single WMI filter away.
        """
        roster = self._roster()
        records = {member.key: self._recorded_pid(member.key) for member in roster}
        live = _command_lines(
            [record.pid for record in records.values() if record.pid is not None]
        )
        return tuple(self._observe(member, records[member.key], live) for member in roster)

    def _roster(self) -> tuple[RosterMember, ...]:
        document = json.loads(self._config.members_file.read_text(encoding="utf-8"))
        members = [
            RosterMember(key=entry["key"], agent_id=entry["agentId"], role=entry["role"])
            for entry in document["members"]
            # ``-contains`` on an absent property is false in PowerShell, and the
            # launcher has to select exactly the members the scripts will act on.
            if self._config.subset is None or self._config.subset in entry.get("subsets", [])
        ]
        return tuple(members)

    def _observe(
        self, member: RosterMember, record: PidRecord, live: dict[int, str]
    ) -> MemberObservation:
        """Decide what a member's PID file amounts to, given who is actually alive.

        Four outcomes. The PID is this member's Bridge, so it is running. The PID
        is not alive at all -- an ordinary stopped member, whose leftover file the
        start script clears itself. The file makes no claim yet, which is a
        member mid-start. Or the file stands in the way, which is the only case
        the launcher cannot act on and the only one it has to speak up about.

        A PID is "in the way" when it is alive and is not this member's Bridge.
        Note that this turns on ``is None``, not on truthiness, and the
        difference is load-bearing: a protected process answers the query with an
        **empty** command line, which is present-and-unverifiable, not absent.
        Reading it as absent -- ``live.get(pid) or None`` -- would quietly file
        that member under "stopped" and send the start script at a live PID it
        will then refuse.
        """
        command_line = None if record.pid is None else live.get(record.pid)
        confirmed = command_line is not None and is_member_process(command_line, member.key)
        blocked = record.garbled or (command_line is not None and not confirmed)
        return MemberObservation(
            member=MemberProcess(
                agent_id=member.agent_id,
                display_name=member.key,
                role=member.role,
                running=confirmed,
                pid=record.pid if confirmed else None,
                log_path=str(self._config.log_dir / f"{member.key}.out.log"),
            ),
            stale=(
                StalePidClaim(
                    member_name=member.key,
                    pid_file=str(self._config.pid_dir / f"{member.key}.pid"),
                )
                if blocked
                else None
            ),
        )

    def _recorded_pid(self, member_name: str) -> PidRecord:
        """Read the PID file, and tell "not yet" from "not a number".

        An empty or half-written file is "no claim yet" rather than an error,
        because it is a race and not corruption: ``start_members.ps1`` writes the
        file with ``Set-Content`` *after* ``Start-Process`` returns, so a sweep
        landing between the two finds a member with a real process and a file
        with nothing in it. The next sweep sees it.

        Content that is not a number is the other thing entirely, and it gets
        :attr:`PidRecord.garbled` so the operator is told to delete the file --
        the same fix, and the same 409, as any other PID file in the way.

        First line only, which is how the two scripts read it
        (``Select-Object -First 1``). ``isdecimal`` rather than ``isdigit``
        because it is the exact predicate for ``int()`` accepting the string.
        """
        pid_file = self._config.pid_dir / f"{member_name}.pid"
        if not pid_file.exists():
            return PidRecord(pid=None, garbled=False)
        lines = pid_file.read_text(encoding="utf-8").splitlines()
        recorded = lines[0].strip() if lines else ""
        if recorded.isdecimal():
            return PidRecord(pid=int(recorded), garbled=False)
        return PidRecord(pid=None, garbled=bool(recorded))

    def _start(self, member_name: str) -> None:
        arguments = [
            "-Members",
            str(self._config.members_file),
            "-EnrollmentDir",
            str(self._config.enrollment_dir),
            "-EnvFile",
            str(self._config.env_file),
            "-RuntimeDir",
            str(self._config.runtime_dir),
            "-Only",
            member_name,
        ]
        if self._config.workspace_root is not None:
            arguments += ["-WorkspaceRoot", str(self._config.workspace_root)]
        _run(START_SCRIPT, arguments)

    def _stop(self, member_name: str) -> None:
        _run(
            STOP_SCRIPT,
            [
                "-Members",
                str(self._config.members_file),
                "-PidDir",
                str(self._config.pid_dir),
                "-Only",
                member_name,
            ],
        )


def _run(script: Path, arguments: list[str]) -> None:
    """Run one of the two scripts, and let it fail in the operator's face.

    Output is not captured: the scripts narrate what they did, the launcher runs
    in a window the operator opened, and that window is the right place for it.
    A non-zero exit raises, which the app answers as a 500 carrying no detail --
    the reason belongs on this machine's console, not in a body a browser reads.
    """
    subprocess.run([*POWERSHELL, str(script), *arguments], check=True)


def _command_lines(pids: Sequence[int]) -> dict[int, str]:
    """Ask this machine, in one query, what each of *pids* is running.

    ``Get-CimInstance Win32_Process`` is the only thing on Windows that sees a
    full argument list, which is what the identity check needs and what
    ``Get-Process`` cannot give. The filter names the exact ids being asked
    about: enumerating every process on the machine is what
    ``stop_members.ps1 -Sweep`` is for, and that is an operator's recovery tool,
    not something a web page gets to trigger.

    The ids are interpolated into the filter and every one of them is an ``int``
    this module parsed out of a PID file, so no caller-supplied text reaches it.

    Output is one ``<pid>\\t<command line>`` line per process that exists, which
    survives the two shapes JSON would not: Windows PowerShell 5.1 serialises a
    single match as an object rather than a one-element array, and a match with
    no command line at all (a protected process) still has to come back so the
    identity check can reject it. A PID with no line is simply absent from the
    map, which is how a stopped member is told from a live impostor.
    """
    if not pids:
        return {}
    matches = " OR ".join(f"ProcessId={pid}" for pid in pids)
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f'Get-CimInstance Win32_Process -Filter "{matches}" | '
            # Deliberately not an f-string: the rest is PowerShell's own syntax,
            # and its ``{ }`` block and ``$( )`` would have to be doubled and
            # escaped to survive one.
            'ForEach-Object { "$($_.ProcessId)`t$($_.CommandLine)" }',
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    found: dict[int, str] = {}
    for line in completed.stdout.splitlines():
        pid, _, command_line = line.partition("\t")
        if pid.strip().isdecimal():
            # First line wins. A command line containing a newline arrives split
            # across rows, and the tail of it could be read as another PID's
            # entry; ``setdefault`` means such a row can add a process the query
            # did not ask about, but can never overwrite one it did.
            found.setdefault(int(pid), command_line)
    return found
