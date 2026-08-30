"""The seam the four operations act on, and the one rule that guards a kill.

:class:`MemberProcessPlane` is a seam for the ordinary reason: behind it are a
Windows host that really has processes on it and an in-memory double that never
will, and the app's behaviour -- which operations exist, who may call them, what
the answer may contain -- is worth stating without either. It carries exactly the
four operations FR-09 fixes and nothing that would let a caller invent a fifth.

:class:`MemberProcess` is a view and not a process handle. It is what a member
looks like from outside: a name, a role, whether something is running for it and
where that something writes. Deliberately no readiness field. Whether a member
can be given work is a lease the member itself reports to the control plane, and
a process that exists says nothing about it; the Console merges the two and the
gate reads only the lease. Keeping them apart is what makes "the launcher was
never running, the operator started the Bridge by hand, the member still goes
green" true for free.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "BRIDGE_MODULE_ARGUMENT",
    "MemberProcess",
    "MemberProcessPlane",
    "StalePidClaim",
    "StalePidFileClaimed",
    "UnknownMember",
    "is_member_process",
]

BRIDGE_MODULE_ARGUMENT = "-m repomesh_agent_bridge"
"""How ``start_members.ps1`` spells the module, verbatim.

It launches ``python.exe -m repomesh_agent_bridge run --enrollment <path>``, so
this substring is on the command line of every process this launcher may touch
and on no other process that matters.
"""


@dataclass(frozen=True, slots=True)
class MemberProcess:
    """One roster member, as this machine currently answers for it."""

    agent_id: str
    display_name: str
    role: str
    running: bool
    pid: int | None
    log_path: str | None


class UnknownMember(LookupError):
    """Raised when an operation names an agent id the local roster does not have.

    A distinct type because the answer is distinct: the caller asked about a
    member this machine does not run, which is a 404 and not a failure of the
    machine. It carries the id it was given and nothing about the roster.
    """


@dataclass(frozen=True, slots=True)
class StalePidClaim:
    """A PID file naming a live process that is not this member's Bridge."""

    member_name: str
    pid_file: str


class StalePidFileClaimed(RuntimeError):
    """Raised when starting a member would run into its own stale PID file.

    This is the one state the launcher can see clearly and cannot resolve on its
    own. The PID file names a process that is alive but is not this member's
    Bridge -- a recycled id, a hand-copied file, a session whose records outlived
    it. The launcher's own check (:func:`is_member_process`) is why the member is
    reported down and why no kill is aimed at that PID. But ``start_members.ps1``
    guards its launch with ``Get-Process -Id`` alone, which asks only whether the
    id is alive, so invoking it here would throw "already has a live process;
    stop it first" -- and with ``$ErrorActionPreference = "Stop"`` that throw
    ends the batch.

    So the launcher refuses first, and says which file to delete. Raised after
    the startable members have been started: one member's stale record is not a
    reason to leave the other five down.
    """

    def __init__(self, claims: Sequence[StalePidClaim]) -> None:
        super().__init__(f"{len(claims)} member(s) have a stale PID file")
        self.claims = tuple(claims)


class MemberProcessPlane(Protocol):
    """The four fixed operations, and the only surface the app is given.

    ``restart`` raises :class:`UnknownMember` for an agent id this machine does
    not run. ``start_all`` and ``restart`` raise :class:`StalePidFileClaimed`
    when a PID file stands in the way; both are refusals the app maps to a status
    code, which is why they are named here and not left to an adapter.
    """

    def status(self) -> tuple[MemberProcess, ...]: ...

    def start_all(self) -> tuple[MemberProcess, ...]: ...

    def stop_all(self) -> tuple[MemberProcess, ...]: ...

    def restart(self, agent_id: str) -> tuple[MemberProcess, ...]: ...


def is_member_process(command_line: str, member_name: str) -> bool:
    """Does this command line belong to *member_name*'s Bridge?

    This is FR-08's "never kill by PID alone" in one place. A PID file is a
    claim, not a fact: Windows reuses process ids freely, so a file left behind
    by a session that ended can name a live process belonging to somebody else
    entirely -- an editor, a Codex CLI, another member's Bridge. Killing on the
    strength of that file alone is how a launcher takes down the operator's
    unrelated work, so this answer stands between the PID file and both things
    the launcher does with it: reporting a member as running, and stopping it.

    Two substrings have to be present, and each rejects a different impostor.
    :data:`BRIDGE_MODULE_ARGUMENT` rejects everything that merely *mentions* a
    member -- the provisioning scripts and an open editor both hold the very
    same enrollment path. The enrollment file name rejects another member's
    Bridge, which is otherwise indistinguishable: six of them run at once, from
    one interpreter, differing only in that argument.

    The file name is matched whole, ``enrollment.<key>.json``, rather than the
    key alone. ``alpha`` is a prefix of ``alpha-leader``, and a bare-key match
    would let one member's launcher answer for another's process; the suffix is
    what makes the comparison exact without parsing a Windows command line.
    """
    return (
        BRIDGE_MODULE_ARGUMENT in command_line
        and f"enrollment.{member_name}.json" in command_line
    )
