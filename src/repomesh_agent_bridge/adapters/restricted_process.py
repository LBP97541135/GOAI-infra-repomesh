"""A Windows-first restricted process factory with a verifiable isolation probe.

This is the write-side of the Bridge's containment story (PR 4, decisions H-8 and
H-9). It implements the runner's ``ProcessFactory`` / ``ProcessHandle`` protocols
(``repomesh_runner.drivers.supervision``) so that ``AppServerDriver`` can spawn a
coding CLI without ever handing it the operator's environment, a writable view of
any real directory, or a process it cannot kill.

What it does, and how each claim is backed by a real Win32 mechanism:

* **env allowlist** — the child receives *only* the keys in ``SpawnSpec.environment``.
  Unlike the runner's ``SubprocessFactory``, ``os.environ`` is never merged in, so
  SCM credentials and the control-plane token cannot leak through inherited
  variables. Enforced by building the process environment block by hand and
  passing ``CREATE_UNICODE_ENVIRONMENT``.

* **write isolation** — the child runs on a *Low* integrity copy of our own token
  (``OpenProcessToken`` -> ``DuplicateTokenEx`` -> ``SetTokenInformation`` with a
  ``S-1-16-4096`` mandatory label -> ``CreateProcessAsUserW``). Mandatory Integrity
  Control then blocks the Low child from writing any object left at the default
  Medium label (the workspace, a real repo, the user profile) while still letting
  it write directories we explicitly relabel Low. No administrator rights are
  required because the token is a derived, lowered copy of the caller's own.

* **whole-tree termination** — the child is created ``CREATE_SUSPENDED`` inside a
  job object carrying ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``; the job is assigned
  before the thread resumes, so every descendant (a launcher trampoline, the real
  interpreter, any tool it spawns) is captured. ``terminate`` kills the root then
  the job; closing the job handle alone also kills the tree.

* **read isolation** — deliberately *not* attempted this cycle (H-8 item 4). The
  :class:`IsolationReport` records it as an unsupported capability rather than
  implying a guarantee the process does not enforce.

stdio uses hand-made anonymous pipes bridged to asyncio by background threads
(anonymous pipes cannot do overlapped IO, so the Proactor loop is bypassed on
purpose). Line framing, oversized-line skipping and the 8 KiB stderr tail mirror
``SubprocessHandle`` so the driver behaves identically whichever factory it holds.

On non-Windows hosts the factory refuses to spawn and the probe reports every
capability as unsupported: a POSIX host isolation adapter is future work, and the
honest answer is "no containment here", so real mode declines and only the inert
stand-in remains usable.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from repomesh_runner.drivers.supervision import STDERR_TAIL_BYTES, ProcessHandle, SpawnSpec

_IS_WINDOWS = os.name == "nt"

# S-1-16-4096 is the Low mandatory integrity level. The child (and everything it
# spawns) runs here; Medium (0x2000) objects become read-only to it.
LOW_INTEGRITY_SID = "S-1-16-4096"
_LOW_INTEGRITY_RID = 0x1000

# Match the runner's framing exactly so the driver cannot tell the factories apart.
MAX_LINE_BYTES = 10 * 1024 * 1024
_TERMINATE_GRACE_SECONDS = 5.0
_PROBE_STARTUP_TIMEOUT = 30.0


class RestrictedProcessUnavailable(RuntimeError):
    """Raised when a restricted spawn is requested where it cannot be enforced."""


# --------------------------------------------------------------------------- report


@dataclass(frozen=True, slots=True)
class IsolationCheck:
    """One isolation claim and whether a live child actually demonstrated it."""

    name: str
    verified: bool
    supported: bool
    required: bool
    detail: str


@dataclass(frozen=True, slots=True)
class IsolationReport:
    """The outcome of a real spawn that exercises every containment claim.

    ``required_ok`` is the gate the caller (``ensure_ready``, H-9) reads: a real
    coding session may start only when every *required* and *supported* check was
    verified. Unsupported capabilities (read isolation on any host; everything on
    POSIX) are reported truthfully and never counted as verified.
    """

    checks: tuple[IsolationCheck, ...]
    platform: str

    def get(self, name: str) -> IsolationCheck | None:
        return next((c for c in self.checks if c.name == name), None)

    @property
    def required_ok(self) -> bool:
        required = [check for check in self.checks if check.required]
        # An empty required set, or any required capability that is unsupported on
        # this host, means containment is not proven — never vacuously true.
        return bool(required) and all(
            check.supported and check.verified for check in required
        )

    @property
    def unmet(self) -> tuple[IsolationCheck, ...]:
        return tuple(
            c for c in self.checks if c.required and not (c.supported and c.verified)
        )

    def summary(self) -> str:
        lines = [f"isolation probe ({self.platform}): required_ok={self.required_ok}"]
        for check in self.checks:
            if not check.supported:
                mark = "unsupported"
            elif check.verified:
                mark = "verified"
            else:
                mark = "FAILED"
            lines.append(f"  [{mark}] {check.name}: {check.detail}")
        return "\n".join(lines)


# ------------------------------------------------------------------- session dirs


@dataclass(frozen=True, slots=True)
class SessionDirs:
    """The three directories a coding session runs against (H-7).

    ``workspace`` is left at the default (Medium) label so the Low child may read
    but not write it; ``codex_home`` and ``tmp`` are relabelled Low so the child
    can write exactly there and nowhere else.
    """

    workspace: Path
    codex_home: Path
    tmp: Path


def set_low_integrity(path: Path) -> bool:
    """Relabel ``path`` (and its tree) to Low integrity. Returns whether it stuck.

    Uses ``icacls`` rather than a SetSecurityInfo dance: it is the documented tool
    and only ever touches a directory the Bridge owns. A no-op that reports
    ``False`` on non-Windows, where mandatory labels do not exist.
    """

    if not _IS_WINDOWS:
        return False
    completed = subprocess.run(
        ["icacls", str(path), "/setintegritylevel", "(OI)(CI)Low"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def prepare_session_dirs(session_dir: Path, *, reset_workspace: bool = False) -> SessionDirs:
    """Create (and label) the workspace/codex-home/tmp trio under ``session_dir``.

    With ``reset_workspace`` the workspace and tmp are emptied and rebuilt while
    codex-home is preserved (its rollout files are what ``thread/resume`` needs).
    Only ever operates on Bridge-owned directories.
    """

    session_dir = Path(session_dir)
    workspace = session_dir / "workspace"
    codex_home = session_dir / "codex-home"
    tmp = session_dir / "tmp"

    if reset_workspace:
        for scratch in (workspace, tmp):
            if scratch.exists():
                shutil.rmtree(scratch, ignore_errors=True)

    for directory in (workspace, codex_home, tmp):
        directory.mkdir(parents=True, exist_ok=True)

    # Writable islands get the Low label; the workspace stays Medium (read-only).
    set_low_integrity(codex_home)
    set_low_integrity(tmp)
    return SessionDirs(workspace=workspace, codex_home=codex_home, tmp=tmp)


# --------------------------------------------------------------------------- win32

if _IS_WINDOWS:  # pragma: no branch - platform gate
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _adv = ctypes.WinDLL("advapi32", use_last_error=True)

    _HANDLE = wintypes.HANDLE
    _DWORD = wintypes.DWORD
    _LPDWORD = ctypes.POINTER(_DWORD)
    _PHANDLE = ctypes.POINTER(_HANDLE)

    _TOKEN_DUPLICATE = 0x0002
    _TOKEN_QUERY = 0x0008
    _TOKEN_ADJUST_DEFAULT = 0x0080
    _TOKEN_ASSIGN_PRIMARY = 0x0001
    _SECURITY_IMPERSONATION = 2
    _TOKEN_PRIMARY = 1
    _TOKEN_INTEGRITY_LEVEL = 25
    _SE_GROUP_INTEGRITY = 0x00000020
    _MAXIMUM_ALLOWED = 0x02000000

    _CREATE_SUSPENDED = 0x00000004
    _CREATE_UNICODE_ENVIRONMENT = 0x00000400
    _CREATE_NO_WINDOW = 0x08000000
    _STARTF_USESTDHANDLES = 0x00000100
    _HANDLE_FLAG_INHERIT = 0x00000001

    _JOB_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

    _STILL_ACTIVE = 259
    _SYNCHRONIZE = 0x00100000
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _INFINITE = 0xFFFFFFFF
    _WAIT_OBJECT_0 = 0x0

    class _SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", _DWORD)]

    class _TokenMandatoryLabel(ctypes.Structure):
        _fields_ = [("Label", _SidAndAttributes)]

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("nLength", _DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    class _StartupInfoW(ctypes.Structure):
        _fields_ = [
            ("cb", _DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", _DWORD),
            ("dwY", _DWORD),
            ("dwXSize", _DWORD),
            ("dwYSize", _DWORD),
            ("dwXCountChars", _DWORD),
            ("dwYCountChars", _DWORD),
            ("dwFillAttribute", _DWORD),
            ("dwFlags", _DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", _HANDLE),
            ("hStdOutput", _HANDLE),
            ("hStdError", _HANDLE),
        ]

    class _ProcessInformation(ctypes.Structure):
        _fields_ = [
            ("hProcess", _HANDLE),
            ("hThread", _HANDLE),
            ("dwProcessId", _DWORD),
            ("dwThreadId", _DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _JobBasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", _DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", _DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", _DWORD),
            ("SchedulingClass", _DWORD),
        ]

    class _JobExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JobBasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    def _bind(fn, argtypes, restype=wintypes.BOOL) -> None:
        fn.argtypes = argtypes
        fn.restype = restype

    _bind(_adv.OpenProcessToken, [_HANDLE, _DWORD, _PHANDLE])
    _bind(
        _adv.DuplicateTokenEx,
        [_HANDLE, _DWORD, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, _PHANDLE],
    )
    _bind(_adv.ConvertStringSidToSidW, [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)])
    _bind(_adv.GetLengthSid, [ctypes.c_void_p], _DWORD)
    _bind(_adv.SetTokenInformation, [_HANDLE, ctypes.c_int, ctypes.c_void_p, _DWORD])
    _bind(
        _adv.CreateProcessAsUserW,
        [
            _HANDLE,
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            _DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(_StartupInfoW),
            ctypes.POINTER(_ProcessInformation),
        ],
    )
    _bind(_k32.CreatePipe, [_PHANDLE, _PHANDLE, ctypes.POINTER(_SecurityAttributes), _DWORD])
    _bind(_k32.SetHandleInformation, [_HANDLE, _DWORD, _DWORD])
    _bind(_k32.ReadFile, [_HANDLE, ctypes.c_void_p, _DWORD, _LPDWORD, ctypes.c_void_p])
    _bind(_k32.WriteFile, [_HANDLE, ctypes.c_void_p, _DWORD, _LPDWORD, ctypes.c_void_p])
    _bind(_k32.CreateJobObjectW, [ctypes.c_void_p, wintypes.LPCWSTR], _HANDLE)
    _bind(_k32.SetInformationJobObject, [_HANDLE, ctypes.c_int, ctypes.c_void_p, _DWORD])
    _bind(_k32.AssignProcessToJobObject, [_HANDLE, _HANDLE])
    _bind(_k32.TerminateJobObject, [_HANDLE, wintypes.UINT])
    _bind(_k32.TerminateProcess, [_HANDLE, wintypes.UINT])
    _bind(_k32.ResumeThread, [_HANDLE], _DWORD)
    _bind(_k32.CloseHandle, [_HANDLE])
    _bind(_k32.GetExitCodeProcess, [_HANDLE, _LPDWORD])
    _bind(_k32.OpenProcess, [_DWORD, wintypes.BOOL, _DWORD], _HANDLE)
    _bind(_k32.WaitForSingleObject, [_HANDLE, _DWORD], _DWORD)
    _k32.GetCurrentProcess.restype = _HANDLE
    _k32.LocalFree.argtypes = [ctypes.c_void_p]

    def _fail(name: str) -> None:
        err = ctypes.get_last_error()
        raise OSError(err, f"{name} failed: {ctypes.WinError(err).strerror}")

    def _make_low_token() -> object:
        """A primary, Low-integrity copy of the current process token."""

        source = _HANDLE()
        access = (
            _TOKEN_DUPLICATE | _TOKEN_QUERY | _TOKEN_ADJUST_DEFAULT | _TOKEN_ASSIGN_PRIMARY
        )
        if not _adv.OpenProcessToken(_k32.GetCurrentProcess(), access, ctypes.byref(source)):
            _fail("OpenProcessToken")
        try:
            lowered = _HANDLE()
            if not _adv.DuplicateTokenEx(
                source,
                _MAXIMUM_ALLOWED,
                None,
                _SECURITY_IMPERSONATION,
                _TOKEN_PRIMARY,
                ctypes.byref(lowered),
            ):
                _fail("DuplicateTokenEx")
            sid = ctypes.c_void_p()
            if not _adv.ConvertStringSidToSidW(LOW_INTEGRITY_SID, ctypes.byref(sid)):
                _fail("ConvertStringSidToSidW")
            try:
                label = _TokenMandatoryLabel()
                label.Label.Sid = sid
                label.Label.Attributes = _SE_GROUP_INTEGRITY
                size = ctypes.sizeof(_TokenMandatoryLabel) + _adv.GetLengthSid(sid)
                if not _adv.SetTokenInformation(
                    lowered, _TOKEN_INTEGRITY_LEVEL, ctypes.byref(label), size
                ):
                    _fail("SetTokenInformation(TokenIntegrityLevel=Low)")
            finally:
                _k32.LocalFree(sid)
            return lowered
        finally:
            _k32.CloseHandle(source)

    def _environment_block(environment: Mapping[str, str]):
        """A UTF-16 ``KEY=VALUE\\0...\\0\\0`` block from *exactly* ``environment``."""

        items = sorted(environment.items(), key=lambda kv: kv[0].upper())
        if items:
            blob = "\x00".join(f"{key}={value}" for key, value in items) + "\x00\x00"
        else:
            blob = "\x00"
        return ctypes.create_unicode_buffer(blob)

    def _make_kill_on_close_job() -> object:
        job = _k32.CreateJobObjectW(None, None)
        if not job:
            _fail("CreateJobObjectW")
        info = _JobExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _k32.SetInformationJobObject(
            job, _JOB_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
        ):
            _fail("SetInformationJobObject")
        return job

    def _inheritable_pipe(inherit_read: bool):
        """Return ``(read, write)`` where the non-inherited parent end cannot leak."""

        sa = _SecurityAttributes()
        sa.nLength = ctypes.sizeof(sa)
        sa.bInheritHandle = True
        read, write = _HANDLE(), _HANDLE()
        if not _k32.CreatePipe(ctypes.byref(read), ctypes.byref(write), ctypes.byref(sa), 0):
            _fail("CreatePipe")
        parent_end = read if not inherit_read else write
        _k32.SetHandleInformation(parent_end, _HANDLE_FLAG_INHERIT, 0)
        return read, write

    def _process_alive(pid: int) -> bool:
        handle = _k32.OpenProcess(
            _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        try:
            code = _DWORD()
            if not _k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            _k32.CloseHandle(handle)


@dataclass(slots=True)
class _RawProcess:
    process: object
    job: object
    pid: int
    stdin_write: object
    stdout_read: object
    stderr_read: object


def _spawn_restricted(spec: SpawnSpec) -> _RawProcess:
    """Do the whole Win32 dance and hand back live handles.

    Order matters: create suspended, assign to the job, *then* resume, so no
    descendant can escape the job between creation and assignment.
    """

    stdout_read, stdout_write = _inheritable_pipe(inherit_read=False)
    stdin_read, stdin_write = _inheritable_pipe(inherit_read=True)
    stderr_read, stderr_write = _inheritable_pipe(inherit_read=False)

    token = _make_low_token()
    job = _make_kill_on_close_job()
    env_block = _environment_block(spec.environment)

    startup = _StartupInfoW()
    startup.cb = ctypes.sizeof(_StartupInfoW)
    startup.dwFlags = _STARTF_USESTDHANDLES
    startup.hStdInput = stdin_read
    startup.hStdOutput = stdout_write
    startup.hStdError = stderr_write
    info = _ProcessInformation()

    command_line = subprocess.list2cmdline(list(spec.argv))
    created = _adv.CreateProcessAsUserW(
        token,
        None,
        ctypes.create_unicode_buffer(command_line),
        None,
        None,
        True,
        _CREATE_SUSPENDED | _CREATE_UNICODE_ENVIRONMENT | _CREATE_NO_WINDOW,
        ctypes.cast(env_block, ctypes.c_void_p),
        str(spec.working_directory),
        ctypes.byref(startup),
        ctypes.byref(info),
    )
    if not created:
        err = ctypes.get_last_error()
        for handle in (stdout_read, stdout_write, stdin_read, stdin_write,
                       stderr_read, stderr_write, token, job):
            _k32.CloseHandle(handle)
        raise OSError(
            err,
            f"CreateProcessAsUserW failed for {spec.executable!r}: "
            f"{ctypes.WinError(err).strerror}",
        )

    if not _k32.AssignProcessToJobObject(job, info.hProcess):
        err = ctypes.get_last_error()
        _k32.TerminateProcess(info.hProcess, 1)
        for handle in (info.hProcess, info.hThread, stdout_read, stdout_write,
                       stdin_read, stdin_write, stderr_read, stderr_write, token, job):
            _k32.CloseHandle(handle)
        raise OSError(
            err,
            "AssignProcessToJobObject failed; refusing to run without whole-tree "
            f"termination: {ctypes.WinError(err).strerror}",
        )

    _k32.ResumeThread(info.hThread)

    # The child now owns its ends; drop the parent's copies so EOF is observable.
    for handle in (stdout_write, stdin_read, stderr_write, info.hThread, token):
        _k32.CloseHandle(handle)

    return _RawProcess(
        process=info.hProcess,
        job=job,
        pid=int(info.dwProcessId),
        stdin_write=stdin_write,
        stdout_read=stdout_read,
        stderr_read=stderr_read,
    )


class _StderrTail:
    """Thread-safe last-``limit``-bytes ring, mirroring supervision's tail."""

    def __init__(self, limit: int = STDERR_TAIL_BYTES) -> None:
        self._limit = limit
        self._buffer = bytearray()
        self._lock = threading.Lock()

    def feed(self, chunk: bytes) -> None:
        with self._lock:
            self._buffer.extend(chunk)
            if len(self._buffer) > self._limit:
                del self._buffer[: len(self._buffer) - self._limit]

    def text(self) -> str:
        with self._lock:
            return self._buffer.decode(errors="replace")


