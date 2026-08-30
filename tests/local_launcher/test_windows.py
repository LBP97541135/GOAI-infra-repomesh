"""The rules the launcher adds on top of the PowerShell scripts.

Everything else about starting a Bridge belongs to ``start-local-cli.ps1`` and is
verified by running it. What cannot be verified that way is the part the scripts
do not do and a web page makes necessary: deciding *which* members to invoke them
for. Several of those decisions are refusals -- do not start a member that is
already up, do not aim a kill at a PID that has not proved whose it is, do not
walk into a PID file the start script will throw on -- and a refusal is exactly
what a live smoke cannot show you, because the evidence is an absence.

So the host is faked at its two edges, the command-line query and the script
invocation. The fake does not merely record: starting a member writes its PID
file and puts a matching command line on the machine, stopping one takes both
away. Without that the plane could return a pre-start sweep and every assertion
about a start would still pass.
"""

import json
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from repomesh_local_launcher import windows
from repomesh_local_launcher.config import LauncherConfig
from repomesh_local_launcher.process import StalePidFileClaimed, UnknownMember
from repomesh_local_launcher.windows import (
    START_SCRIPT,
    STOP_SCRIPT,
    WindowsMemberProcessPlane,
)

LEADER_PID = 1001
FOREIGN_PID = 1003
DEAD_PID = 1004
PROTECTED_PID = 1005
FIRST_FAKE_PID = 5001
WORKSPACE_ROOT = Path("D:/Project4work/.repomesh-e1/workspaces")

ALPHA_LEADER = "4d1e6f00-0000-4000-8000-0000000000a1"
ALPHA_WORKER = "4d1e6f00-0000-4000-8000-0000000000a2"
BETA_LEADER = "4d1e6f00-0000-4000-8000-0000000000b1"
DELTA_WORKER = "4d1e6f00-0000-4000-8000-0000000000d1"
SIGMA_WORKER = "4d1e6f00-0000-4000-8000-0000000000e1"
GAMMA_LEADER = "4d1e6f00-0000-4000-8000-0000000000c1"

#: Members whose PID file stands in the way of a start, in roster order.
BLOCKED = ("beta-leader", "delta-worker", "sigma-worker")
#: Members that are down and startable while those three files are still there.
STARTABLE = ("alpha-worker", "beta-worker", "omega-worker")
#: And once the operator has deleted them, every member but the running one.
STARTABLE_UNBLOCKED = (
    "alpha-worker",
    "beta-leader",
    "beta-worker",
    "omega-worker",
    "delta-worker",
    "sigma-worker",
)


def member(key: str, agent_id: str, role: str, subset: str = "e1") -> dict[str, object]:
    return {"key": key, "role": role, "agentId": agent_id, "subsets": [subset]}


ROSTER = {
    "members": [
        member("alpha-leader", ALPHA_LEADER, "repository_leader"),
        member("alpha-worker", ALPHA_WORKER, "worker"),
        member("beta-leader", BETA_LEADER, "repository_leader"),
        member("beta-worker", "4d1e6f00-0000-4000-8000-0000000000b2", "worker"),
        member("omega-worker", "4d1e6f00-0000-4000-8000-0000000000f2", "worker"),
        member("delta-worker", DELTA_WORKER, "worker"),
        member("sigma-worker", SIGMA_WORKER, "worker"),
        member("gamma-leader", GAMMA_LEADER, "repository_leader", subset="other-scenario"),
    ]
}


def bridge_line(member_name: str) -> str:
    """What ``start_members.ps1`` puts on a member's command line."""
    return (
        r"D:\repo\.venv\Scripts\python.exe -m repomesh_agent_bridge run "
        rf"--enrollment D:\repo\out\enrollments\enrollment.{member_name}.json"
    )


