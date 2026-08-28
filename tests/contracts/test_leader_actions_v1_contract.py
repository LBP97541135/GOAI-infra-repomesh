"""Executable freeze of the Leader Actions v1 contract — wave-0 baseline.

``contracts/leader-actions/v1`` is the wire contract between the PR 7 server (producer)
and the PR 8 Bridge leader lane (consumer). Both sides' test suites must consume the
fixture files under ``fixtures/`` rather than hand-rolling copies; this module referees
that the fixtures and schemas agree with each other and that the frozen invariants
(README "Frozen invariants" 1–5) hold on the fixtures themselves:

1. phase/evidence coupling, 2. idempotent-receipt identity, 3. DAG validity,
4. envelope clamp, 5. provenance + rework revision.

A schema here nests objects several levels deep, so the flat structural check used by
the v1 agent-bridge test is not enough; ``validate`` below is a deliberately small
recursive walker over the subset of JSON Schema these documents use. It is a test
helper, not a validator library: it exists so a fixture that stops matching its schema
fails with the offending path, and it refuses schema keywords it does not know rather
than silently passing them.
"""

import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

ROOT = Path(__file__).parents[2] / "contracts" / "leader-actions" / "v1"
FIXTURES = ROOT / "fixtures"

_HANDLED_KEYWORDS = {
    "$schema",
    "$id",
    "$defs",
    "$ref",
    "title",
    "description",
    "type",
    "const",
    "enum",
    "oneOf",
    "required",
    "properties",
    "additionalProperties",
    "items",
    "minItems",
    "uniqueItems",
    "pattern",
    "minLength",
    "maxLength",
    "minimum",
    "format",
}

_TYPE_CHECKS = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "boolean": bool,
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(schema: dict[str, Any], value: Any, *, defs: dict[str, Any], path: str = "$") -> None:
    unknown = set(schema) - _HANDLED_KEYWORDS
    assert not unknown, f"{path}: schema uses keywords this walker does not check: {unknown}"

    if "$ref" in schema:
        target = schema["$ref"]
        assert target.startswith("#/$defs/"), f"{path}: unsupported $ref {target}"
        validate(defs[target.removeprefix("#/$defs/")], value, defs=defs, path=path)
        return

    if "oneOf" in schema:
        errors = []
        for i, alternative in enumerate(schema["oneOf"]):
            try:
                validate(alternative, value, defs=defs, path=f"{path}|oneOf[{i}]")
                return
            except AssertionError as error:  # noqa: PERF203 - collecting diagnostics
                errors.append(str(error))
        raise AssertionError(f"{path}: no oneOf alternative matched: {errors}")

    if "const" in schema:
        assert value == schema["const"], f"{path}: {value!r} != const {schema['const']!r}"
    if "enum" in schema:
        assert value in schema["enum"], f"{path}: {value!r} not in enum"

    declared = schema.get("type")
    if declared == "null":
        assert value is None, f"{path}: expected null"
        return
    if declared is not None:
        expected = _TYPE_CHECKS[declared]
        got = type(value).__name__
        assert isinstance(value, expected), f"{path}: expected {declared}, got {got}"
        if declared == "integer":
            assert not isinstance(value, bool), f"{path}: bool is not an integer"

    if declared == "object":
        assert schema.get("additionalProperties") is False, f"{path}: objects must be closed"
        properties = schema.get("properties", {})
        for key in schema.get("required", ()):
            assert key in value, f"{path}: missing required {key!r}"
        for key, item in value.items():
            assert key in properties, f"{path}: undeclared property {key!r}"
            validate(properties[key], item, defs=defs, path=f"{path}.{key}")

    if declared == "array":
        if "minItems" in schema:
            minimum_items = schema["minItems"]
            assert len(value) >= minimum_items, f"{path}: fewer than {minimum_items} items"
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, sort_keys=True) for item in value]
            assert len(set(canonical)) == len(canonical), f"{path}: duplicate items"
        for i, item in enumerate(value):
            validate(schema["items"], item, defs=defs, path=f"{path}[{i}]")

    if declared == "string":
        if "minLength" in schema:
            assert len(value) >= schema["minLength"], f"{path}: shorter than minLength"
        if "maxLength" in schema:
            assert len(value) <= schema["maxLength"], f"{path}: longer than maxLength"
        if "pattern" in schema:
            assert re.search(schema["pattern"], value), f"{path}: does not match pattern"
        if schema.get("format") == "uuid":
            UUID(value)

    if declared == "integer" and "minimum" in schema:
        assert value >= schema["minimum"], f"{path}: below minimum"


def validate_document(schema: dict[str, Any], document: Any) -> None:
    validate(schema, document, defs=schema.get("$defs", {}))


@pytest.fixture(scope="module")
def package_schema() -> dict[str, Any]:
    return load_json(ROOT / "repository-assignment-package.schema.json")


@pytest.fixture(scope="module")
def plan_schema() -> dict[str, Any]:
    return load_json(ROOT / "repository-plan-decision.schema.json")


