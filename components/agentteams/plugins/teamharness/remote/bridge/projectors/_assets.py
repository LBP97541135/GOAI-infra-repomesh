#!/usr/bin/env python3
"""Projection logic shared by every runtime's ``AssetProjector``.

What differs between runtimes is only *where* assets go: ``CLAUDE.md`` vs
``AGENTS.md``, ``.claude/skills`` vs ``.codex/skills``. What the assets *are*,
and the rules for writing them into somebody's own repository without
destroying it, are identical -- so they live here and each projector supplies
the paths.

The marker algebra in particular must exist exactly once. It is the code that
decides which bytes of an operator's context file get replaced, and a second
copy is how one of them starts eating notes that were never ours.
"""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any

from ..protocol import AssetContext

# Everything between these two lines belongs to us; everything outside belongs
# to the operator. The marker text is part of the on-disk contract -- changing
# it would orphan the previous section instead of replacing it, so a user who
# upgraded the bridge would find two team contracts in their context file.
MANAGED_BEGIN = "<!-- BEGIN AGENTTEAMS TEAMHARNESS (managed; edits inside are overwritten) -->"
MANAGED_END = "<!-- END AGENTTEAMS TEAMHARNESS -->"

TRAILER = "Do not write secrets, credentials, or live task status into this file."

_COPY_IGNORE = shutil.ignore_patterns("__pycache__", ".DS_Store", "*.pyc")


# ---- context file ----------------------------------------------------


def write_context(target: Path, ctx: AssetContext, warnings: list[str]) -> str:
    """Write the managed section into ``target``. Returns its workspace name."""
    block = render_block(ctx, warnings)
    existing = read_text(target)

    if existing is None:
        new_text = block + "\n"
    else:
        span = managed_span(existing)
        if span is None:
            # Append, never prepend and never overwrite: the top of someone
            # else's context file is the part they actually read.
            head = existing if existing.endswith("\n") else existing + "\n"
            new_text = f"{head}\n{block}\n"
        else:
            start, end = span
            new_text = existing[:start] + block + existing[end:]

    if new_text != existing:
        target.write_text(new_text, encoding="utf-8")
    return target.name


def render_block(ctx: AssetContext, warnings: list[str]) -> str:
    plugin_dir = Path(ctx.plugin_dir)
    lines = [MANAGED_BEGIN, ""]

    contract = plugin_dir / "prompts" / "team" / "TEAMS.md"
    contract_text = read_text(contract)
    if contract_text:
        lines.append(contract_text.strip())
    else:
        warnings.append(f"team contract not found: {contract}")
        lines.append("# Team Contract")

    role_prompt = plugin_dir / "prompts" / "agent" / f"{role(ctx)}.md"
    role_text = read_text(role_prompt)
    if role_text:
        lines.extend(["", role_text.strip()])
    else:
        warnings.append(f"role prompt not found: {role_prompt}")

    lines.extend(["", "## Runtime Team Context", ""])
    for key, value in facts(ctx):
        if value:
            lines.append(f"- {key}: {value}")
    lines.extend(["", TRAILER, MANAGED_END])
    return "\n".join(lines)


def strip_context(target: Path) -> bool:
    existing = read_text(target)
    if existing is None:
        return False
    span = managed_span(existing)
    if span is None:
        return False
    start, end = span
    remainder = existing[:start] + existing[end:]
    if not remainder.strip():
        # The file existed only to carry our section; leaving an empty context
        # file behind would still change how the runtime starts up.
        target.unlink()
        return True
    target.write_text(remainder.rstrip("\n") + "\n", encoding="utf-8")
    return True


# ---- skills ----------------------------------------------------------


def install_skills(root: Path, ctx: AssetContext, warnings: list[str]) -> list[str]:
    installed: list[str] = []
    for skill_id, source in skills_for_role(ctx, warnings):
        if not source.is_dir():
            warnings.append(f"skill source missing: {source}")
            continue
        target = root / skill_id
        target.parent.mkdir(parents=True, exist_ok=True)
        # Full replace, not merge: these are managed assets, and a merge would
        # keep files deleted upstream alive forever.
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, ignore=_COPY_IGNORE)
        installed.append(skill_id)
    return installed


