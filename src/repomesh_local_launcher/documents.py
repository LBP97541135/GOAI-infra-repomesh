"""Read/modify/write the two documents the Console's settings page edits.

FR-09's original posture -- "the launcher passes the env file's path and never
opens it"; "the roster arrives from a file the operator wrote" -- described a
read-only relationship with both files. The settings page edits them now,
through the launcher, because the browser cannot touch this machine's files and
the launcher is the process already trusted with starts and stops. The
structural rule survives the change, narrowed to what it was actually guarding:
the page still supplies **no path and no command line**. It supplies content
for the two files this process already owns by config, and both writes are
atomic-with-backup, so a half-served request can never leave either document
unparsable for ``start_members.ps1``.

The env file's values are never returned. The read side answers masked tails
(``****`` + last four), which is enough for an operator to tell *which* secret
a line holds and never enough to reuse it; the write side accepts new values
and answers with the same mask.
"""

import json
import os
from pathlib import Path

__all__ = [
    "allowed_env_names",
    "env_stem",
    "mask_secret",
    "read_env_entries",
    "read_roster",
    "write_env_entries",
    "write_roster",
]

_ROSTER_REQUIRED_MEMBER_FIELDS = ("key", "agentId", "role")


class DocumentInvalid(ValueError):
    """The submitted roster document fails the shape the launcher can vouch for."""


def env_stem(member_key: str) -> str:
    """The env-name stem, spelled exactly as ``scripts/bridge-e1/e1_config.py``.

    One derivation, two readers: the provisioning scripts mint these names and
    this module must accept no others, or a settings-page write could plant an
    arbitrary variable into a file that is exported into member processes.
    """

    return "E1_" + member_key.upper().replace("-", "_")


def allowed_env_names(roster: dict) -> set[str]:
    """Every env name this roster's members may have: ``<STEM>_MATRIX|REPOMESH_TOKEN``."""

    names: set[str] = set()
    for member in roster.get("members", []):
        if not isinstance(member, dict) or not isinstance(member.get("key"), str):
            continue
        stem = env_stem(member["key"])
        names.add(f"{stem}_MATRIX_TOKEN")
        names.add(f"{stem}_REPOMESH_TOKEN")
    return names


def mask_secret(value: str) -> str:
    """``****`` plus the last four characters; short values never echo at all."""

    if len(value) <= 4:
        return "****"
    return f"****{value[-4:]}"


def read_roster(path: Path) -> dict:
    """The parsed roster document; the plane re-reads the same file per call."""

    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise DocumentInvalid("roster document is not a JSON object")
    return document


def validate_roster(document: object) -> dict:
    """The minimum shape the start/stop plane needs from a submitted roster.

    Deliberately shallow: the plane reads ``key``/``agentId``/``role`` off every
    member and everything else is operator data this launcher never interprets.
    Validating deeply here would mean re-stating the provisioning scripts'
    rules, and a second, weaker copy of those rules is worse than none.
    """

    if not isinstance(document, dict):
        raise DocumentInvalid("名册必须是 JSON 对象")
    members = document.get("members")
    if not isinstance(members, list) or not members:
        raise DocumentInvalid("名册的 members 必须是非空数组")
    for member in members:
        if not isinstance(member, dict):
            raise DocumentInvalid("members 里每一项都必须是对象")
        for field in _ROSTER_REQUIRED_MEMBER_FIELDS:
            value = member.get(field)
            if not isinstance(value, str) or not value.strip():
                raise DocumentInvalid(f"成员缺少必填字段 {field}")
    return document


def write_roster(path: Path, document: dict) -> None:
    """Atomically replace the roster, keeping a ``.bak`` of the previous file."""

    validate_roster(document)
    _atomic_write(path, json.dumps(document, ensure_ascii=False, indent=2) + "\n")


def read_env_entries(path: Path) -> list[dict[str, str]]:
    """Every ``NAME=value`` line as ``{name, masked}``; values never leave home."""

    if not path.exists():
        return []
    entries: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if not separator or not name.strip() or name.strip().startswith("#"):
            continue
        entries.append({"name": name.strip(), "masked": mask_secret(value.strip())})
    return entries


def write_env_entries(path: Path, updates: dict[str, str], allowed_names: set[str]) -> None:
    """Set the named env lines, leaving every other line byte-for-byte alone.

    A name outside *allowed_names* is a refusal, not a new line: the point of
    deriving the set from the roster is that a settings page can only ever write
    a token of a member this machine actually has.
    """

    unknown = sorted(set(updates) - allowed_names)
    if unknown:
        raise DocumentInvalid(f"未知的密钥名：{', '.join(unknown)}")

    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    written: list[str] = []
    for line in lines:
        name, separator, _ = line.partition("=")
        stripped = name.strip()
        if separator and stripped in remaining:
            written.append(f"{stripped}={remaining.pop(stripped)}")
        else:
            written.append(line)
    for name, value in remaining.items():
        written.append(f"{name}={value}")
    _atomic_write(path, "\n".join(written) + "\n")


def _atomic_write(path: Path, content: str) -> None:
    """Write via sibling temp file + replace, keeping ``<name>.bak`` of the old."""

    backup = path.with_suffix(path.suffix + ".bak")
    if path.exists():
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(content, encoding="utf-8")
    os.replace(temp, path)