@pytest.fixture(scope="module")
def review_schema() -> dict[str, Any]:
    return load_json(ROOT / "repository-review-decision.schema.json")


@pytest.fixture(scope="module")
def plan_receipt_schema() -> dict[str, Any]:
    return load_json(ROOT / "plan-receipt.schema.json")


@pytest.fixture(scope="module")
def review_receipt_schema() -> dict[str, Any]:
    return load_json(ROOT / "review-receipt.schema.json")


@pytest.fixture(scope="module")
def error_schema() -> dict[str, Any]:
    return load_json(ROOT / "structured-error.schema.json")


def fixture(name: str) -> Any:
    return load_json(FIXTURES / name)


# ---------------------------------------------------------------------------
# Every fixture validates against its schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("schema_name", "fixture_name"),
    [
        ("package_schema", "assignment-package.planning.json"),
        ("package_schema", "assignment-package.review-due.json"),
        ("plan_schema", "plan-decision.valid.json"),
        ("plan_schema", "plan-decision.invalid-dag-cycle.json"),
        ("plan_schema", "plan-decision.invalid-assignee.json"),
        ("review_schema", "review-decision.approve.json"),
        ("review_schema", "review-decision.request-rework.json"),
        ("plan_receipt_schema", "plan-receipt.json"),
        ("review_receipt_schema", "review-receipt.approve.json"),
        ("error_schema", "error.401.invalid-token.json"),
        ("error_schema", "error.403.forbidden-not-assignee.json"),
        ("error_schema", "error.404.assignment-not-found.json"),
        ("error_schema", "error.409.plan-invalid-dag-cycle.json"),
    ],
)
def test_fixture_validates_against_schema(
    schema_name: str, fixture_name: str, request: pytest.FixtureRequest
) -> None:
    """The two invalid plan fixtures are schema-valid on purpose: their defects are
    cross-document (cycle, foreign assignee), catchable only by the invariant tests
    below and by the server's 409s — not by shape validation."""
    validate_document(request.getfixturevalue(schema_name), fixture(fixture_name))


# ---------------------------------------------------------------------------
# Invariant 1 — phase/evidence coupling
# ---------------------------------------------------------------------------


def test_planning_package_has_no_review_evidence() -> None:
    package = fixture("assignment-package.planning.json")
    assert package["phase"] == "planning"
    assert package["reviewEvidence"] is None


def test_review_due_package_carries_complete_worker_evidence() -> None:
    package = fixture("assignment-package.review-due.json")
    assert package["phase"] == "review_due"
    evidence = package["reviewEvidence"]
    assert evidence is not None
    assert evidence["reviewRevision"] == 1
    for worker_evidence in evidence["workerEvidence"]:
        # The evidence must let a leader trace Task -> Run -> commit -> tests.
        assert UUID(worker_evidence["workerTaskId"])
        assert worker_evidence["runId"] is not None
        assert worker_evidence["commitSha"] is not None
        assert worker_evidence["changedFiles"]
        assert worker_evidence["testResults"]


# ---------------------------------------------------------------------------
# Invariant 3 — DAG validity (coverage + acyclicity)
# ---------------------------------------------------------------------------


def dag_has_cycle(nodes: list[str], edges: list[dict[str, str]]) -> bool:
    remaining = {node: 0 for node in nodes}
    dependants: dict[str, list[str]] = {node: [] for node in nodes}
    for edge in edges:
        remaining[edge["to"]] += 1
        dependants[edge["from"]].append(edge["to"])
    frontier = [node for node, degree in remaining.items() if degree == 0]
    visited = 0
    while frontier:
        node = frontier.pop()
        visited += 1
        for dependant in dependants[node]:
            remaining[dependant] -= 1
            if remaining[dependant] == 0:
                frontier.append(dependant)
    return visited != len(nodes)


def test_valid_plan_dag_is_acyclic_and_covers_worker_tasks_one_to_one() -> None:
    plan = fixture("plan-decision.valid.json")
    node_ids = [node["nodeId"] for node in plan["taskDag"]["nodes"]]
    task_node_ids = [task["nodeId"] for task in plan["workerTasks"]]
    assert sorted(node_ids) == sorted(set(node_ids)), "duplicate DAG nodes"
    assert sorted(task_node_ids) == sorted(node_ids), "nodes and worker tasks must map 1:1"
    for edge in plan["taskDag"]["edges"]:
        assert edge["from"] in node_ids and edge["to"] in node_ids
    assert not dag_has_cycle(node_ids, plan["taskDag"]["edges"])


def test_cycle_fixture_really_contains_a_cycle() -> None:
    plan = fixture("plan-decision.invalid-dag-cycle.json")
    node_ids = [node["nodeId"] for node in plan["taskDag"]["nodes"]]
    assert dag_has_cycle(node_ids, plan["taskDag"]["edges"])


