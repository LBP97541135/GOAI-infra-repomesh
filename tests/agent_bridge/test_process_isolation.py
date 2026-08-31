"""Live isolation tests for the restricted process factory (PR 4, H-8/H-9).

Every Windows test spawns a *real* child through ``RestrictedProcessFactory`` and
lets the operating system, not a mock, decide whether the write was denied or the
process tree died. The claims under test are exactly the ones the factory reports
through :class:`IsolationReport`, so a passing suite and a passing ``probe()`` mean
the same thing. The single non-Windows test forces the unsupported branch to prove
the factory declines rather than pretending to isolate.
"""

import asyncio
import json
import os
import sys

import pytest

from repomesh_agent_bridge.adapters import restricted_process as rp
from repomesh_agent_bridge.adapters.restricted_process import (
    RestrictedProcessFactory,
    RestrictedProcessUnavailable,
    prepare_session_dirs,
    set_low_integrity,
)
from repomesh_runner.drivers.supervision import SpawnSpec

windows_only = pytest.mark.skipif(
    os.name != "nt", reason="restricted process isolation requires Windows MIC"
)


# --------------------------------------------------------------------------- helpers

# Enough for python itself to start under a non-merged env; individual tests add
# their own allowlisted keys on top.
def base_env(**extra: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in ("SystemRoot", "windir"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    env.update(extra)
    return env


def spec_for(code: str, *args: str, cwd, env: dict[str, str]) -> SpawnSpec:
    return SpawnSpec(
        executable=sys.executable,
        arguments=("-c", code, *args),
        working_directory=str(cwd),
        environment=env,
    )


async def first_stdout_line(handle) -> str:
    iterator = handle.stdout_lines()
    async for line in iterator:
        text = line.decode(errors="replace").strip()
        if text:
            return text
    return ""


async def run_to_completion(factory, spec):
    handle = await factory.spawn(spec)
    lines: list[str] = []
    async for line in handle.stdout_lines():
        decoded = line.decode(errors="replace").strip()
        if decoded:
            lines.append(decoded)
    code = await handle.wait()
    return lines, code


WRITE_PROBE = (
    "import os, sys\n"
    "target = sys.argv[1]\n"
    "try:\n"
    "    with open(os.path.join(target, 'sentinel.txt'), 'w') as handle:\n"
    "        handle.write('written')\n"
    "    print('WROTE')\n"
    "except Exception as exc:\n"
    "    print('DENIED:' + type(exc).__name__)\n"
)

READ_PROBE = (
    "import os, sys\n"
    "workspace = sys.argv[1]\n"
    "try:\n"
    "    with open(os.path.join(workspace, 'readme.txt')) as handle:\n"
    "        print('READ:' + handle.read().strip())\n"
    "except Exception as exc:\n"
    "    print('READ_DENIED:' + type(exc).__name__)\n"
)


@pytest.fixture
def factory() -> RestrictedProcessFactory:
    return RestrictedProcessFactory()


# ----------------------------------------------------------------- write isolation


@windows_only
async def test_out_of_bounds_write_is_denied_and_leaves_no_sentinel(factory, tmp_path):
    outside = tmp_path / "repo-shaped"
    outside.mkdir()
    (outside / ".git").mkdir()

    lines, code = await run_to_completion(
        factory, spec_for(WRITE_PROBE, str(outside), cwd=tmp_path, env=base_env())
    )

    assert lines == ["DENIED:PermissionError"]
    assert not (outside / "sentinel.txt").exists()
    assert code == 0


@windows_only
async def test_low_labelled_directory_is_writable(factory, tmp_path):
    writable = tmp_path / "writable"
    writable.mkdir()
    assert set_low_integrity(writable) is True

    lines, code = await run_to_completion(
        factory, spec_for(WRITE_PROBE, str(writable), cwd=tmp_path, env=base_env())
    )

    assert lines == ["WROTE"]
    assert (writable / "sentinel.txt").read_text() == "written"


@windows_only
async def test_workspace_is_readable_but_not_writable(factory, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()  # left at the default Medium label
    (workspace / "readme.txt").write_text("hello-from-workspace")

    read_lines, _ = await run_to_completion(
        factory, spec_for(READ_PROBE, str(workspace), cwd=workspace, env=base_env())
    )
    write_lines, _ = await run_to_completion(
        factory, spec_for(WRITE_PROBE, str(workspace), cwd=workspace, env=base_env())
    )

    assert read_lines == ["READ:hello-from-workspace"]
    assert write_lines == ["DENIED:PermissionError"]
    assert not (workspace / "sentinel.txt").exists()


# ------------------------------------------------------------------- env allowlist


@windows_only
async def test_child_env_is_exactly_the_allowlist(factory, tmp_path, monkeypatch):
    # A secret that lives in the parent environment but not the allowlist must not
    # cross into the child. Distinctive value so its absence is meaningful.
    monkeypatch.setenv("BRIDGE_SECRET_CANARY", "must-not-leak-into-child")
    allow = base_env(BRIDGE_ALLOWED_KEY="present-and-correct")

    lines, code = await run_to_completion(
        factory,
        spec_for(
            "import json, os; print(json.dumps(dict(os.environ)))",
            cwd=tmp_path,
            env=allow,
        ),
    )
    child_env = json.loads(lines[0])
    # Windows exposes per-drive current dirs as ``=C:`` names; they are not leakage.
    observed = {k: v for k, v in child_env.items() if not k.startswith("=")}

    assert "BRIDGE_SECRET_CANARY" not in child_env
    assert observed.get("BRIDGE_ALLOWED_KEY") == "present-and-correct"
    assert {k.upper() for k in observed} == {k.upper() for k in allow}
    assert code == 0


# ------------------------------------------------------------- whole-tree teardown

_GRANDCHILD_SPAWNER = (
    "import os, subprocess, sys, time\n"
    "writable = sys.argv[1]\n"
    "heartbeat = os.path.join(writable, 'gc.txt')\n"
    "child = subprocess.Popen(\n"
    "    [sys.executable, '-c',\n"
    "     \"import sys,time; open(sys.argv[1],'w').write('alive'); time.sleep(60)\",\n"
    "     heartbeat],\n"
    "    creationflags=0x00000008,\n"
    ")\n"
    "for _ in range(200):\n"
    "    if os.path.isfile(heartbeat):\n"
    "        break\n"
    "    time.sleep(0.05)\n"
    "print(child.pid)\n"
    "sys.stdout.flush()\n"
)


@windows_only
async def test_grandchild_dies_when_job_is_terminated(factory, tmp_path):
    writable = tmp_path / "writable"
    writable.mkdir()
    set_low_integrity(writable)

    handle = await factory.spawn(
        spec_for(_GRANDCHILD_SPAWNER, str(writable), cwd=tmp_path, env=base_env())
    )
    grandchild_pid = int(await first_stdout_line(handle))

    assert (writable / "gc.txt").is_file()  # it really launched
    assert rp._process_alive(grandchild_pid) is True

    await handle.terminate()
    # Give the kernel a beat to reap the terminated tree.
    for _ in range(50):
        if not rp._process_alive(grandchild_pid):
            break
        await asyncio.sleep(0.05)

    assert rp._process_alive(grandchild_pid) is False


@windows_only
async def test_terminate_is_idempotent(factory, tmp_path):
    handle = await factory.spawn(
        spec_for("import time; time.sleep(60)", cwd=tmp_path, env=base_env())
    )
    await handle.terminate()
    await handle.terminate()  # must not raise on an already-cleaned handle


# --------------------------------------------------------------------------- probe


@windows_only
async def test_probe_matches_reality(factory):
    report = await factory.probe()

    assert report.required_ok is True
    assert report.unmet == ()
    verified = {check.name: check.verified for check in report.checks}
    assert verified["low_integrity_token"] is True
    assert verified["env_allowlist"] is True
    assert verified["workspace_read_only"] is True
    assert verified["out_of_bounds_write_denied"] is True
    assert verified["low_dir_writable"] is True
    assert verified["process_tree_terminated"] is True

    read_iso = report.get("read_isolation_restricted_sids")
    assert read_iso is not None
    assert read_iso.supported is False
    assert read_iso.required is False
    assert read_iso.verified is False


@windows_only
async def test_probe_leaves_no_lingering_process(factory):
    # The probe's grandchild must be gone once probe() returns.
    report = await factory.probe()
    tree_check = report.get("process_tree_terminated")
    assert tree_check is not None
    assert tree_check.verified is True


# ------------------------------------------------------------- non-windows branch


async def test_probe_reports_unsupported_off_windows(monkeypatch, factory):
    monkeypatch.setattr(rp, "_IS_WINDOWS", False)

    report = await factory.probe()

    assert report.required_ok is False
    assert all(not check.verified for check in report.checks)
    for check in report.checks:
        if check.required:
            assert check.supported is False
    # Every required capability is now among the unmet set.
    assert {c.name for c in report.unmet} == {
        c.name for c in report.checks if c.required
    }


async def test_spawn_refuses_off_windows(monkeypatch, factory, tmp_path):
    monkeypatch.setattr(rp, "_IS_WINDOWS", False)
    spec = spec_for("print('unreachable')", cwd=tmp_path, env=base_env())

    with pytest.raises(RestrictedProcessUnavailable):
        await factory.spawn(spec)


# --------------------------------------------------------------------- dir helpers


@windows_only
def test_prepare_session_dirs_labels_writables_only(tmp_path):
    dirs = prepare_session_dirs(tmp_path / "session")

    assert dirs.workspace.is_dir()
    assert dirs.codex_home.is_dir()
    assert dirs.tmp.is_dir()

    # Seed files, then confirm reset clears workspace/tmp but keeps codex-home.
    (dirs.workspace / "stale.txt").write_text("x")
    (dirs.tmp / "stale.txt").write_text("x")
    (dirs.codex_home / "rollout.json").write_text("keep-me")

    again = prepare_session_dirs(tmp_path / "session", reset_workspace=True)
    assert not (again.workspace / "stale.txt").exists()
    assert not (again.tmp / "stale.txt").exists()
    assert (again.codex_home / "rollout.json").read_text() == "keep-me"