class RestrictedProcessHandle:
    """A ``ProcessHandle`` over a Low-integrity, job-contained Win32 process."""

    def __init__(self, raw: _RawProcess) -> None:
        self._raw = raw
        self._loop = asyncio.get_running_loop()
        self._stdout_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._tail = _StderrTail()
        self._stdin_closed = False
        self._cleanup_lock = threading.Lock()
        self._cleaned = False
        self._exit_code: int | None = None
        self._exit_future: asyncio.Future[int] = self._loop.create_future()

        self._stdout_thread = threading.Thread(
            target=self._pump_stdout, name="rmab-stdout", daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._pump_stderr, name="rmab-stderr", daemon=True
        )
        self._exit_thread = threading.Thread(
            target=self._await_exit, name="rmab-exit", daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread.start()
        self._exit_thread.start()

    @property
    def pid(self) -> int:
        return self._raw.pid

    # ------------------------------------------------------------------ threads

    def _read_chunks(self, handle: object):
        buffer = ctypes.create_string_buffer(65536)
        read = _DWORD()
        while True:
            ok = _k32.ReadFile(handle, buffer, 65536, ctypes.byref(read), None)
            if not ok or read.value == 0:
                return
            yield buffer.raw[: read.value]

    def _pump_stdout(self) -> None:
        pending = bytearray()
        skipping = False
        try:
            for chunk in self._read_chunks(self._raw.stdout_read):
                pending.extend(chunk)
                while True:
                    newline = pending.find(b"\n")
                    if newline == -1:
                        if len(pending) > MAX_LINE_BYTES:
                            # Oversized line with no terminator yet: drop what we
                            # have and skip until the next newline, matching
                            # SubprocessHandle rather than aborting the stream.
                            skipping = True
                            pending.clear()
                        break
                    line = bytes(pending[: newline + 1])
                    del pending[: newline + 1]
                    if skipping:
                        skipping = False
                        continue
                    self._emit_line(line)
        finally:
            self._loop.call_soon_threadsafe(self._stdout_queue.put_nowait, None)

    def _emit_line(self, line: bytes) -> None:
        self._loop.call_soon_threadsafe(self._stdout_queue.put_nowait, line)

    def _pump_stderr(self) -> None:
        for chunk in self._read_chunks(self._raw.stderr_read):
            self._tail.feed(chunk)

    def _await_exit(self) -> None:
        _k32.WaitForSingleObject(self._raw.process, _INFINITE)
        code = _DWORD()
        _k32.GetExitCodeProcess(self._raw.process, ctypes.byref(code))
        self._loop.call_soon_threadsafe(self._resolve_exit, int(code.value))

    def _resolve_exit(self, code: int) -> None:
        if not self._exit_future.done():
            self._exit_code = code
            self._exit_future.set_result(code)

    # -------------------------------------------------------------------- stdin

    def write_stdin(self, data: bytes) -> None:
        if self._stdin_closed:
            raise RuntimeError("stdin is not writable")
        written = _DWORD()
        ok = _k32.WriteFile(self._raw.stdin_write, data, len(data), ctypes.byref(written), None)
        if not ok:
            raise RuntimeError("stdin is not writable")

    def close_stdin(self) -> None:
        if not self._stdin_closed:
            self._stdin_closed = True
            _k32.CloseHandle(self._raw.stdin_write)

    # ------------------------------------------------------------------- stdout

    async def stdout_lines(self) -> AsyncIterator[bytes]:
        while True:
            item = await self._stdout_queue.get()
            if item is None:
                return
            yield item

    def stderr_tail(self) -> str:
        return self._tail.text()

    # --------------------------------------------------------------------- wait

    async def wait(self) -> int:
        code = await asyncio.shield(self._exit_future)
        # A natural exit still closes the job: KILL_ON_JOB_CLOSE mops up any helper
        # the CLI left running, so a "successful" turn cannot strand a background
        # process.
        self._cleanup()
        return code

    async def terminate(self, grace_seconds: float = _TERMINATE_GRACE_SECONDS) -> None:
        if self._cleaned:
            return
        _k32.TerminateProcess(self._raw.process, 1)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(self._exit_future), timeout=grace_seconds)
        _k32.TerminateJobObject(self._raw.job, 1)
        self._cleanup()

    def _cleanup(self) -> None:
        with self._cleanup_lock:
            if self._cleaned:
                return
            self._cleaned = True
        # Closing the job kills any survivor (KILL_ON_JOB_CLOSE) and forces the
        # child's pipe ends shut, so the waiter and pump threads observe exit/EOF.
        self.close_stdin()
        _k32.CloseHandle(self._raw.job)
        # A handle must not be closed while a thread is still waiting/reading on
        # it, so drain the background threads before reclaiming their handles.
        for thread in (self._exit_thread, self._stdout_thread, self._stderr_thread):
            thread.join(timeout=2.0)
        _k32.CloseHandle(self._raw.process)
        _k32.CloseHandle(self._raw.stdout_read)
        _k32.CloseHandle(self._raw.stderr_read)
        if not self._exit_future.done():
            self._loop.call_soon_threadsafe(self._resolve_exit, self._exit_code or 1)


