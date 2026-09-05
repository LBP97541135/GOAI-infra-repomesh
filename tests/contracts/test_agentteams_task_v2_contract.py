"""Producer-side contract tests for ``contracts/agentteams-task/v2``.

The publisher writes one construction and one review package to disk; every
JSON file is then checked against its ``.schema.json`` with a small
structural validator driven by the schema text itself (required keys, types,
enums, consts, patterns, formats), so the schema files are exercised and not
just the code. ``jsonschema`` is deliberately not a dependency.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest

from repomesh.integrations.agentteams.task_package import HELPER_COMMANDS, load_helper_script
from repomesh.integrations.agentteams.task_publishing import AgentTeamsTaskPublisher
from repomesh.modules.task_orchestration.contracts import (
    PackageInputs,
    PathPolicy,
    ReviewInputs,
    TaskStatus,
    TaskView,
)

CONTRACT_ROOT = Path(__file__).parents[2] / "contracts" / "agentteams-task" / "v2"
FIXTURES = Path(__file__).parent / "fixtures"

TASK_ID = UUID("00000000-0000-0000-0000-000000000101")
CONSTRUCTION_ATTEMPT = UUID("00000000-0000-0000-0000-00000000aaaa")
REVIEW_ATTEMPT = UUID("00000000-0000-0000-0000-00000000bbbb")
BASE_SHA = "882231dd887688a986b0faec656a90d29141406c"
HEAD_SHA = "5d9f0c2a" + "1" * 32


def load_schema(name: str) -> dict[str, object]:
    return json.loads((CONTRACT_ROOT / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# A structural validator small enough to read, driven entirely by the schema
# ---------------------------------------------------------------------------

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


def _resolve(ref: str, root: dict) -> dict:
    assert ref.startswith("#/"), ref
    node: object = root
    for part in ref[2:].split("/"):
        node = node[part]  # type: ignore[index]
    return node  # type: ignore[return-value]


def _is_type(instance: object, name: str) -> bool:
    if name == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    return isinstance(instance, _TYPES[name])


def check(instance: object, schema: dict, root: dict, where: str = "$") -> None:
    if "$ref" in schema:
        check(instance, _resolve(schema["$ref"], root), root, where)
        return
    if "oneOf" in schema:
        passes = 0
        for branch in schema["oneOf"]:
            try:
                check(instance, branch, root, where)
            except AssertionError:
                continue
            passes += 1
        assert passes == 1, f"{where}: {passes} oneOf branches matched"
        return
    if "const" in schema:
        assert instance == schema["const"], f"{where}: {instance!r} != {schema['const']!r}"
    if "enum" in schema:
        assert instance in schema["enum"], f"{where}: {instance!r} not in {schema['enum']}"
    declared = schema.get("type")
    if declared is not None:
        names = declared if isinstance(declared, list) else [declared]
        assert any(_is_type(instance, name) for name in names), f"{where}: not {names}"
    if isinstance(instance, dict):
        for key in schema.get("required", ()):
            assert key in instance, f"{where}: missing {key}"
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties:
                check(value, properties[key], root, f"{where}.{key}")
                continue
            extra = schema.get("additionalProperties", True)
            assert extra is not False, f"{where}: unexpected key {key}"
            if isinstance(extra, dict):
                check(value, extra, root, f"{where}.{key}")
    if isinstance(instance, list):
        if "minItems" in schema:
            assert len(instance) >= schema["minItems"], f"{where}: too few items"
        if "maxItems" in schema:
            assert len(instance) <= schema["maxItems"], f"{where}: too many items"
        if schema.get("uniqueItems"):
            assert len(set(map(json.dumps, instance))) == len(instance), f"{where}: duplicates"
        if "items" in schema:
            for index, item in enumerate(instance):
                check(item, schema["items"], root, f"{where}[{index}]")
    if isinstance(instance, str):
        if "pattern" in schema:
            assert re.search(schema["pattern"], instance), f"{where}: {instance!r} !~ pattern"
        if "minLength" in schema:
            assert len(instance) >= schema["minLength"], f"{where}: too short"
        if "maxLength" in schema:
            assert len(instance) <= schema["maxLength"], f"{where}: too long"
        if schema.get("format") == "uuid":
            UUID(instance)
        if schema.get("format") == "date-time":
            datetime.fromisoformat(instance)
    if _is_type(instance, "integer") and "minimum" in schema:
        assert instance >= schema["minimum"], f"{where}: below minimum"


# ---------------------------------------------------------------------------
# Fixtures: one task, one construction attempt, one review of it
# ---------------------------------------------------------------------------


def task_view() -> TaskView:
    return TaskView(
        id=TASK_ID,
        organization_id=UUID("00000000-0000-0000-0000-000000000001"),
        project_id=UUID("00000000-0000-0000-0000-000000000002"),
        repository_id=UUID("00000000-0000-0000-0000-000000000003"),
        parent_task_id=None,
        assigned_by_agent_id=UUID("00000000-0000-0000-0000-000000000010"),
        assignee_agent_id=UUID("00000000-0000-0000-0000-000000000011"),
        title="Implement multi-currency quote()",
        instruction="Modify quote() to accept a mandatory currency parameter.",
        acceptance=("Code compiles without errors.", "Existing tests pass unchanged."),
        status=TaskStatus.ASSIGNED,
        result_summary=None,
        version=0,
    )


POLICY = PathPolicy(allowed_paths=("src/**", "tests/**", "README.md"), denied_paths=(".github/**",))
TEST_COMMANDS = ("python scripts/run_tests.py",)


def construction_inputs() -> PackageInputs:
    return PackageInputs(
        kind="construction",
        attempt_id=CONSTRUCTION_ATTEMPT,
        generation=1,
        budget_seconds=2700,
        base_sha=BASE_SHA,
        helper_script=load_helper_script(),
        policy=POLICY,
        test_commands=TEST_COMMANDS,
        base_bundle=b"# git bundle v2\n",
    )


def candidate_files() -> tuple[str, str, str]:
    changes = {
        "attempt_id": str(CONSTRUCTION_ATTEMPT),
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "changed_files": [
            {"status": "M", "path": "src/pricing_core/quote.py"},
            {"status": "M", "path": "tests/test_quote.py"},
        ],
    }
    evidence = {
        "attempt_id": str(CONSTRUCTION_ATTEMPT),
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "tree": "c" * 40,
        "tests_ran_at": "2026-09-03T20:26:40+00:00",
        "tests": [
            {"command": TEST_COMMANDS[0], "exit_code": 0, "excerpt": "Ran 9 tests\n\nOK"},
        ],
        "produced_at": "2026-09-03T20:27:01+00:00",
    }
    diff = (
        "diff --git a/src/pricing_core/quote.py b/src/pricing_core/quote.py\n"
        "--- a/src/pricing_core/quote.py\n+++ b/src/pricing_core/quote.py\n"
        "@@ -1 +1 @@\n-def quote(amount):\n+def quote(amount, currency):\n"
    )
    return (
        diff,
        json.dumps(changes, indent=2) + "\n",
        json.dumps(evidence, indent=2) + "\n",
    )


def review_inputs() -> PackageInputs:
    diff, changes_json, evidence_json = candidate_files()
    return PackageInputs(
        kind="review",
        attempt_id=REVIEW_ATTEMPT,
        generation=1,
        budget_seconds=900,
        base_sha=BASE_SHA,
        helper_script=load_helper_script(),
        policy=POLICY,
        test_commands=TEST_COMMANDS,
        review=ReviewInputs(
            review_of=CONSTRUCTION_ATTEMPT,
            head_sha=HEAD_SHA,
            candidate_diff=diff,
            changes_json=changes_json,
            evidence_json=evidence_json,
        ),
    )


async def publish(tmp_path: Path, package: PackageInputs, *, assignee: str, room: str) -> Path:
    published = await AgentTeamsTaskPublisher(tmp_path).publish(
        task_view(),
        team_name="repomesh-team-dfb8a4cd",
        room_id=room,
        assignee_resource_name=assignee,
        idempotency_key=f"publish-{package.attempt_id}",
        package=package,
    )
    return tmp_path / published.task_path


def files_on_disk(task_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(task_dir).as_posix(): path.read_bytes()
        for path in sorted(task_dir.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }


def spike_content_hash(files: dict[str, bytes]) -> str:
    """The wave-0 ``write_manifest`` recipe, written out independently of the publisher."""

    joined = "".join(
        f"{name}\0sha256:{hashlib.sha256(files[name]).hexdigest()}\n" for name in sorted(files)
    )
    return f"sha256:{hashlib.sha256(joined.encode()).hexdigest()}"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Package files against their schemas
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_construction_package_files_match_their_schemas(tmp_path: Path) -> None:
    task_dir = await publish(
        tmp_path, construction_inputs(), assignee="agt-worker-dfb8a4cd", room="!team:matrix"
    )
    manifest = read_json(task_dir / "manifest.json")
    meta = read_json(task_dir / "meta.json")
    control = read_json(task_dir / "base" / "package.json")

    check(manifest, load_schema("manifest.schema.json"), load_schema("manifest.schema.json"))
    check(meta, load_schema("meta.schema.json"), load_schema("meta.schema.json"))
    check(control, load_schema("package.schema.json"), load_schema("package.schema.json"))

    assert task_dir.name == str(CONSTRUCTION_ATTEMPT)
    assert meta["task_id"] == str(CONSTRUCTION_ATTEMPT)
    assert meta["repomesh"]["task_id"] == str(TASK_ID)
    assert "review_of" not in meta["repomesh"]
    assert control["kind"] == "construction"
    assert control["base_sha"] == BASE_SHA
    assert control["test_commands"] == list(TEST_COMMANDS)
    assert control["allowed_paths"] == list(POLICY.allowed_paths)
    assert control["denied_paths"] == list(POLICY.denied_paths)
    assert control["workspace_root"] == "/work"

    written = files_on_disk(task_dir)
    assert manifest["files"] == sorted(written)
    assert {"meta.json", "spec.md", "base/base.bundle", "base/package.json"} <= set(written)
    assert "base/tools/repomesh-work.sh" in written
    assert not any(name.startswith("review/") for name in written)
    assert manifest["content_hash"] == spike_content_hash(written)
    assert set(manifest["file_digests"]) == set(written)
    assert manifest["file_sizes"] == {name: len(data) for name, data in written.items()}
    assert (task_dir / "base" / "base.bundle").read_bytes() == b"# git bundle v2\n"
    assert (task_dir / "base" / "tools" / "repomesh-work.sh").read_bytes() == load_helper_script()


@pytest.mark.asyncio
async def test_review_package_files_match_their_schemas(tmp_path: Path) -> None:
    task_dir = await publish(
        tmp_path, review_inputs(), assignee="agt-leader-dfb8a4cd", room="!leader:matrix"
    )
    manifest = read_json(task_dir / "manifest.json")
    meta = read_json(task_dir / "meta.json")
    control = read_json(task_dir / "base" / "package.json")
    candidate = load_schema("candidate.schema.json")

    check(manifest, load_schema("manifest.schema.json"), load_schema("manifest.schema.json"))
    check(meta, load_schema("meta.schema.json"), load_schema("meta.schema.json"))
    check(control, load_schema("package.schema.json"), load_schema("package.schema.json"))
    check(read_json(task_dir / "review" / "changes.json"), candidate["$defs"]["changes"], candidate)
    check(
        read_json(task_dir / "review" / "evidence.json"), candidate["$defs"]["evidence"], candidate
    )

    assert task_dir.name == str(REVIEW_ATTEMPT)
    assert meta["assigned_to"] == "agt-leader-dfb8a4cd"
    assert meta["room_id"] == "!leader:matrix"
    assert meta["repomesh"]["kind"] == "review"
    assert meta["repomesh"]["review_of"] == str(CONSTRUCTION_ATTEMPT)
    assert control["kind"] == "review"

    written = files_on_disk(task_dir)
    assert manifest["files"] == sorted(written)
    assert {"review/candidate.diff", "review/changes.json", "review/evidence.json"} <= set(written)
    assert "base/base.bundle" not in written
    assert manifest["content_hash"] == spike_content_hash(written)
    diff, changes_json, evidence_json = candidate_files()
    assert written["review/candidate.diff"] == diff.encode()
    assert written["review/changes.json"] == changes_json.encode()
    assert written["review/evidence.json"] == evidence_json.encode()


def test_meta_schema_says_the_repomesh_block_is_publish_time_only() -> None:
    schema = load_schema("meta.schema.json")
    text = schema["description"] + schema["properties"]["repomesh"]["description"]

    assert "publish time" in text
    assert "ack_task" in text and "submit_task" in text
    assert "must not depend" in text


# ---------------------------------------------------------------------------
# The four helper command lines
# ---------------------------------------------------------------------------


def helper_lines_from_doc() -> list[str]:
    doc = (CONTRACT_ROOT / "helper-cli.md").read_text(encoding="utf-8")
    block = re.search(r"## Command lines \(verbatim\).*?```text\n(.*?)```", doc, re.DOTALL)
    assert block, "helper-cli.md lost its verbatim command block"
    return block.group(1).splitlines()


@pytest.mark.asyncio
async def test_helper_commands_are_verbatim_everywhere(tmp_path: Path) -> None:
    task_dir = await publish(
        tmp_path, construction_inputs(), assignee="agt-worker-dfb8a4cd", room="!team:matrix"
    )
    control = read_json(task_dir / "base" / "package.json")

    assert control["helper_commands"] == list(HELPER_COMMANDS)
    assert helper_lines_from_doc() == list(HELPER_COMMANDS)
    assert control["helper"] == "base/tools/repomesh-work.sh"
    verbs = [line.rsplit(" ", 1)[1] for line in HELPER_COMMANDS]
    assert verbs == ["init", "test", "bundle", "clean"]
    for line in HELPER_COMMANDS:
        assert line.startswith("bash base/tools/repomesh-work.sh ")
        assert not re.search(r"\brm\b", line), line
        for fragment in ("rm ", "rm-", "/rm"):
            assert fragment not in line, (line, fragment)


# ---------------------------------------------------------------------------
# The specs the assignees read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_construction_spec_names_the_commands_and_keeps_the_leader_out(
    tmp_path: Path,
) -> None:
    task_dir = await publish(
        tmp_path, construction_inputs(), assignee="agt-worker-dfb8a4cd", room="!team:matrix"
    )
    spec = (task_dir / "spec.md").read_text(encoding="utf-8")

    assert spec.startswith("# Implement multi-currency quote()\n")
    for command in HELPER_COMMANDS[:3]:
        assert command in spec
    assert HELPER_COMMANDS[3] not in spec  # clean is the platform's, not the worker's
    assert spec.count("bash base/tools/repomesh-work.sh") == 3
    assert "Modify quote() to accept a mandatory currency parameter." in spec
    assert "- Code compiles without errors.\n- Existing tests pass unchanged.\n" in spec
    assert "`python scripts/run_tests.py`" in spec
    assert "`src/**`, `tests/**`, `README.md`" in spec and "`.github/**`" in spec
    for deliverable in ("candidate.bundle", "candidate.diff", "changes.json", "evidence.json"):
        assert f"shared/tasks/{CONSTRUCTION_ATTEMPT}/candidate/{deliverable}" in spec
    assert 'taskflow(action="ack_task"' in spec and 'taskflow(action="submit_task"' in spec
    assert "SUCCESS_WITH_NOTES" in spec and "BLOCKED" in spec
    assert f"TASK_COMPLETED: {CONSTRUCTION_ATTEMPT} - " in spec
    assert "`@admin`" in spec
    assert re.search(r"never @mention the team leader", spec, re.IGNORECASE)
    assert "@Leader" not in spec and "@leader" not in spec.lower()
    assert "mention your coordinator" not in spec
    assert "@mention the Leader" not in spec
    assert "repomesh-task-control" in spec and "MCP" in spec
    assert spec.count("`Progress:`") == 1
    assert "\r" not in spec and spec.endswith("\n")


@pytest.mark.asyncio
async def test_review_spec_carries_the_verdict_protocol(tmp_path: Path) -> None:
    task_dir = await publish(
        tmp_path, review_inputs(), assignee="agt-leader-dfb8a4cd", room="!leader:matrix"
    )
    spec = (task_dir / "spec.md").read_text(encoding="utf-8")

    assert spec.startswith(f"# Review candidate `{HEAD_SHA}`: Implement multi-currency quote()\n")
    assert f"`{CONSTRUCTION_ATTEMPT}`" in spec and f"`{REVIEW_ATTEMPT}`" in spec
    assert "VERDICT: <ACCEPT | REVISION | BLOCKED>" in spec
    assert "`SUCCESS` = `ACCEPT`" in spec
    assert "`SUCCESS_WITH_NOTES` = `ACCEPT`" in spec
    assert "`REVISION_NEEDED` = `REVISION`" in spec
    assert "`BLOCKED` = `BLOCKED`" in spec
    assert (
        "Do not use `delegate_task`, do not create a project, and do not @mention the Worker."
        in spec
    )
    assert "Do not run the tests yourself" in spec
    assert f"REVIEW_DONE: {REVIEW_ATTEMPT} - VERDICT:" in spec
    assert "- `M` `src/pricing_core/quote.py`" in spec
    assert "- `python scripts/run_tests.py` -> exit 0" in spec
    assert "+def quote(amount, currency):" in spec
    assert "bash base/tools/repomesh-work.sh" not in spec


# ---------------------------------------------------------------------------
# D-21: the command lines against the copaw Tool Guard rule set
# ---------------------------------------------------------------------------


def load_tool_guard_rules() -> list[dict]:
    fixture = json.loads((FIXTURES / "copaw_tool_guard_rules.json").read_text(encoding="utf-8"))
    provenance = fixture["_provenance"]
    assert provenance["copaw_version"] and provenance["image_id"].startswith("sha256:")
    return fixture["rules"]


def guard_findings(command: str, rules: list[dict]) -> list[str]:
    """Mirror ``RuleBasedToolGuardian.guard`` for ``execute_shell_command(command=...)``."""

    findings: list[str] = []
    for rule in rules:
        if "execute_shell_command" not in rule.get("tools", []):
            continue
        if rule.get("params") and "command" not in rule["params"]:
            continue
        if any(
            re.compile(pattern, re.IGNORECASE).search(command)
            for pattern in rule.get("exclude_patterns", [])
        ):
            continue
        if any(re.compile(pattern, re.IGNORECASE).search(command) for pattern in rule["patterns"]):
            findings.append(rule["id"])
    return findings


def test_helper_command_lines_pass_the_copaw_tool_guard_rules() -> None:
    rules = load_tool_guard_rules()
    assert {rule["id"] for rule in rules} >= {
        "TOOL_CMD_DANGEROUS_RM",
        "TOOL_CMD_PRIVILEGE_ESCALATION",
        "TOOL_CMD_PIPE_TO_SHELL",
        "TOOL_CMD_SYSTEM_REBOOT",
    }
    assert all(rule["severity"] in {"CRITICAL", "HIGH", "MEDIUM", "LOW"} for rule in rules)

    for line in HELPER_COMMANDS:
        assert guard_findings(line, rules) == [], line
    # Workers in the spike prefixed the task directory; that form must stay clean too.
    prefixed = f"cd shared/tasks/{CONSTRUCTION_ATTEMPT} && {HELPER_COMMANDS[0]}"
    assert guard_findings(prefixed, rules) == []

    # Control: the wave-0 name is exactly what the rule set stops (spike S-1).
    assert guard_findings("bash base/tools/rm-work.sh init", rules) == ["TOOL_CMD_DANGEROUS_RM"]
    assert "TOOL_CMD_PRIVILEGE_ESCALATION" in guard_findings("sudo bash base/tools/x.sh", rules)
