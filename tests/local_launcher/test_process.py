"""The identity check, as a truth table.

This is the whole of FR-08's "never kill by PID alone" in one pure function, so
it is tested the way a rule is tested rather than the way a code path is: every
row is a command line this machine could really produce, paired with the member
somebody is about to attribute it to.
"""

import pytest

from repomesh_local_launcher.process import is_member_process

PYTHON = r"D:\repo\.venv\Scripts\python.exe"
ENROLLMENTS = r"D:\repo\output\bridge-team\e1\enrollments"
BRIDGE = f"{PYTHON} -m repomesh_agent_bridge run --enrollment"

LEADER_LINE = rf"{BRIDGE} {ENROLLMENTS}\enrollment.alpha-leader.json"
WORKER_LINE = (
    rf"{BRIDGE} {ENROLLMENTS}\enrollment.alpha-worker.json"
    r" --workspace-root D:\Project4work\.repomesh-e1\workspaces"
)
PROVISIONER_LINE = rf"{PYTHON} make_enrollments.py --out {ENROLLMENTS}\enrollment.alpha-leader.json"


@pytest.mark.parametrize(
    ("command_line", "member_name", "expected"),
    [
        # The two shapes start_members.ps1 actually launches.
        pytest.param(LEADER_LINE, "alpha-leader", True, id="leader-owns-its-line"),
        pytest.param(WORKER_LINE, "alpha-worker", True, id="worker-owns-its-line"),
        # A live Bridge, but serving somebody else: stopping it would take down
        # a member the operator did not ask about.
        pytest.param(LEADER_LINE, "alpha-worker", False, id="another-members-bridge"),
        pytest.param(WORKER_LINE, "beta-worker", False, id="another-repositorys-bridge"),
        # ``enrollment.alpha.json`` is not a prefix of ``enrollment.alpha-leader.json``
        # only because the suffix is part of the comparison. A bare key would match.
        pytest.param(LEADER_LINE, "alpha", False, id="key-is-a-prefix-of-another-key"),
        # Holding the enrollment file is not the same as being the Bridge: the
        # provisioning scripts and an editor open the very same path.
        pytest.param(
            PROVISIONER_LINE, "alpha-leader", False, id="reads-the-enrollment-but-is-not-the-bridge"
        ),
        # A Bridge with no enrollment on its line cannot be attributed to anyone.
        pytest.param(
            f"{PYTHON} -m repomesh_agent_bridge --help",
            "alpha-leader",
            False,
            id="bridge-without-an-enrollment",
        ),
        # What a recycled PID looks like: the id is live, the process is not ours.
        pytest.param(r"C:\Windows\system32\notepad.exe", "alpha-leader", False, id="recycled-pid"),
        pytest.param(f"{PYTHON} -m http.server 8000", "alpha-leader", False, id="unrelated-python"),
        # No command line at all is what a dead PID answers with.
        pytest.param("", "alpha-leader", False, id="no-command-line"),
    ],
)
def test_is_member_process(command_line: str, member_name: str, expected: bool) -> None:
    assert is_member_process(command_line, member_name) is expected
