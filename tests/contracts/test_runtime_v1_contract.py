import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from repomesh_runner import (
    ContextBundleRef,
    RepositoryCheckout,
    RunnerEventType,
    RunnerPermissions,
    RunnerTask,
)

CONTRACT_ROOT = Path(__file__).parents[2] / "contracts" / "runtime" / "v1"


def load_schema(name: str) -> dict[str, object]:
    return json.loads((CONTRACT_ROOT / name).read_text(encoding="utf-8"))


def make_task() -> RunnerTask:
    return RunnerTask(
        organization_id=UUID("00000000-0000-0000-0000-000000000001"),
        project_id=UUID("00000000-0000-0000-0000-000000000002"),
        task_id=UUID("00000000-0000-0000-0000-000000000003"),
        run_id=UUID("00000000-0000-0000-0000-000000000004"),
        correlation_id=UUID("00000000-0000-0000-0000-000000000005"),
        attempt=1,
        adapter_id="codex",
        instruction="Implement the accepted specification",
        repository=RepositoryCheckout(
            repository_id=UUID("00000000-0000-0000-0000-000000000006"),
            url="https://github.com/example/service.git",
            base_revision="main",
        ),
        context_bundle=ContextBundleRef(
            bundle_id=UUID("00000000-0000-0000-0000-000000000007"),
            version=3,
            manifest_uri="s3://repomesh-context/bundles/7/manifest.json",
            content_hash="sha256:" + "a" * 64,
        ),
        permissions=RunnerPermissions(allowed_tools=("git", "pytest")),
        idempotency_key="run-4-attempt-1",
        issued_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
    )


def test_runner_task_wire_shape_matches_v1_schema() -> None:
    schema = load_schema("runner-task.schema.json")
    payload = make_task().to_wire()
    properties = schema["properties"]

    assert schema["additionalProperties"] is False
    assert set(schema["required"]).issubset(payload)
    assert set(payload).issubset(properties)
    assert payload["schemaVersion"] == properties["schemaVersion"]["const"]

    for field in ("repository", "contextBundle", "permissions"):
        field_schema = properties[field]
        assert set(field_schema["required"]).issubset(payload[field])
        assert set(payload[field]).issubset(field_schema["properties"])


def test_runner_event_enum_matches_python_contract() -> None:
    schema = load_schema("runner-event.schema.json")
    schema_values = set(schema["properties"]["eventType"]["enum"])

    assert schema_values == {event_type.value for event_type in RunnerEventType}


def test_runtime_contract_exposes_references_not_secret_values() -> None:
    task_schema = load_schema("runner-task.schema.json")
    property_names = set(task_schema["properties"])

    assert "credentialRefs" in property_names
    assert property_names.isdisjoint({"credentials", "token", "password", "secret"})


def test_runtime_metadata_is_correlation_only() -> None:
    schema = load_schema("runtime-metadata.schema.json")

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "schemaVersion",
        "organizationId",
        "projectId",
        "correlationId",
    }
