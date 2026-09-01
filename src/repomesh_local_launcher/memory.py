"""The plane with no machine behind it.

It ships with the package rather than living in the test tree because it is the
counterpart every adapter here is expected to have, and because what it stands
in for is not a detail of the tests: the app's face -- four operations, three
guards, one response shape -- is meant to be statable without a Windows host,
and this is what makes that true. It records the calls it was asked for, so a
test can assert the thing that matters most about a refusal: that the operation
did not reach the machine at all.
"""

from collections.abc import Sequence
from dataclasses import replace

from .process import MemberProcess, UnknownMember

__all__ = ["MemoryMemberProcessPlane"]

FIRST_PID = 4001
"""What the first member started here is given. Any number does; a recognisable one
reads better in a failure."""


class MemoryMemberProcessPlane:
    """A roster that starts and stops in a list."""

    def __init__(self, members: Sequence[MemberProcess]) -> None:
        self._members = list(members)
        self._next_pid = FIRST_PID
        self.calls: list[str] = []

    def status(self) -> tuple[MemberProcess, ...]:
        self.calls.append("status")
        return tuple(self._members)

    def start_all(self) -> tuple[MemberProcess, ...]:
        self.calls.append("start_all")
        self._members = [
            member if member.running else self._started(member) for member in self._members
        ]
        return tuple(self._members)

    def stop_all(self) -> tuple[MemberProcess, ...]:
        self.calls.append("stop_all")
        self._members = [replace(member, running=False, pid=None) for member in self._members]
        return tuple(self._members)

    def restart(self, agent_id: str) -> tuple[MemberProcess, ...]:
        self.calls.append(f"restart:{agent_id}")
        if not any(member.agent_id == agent_id for member in self._members):
            raise UnknownMember(agent_id)
        self._members = [
            self._started(member) if member.agent_id == agent_id else member
            for member in self._members
        ]
        return tuple(self._members)

    def _started(self, member: MemberProcess) -> MemberProcess:
        """A member that is now running, under a pid nothing else has had."""
        pid = self._next_pid
        self._next_pid += 1
        return replace(member, running=True, pid=pid)
