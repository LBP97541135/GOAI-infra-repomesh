"""Everything the launcher is allowed to know, read once from a file on disk.

The config is a file and not a request body, and that is the whole of FR-09's
"the page may not pass a command line, a script path, a credential env or a
member definition". A caller cannot supply what the API has no field for: the
roster, the enrollment directory, the env file and the workspace root arrive
here, from a file the operator wrote, and the four operations name a member at
most. This is why the write routes declare no body model at all -- the refusal
is structural rather than a validation rule somebody could relax.

The file lives under the gitignored ``output/`` (the construction spec puts it
at ``output/local-launcher/config.json``) because it names this machine's real
paths, and no example with real paths is tracked. The keys, therefore, are
documented here rather than in a sample file:

Every path key is an absolute path on this machine. The scripts resolve a
relative one against the repository root and the launcher does not, so leaving
one relative would mean two different files answering to the same config.

``membersFile``
    Absolute path to the bridge-e1 roster, the same file ``start_members.ps1``
    reads.
``enrollmentDir``
    Absolute path to the directory holding ``enrollment.<key>.json``, one per
    member.
``envFile``
    Absolute path to the gitignored ``NAME=value`` file holding the members'
    Matrix credentials. The launcher passes the path and never opens it.
``runtimeDir``
    Absolute path to the directory whose ``pids/`` and ``logs/`` the start
    script writes into.
``workspaceRoot`` (optional)
    Absolute path to the control plane's shared workspace root for workers.
    Absent means the start script picks its own default; leaders never receive
    one either way.
``subset`` (optional)
    A roster tag. When present the launcher sees only the members carrying it,
    so status, start and stop all mean the same set of members.
``rosterVersion``
    An opaque string the Console echoes and derives its start key from (FR-10).
    The launcher does not interpret it.
``allowedOrigins``
    The exact ``Origin`` values a write may come from.
``port``
    Loopback port, default 8121.

Every required key is read by subscript, so a config missing one raises
``KeyError`` at load and the launcher never binds a socket. There is no default
worth guessing for any of them: a missing roster path would serve nobody and a
missing allowlist would serve everybody.
"""

import json
from dataclasses import dataclass
from pathlib import Path

__all__ = ["DEFAULT_PORT", "LauncherConfig", "load_config"]

DEFAULT_PORT = 8121


@dataclass(frozen=True, slots=True)
class LauncherConfig:
    """The operator's answers, frozen for the life of the process."""

    members_file: Path
    enrollment_dir: Path
    env_file: Path
    runtime_dir: Path
    workspace_root: Path | None
    subset: str | None
    roster_version: str
    allowed_origins: tuple[str, ...]
    port: int

    @property
    def pid_dir(self) -> Path:
        """Where ``start-local-cli.ps1`` puts PID files, derived exactly as it derives it."""
        return self.runtime_dir / "pids"

    @property
    def log_dir(self) -> Path:
        """The sibling of :attr:`pid_dir`, and the other half of the same convention."""
        return self.runtime_dir / "logs"


def load_config(path: Path) -> LauncherConfig:
    """Read the config file, or fail before anything else happens."""
    document = json.loads(path.read_text(encoding="utf-8"))
    workspace_root = document.get("workspaceRoot")
    return LauncherConfig(
        members_file=Path(document["membersFile"]),
        enrollment_dir=Path(document["enrollmentDir"]),
        env_file=Path(document["envFile"]),
        runtime_dir=Path(document["runtimeDir"]),
        workspace_root=None if workspace_root is None else Path(workspace_root),
        subset=document.get("subset"),
        roster_version=document["rosterVersion"],
        allowed_origins=tuple(document["allowedOrigins"]),
        port=document.get("port", DEFAULT_PORT),
    )
