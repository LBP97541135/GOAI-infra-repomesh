import os
from pathlib import Path

from cryptography.fernet import Fernet

DEFAULT_KEY_PATH = Path(".secrets/platform-credentials.key")


def credentials_key_path() -> Path:
    return Path(os.environ.get("REPOMESH_CREDENTIALS_ENCRYPTION_KEY_FILE", DEFAULT_KEY_PATH))


def get_credentials_fernet() -> Fernet:
    configured = os.environ.get("REPOMESH_CREDENTIALS_ENCRYPTION_KEY", "").strip()
    if configured:
        return Fernet(configured.encode("ascii"))

    path = credentials_key_path()
    try:
        key = path.read_bytes().strip()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        try:
            with path.open("xb") as handle:
                handle.write(key + b"\n")
        except FileExistsError:
            key = path.read_bytes().strip()
    return Fernet(key)