@dataclass
class FakeHost:
    """This machine, at the two edges the Windows plane touches it.

    Starting and stopping have consequences here, because the thing worth
    asserting about ``start_all`` is that the status it hands back is the one
    from *after* the work.
    """

    pid_dir: Path
    live: dict[int, str]
    invocations: list[tuple[Path, list[str]]] = field(default_factory=list)
    queries: list[tuple[int, ...]] = field(default_factory=list)
    next_pid: int = FIRST_FAKE_PID

    def run(self, script: Path, arguments: list[str]) -> None:
        name = _only(arguments)
        self.invocations.append((script, arguments))
        if script == START_SCRIPT:
            self.live[self.next_pid] = bridge_line(name)
            (self.pid_dir / f"{name}.pid").write_text(f"{self.next_pid}\n", encoding="utf-8")
            self.next_pid += 1
        else:
            pid_file = self.pid_dir / f"{name}.pid"
            self.live.pop(int(pid_file.read_text(encoding="utf-8").strip()), None)
            pid_file.unlink()

    def command_lines(self, pids: list[int]) -> dict[int, str]:
        self.queries.append(tuple(pids))
        return {pid: self.live[pid] for pid in pids if pid in self.live}

    def started(self) -> list[str]:
        return self._only_for(START_SCRIPT)

    def stopped(self) -> list[str]:
        return self._only_for(STOP_SCRIPT)

    def _only_for(self, script: Path) -> list[str]:
        return [_only(arguments) for called, arguments in self.invocations if called == script]


def _only(arguments: list[str]) -> str:
    return arguments[arguments.index("-Only") + 1]


@pytest.fixture
def config(tmp_path: Path) -> LauncherConfig:
    """A roster holding every PID state that matters, all at once.

    ``alpha-leader`` is up. ``alpha-worker`` has no PID file and ``beta-worker``
    has one naming a process that has exited -- the two ordinary ways to be
    down. ``omega-worker``'s file is empty, which is the race and not corruption:
    ``Set-Content`` lands after ``Start-Process`` returns. The last three are the
    ways a file can stand in the way: ``beta-leader`` names a live process that
    is another member's Bridge, ``delta-worker`` names a live process that
    answers with no command line at all, and ``sigma-worker``'s file is not a
    number.
    """
    members_file = tmp_path / "members.json"
    members_file.write_text(json.dumps(ROSTER), encoding="utf-8")
    pid_dir = tmp_path / "runtime" / "pids"
    pid_dir.mkdir(parents=True)
    (pid_dir / "alpha-leader.pid").write_text(f"{LEADER_PID}\n", encoding="utf-8")
    (pid_dir / "beta-leader.pid").write_text(f"{FOREIGN_PID}\n", encoding="utf-8")
    (pid_dir / "beta-worker.pid").write_text(f"{DEAD_PID}\n", encoding="utf-8")
    (pid_dir / "omega-worker.pid").write_text("", encoding="utf-8")
    (pid_dir / "delta-worker.pid").write_text(f"{PROTECTED_PID}\n", encoding="utf-8")
    (pid_dir / "sigma-worker.pid").write_text("not-a-pid\n", encoding="utf-8")
    return LauncherConfig(
        members_file=members_file,
        enrollment_dir=tmp_path / "enrollments",
        env_file=tmp_path / "members.env",
        runtime_dir=tmp_path / "runtime",
        workspace_root=WORKSPACE_ROOT,
        subset="e1",
        roster_version="e1-2026-08-29",
        allowed_origins=("http://127.0.0.1:5280",),
        port=8121,
    )


@pytest.fixture
def host(config: LauncherConfig, monkeypatch: pytest.MonkeyPatch) -> FakeHost:
    """One live Bridge, one live impostor, one process that will not say."""
    recorded = FakeHost(
        pid_dir=config.pid_dir,
        live={
            LEADER_PID: bridge_line("alpha-leader"),
            # Alive, and a Bridge -- just not beta-leader's. Nothing weaker than
            # the enrollment name tells this from the real thing, and the stop
            # script's own "*repomesh_agent_bridge*" check would wave it through.
            FOREIGN_PID: bridge_line("alpha-leader"),
            # Alive and answering with nothing: a protected process. Present and
            # unverifiable, which is not the same as absent.
            PROTECTED_PID: "",
        },
    )
    monkeypatch.setattr(windows, "_run", recorded.run)
    monkeypatch.setattr(windows, "_command_lines", recorded.command_lines)
    return recorded


@pytest.fixture
def plane(config: LauncherConfig) -> WindowsMemberProcessPlane:
    return WindowsMemberProcessPlane(config)


