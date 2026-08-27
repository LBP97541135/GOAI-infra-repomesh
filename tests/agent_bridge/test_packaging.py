"""The wheel installs, and the console script it declares actually runs.

Every other module in ``tests/agent_bridge`` imports the package straight from
the checkout, so all of them would stay green if ``pyproject.toml`` stopped
shipping ``repomesh_agent_bridge`` or stopped declaring the
``repomesh-agent-bridge`` entry point. This is the one test that can notice: it
builds a wheel, installs it into a throwaway virtualenv, and runs the installed
script as a subprocess.

Three properties keep it honest and cheap:

*   **It builds from a copy of the source tree, never from the checkout.** The
    setuptools backend writes ``build/`` and ``src/*.egg-info`` next to the
    ``pyproject.toml`` it is handed, and a test that litters the repository is a
    test people learn to distrust.
*   **It resolves nothing over the network.** The virtualenv comes from stdlib
    ``venv`` (whose bundled pip needs no index), the wheel goes in with
    ``--no-index --no-deps``, and the third-party runtime dependencies are lent
    from the parent environment. Under test are the wheel's contents and its
    metadata, not dependency resolution.
*   **It exercises only the local fail-fast path.** ``check`` on an enrollment
    that stage 1 rejects never builds a port, so the subprocess talks to nobody
    and cannot hang waiting for a control plane.
"""

import json
import os
import shutil
import subprocess
import sys
import sysconfig
import venv
from collections.abc import Callable, Iterator
from importlib.util import find_spec
from pathlib import Path

import pytest

from .conftest import REPOMESH_TOKEN_REF, REPOMESH_TOKEN_VALUE, enrollment_wire

pytestmark = pytest.mark.packaging

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

BUILD_INPUTS = ("pyproject.toml", "README.md", "LICENSE", "src")
"""Everything the build backend reads, and nothing else.

Spelled out rather than derived, because copying the whole checkout would drag
in ``frontend/`` and friends for no gain. If ``pyproject.toml`` grows a
reference to another file the build fails loudly and names it.
"""

CONSOLE_SCRIPT = "repomesh-agent-bridge"

EXIT_STARTUP_REFUSED = 2
"""Pinned as a literal, not imported from the package under test.

What this smoke defends is the number a supervisor sees; importing the constant
would let a renumbering pass unnoticed by exactly the test that exists to catch
it.
"""

SUBPROCESS_TIMEOUT = 600

