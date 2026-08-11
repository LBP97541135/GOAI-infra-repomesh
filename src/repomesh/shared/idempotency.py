"""Canonical request fingerprints for idempotent application commands."""

import hashlib
import json
from dataclasses import asdict


def command_fingerprint(command: object) -> str:
    encoded = json.dumps(
        asdict(command), sort_keys=True, default=str, separators=(",", ":")
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
