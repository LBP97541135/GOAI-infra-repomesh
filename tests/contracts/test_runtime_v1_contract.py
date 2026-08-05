import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from repomesh_runner import (
    ContextBundleRef,
    RepositoryCheckout,
    RunnerEventType,
    RunnerExecutionResult,
    RunnerPermissions,
    RunnerResultStatus,
    RunnerTask,
    WorkspaceAssignment,
)
from repomesh_runner import (
    TestCommandResult as CommandOutcome,
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


def test_runner_task_without_workspace_emits_null_optional_fields() -> None:
    payload = make_task().to_wire()

    assert payload["workspace"] is None
    assert payload["workerAgentId"] is None
    assert payload["testCommands"] == []
    assert payload["contextBundle"]["codingPackageHash"] is None
    assert payload["permissions"]["allowedPaths"] == []
    assert payload["permissions"]["deniedPaths"] == []


def test_runner_task_with_workspace_wire_shape_matches_v1_schema() -> None:
    schema = load_schema("runner-task.schema.json")
    properties = schema["properties"]
    task = replace(
        make_task(),
        workspace=WorkspaceAssignment(
            workspace_id="ws-run-4",
            path="/srv/repomesh/workspaces/ws-run-4",
            base_sha="d5e9775" + "0" * 33,
        ),
        worker_agent_id=UUID("00000000-0000-0000-0000-000000000008"),
        test_commands=("pytest -q", "ruff check src"),
    )

    payload = task.to_wire()

    assert payload["workspace"] == {
        "workspaceId": "ws-run-4",
        "path": "/srv/repomesh/workspaces/ws-run-4",
        "baseSha": "d5e9775" + "0" * 33,
    }
    assert payload["workerAgentId"] == "00000000-0000-0000-0000-000000000008"
    assert payload["testCommands"] == ["pytest -q", "ruff check src"]
    assert set(payload).issubset(properties)
    assert set(properties["workspace"]["required"]).issubset(payload["workspace"])
    assert set(payload["workspace"]).issubset(properties["workspace"]["properties"])


def test_workspace_assignment_requires_identifier_path_and_base_sha() -> None:
    for field, value in (("workspace_id", ""), ("path", "  "), ("base_sha", "")):
        with pytest.raises(ValueError):
            WorkspaceAssignment(
                **{
                    "workspace_id": "ws-run-4",
                    "path": "/srv/repomesh/workspaces/ws-run-4",
                    "base_sha": "0" * 40,
                    field: value,
                }
            )


def test_permission_paths_reject_empty_and_duplicate_values() -> None:
    assert RunnerPermissions(allowed_paths=("src", "tests"), denied_paths=(".git",))

    with pytest.raises(ValueError, match="allowed_paths cannot contain empty values"):
        RunnerPermissions(allowed_paths=("src", " "))
    with pytest.raises(ValueError, match="allowed_paths must contain unique values"):
        RunnerPermissions(allowed_paths=("src", "src"))
    with pytest.raises(ValueError, match="denied_paths cannot contain empty values"):
        RunnerPermissions(denied_paths=("",))
    with pytest.raises(ValueError, match="denied_paths must contain unique values"):
        RunnerPermissions(denied_paths=(".git", ".git"))


def test_context_bundle_coding_package_hash_is_optional_but_validated() -> None:
    def build(coding_package_hash: str | None) -> ContextBundleRef:
        return ContextBundleRef(
            bundle_id=UUID("00000000-0000-0000-0000-000000000007"),
            version=3,
            manifest_uri="s3://repomesh-context/bundles/7/manifest.json",
            content_hash="sha256:" + "a" * 64,
            coding_package_hash=coding_package_hash,
        )

    assert build(None).coding_package_hash is None
    assert build("sha256:" + "b" * 64).coding_package_hash == "sha256:" + "b" * 64

    with pytest.raises(ValueError):
        build("b" * 64)
    with pytest.raises(ValueError):
        build("sha256:" + "B" * 64)


def test_runner_task_test_commands_must_be_unique() -> None:
    with pytest.raises(ValueError, match="test_commands must contain unique values"):
        replace(make_task(), test_commands=("pytest -q", "pytest -q"))
    with pytest.raises(ValueError, match="test_commands cannot contain empty values"):
        replace(make_task(), test_commands=("",))


def test_test_command_result_requires_a_command() -> None:
    assert CommandOutcome(command="pytest -q", exit_code=1).exit_code == 1

    with pytest.raises(ValueError, match="test command is required"):
        CommandOutcome(command="  ", exit_code=0)


def test_runner_execution_result_new_fields_default_to_empty() -> None:
    result = RunnerExecutionResult(
        status=RunnerResultStatus.SUCCEEDED,
        summary="applied the specification",
    )

    assert result.changed_files == ()
    assert result.test_results == ()

    enriched = RunnerExecutionResult(
        status=RunnerResultStatus.SUCCEEDED,
        summary="applied the specification",
        changed_files=("src/repomesh_runner/contracts.py",),
        test_results=(CommandOutcome(command="pytest -q", exit_code=0),),
    )

    assert enriched.test_results[0].command == "pytest -q"

    with pytest.raises(ValueError, match="changed_files must contain unique values"):
        RunnerExecutionResult(
            status=RunnerResultStatus.SUCCEEDED,
            summary="applied the specification",
            changed_files=("a.py", "a.py"),
        )


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