Completed = subprocess.CompletedProcess[str]
WheelBuilder = Callable[[Path, Path], Completed]


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def bridge_venv(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A throwaway virtualenv with the wheel installed. Built once per session.

    Session-scoped because building and seeding costs tens of seconds and every
    assertion below is a read of the same artefact.
    """

    root = tmp_path_factory.mktemp("agent-bridge-packaging")
    try:
        yield _install_wheel(_build_wheel(root), root / "venv")
    finally:
        # tmp_path_factory keeps its directories around for a few runs; a
        # virtualenv is too large to leave behind on that schedule.
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="session")
def refused_check(
    bridge_venv: Path, tmp_path_factory: pytest.TempPathFactory
) -> Completed:
    """One ``check`` run against an enrollment that stage 1 rejects on its own.

    ``codingProfile`` names no known Runner profile, which the CLI decides from
    the file alone — the same case ``test_cli`` proves builds no port. So this
    subprocess opens no socket, and the smoke needs no control plane.
    """

    enrollment = tmp_path_factory.mktemp("enrollment") / "enrollment.json"
    enrollment.write_text(json.dumps(enrollment_wire(codingProfile="cursor")), encoding="utf-8")
    return _run(
        [
            str(_venv_script(bridge_venv, CONSOLE_SCRIPT)),
            "check",
            "--enrollment",
            str(enrollment),
        ]
    )


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #


def test_the_wheel_installs_the_declared_console_script(bridge_venv: Path) -> None:
    """``[project.scripts]`` is metadata no other test in this suite reads."""

    script = _venv_script(bridge_venv, CONSOLE_SCRIPT)

    assert script.exists(), f"{CONSOLE_SCRIPT} is missing from {script.parent}"


def test_the_installed_package_is_the_one_from_the_wheel(bridge_venv: Path) -> None:
    """Guards the smoke against itself.

    The lent site-packages is the one way the checkout could sneak back in;
    if it ever wins the import, every assertion below stops meaning anything.
    """

    located = _check(
        _run(
            [
                str(_venv_script(bridge_venv, "python")),
                "-c",
                "import repomesh_agent_bridge as bridge; print(bridge.__file__)",
            ]
        ),
        "locating the installed package",
    )
    origin = Path(located.stdout.strip())

    assert origin.is_relative_to(bridge_venv), f"imported {origin}, which is not from the wheel"


def test_the_installed_script_refuses_a_malformed_enrollment(
    refused_check: Completed,
) -> None:
    """Exit code and a message on stderr: the whole interface a supervisor has."""

    assert refused_check.returncode == EXIT_STARTUP_REFUSED, refused_check.stderr
    assert "error:" in refused_check.stderr


def test_the_refusal_names_nothing_a_credential_could_be_found_by(
    refused_check: Completed,
) -> None:
    """A refusal is the moment an implementation is most tempted to dump the payload."""

    assert REPOMESH_TOKEN_REF not in refused_check.stderr + refused_check.stdout
    assert REPOMESH_TOKEN_VALUE not in refused_check.stderr + refused_check.stdout


# --------------------------------------------------------------------------- #
# building
# --------------------------------------------------------------------------- #


def _build_with_build(source: Path, dist: Path) -> Completed:
    command = [sys.executable, "-m", "build", "--wheel", "--no-isolation"]
    return _run([*command, "--outdir", str(dist), str(source)])


def _build_with_pip(source: Path, dist: Path) -> Completed:
    command = [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation"]
    return _run([*command, "--no-index", "--wheel-dir", str(dist), str(source)])


def _build_with_uv(source: Path, dist: Path) -> Completed:
    """uv provisions the build backend itself, so it needs no setuptools here.

    ``--offline`` first, because any machine that has synced this project has
    setuptools in uv's cache and the run should touch the network at no point.
    A cold cache is an environment fact, not a broken wheel, so a failed offline
    attempt is retried rather than reported.
    """

    command = ["uv", "build", "--wheel", "--out-dir", str(dist), str(source)]
    offline = _run([*command, "--offline"])
    return offline if offline.returncode == 0 else _run(command)


def _build_module_usable() -> bool:
    return _importable("build") and _importable("setuptools")


def _pip_usable() -> bool:
    return _importable("pip") and _importable("setuptools")


def _uv_usable() -> bool:
    return shutil.which("uv") is not None


WHEEL_BUILDERS: tuple[tuple[str, Callable[[], bool], WheelBuilder], ...] = (
    # Ordered by directness. Every entry must be able to build without an index:
    # the first two therefore require the backend to be importable already,
    # which is what disqualifies them in a uv-managed environment.
    ("python -m build", _build_module_usable, _build_with_build),
    ("pip wheel", _pip_usable, _build_with_pip),
    ("uv build", _uv_usable, _build_with_uv),
)


def _build_wheel(root: Path) -> Path:
    name, build = _first_available_builder()
    dist = root / "dist"

    built = build(_copy_build_inputs(root / "source"), dist)

    _check(built, f"building a wheel with {name}")
    wheels = sorted(dist.glob("*.whl"))
    assert len(wheels) == 1, f"expected one wheel in {dist}, found {[w.name for w in wheels]}"
    return wheels[0]


def _first_available_builder() -> tuple[str, WheelBuilder]:
    for name, available, build in WHEEL_BUILDERS:
        if available():
            return name, build
    pytest.skip(
        "no offline wheel builder available: needs the 'build' or 'pip' module "
        "together with 'setuptools' in this interpreter, or 'uv' on PATH"
    )


def _copy_build_inputs(destination: Path) -> Path:
    destination.mkdir(parents=True)
    for name in BUILD_INPUTS:
        origin = REPOSITORY_ROOT / name
        assert origin.exists(), f"{origin} is missing; the wheel cannot be built without it"
        if origin.is_dir():
            shutil.copytree(
                origin,
                destination / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"),
            )
        else:
            shutil.copy2(origin, destination / name)
    return destination


def _importable(module: str) -> bool:
    try:
        return find_spec(module) is not None
    except (ImportError, ValueError):  # a broken or namespace-shadowed install
        return False


# --------------------------------------------------------------------------- #
# installing
# --------------------------------------------------------------------------- #


def _install_wheel(wheel: Path, home: Path) -> Path:
    # Built from the interpreter running the tests, which is what makes lending
    # that interpreter's site-packages below ABI-compatible.
    venv.EnvBuilder(with_pip=True).create(home)
    python = _venv_script(home, "python")

    _check(
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--no-index",
                "--disable-pip-version-check",
                str(wheel),
            ]
        ),
        "installing the wheel",
    )
    _lend_third_party_packages(python)
    return home


def _lend_third_party_packages(python: Path) -> None:
    """Make httpx and friends importable without resolving a single dependency.

    A path listed in a ``.pth`` file is appended *after* the site-packages
    directory holding it, so the wheel's own ``repomesh_agent_bridge`` still
    wins. ``PYTHONPATH`` would land ahead of it and quietly turn this module
    into one more test of the checkout.
    """

    located = _check(
        _run([str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"]),
        "locating the virtualenv's site-packages",
    )
    lent = sysconfig.get_path("purelib")
    Path(located.stdout.strip()).joinpath("_bridge_smoke_dependencies.pth").write_text(
        f"{lent}\n", encoding="utf-8"
    )


def _venv_script(home: Path, name: str) -> Path:
    directory = home / ("Scripts" if os.name == "nt" else "bin")
    return directory / (f"{name}.exe" if os.name == "nt" else name)


# --------------------------------------------------------------------------- #
# subprocesses
# --------------------------------------------------------------------------- #


def _run(command: list[str]) -> Completed:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_child_environment(),
        timeout=SUBPROCESS_TIMEOUT,
        check=False,
    )


def _child_environment() -> dict[str, str]:
    environment = dict(os.environ)
    # An inherited PYTHONPATH would put the checkout ahead of the installed
    # wheel, and this module would stop testing packaging without saying so.
    environment.pop("PYTHONPATH", None)
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def _check(done: Completed, what: str) -> Completed:
    assert done.returncode == 0, f"{what} failed ({done.returncode}):\n{done.stdout}\n{done.stderr}"
    return done