@pytest.fixture
def unblocked_plane(config: LauncherConfig, host: FakeHost) -> WindowsMemberProcessPlane:
    """The same machine after the operator has done what the 409 asked.

    The three PID files that stood in the way are gone, which is the state every
    ordinary start happens in and the only one where ``start_all`` returns rather
    than raises.
    """
    for member_name in BLOCKED:
        (config.pid_dir / f"{member_name}.pid").unlink()
    return WindowsMemberProcessPlane(config)


def test_status_believes_a_pid_only_once_the_process_agrees(
    plane: WindowsMemberProcessPlane, host: FakeHost
) -> None:
    members = {member.display_name: member for member in plane.status()}

    assert members["alpha-leader"].running is True
    assert members["alpha-leader"].pid == LEADER_PID
    assert members["alpha-worker"].running is False
    assert members["beta-worker"].running is False
    # A PID file is a claim. These name live processes that are not this
    # member's, and reporting either as running would be the first half of
    # killing the wrong process.
    assert members["beta-leader"].running is False
    assert members["beta-leader"].pid is None
    assert members["delta-worker"].running is False
    assert members["delta-worker"].pid is None
    assert host.invocations == []


def test_an_unwritten_pid_file_reads_as_down_rather_than_as_an_error(
    plane: WindowsMemberProcessPlane, host: FakeHost
) -> None:
    """The start script writes the file after the process exists, so this is a race."""
    members = {member.display_name: member for member in plane.status()}

    assert members["omega-worker"].running is False
    assert members["omega-worker"].pid is None
    # Neither the empty file nor the unparseable one is offered to the machine
    # as a PID to look up.
    assert host.queries == [(LEADER_PID, FOREIGN_PID, DEAD_PID, PROTECTED_PID)]


def test_status_asks_this_machine_once_however_many_members_there_are(
    plane: WindowsMemberProcessPlane, host: FakeHost
) -> None:
    """One query per sweep, not one per member: the Console polls this route."""
    plane.status()

    assert len(host.queries) == 1


def test_status_shows_the_roles_and_log_paths_the_scripts_use(
    plane: WindowsMemberProcessPlane, config: LauncherConfig, host: FakeHost
) -> None:
    members = {member.display_name: member for member in plane.status()}

    assert members["alpha-leader"].role == "repository_leader"
    assert members["alpha-worker"].role == "worker"
    assert members["alpha-worker"].log_path == str(config.log_dir / "alpha-worker.out.log")


def test_the_subset_selects_the_members_the_scripts_would(
    plane: WindowsMemberProcessPlane, host: FakeHost
) -> None:
    """``gamma-leader`` carries another tag, so this launcher does not answer for it."""
    assert [member.display_name for member in plane.status()] == [
        "alpha-leader",
        "alpha-worker",
        "beta-leader",
        "beta-worker",
        "omega-worker",
        "delta-worker",
        "sigma-worker",
    ]


def test_start_leaves_a_running_member_alone_and_brings_the_others_up(
    unblocked_plane: WindowsMemberProcessPlane, host: FakeHost
) -> None:
    """AC-01: a second click must not produce a second Bridge for the same member.

    The start script cannot be asked to work this out. It throws on an already
    live PID and ``$ErrorActionPreference = "Stop"`` abandons the batch, so a
    re-run with one member up would fail *after* starting some of the others.
    Which is why this asserts both halves: the one that was up was left alone,
    and every other one was started.
    """
    unblocked_plane.start_all()

    assert host.started() == list(STARTABLE_UNBLOCKED)
    assert "alpha-leader" not in host.started()


def test_start_answers_with_the_status_from_after_the_work(
    unblocked_plane: WindowsMemberProcessPlane, host: FakeHost
) -> None:
    """The body of a start is what the machine looks like now, not a moment ago.

    A plane that swept once and returned that sweep would answer every start
    with "all still down" -- true when it was taken, and useless to the page
    that asked. The fake gives the started members real PIDs, so a pre-start
    sweep and a post-start sweep are different answers.
    """
    members = {member.display_name: member for member in unblocked_plane.start_all()}

    for member_name in STARTABLE_UNBLOCKED:
        assert members[member_name].running is True
        assert members[member_name].pid >= FIRST_FAKE_PID
    # The one that was already up keeps the PID it already had.
    assert members["alpha-leader"].running is True
    assert members["alpha-leader"].pid == LEADER_PID


