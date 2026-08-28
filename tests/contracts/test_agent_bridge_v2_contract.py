"""Round-trip tests for the Agent Bridge v2 (external member) contracts — wave-0 freeze.

v2 is v1 plus exactly one required field, ``role``. These tests pin three things the
README promises: the shared fields have not drifted from v1 (machine-checked, not
prose), every valid v1 worker document upgrades to a valid v2 document and back, and
the invalid fixtures under ``contracts/agent-bridge/v2/fixtures`` really are invalid
for the reason their filename claims. Server-side (PR 5.5A) and Bridge-side (PR 8)
suites must consume the same fixture files; this module is the referee that the files
themselves stay honest.

Same hand-rolled structural style as ``test_agent_bridge_v1_contract.py``: no
jsonschema dependency, the schema's own declarations are the assertions.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

V1_ROOT = Path(__file__).parents[2] / "contracts" / "agent-bridge" / "v1"
V2_ROOT = Path(__file__).parents[2] / "contracts" / "agent-bridge" / "v2"
FIXTURES = V2_ROOT / "fixtures"

ENROLLMENT_V2 = "repomesh.agent-bridge.enrollment.v2"
BINDING_V2 = "repomesh.agent-bridge.binding.v2"
ENROLLMENT_V1 = "repomesh.agent-bridge.enrollment.v1"
BINDING_V1 = "repomesh.agent-bridge.binding.v1"

ROLES = ("worker", "repository_leader")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def enrollment_v2() -> dict[str, Any]:
    return load_json(V2_ROOT / "external-member-enrollment.schema.json")


@pytest.fixture(scope="module")
def binding_v2() -> dict[str, Any]:
    return load_json(V2_ROOT / "external-member-binding.schema.json")


@pytest.fixture(scope="module")
def enrollment_v1() -> dict[str, Any]:
    return load_json(V1_ROOT / "external-worker-enrollment.schema.json")


@pytest.fixture(scope="module")
def binding_v1() -> dict[str, Any]:
    return load_json(V1_ROOT / "external-worker-binding.schema.json")


def fixture(name: str) -> dict[str, Any]:
    return load_json(FIXTURES / name)


def assert_fixture_matches_schema(schema: dict[str, Any], doc: dict[str, Any]) -> None:
    """Required keys present, no undeclared keys (additionalProperties: false)."""
    assert schema["additionalProperties"] is False
    required = set(schema["required"])
    properties = set(schema["properties"])
    assert required.issubset(doc), required - set(doc)
    assert set(doc).issubset(properties), set(doc) - properties


def strip_descriptions(node: Any) -> Any:
    """Shape-relevant view of a schema fragment: descriptions are prose, not contract."""
    if isinstance(node, dict):
        return {k: strip_descriptions(v) for k, v in node.items() if k != "description"}
    if isinstance(node, list):
        return [strip_descriptions(item) for item in node]
    return node


# ---------------------------------------------------------------------------
# v2 = v1 + role, and nothing else (machine-checked drift guard)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("v1_name", "v2_name"),
    [("enrollment_v1", "enrollment_v2"), ("binding_v1", "binding_v2")],
)
def test_v2_is_v1_plus_role_and_nothing_else(
    v1_name: str, v2_name: str, request: pytest.FixtureRequest
) -> None:
    v1 = request.getfixturevalue(v1_name)
    v2 = request.getfixturevalue(v2_name)

    assert set(v2["required"]) == set(v1["required"]) | {"role"}
    assert set(v2["properties"]) == set(v1["properties"]) | {"role"}
    for name, v1_prop in v1["properties"].items():
        if name == "schemaVersion":
            continue
        assert strip_descriptions(v2["properties"][name]) == strip_descriptions(v1_prop), name
    assert strip_descriptions(v2.get("$defs", {})) == strip_descriptions(v1.get("$defs", {}))


def test_role_enum_is_frozen_and_excludes_organization_leader(
    enrollment_v2: dict[str, Any], binding_v2: dict[str, Any]
) -> None:
    for schema in (enrollment_v2, binding_v2):
        assert tuple(schema["properties"]["role"]["enum"]) == ROLES
    # The rejection of an Organization Leader is structural, not a server-side favor.
    assert "organization_leader" not in ROLES


def test_schema_versions_are_v2(
    enrollment_v2: dict[str, Any], binding_v2: dict[str, Any]
) -> None:
    assert enrollment_v2["properties"]["schemaVersion"]["const"] == ENROLLMENT_V2
    assert binding_v2["properties"]["schemaVersion"]["const"] == BINDING_V2


# ---------------------------------------------------------------------------
# Valid fixtures round-trip against the schemas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "schema_fixture", "role"),
    [
        ("enrollment.worker.json", "enrollment_v2", "worker"),
        ("enrollment.repository-leader.json", "enrollment_v2", "repository_leader"),
        ("binding.worker.json", "binding_v2", "worker"),
        ("binding.repository-leader.json", "binding_v2", "repository_leader"),
    ],
)
def test_valid_fixture_matches_schema(
    name: str, schema_fixture: str, role: str, request: pytest.FixtureRequest
) -> None:
    schema = request.getfixturevalue(schema_fixture)
    doc = fixture(name)
    assert_fixture_matches_schema(schema, doc)
    assert doc["schemaVersion"] == schema["properties"]["schemaVersion"]["const"]
    assert doc["role"] == role
    assert re.match(schema["properties"]["matrixUserId"]["pattern"], doc["matrixUserId"])
    room_pattern = schema["properties"]["allowedRoomIds"]["items"]["pattern"]
    for room_id in doc["allowedRoomIds"]:
        assert re.match(room_pattern, room_id)


def test_worker_and_leader_fixtures_are_distinct_identities() -> None:
    worker = fixture("enrollment.worker.json")
    leader = fixture("enrollment.repository-leader.json")
    assert worker["workerAgentId"] != leader["workerAgentId"]
    assert worker["matrixUserId"] != leader["matrixUserId"]
    # Same team, different DM room: the role-aware allowlist is visible in the fixtures.
    assert worker["teamName"] == leader["teamName"]
    assert set(worker["allowedRoomIds"]) != set(leader["allowedRoomIds"])
    assert set(worker["allowedRoomIds"]) & set(leader["allowedRoomIds"])


# ---------------------------------------------------------------------------
# Invalid fixtures are invalid for the reason their filename claims
# ---------------------------------------------------------------------------


def test_organization_leader_enrollment_is_rejected_by_the_role_enum(
    enrollment_v2: dict[str, Any],
) -> None:
    doc = fixture("enrollment.invalid-role.organization-leader.json")
    # Everything else about the document is well-formed...
    assert_fixture_matches_schema(enrollment_v2, doc)
    # ...only the role is outside the enum, so the rejection is attributable.
    assert doc["role"] not in enrollment_v2["properties"]["role"]["enum"]


def test_malformed_room_id_binding_fails_the_room_pattern(
    binding_v2: dict[str, Any],
) -> None:
    doc = fixture("binding.invalid-room.malformed-room-id.json")
    assert_fixture_matches_schema(binding_v2, doc)
    room_pattern = binding_v2["properties"]["allowedRoomIds"]["items"]["pattern"]
    assert any(not re.match(room_pattern, room_id) for room_id in doc["allowedRoomIds"])


# ---------------------------------------------------------------------------
# v1 <-> v2 round-trip rules
# ---------------------------------------------------------------------------


def upgrade_to_v2(v1_doc: dict[str, Any], v2_const: str) -> dict[str, Any]:
    upgraded = dict(v1_doc)
    upgraded["schemaVersion"] = v2_const
    upgraded["role"] = "worker"
    return upgraded


def downgrade_to_v1(v2_doc: dict[str, Any], v1_const: str) -> dict[str, Any]:
    assert v2_doc["role"] == "worker", "only the worker form has a v1 representation"
    downgraded = {k: v for k, v in v2_doc.items() if k != "role"}
    downgraded["schemaVersion"] = v1_const
    return downgraded


def test_v2_worker_enrollment_downgrades_to_valid_v1_and_back(
    enrollment_v1: dict[str, Any], enrollment_v2: dict[str, Any]
) -> None:
    v2_doc = fixture("enrollment.worker.json")
    v1_doc = downgrade_to_v1(v2_doc, ENROLLMENT_V1)
    assert_fixture_matches_schema(enrollment_v1, v1_doc)
    assert upgrade_to_v2(v1_doc, ENROLLMENT_V2) == v2_doc


def test_v2_worker_binding_downgrades_to_valid_v1_and_back(
    binding_v1: dict[str, Any], binding_v2: dict[str, Any]
) -> None:
    v2_doc = fixture("binding.worker.json")
    v1_doc = downgrade_to_v1(v2_doc, BINDING_V1)
    assert_fixture_matches_schema(binding_v1, v1_doc)
    assert upgrade_to_v2(v1_doc, BINDING_V2) == v2_doc


def test_a_repository_leader_has_no_v1_representation(
    enrollment_v1: dict[str, Any], binding_v1: dict[str, Any]
) -> None:
    """v1 cannot say "repository_leader": it has no role property at all, so any
    downgrade would silently erase the very fact v2 exists to carry."""
    assert "role" not in enrollment_v1["properties"]
    assert "role" not in binding_v1["properties"]
    leader = fixture("enrollment.repository-leader.json")
    with pytest.raises(AssertionError):
        downgrade_to_v1(leader, ENROLLMENT_V1)