def remove_skills(root: Path, ctx: AssetContext, warnings: list[str]) -> list[str]:
    removed: list[str] = []
    for skill_id, _source in skills_for_role(ctx, warnings):
        target = root / skill_id
        if target.is_dir():
            shutil.rmtree(target)
            removed.append(skill_id)
    if root.is_dir() and not any(root.iterdir()):
        root.rmdir()
    return removed


def skills_for_role(ctx: AssetContext, warnings: list[str]) -> list[tuple[str, Path]]:
    plugin_dir = Path(ctx.plugin_dir)
    manifest_path = plugin_dir / "plugin.yaml"
    manifest = read_yaml(manifest_path, warnings)
    skills = manifest.get("skills")
    if not isinstance(skills, dict):
        if manifest:
            warnings.append(f"no skills section in {manifest_path}")
        return []

    aliases = role_aliases(role(ctx))
    selected: list[tuple[str, Path]] = []
    for group in ("agent", "team"):
        for entry in skills.get(group) or []:
            if not isinstance(entry, dict):
                continue
            skill_id = text(entry.get("id"))
            rel_path = text(entry.get("path"))
            if not skill_id or not rel_path:
                continue
            roles = [text(item) for item in entry.get("roles") or []]
            # An entry with no ``roles`` is unrestricted, which matches how the
            # qwenpaw adapter reads the same manifest.
            if roles and not aliases.intersection(roles):
                continue
            selected.append((skill_id, plugin_dir / rel_path))
    return selected


# ---- facts and roles -------------------------------------------------


def facts(ctx: AssetContext) -> list[tuple[str, str]]:
    """Non-secret runtime facts, in the qwenpaw ``- key: value`` shape."""
    return [
        ("team.name", text(ctx.team_name)),
        ("team.teamRoomId", text(ctx.team_room_id)),
        ("team.leaderName", text(ctx.leader_name)),
        ("member.name", text(ctx.member_name)),
        ("member.role", role(ctx)),
        ("member.matrixUserId", text(ctx.matrix_user_id)),
        ("member.personalRoomId", text(ctx.personal_room_id)),
    ]


def role(ctx: AssetContext) -> str:
    return text(ctx.role) or "remote-member"


def role_aliases(name: str) -> set[str]:
    """Tolerate the underscore spellings the manifest does not use.

    ``plugin.yaml`` says ``remote-member``; runtime configs have been seen
    carrying ``remote_member`` and ``team_leader``. Matching only the manifest
    spelling would silently install zero skills.
    """
    aliases = {name}
    if name == "remote_member":
        aliases.add("remote-member")
    if name == "team_leader":
        aliases.add("leader")
    return aliases


# ---- io --------------------------------------------------------------


def managed_span(body: str) -> tuple[int, int] | None:
    """``(start, end)`` of the managed section, or ``None`` if there is none.

    Both markers must be present and in order. A half-marker means someone
    edited the file by hand, and replacing a span we cannot delimit would eat
    whatever they wrote -- appending a fresh section is the recoverable choice.
    """
    start = body.find(MANAGED_BEGIN)
    if start < 0:
        return None
    end = body.find(MANAGED_END, start)
    if end < 0:
        return None
    return start, end + len(MANAGED_END)


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError, OSError):
        return None


def text(value: Any) -> str:
    # dict/list/tuple would stringify into something that looks like a value
    # but is really a parse error leaking into a projected file.
    if value is None or isinstance(value, (dict, list, tuple)):
        return ""
    return str(value).strip()


def read_yaml(path: Path, warnings: list[str]) -> dict[str, Any]:
    """Same parser as ``bootstrap.py``; a missing PyYAML degrades to a warning.

    Unlike the bootstrap file, an unreadable manifest is not fatal: the agent
    still gets its team contract and its tools, just no skills.
    """
    try:
        import yaml
    except ImportError:  # pragma: no cover - depends on the environment
        warnings.append("PyYAML is unavailable; no skills were projected")
        return {}

    raw = read_text(path)
    if raw is None:
        warnings.append(f"plugin manifest not found: {path}")
        return {}
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        warnings.append(f"{path}: invalid YAML: {exc}")
        return {}
    return data if isinstance(data, dict) else {}
