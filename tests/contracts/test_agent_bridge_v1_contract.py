"""Structural round-trip tests for the Agent Bridge v1 contracts (PR 0).

No implementation code exists yet (``src/repomesh_agent_bridge`` lands in PR 2), so "round-trip"
here means: canonical fixture payloads are checked structurally against the schema's own
``required``/``properties``/``enum``/``pattern``/``const`` declarations, the same hand-rolled
style as ``tests/contracts/test_runtime_v1_contract.py``. When ``contracts.py`` lands in PR 2,
these fixtures should be replaced by real dataclass ``to_wire()`` payloads without changing the
shape of these assertions.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

CONTRACT_ROOT = Path(__file__).parents[2] / "contracts" / "agent-bridge" / "v1"

ENROLLMENT_SCHEMA_VERSION = "repomesh.agent-bridge.enrollment.v1"
BINDING_SCHEMA_VERSION = "repomesh.agent-bridge.binding.v1"
OBSERVATION_SCHEMA_VERSION = "repomesh.room-observation.v1"


def load_schema(name: str) -> dict[str, Any]:
    return json.loads((CONTRACT_ROOT / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def enrollment_schema() -> dict[str, Any]:
    return load_schema("external-worker-enrollment.schema.json")


@pytest.fixture(scope="module")
def binding_schema() -> dict[str, Any]:
    return load_schema("external-worker-binding.schema.json")


@pytest.fixture(scope="module")
def observation_schema() -> dict[str, Any]:
    return load_schema("room-observation.schema.json")


def make_enrollment() -> dict[str, Any]:
    return {
        "schemaVersion": ENROLLMENT_SCHEMA_VERSION,
        "organizationId": "00000000-0000-0000-0000-000000000001",
        "workerAgentId": "00000000-0000-0000-0000-000000000002",
        "workerName": "pricing-codex-worker",
        "teamName": "pricing-repo-team",
        "matrixUserId": "@pricing-codex-worker:matrix.example.org",
        "matrixHomeserverUrl": "https://matrix.example.org",
        "allowedRoomIds": ["!room1:matrix.example.org"],
        "repomeshEndpoint": "https://repomesh.example.org",
        "codingProfile": "codex",
        "credentialRefs": {"matrix": "keyring://bridge/matrix-token"},
    }


def make_binding() -> dict[str, Any]:
    return {
        "schemaVersion": BINDING_SCHEMA_VERSION,
        "organizationId": "00000000-0000-0000-0000-000000000001",
        "teamName": "pricing-repo-team",
        "workerAgentId": "00000000-0000-0000-0000-000000000002",
        "workerName": "pricing-codex-worker",
        "matrixUserId": "@pricing-codex-worker:matrix.example.org",
        "allowedRoomIds": ["!room1:matrix.example.org"],
        "containerManaged": False,
    }


def make_observation() -> dict[str, Any]:
    return {
        "schemaVersion": OBSERVATION_SCHEMA_VERSION,
        "observationId": "00000000-0000-0000-0000-000000000003",
        "emittedAt": "2026-08-26T12:00:00Z",
        "workerName": "pricing-codex-worker",
        "roomId": "!room1:matrix.example.org",
        "kind": "run_started",
        "body": "Starting task",
    }


def assert_fixture_matches_schema(schema: dict[str, Any], fixture: dict[str, Any]) -> None:
    """A structural stand-in for "validates against the schema": every required key is
    present, and every fixture key is a declared property (schemas set
    additionalProperties: false, so this mirrors what a real validator would reject)."""
    assert schema["additionalProperties"] is False
    required = set(schema["required"])
    properties = set(schema["properties"])
    assert required.issubset(fixture), required - set(fixture)
    assert set(fixture).issubset(properties), set(fixture) - properties


# ---------------------------------------------------------------------------
# schemaVersion unification (item 4)
# ---------------------------------------------------------------------------


def test_schema_versions_are_unified(
    enrollment_schema: dict[str, Any],
    binding_schema: dict[str, Any],
    observation_schema: dict[str, Any],
) -> None:
    assert enrollment_schema["properties"]["schemaVersion"]["const"] == ENROLLMENT_SCHEMA_VERSION
    assert binding_schema["properties"]["schemaVersion"]["const"] == BINDING_SCHEMA_VERSION
    assert observation_schema["properties"]["schemaVersion"]["const"] == OBSERVATION_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Canonical valid fixtures round-trip against each schema (item: three schemas
# have implementation-side round-trip contract tests)
# ---------------------------------------------------------------------------


def test_enrollment_canonical_fixture_matches_schema(enrollment_schema: dict[str, Any]) -> None:
    fixture = make_enrollment()
    assert_fixture_matches_schema(enrollment_schema, fixture)

    properties = enrollment_schema["properties"]
    assert fixture["codingProfile"] in properties["codingProfile"]["enum"]
    assert re.match(properties["matrixUserId"]["pattern"], fixture["matrixUserId"])
    room_pattern = properties["allowedRoomIds"]["items"]["pattern"]
    for room_id in fixture["allowedRoomIds"]:
        assert re.match(room_pattern, room_id)

    cred_schema = properties["credentialRefs"]
    assert set(cred_schema["required"]).issubset(fixture["credentialRefs"])
    assert set(fixture["credentialRefs"]).issubset(cred_schema["properties"])


def test_enrollment_requires_worker_agent_id_and_homeserver_url(
    enrollment_schema: dict[str, Any],
) -> None:
    assert "workerAgentId" in enrollment_schema["required"]
    assert "matrixHomeserverUrl" in enrollment_schema["required"]
    assert enrollment_schema["properties"]["workerAgentId"]["format"] == "uuid"
    assert enrollment_schema["properties"]["matrixHomeserverUrl"]["format"] == "uri"


def test_binding_canonical_fixture_matches_schema(binding_schema: dict[str, Any]) -> None:
    fixture = make_binding()
    assert_fixture_matches_schema(binding_schema, fixture)

    properties = binding_schema["properties"]
    assert properties["containerManaged"]["const"] is False
    assert fixture["containerManaged"] is False
    assert re.match(properties["matrixUserId"]["pattern"], fixture["matrixUserId"])
    room_pattern = properties["allowedRoomIds"]["items"]["pattern"]
    for room_id in fixture["allowedRoomIds"]:
        assert re.match(room_pattern, room_id)


def test_binding_carries_no_secrets_or_controller_addresses(binding_schema: dict[str, Any]) -> None:
    property_names = set(binding_schema["properties"])
    assert property_names.isdisjoint(
        {"token", "accessToken", "matrixAccessToken", "secret", "password", "controllerUrl"}
    )


def test_observation_canonical_fixture_matches_schema(observation_schema: dict[str, Any]) -> None:
    fixture = make_observation()
    assert_fixture_matches_schema(observation_schema, fixture)

    properties = observation_schema["properties"]
    assert fixture["kind"] in properties["kind"]["enum"]
    assert re.match(properties["roomId"]["pattern"], fixture["roomId"])


# ---------------------------------------------------------------------------
# Negative fixtures (item: negative fixtures for secret leakage, missing
# containerManaged confirmation, and out-of-allowlist observation kind)
# ---------------------------------------------------------------------------


def test_enrollment_schema_does_not_declare_a_secret_bearing_field(
    enrollment_schema: dict[str, Any],
) -> None:
    fixture = make_enrollment()
    fixture["matrixAccessToken"] = "syt_fake_secret_value_never_persisted"

    assert "matrixAccessToken" not in enrollment_schema["properties"]
    # additionalProperties: false means a fixture carrying this key would be rejected by a
    # real validator; structurally, its keys are no longer a subset of the declared properties.
    assert not set(fixture).issubset(enrollment_schema["properties"])


def test_binding_missing_container_managed_confirmation_fails_required_check(
    binding_schema: dict[str, Any],
) -> None:
    fixture = make_binding()
    del fixture["containerManaged"]

    assert "containerManaged" in binding_schema["required"]
    assert not set(binding_schema["required"]).issubset(fixture)


def test_observation_rejects_out_of_allowlist_kind(observation_schema: dict[str, Any]) -> None:
    fixture = make_observation()
    fixture["kind"] = "heartbeat"  # not a projected kind; the Bridge does not emit heartbeats

    assert fixture["kind"] not in observation_schema["properties"]["kind"]["enum"]


# ---------------------------------------------------------------------------
# Field-name consistency across the three schemas (item: 字段命名无冲突,
# machine-checked)
# ---------------------------------------------------------------------------


def test_worker_agent_id_is_a_consistent_uuid_identity(
    enrollment_schema: dict[str, Any], binding_schema: dict[str, Any]
) -> None:
    assert enrollment_schema["properties"]["workerAgentId"]["format"] == "uuid"
    assert binding_schema["properties"]["workerAgentId"]["format"] == "uuid"


def test_worker_name_has_the_same_shape_everywhere(
    enrollment_schema: dict[str, Any],
    binding_schema: dict[str, Any],
    observation_schema: dict[str, Any],
) -> None:
    for schema in (enrollment_schema, binding_schema, observation_schema):
        worker_name = schema["properties"]["workerName"]
        assert worker_name["type"] == "string"
        assert worker_name["minLength"] == 1
        assert worker_name["maxLength"] == 100


def test_matrix_user_id_pattern_is_identical_in_enrollment_and_binding(
    enrollment_schema: dict[str, Any], binding_schema: dict[str, Any]
) -> None:
    assert (
        enrollment_schema["properties"]["matrixUserId"]["pattern"]
        == binding_schema["properties"]["matrixUserId"]["pattern"]
    )


def test_room_id_pattern_is_identical_across_all_three_schemas(
    enrollment_schema: dict[str, Any],
    binding_schema: dict[str, Any],
    observation_schema: dict[str, Any],
) -> None:
    enrollment_pattern = enrollment_schema["properties"]["allowedRoomIds"]["items"]["pattern"]
    binding_pattern = binding_schema["properties"]["allowedRoomIds"]["items"]["pattern"]
    observation_pattern = observation_schema["properties"]["roomId"]["pattern"]

    assert enrollment_pattern == binding_pattern == observation_pattern


def test_organization_and_team_identity_match_between_enrollment_and_binding(
    enrollment_schema: dict[str, Any], binding_schema: dict[str, Any]
) -> None:
    assert enrollment_schema["properties"]["organizationId"]["format"] == "uuid"
    assert binding_schema["properties"]["organizationId"]["format"] == "uuid"
    assert enrollment_schema["properties"]["teamName"] == binding_schema["properties"]["teamName"]