def test_start_costs_one_query_per_sweep_and_one_spawn_per_member_started(
    unblocked_plane: WindowsMemberProcessPlane, host: FakeHost
) -> None:
    """Two sweeps -- the one that decides and the one that answers -- and no more."""
    unblocked_plane.start_all()

    assert len(host.queries) == 2
    assert len(host.invocations) == len(STARTABLE_UNBLOCKED)


def test_start_refuses_the_member_whose_pid_file_is_in_the_way_and_starts_the_rest(
    plane: WindowsMemberProcessPlane, config: LauncherConfig, host: FakeHost
) -> None:
    """The stale PID file is a dead end unless the launcher says so out loud.

    Reporting the member down is right, and refusing to kill that PID is right,
    but the start script's own guard is ``Get-Process -Id`` with no command line:
    hand it this member and it throws "stop it first" and takes the batch with
    it. So the launcher names the file instead -- and the other members, which
    have nothing to do with this one's leftovers, still come up.
    """
    with pytest.raises(StalePidFileClaimed) as refusal:
        plane.start_all()

    assert [claim.member_name for claim in refusal.value.claims] == list(BLOCKED)
    assert refusal.value.claims[0].pid_file == str(config.pid_dir / "beta-leader.pid")
    assert host.started() == list(STARTABLE)
    for member_name in BLOCKED:
        assert member_name not in host.started()


def test_a_process_that_answers_with_no_command_line_blocks_rather_than_reads_as_down(
    plane: WindowsMemberProcessPlane, config: LauncherConfig, host: FakeHost
) -> None:
    """``delta-worker``'s PID is alive but will not say what it is.

    A protected process comes back from the query with an empty command line.
    Empty is present-and-unverifiable, and the distinction is one keystroke wide:
    ``live.get(pid) or None`` reads it as absent, files the member under
    "stopped", and sends the start script at a live PID it will refuse. So this
    pins the seam a plausible cleanup would break.
    """
    with pytest.raises(StalePidFileClaimed) as refusal:
        plane.start_all()

    claims = {claim.member_name: claim for claim in refusal.value.claims}
    assert claims["delta-worker"].pid_file == str(config.pid_dir / "delta-worker.pid")
    assert "delta-worker" not in host.started()


def test_a_pid_file_that_is_not_a_number_blocks_rather_than_reads_as_down(
    plane: WindowsMemberProcessPlane, config: LauncherConfig, host: FakeHost
) -> None:
    """The last bodiless 500: ``Get-Process -Id "not-a-pid"`` cannot even convert.

    Nothing about it is a race -- no ``Set-Content`` writes that -- so the answer
    is the operator's, and it is the same answer as every other unusable PID
    file: delete it.
    """
    with pytest.raises(StalePidFileClaimed) as refusal:
        plane.start_all()

    claims = {claim.member_name: claim for claim in refusal.value.claims}
    assert claims["sigma-worker"].pid_file == str(config.pid_dir / "sigma-worker.pid")
    assert "sigma-worker" not in host.started()


def test_start_passes_the_operator_config_and_nothing_else(
    unblocked_plane: WindowsMemberProcessPlane, config: LauncherConfig, host: FakeHost
) -> None:
    unblocked_plane.start_all()

    script, arguments = host.invocations[0]
    assert script == START_SCRIPT
    assert arguments == [
        "-Members",
        str(config.members_file),
        "-EnrollmentDir",
        str(config.enrollment_dir),
        "-EnvFile",
        str(config.env_file),
        "-RuntimeDir",
        str(config.runtime_dir),
        "-Only",
        "alpha-worker",
        "-WorkspaceRoot",
        str(WORKSPACE_ROOT),
    ]


def test_start_omits_the_workspace_root_when_the_config_has_none(
    config: LauncherConfig, host: FakeHost
) -> None:
    """Absent means "let the start script choose", which is not the same as empty."""
    with pytest.raises(StalePidFileClaimed):
        WindowsMemberProcessPlane(replace(config, workspace_root=None)).start_all()

    assert "-WorkspaceRoot" not in host.invocations[0][1]


