"""Worker-facing files that ship inside a hosted-native task package (v2).

The three non-Python files next to this module are package data — the spec
templates the publisher renders and the helper script it copies verbatim into
``base/tools/repomesh-work.sh`` — so they version with the wheel, not with a
deployment. ``pyproject.toml`` lists them under ``[tool.setuptools.package-data]``.

The four helper command lines are spelled out here once because two
independent consumers compare against them character for character: the
publisher writes them into ``base/package.json.helper_commands[]`` and the
observer's auto-approval (spec D-23) only ever approves a Tool Guard request
whose intercepted command line equals one of them. None of the four may
contain a token the copaw Tool Guard reads as dangerous (D-21; the wave-0
script name ``rm-work.sh`` was stopped by ``TOOL_CMD_DANGEROUS_RM`` on the
``rm`` in its own name). ``tests/contracts/test_agentteams_task_v2_contract.py``
runs them through the rule set exported from the live worker image.
"""

from __future__ import annotations

import json
import string
from importlib import resources

HELPER_PATH = "base/tools/repomesh-work.sh"
HELPER_COMMANDS: tuple[str, str, str, str] = (
    "bash base/tools/repomesh-work.sh init",
    "bash base/tools/repomesh-work.sh test",
    "bash base/tools/repomesh-work.sh bundle",
    "bash base/tools/repomesh-work.sh clean",
)

_PACKAGE = resources.files(__name__)


def _read_text(name: str) -> str:
    # ``newline=""`` keeps the LF the files are committed with; the rendered
    # spec must hash identically on every platform.
    with _PACKAGE.joinpath(name).open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def load_helper_script() -> bytes:
    """The helper script exactly as committed (LF, no trailing changes)."""

    return _PACKAGE.joinpath("repomesh-work.sh").read_bytes()


def _render(template_name: str, values: dict[str, object]) -> str:
    text = string.Template(_read_text(template_name)).substitute(values)
    return text if text.endswith("\n") else text + "\n"


def _bullets(items: tuple[str, ...]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _code_list(items: tuple[str, ...]) -> str:
    return ", ".join(f"`{item}`" for item in items)


def path_rule(allowed_paths: tuple[str, ...], denied_paths: tuple[str, ...]) -> str:
    """One sentence stating the path policy, for both spec kinds."""

    allowed = (
        f"Only files under {_code_list(allowed_paths)} may change."
        if allowed_paths
        else "Any file in the repository may change."
    )
    denied = (
        f" Never touch {_code_list(denied_paths)}."
        if denied_paths
        else " No path is denied beyond that."
    )
    return allowed + denied


def render_construction_spec(
    *,
    title: str,
    attempt_id: str,
    generation: int,
    task_id: str,
    base_sha: str,
    budget_seconds: int,
    instruction: str,
    acceptance: tuple[str, ...],
    test_commands: tuple[str, ...],
    allowed_paths: tuple[str, ...],
    denied_paths: tuple[str, ...],
) -> str:
    return _render(
        "spec_construction.md.tpl",
        {
            "title": title,
            "attempt_id": attempt_id,
            "generation": generation,
            "task_id": task_id,
            "base_sha": base_sha,
            "budget_minutes": max(1, budget_seconds // 60),
            "instruction": instruction,
            "acceptance": _bullets(acceptance),
            "test_commands": _code_list(test_commands),
            "path_rule": path_rule(allowed_paths, denied_paths),
        },
    )


def render_review_spec(
    *,
    title: str,
    attempt_id: str,
    review_of: str,
    generation: int,
    task_id: str,
    base_sha: str,
    head_sha: str,
    budget_seconds: int,
    instruction: str,
    acceptance: tuple[str, ...],
    test_commands: tuple[str, ...],
    allowed_paths: tuple[str, ...],
    denied_paths: tuple[str, ...],
    candidate_diff: str,
    changes_json: str,
    evidence_json: str,
) -> str:
    """Render the Leader's review spec around the worker's candidate.

    ``changes_json`` / ``evidence_json`` are the worker's files verbatim; they
    are parsed only to list the changed files and the tail of each test run.
    A file that is not the JSON the helper writes is a publish-time error, not
    a spec that quietly says nothing.
    """

    try:
        changes = json.loads(changes_json)
        evidence = json.loads(evidence_json)
        changed_files = [
            f"- `{entry['status']}` `{entry['path']}`" for entry in changes["changed_files"]
        ]
        tests_block = [
            f"- `{run['command']}` -> exit {run['exit_code']}\n\n  ```text\n  "
            + "\n  ".join(str(run.get("excerpt", "")).splitlines()[-12:])
            + "\n  ```"
            for run in evidence["tests"]
        ]
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(
            f"review inputs are not the candidate files the helper writes: {error}"
        ) from error
    return _render(
        "spec_review.md.tpl",
        {
            "title": title,
            "attempt_id": attempt_id,
            "review_of": review_of,
            "generation": generation,
            "task_id": task_id,
            "base_sha": base_sha,
            "head_sha": head_sha,
            "budget_minutes": max(1, budget_seconds // 60),
            "instruction": instruction,
            "acceptance": _bullets(acceptance),
            "test_commands": _code_list(test_commands),
            "path_rule": path_rule(allowed_paths, denied_paths),
            "changed_files": "\n".join(changed_files) or "- (none)",
            "tests_block": "\n".join(tests_block) or "- (no test runs recorded)",
            "diff": candidate_diff.rstrip("\n"),
        },
    )
