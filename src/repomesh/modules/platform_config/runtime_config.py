import os
import re
import tempfile
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_RUNTIME_CONFIG_PATH = Path(".secrets/platform-runtime.env")

RUNTIME_CONFIG_KEYS = frozenset(
    {
        "REPOMESH_AGENTTEAMS_REQUIRED",
        "REPOMESH_AGENTTEAMS_CONTROLLER_URL",
        "REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN",
        "REPOMESH_AGENTTEAMS_MATRIX_URL",
        "REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN",
        "REPOMESH_AGENTTEAMS_STORAGE_ENDPOINT",
        "REPOMESH_AGENTTEAMS_STORAGE_ACCESS_KEY",
        "REPOMESH_AGENTTEAMS_STORAGE_SECRET_KEY",
        "REPOMESH_AGENTTEAMS_STORAGE_BUCKET",
    }
)

_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
_URL_KEYS = frozenset(
    {
        "REPOMESH_AGENTTEAMS_CONTROLLER_URL",
        "REPOMESH_AGENTTEAMS_MATRIX_URL",
        "REPOMESH_AGENTTEAMS_STORAGE_ENDPOINT",
    }
)


class RuntimeConfigError(RuntimeError):
    pass


def runtime_config_path() -> Path:
    return Path(os.environ.get("REPOMESH_RUNTIME_CONFIG_FILE", DEFAULT_RUNTIME_CONFIG_PATH))


def read_runtime_config(path: Path | None = None) -> dict[str, str]:
    target = path or runtime_config_path()
    try:
        content = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in raw_line:
            raise RuntimeConfigError(f"runtime config line {line_number} is malformed")
        key, value = raw_line.split("=", 1)
        key = key.strip()
        if not _KEY_PATTERN.fullmatch(key) or key not in RUNTIME_CONFIG_KEYS:
            raise RuntimeConfigError(f"runtime config key is not allowed: {key}")
        if key in values:
            raise RuntimeConfigError(f"runtime config key is duplicated: {key}")
        _validate_value(key, value)
        values[key] = value
    return values


def load_runtime_environment(
    path: Path | None = None,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    environment = environ if environ is not None else os.environ
    values = read_runtime_config(path)
    for key, value in values.items():
        if not environment.get(key):
            environment[key] = value
    return values


def write_runtime_config(values: Mapping[str, str], path: Path | None = None) -> Path:
    target = path or runtime_config_path()
    normalized: dict[str, str] = {}
    for key, value in values.items():
        if key not in RUNTIME_CONFIG_KEYS:
            raise RuntimeConfigError(f"runtime config key is not allowed: {key}")
        _validate_value(key, value)
        normalized[key] = value
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            os.chmod(temporary_path, 0o600)
            for key in sorted(normalized):
                handle.write(f"{key}={normalized[key]}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
        return target
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _validate_value(key: str, value: str) -> None:
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise RuntimeConfigError(f"runtime config value contains invalid characters: {key}")
    if key == "REPOMESH_AGENTTEAMS_REQUIRED" and value not in {"true", "false"}:
        raise RuntimeConfigError("REPOMESH_AGENTTEAMS_REQUIRED must be true or false")
    if key in _URL_KEYS and value:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeConfigError(f"runtime config URL is invalid: {key}")