def test_stop_aims_only_at_members_whose_identity_was_confirmed(
    plane: WindowsMemberProcessPlane, host: FakeHost
) -> None:
    """FR-08: never kill by PID alone.

    ``beta-leader``'s PID file names a live process, and the stop script's own
    check -- "does the command line mention repomesh_agent_bridge" -- would pass
    it. The launcher never gives it the chance: a member it could not confirm is
    not named in any kill it issues.
    """
    plane.stop_all()

    assert host.stopped() == ["alpha-leader"]
    assert host.started() == []


def test_stop_answers_with_the_status_from_after_the_work(
    plane: WindowsMemberProcessPlane, host: FakeHost
) -> None:
    members = {member.display_name: member for member in plane.stop_all()}

    assert members["alpha-leader"].running is False
    assert members["alpha-leader"].pid is None


def test_restart_stops_a_running_member_before_starting_it(
    plane: WindowsMemberProcessPlane, host: FakeHost
) -> None:
    members = {member.display_name: member for member in plane.restart(ALPHA_LEADER)}

    assert [(script, _only(arguments)) for script, arguments in host.invocations] == [
        (STOP_SCRIPT, "alpha-leader"),
        (START_SCRIPT, "alpha-leader"),
    ]
    # And it comes back under the new process, not the one that was killed.
    assert members["alpha-leader"].running is True
    assert members["alpha-leader"].pid == FIRST_FAKE_PID


def test_restart_of_a_stopped_member_does_not_stop_anything(
    plane: WindowsMemberProcessPlane, host: FakeHost
) -> None:
    plane.restart(ALPHA_WORKER)

    assert host.stopped() == []
    assert host.started() == ["alpha-worker"]


@pytest.mark.parametrize(
    ("agent_id", "member_name"),
    [
        pytest.param(BETA_LEADER, "beta-leader", id="live-foreign-process"),
        pytest.param(DELTA_WORKER, "delta-worker", id="no-command-line"),
        pytest.param(SIGMA_WORKER, "sigma-worker", id="not-a-number"),
    ],
)
def test_restart_of_a_blocked_member_refuses_without_touching_the_machine(
    plane: WindowsMemberProcessPlane,
    config: LauncherConfig,
    host: FakeHost,
    agent_id: str,
    member_name: str,
) -> None:
    with pytest.raises(StalePidFileClaimed) as refusal:
        plane.restart(agent_id)

    assert refusal.value.claims[0].pid_file == str(config.pid_dir / f"{member_name}.pid")
    assert host.invocations == []


def test_restart_of_a_member_outside_the_subset_touches_nothing(
    plane: WindowsMemberProcessPlane, host: FakeHost
) -> None:
    """The out-of-subset member is unknown here, and unknown means nothing happens."""
    with pytest.raises(UnknownMember):
        plane.restart(GAMMA_LEADER)

    assert host.invocations == []


def test_command_lines_reads_what_this_machine_actually_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parsing, against real output from Windows PowerShell 5.1.

    One ``<pid>\\t<command line>`` line per process that exists. PID 4 is in here
    because it is real: a protected process comes back with an empty command
    line, and it has to reach the identity check to be rejected rather than be
    mistaken for a process that is not running at all.
    """
    stdout = "4\t\n8808\tC:\\Windows\\py.exe -m repomesh_agent_bridge run\n"
    recorded = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        recorded["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(windows.subprocess, "run", fake_run)

    assert windows._command_lines([4, 8808, 999999]) == {
        4: "",
        8808: "C:\\Windows\\py.exe -m repomesh_agent_bridge run",
    }
    assert "ProcessId=4 OR ProcessId=8808 OR ProcessId=999999" in recorded["command"][-1]


def test_a_command_line_containing_a_newline_cannot_overwrite_another_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process may put anything on its own command line, including a newline.

    The query answers line by line, so such a process arrives split across rows
    and its tail can be shaped exactly like an entry for somebody else. First
    line wins, which means a row like that can add a process nobody asked about
    but can never replace the answer for one that was.
    """
    forged = "8808\tpy.exe -m repomesh_agent_bridge run\n8808\tinnocent.exe\n"

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, forged, "")

    monkeypatch.setattr(windows.subprocess, "run", fake_run)

    assert windows._command_lines([8808]) == {8808: "py.exe -m repomesh_agent_bridge run"}


def test_command_lines_asks_nothing_when_no_pid_file_had_a_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("no query should be made")

    monkeypatch.setattr(windows.subprocess, "run", refuse)

    assert windows._command_lines([]) == {}