# ---------------------------------------------------------------------------
# Invariant 4 — envelope clamp, checked against the planning package fixture
# ---------------------------------------------------------------------------


def test_valid_plan_stays_inside_the_safety_envelope() -> None:
    package = fixture("assignment-package.planning.json")
    plan = fixture("plan-decision.valid.json")
    roster = {worker["workerAgentId"] for worker in package["workerRoster"]}
    roots = tuple(package["safetyEnvelope"]["allowedPathRoots"])
    mandatory_tests = set(package["safetyEnvelope"]["testCommands"])
    for task in plan["workerTasks"]:
        assert task["assigneeWorkerAgentId"] in roster
        for allowed_path in task["allowedPaths"]:
            assert allowed_path.startswith(roots), allowed_path
        assert mandatory_tests.issubset(task["tests"]), "envelope test commands were dropped"


def test_invalid_assignee_fixture_names_an_agent_outside_the_roster() -> None:
    package = fixture("assignment-package.planning.json")
    plan = fixture("plan-decision.invalid-assignee.json")
    roster = {worker["workerAgentId"] for worker in package["workerRoster"]}
    assert any(task["assigneeWorkerAgentId"] not in roster for task in plan["workerTasks"])


# ---------------------------------------------------------------------------
# Invariant 5 — provenance + rework findings
# ---------------------------------------------------------------------------


def test_decisions_carry_leader_codex_session_provenance() -> None:
    for name in (
        "plan-decision.valid.json",
        "review-decision.approve.json",
        "review-decision.request-rework.json",
    ):
        assert fixture(name)["provenance"]["source"] == "leader-codex-session", name


def test_request_rework_requires_a_rework_instruction_finding() -> None:
    decision = fixture("review-decision.request-rework.json")
    assert decision["verdict"] == "request_rework"
    assert any("reworkInstruction" in finding for finding in decision["findings"])


def test_rework_findings_reference_worker_tasks_from_the_evidence() -> None:
    evidence = fixture("assignment-package.review-due.json")["reviewEvidence"]
    known_tasks = {entry["workerTaskId"] for entry in evidence["workerEvidence"]}
    decision = fixture("review-decision.request-rework.json")
    for finding in decision["findings"]:
        assert finding["workerTaskId"] in known_tasks


# ---------------------------------------------------------------------------
# Invariant 2 — receipts: one scenario, one identity
# ---------------------------------------------------------------------------


def test_plan_receipt_names_the_worker_tasks_the_evidence_later_reports() -> None:
    receipt = fixture("plan-receipt.json")
    evidence = fixture("assignment-package.review-due.json")["reviewEvidence"]
    evidence_tasks = [entry["workerTaskId"] for entry in evidence["workerEvidence"]]
    assert receipt["workerTaskIds"] == evidence_tasks
    assert receipt["planRevision"] == 1


def test_review_receipt_pins_the_approve_outcome_mapping() -> None:
    receipt = fixture("review-receipt.approve.json")
    assert receipt["verdict"] == "approve"
    assert receipt["leaderTaskStatus"] == "succeeded"
    assert receipt["reworkTaskIds"] == []


def test_the_scenario_shares_one_leader_task_identity() -> None:
    leader_task_ids = {
        fixture("assignment-package.planning.json")["leaderTaskId"],
        fixture("assignment-package.review-due.json")["leaderTaskId"],
        fixture("plan-receipt.json")["leaderTaskId"],
        fixture("review-receipt.approve.json")["leaderTaskId"],
    }
    assert len(leader_task_ids) == 1


# ---------------------------------------------------------------------------
# Error matrix — frozen status -> code mapping
# ---------------------------------------------------------------------------


def test_error_matrix_covers_the_schema_enum_exactly(error_schema: dict[str, Any]) -> None:
    matrix: dict[str, list[str]] = fixture("error-matrix.json")
    assert set(matrix) == {"401", "403", "404", "409"}
    codes = [code for codes in matrix.values() for code in codes]
    assert sorted(codes) == sorted(set(codes)), "a code may map to exactly one status"
    enum = error_schema["properties"]["detail"]["properties"]["code"]["enum"]
    assert sorted(codes) == sorted(enum)


@pytest.mark.parametrize(
    ("fixture_name", "status"),
    [
        ("error.401.invalid-token.json", "401"),
        ("error.403.forbidden-not-assignee.json", "403"),
        ("error.404.assignment-not-found.json", "404"),
        ("error.409.plan-invalid-dag-cycle.json", "409"),
    ],
)
def test_sample_errors_sit_in_their_declared_status_class(fixture_name: str, status: str) -> None:
    matrix: dict[str, list[str]] = fixture("error-matrix.json")
    assert fixture(fixture_name)["detail"]["code"] in matrix[status]


def test_error_messages_never_carry_a_secret_shape(error_schema: dict[str, Any]) -> None:
    detail_properties = set(error_schema["properties"]["detail"]["properties"])
    assert detail_properties == {"code", "message"}