# --------------------------------------------------------------------------- probe

_PROBE_CHILD_SOURCE = r'''
import json
import os
import subprocess
import sys
import time

workspace, writable, repo, profile = sys.argv[1:5]


def _integrity_rid():
    import ctypes
    from ctypes import wintypes

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class TokenMandatoryLabel(ctypes.Structure):
        _fields_ = [("Label", SidAndAttributes)]

    k32.GetCurrentProcess.restype = wintypes.HANDLE
    adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                     ctypes.POINTER(wintypes.HANDLE)]
    adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    adv.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    adv.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    adv.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    adv.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)

    tok = wintypes.HANDLE()
    adv.OpenProcessToken(k32.GetCurrentProcess(), 0x0008, ctypes.byref(tok))
    size = wintypes.DWORD()
    adv.GetTokenInformation(tok, 25, None, 0, ctypes.byref(size))
    buf = ctypes.create_string_buffer(size.value)
    adv.GetTokenInformation(tok, 25, buf, size, ctypes.byref(size))
    label = ctypes.cast(buf, ctypes.POINTER(TokenMandatoryLabel)).contents
    count = adv.GetSidSubAuthorityCount(label.Label.Sid)[0]
    return hex(adv.GetSidSubAuthority(label.Label.Sid, count - 1)[0])


def _try_write(directory):
    try:
        with open(os.path.join(directory, "probe_write.txt"), "w") as handle:
            handle.write("x")
        return "wrote"
    except Exception as exc:  # noqa: BLE001
        return "denied:" + type(exc).__name__


def _read_workspace():
    try:
        with open(os.path.join(workspace, "readme.txt")) as handle:
            return "read:" + handle.read().strip()
    except Exception as exc:  # noqa: BLE001
        return "denied:" + type(exc).__name__


heartbeat = os.path.join(writable, "grandchild_alive.txt")
grandchild = subprocess.Popen(
    [sys.executable, "-c",
     "import sys,time; open(sys.argv[1],'w').write('alive'); time.sleep(30)", heartbeat],
    creationflags=0x00000008,
)
for _ in range(60):
    if os.path.isfile(heartbeat):
        break
    time.sleep(0.05)

report = {
    "integrity_rid": _integrity_rid(),
    "pid": os.getpid(),
    "grandchild_pid": grandchild.pid,
    "env": dict(os.environ),
    "writes": {
        "workspace": _try_write(workspace),
        "writable": _try_write(writable),
        "repo": _try_write(repo),
        "profile": _try_write(profile),
    },
    "workspace_read": _read_workspace(),
}
sys.stdout.write(json.dumps(report) + "\n")
sys.stdout.flush()
'''


def _unsupported_report(platform: str, reason: str) -> IsolationReport:
    names = (
        ("low_integrity_token", True),
        ("env_allowlist", True),
        ("workspace_read_only", True),
        ("out_of_bounds_write_denied", True),
        ("low_dir_writable", True),
        ("process_tree_terminated", True),
        ("read_isolation_restricted_sids", False),
    )
    checks = tuple(
        IsolationCheck(
            name=name,
            verified=False,
            supported=False,
            required=required,
            detail=reason if required else _READ_ISOLATION_DETAIL,
        )
        for name, required in names
    )
    return IsolationReport(checks=checks, platform=platform)


_READ_ISOLATION_DETAIL = (
    "read isolation via restricted SIDs is not implemented this cycle (H-8 item 4): "
    "it would require adding ACEs to real user directories, an intrusion whose payoff "
    "does not justify it; deny-all tool policy already blocks tool-driven reads"
)


class RestrictedProcessFactory:
    """A ``ProcessFactory`` that spawns Low-integrity, job-contained children.

    Construction is allowed on any platform (so the composition root can build one
    and ask :meth:`probe`), but :meth:`spawn` refuses off Windows, where none of
    the containment can be enforced.
    """

    def __init__(self, *, probe_executable: str | None = None) -> None:
        self._probe_executable = probe_executable or sys.executable

    async def spawn(self, spec: SpawnSpec) -> ProcessHandle:
        if not _IS_WINDOWS:
            raise RestrictedProcessUnavailable(
                "restricted process isolation requires Windows mandatory integrity "
                "control; refusing to spawn a child that would run unconstrained"
            )
        raw = _spawn_restricted(spec)
        return RestrictedProcessHandle(raw)

    async def probe(self) -> IsolationReport:
        """Spawn a real Low child and let it demonstrate every containment claim."""

        if not _IS_WINDOWS:
            return _unsupported_report(
                sys.platform,
                "requires Windows mandatory integrity control; this host is "
                f"{sys.platform}, where the restricted factory cannot spawn",
            )
        root = Path(tempfile.mkdtemp(prefix="rmab-probe-"))
        try:
            return await self._probe_in(root)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    async def _probe_in(self, root: Path) -> IsolationReport:
        workspace = root / "workspace"
        writable = root / "writable"
        repo = root / "repo"
        profile = root / "profile"
        for directory in (workspace, writable, repo, profile):
            directory.mkdir()
        (workspace / "readme.txt").write_text("workspace-is-readable")
        (repo / ".git").mkdir()
        (repo / "tracked.txt").write_text("pretend source")
        set_low_integrity(writable)
        child_script = root / "_probe_child.py"
        child_script.write_text(_PROBE_CHILD_SOURCE)

        allowlist = {"PROBE_MARKER": "restricted-process-probe"}
        for key in ("SystemRoot", "windir"):
            value = os.environ.get(key)
            if value:
                allowlist[key] = value

        spec = SpawnSpec(
            executable=self._probe_executable,
            arguments=(
                str(child_script),
                str(workspace),
                str(writable),
                str(repo),
                str(profile),
            ),
            working_directory=workspace,
            environment=allowlist,
        )

        handle = await self.spawn(spec)
        report_line: bytes | None = None
        try:
            report_line = await asyncio.wait_for(
                self._first_line(handle), timeout=_PROBE_STARTUP_TIMEOUT
            )
        except TimeoutError:
            report_line = None
        except Exception:  # noqa: BLE001 - any spawn/read failure is a real finding
            report_line = None

        if not report_line:
            tail = handle.stderr_tail()
            await handle.terminate()
            reason = (
                "restricted child produced no report line within "
                f"{_PROBE_STARTUP_TIMEOUT:g}s"
            )
            if tail:
                reason = f"{reason}; stderr tail: {tail}"
            return _spawn_failure_report(reason)

        data = json.loads(report_line.decode(errors="replace"))
        grandchild_pid = int(data.get("grandchild_pid") or 0)
        alive_before = bool(grandchild_pid) and _process_alive(grandchild_pid)
        await handle.terminate()
        alive_after = bool(grandchild_pid) and _process_alive(grandchild_pid)

        return _build_report(data, allowlist, alive_before, alive_after)

    @staticmethod
    async def _first_line(handle: ProcessHandle) -> bytes | None:
        async for line in handle.stdout_lines():
            stripped = line.strip()
            if stripped:
                return stripped
        return None


def _spawn_failure_report(reason: str) -> IsolationReport:
    required = (
        "low_integrity_token",
        "env_allowlist",
        "workspace_read_only",
        "out_of_bounds_write_denied",
        "low_dir_writable",
        "process_tree_terminated",
    )
    checks = [
        IsolationCheck(name=name, verified=False, supported=True, required=True, detail=reason)
        for name in required
    ]
    checks.append(
        IsolationCheck(
            name="read_isolation_restricted_sids",
            verified=False,
            supported=False,
            required=False,
            detail=_READ_ISOLATION_DETAIL,
        )
    )
    return IsolationReport(checks=tuple(checks), platform="nt")


def _build_report(
    data: Mapping[str, object],
    allowlist: Mapping[str, str],
    alive_before: bool,
    alive_after: bool,
) -> IsolationReport:
    writes = data.get("writes") or {}
    if not isinstance(writes, Mapping):
        writes = {}
    rid = str(data.get("integrity_rid"))
    low_rid = hex(_LOW_INTEGRITY_RID)
    child_env = data.get("env") or {}
    if not isinstance(child_env, Mapping):
        child_env = {}

    checks: list[IsolationCheck] = []

    checks.append(
        IsolationCheck(
            name="low_integrity_token",
            verified=rid == low_rid,
            supported=True,
            required=True,
            detail=f"child token integrity RID reported as {rid} (Low == {low_rid})",
        )
    )

    env_verified, env_detail = _check_env(child_env, allowlist)
    checks.append(
        IsolationCheck(
            name="env_allowlist",
            verified=env_verified,
            supported=True,
            required=True,
            detail=env_detail,
        )
    )

    workspace_write = str(writes.get("workspace"))
    workspace_read = str(data.get("workspace_read"))
    ws_verified = workspace_write.startswith("denied") and workspace_read.startswith("read:")
    checks.append(
        IsolationCheck(
            name="workspace_read_only",
            verified=ws_verified,
            supported=True,
            required=True,
            detail=f"workspace read={workspace_read!r}, write={workspace_write!r}",
        )
    )

    repo_write = str(writes.get("repo"))
    profile_write = str(writes.get("profile"))
    oob_verified = repo_write.startswith("denied") and profile_write.startswith("denied")
    checks.append(
        IsolationCheck(
            name="out_of_bounds_write_denied",
            verified=oob_verified,
            supported=True,
            required=True,
            detail=f"repo write={repo_write!r}, profile write={profile_write!r}",
        )
    )

    writable_write = str(writes.get("writable"))
    checks.append(
        IsolationCheck(
            name="low_dir_writable",
            verified=writable_write == "wrote",
            supported=True,
            required=True,
            detail=f"Low-labelled dir write={writable_write!r}",
        )
    )

    checks.append(
        IsolationCheck(
            name="process_tree_terminated",
            verified=alive_before and not alive_after,
            supported=True,
            required=True,
            detail=(
                f"grandchild alive before terminate={alive_before}, "
                f"after={alive_after}"
            ),
        )
    )

    checks.append(
        IsolationCheck(
            name="read_isolation_restricted_sids",
            verified=False,
            supported=False,
            required=False,
            detail=_READ_ISOLATION_DETAIL,
        )
    )
    return IsolationReport(checks=tuple(checks), platform="nt")


def _check_env(child_env: Mapping[str, object], allowlist: Mapping[str, str]) -> tuple[bool, str]:
    """The child env must equal the allowlist exactly (no merge, no leak)."""

    # Windows hides per-drive current-directory entries as ``=C:`` names; they are
    # not inherited leakage, so they are excluded from the equality.
    observed = {
        key: str(value)
        for key, value in child_env.items()
        if not str(key).startswith("=")
    }
    expected_upper = {key.upper(): value for key, value in allowlist.items()}
    observed_upper = {key.upper(): value for key, value in observed.items()}

    extra = sorted(set(observed_upper) - set(expected_upper))
    missing = sorted(set(expected_upper) - set(observed_upper))
    mismatched = sorted(
        key for key in expected_upper if observed_upper.get(key) != expected_upper[key]
    )
    verified = not extra and not missing and not mismatched
    detail = (
        f"child env has {len(observed)} keys; extra={extra}, missing={missing}, "
        f"value_mismatch={mismatched}"
    )
    return verified, detail
