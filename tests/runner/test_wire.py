"""Wire parsing is the inverse of RunnerTask.to_wire()."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from repomesh_runner import (
    ContextBundleRef,
    RepositoryCheckout,
    RunnerPermissionMode,
    RunnerPermissions,
    RunnerTask,
    WireError,
    WorkspaceAssignment,
    parse_runner_task,
)

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
SHA256 = "sha256:" + "a" * 64
PACKAGE_SHA256 = "sha256:" + "b" * 64


def make_full_task() -> RunnerTask:
    """A task with every optional field populated."""

    return RunnerTask(
        organization_id=UUID("00000000-0000-0000-0000-000000000001"),
        project_id=UUID("00000000-0000-0000-0000-000000000002"),
        task_id=UUID("00000000-0000-0000-0000-000000000003"),
        run_id=UUID("00000000-0000-0000-0000-000000000004"),
        correlation_id=UUID("00000000-0000-0000-0000-000000000005"),
        attempt=2,
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
            content_hash=SHA256,
            coding_package_hash=PACKAGE_SHA256,
        ),
        permissions=RunnerPermissions(
            mode=RunnerPermissionMode.ACCEPT_EDITS,
            allowed_tools=("git", "pytest"),
            disallowed_tools=("curl",),
            network_targets=("https://pypi.org",),
            allowed_paths=("src", "tests"),
            denied_paths=(".git", "secrets"),
        ),
        idempotency_key="run-4-attempt-2",
        issued_at=NOW,
        resume_session_id="session-9",
        credential_refs=("credential://github/project-2",),
        workspace=WorkspaceAssignment(
            workspace_id="ws-1",
            path="/workspaces/ws-1",
            base_sha="0" * 40,
        ),
        worker_agent_id=UUID("00000000-0000-0000-0000-000000000008"),
        test_commands=("pytest -q", "ruff check ."),
    )


def minimal_payload() -> dict[str, Any]:
    """The smallest payload the schema allows: only required keys, no optional ones at all."""

    return {
        "schemaVersion": "runtime.v1",
        "organizationId": "00000000-0000-0000-0000-000000000001",
        "projectId": "00000000-0000-0000-0000-000000000002",
        "taskId": "00000000-0000-0000-0000-000000000003",
        "runId": "00000000-0000-0000-0000-000000000004",
        "correlationId": "00000000-0000-0000-0000-000000000005",
        "attempt": 1,
        "adapterId": "codex",
        "instruction": "Do the work",
        "repository": {
            "repositoryId": "00000000-0000-0000-0000-000000000006",
            "url": "https://github.com/example/service.git",
            "baseRevision": "main",
        },
        "contextBundle": {
            "bundleId": "00000000-0000-0000-0000-000000000007",
            "version": 1,
            "manifestUri": "s3://repomesh-context/manifest.json",
            "contentHash": SHA256,
        },
        "permissions": {
            "mode": "default",
            "allowedTools": [],
            "disallowedTools": [],
            "networkTargets": [],
        },
        "idempotencyKey": "run-4-attempt-1",
        "issuedAt": "2026-08-02T12:00:00+00:00",
    }


def test_full_task_round_trips_through_the_wire() -> None:
    task = make_full_task()

    assert parse_runner_task(task.to_wire()) == task


def test_round_trip_preserves_the_wire_payload() -> None:
    payload = make_full_task().to_wire()

    assert parse_runner_task(payload).to_wire() == payload


def test_minimal_payload_parses_with_optional_fields_defaulted() -> None:
    task = parse_runner_task(minimal_payload())

    assert task.workspace is None
    assert task.worker_agent_id is None
    assert task.resume_session_id is None
    assert task.test_commands == ()
    assert task.credential_refs == ()
    assert task.context_bundle.coding_package_hash is None
    assert task.permissions.allowed_paths == ()
    assert task.permissions.denied_paths == ()


def test_absent_and_null_optional_keys_are_equivalent() -> None:
    absent = minimal_payload()
    explicit_null = minimal_payload()
    explicit_null.update(
        {
            "workspace": None,
            "resumeSessionId": None,
            "credentialRefs": None,
            "workerAgentId": None,
            "testCommands": None,
        }
    )
    explicit_null["contextBundle"]["codingPackageHash"] = None
    explicit_null["permissions"]["allowedPaths"] = None
    explicit_null["permissions"]["deniedPaths"] = None

    assert parse_runner_task(explicit_null) == parse_runner_task(absent)


def test_workspace_and_m1_additions_are_parsed() -> None:
    payload = minimal_payload()
    payload["workspace"] = {
        "workspaceId": "ws-1",
        "path": "/workspaces/ws-1",
        "baseSha": "abc123",
    }
    payload["workerAgentId"] = "00000000-0000-0000-0000-000000000008"
    payload["testCommands"] = ["pytest -q"]
    payload["contextBundle"]["codingPackageHash"] = PACKAGE_SHA256
    payload["permissions"]["allowedPaths"] = ["src"]
    payload["permissions"]["deniedPaths"] = [".git"]

    task = parse_runner_task(payload)

    assert task.workspace == WorkspaceAssignment("ws-1", "/workspaces/ws-1", "abc123")
    assert task.worker_agent_id == UUID("00000000-0000-0000-0000-000000000008")
    assert task.test_commands == ("pytest -q",)
    assert task.context_bundle.coding_package_hash == PACKAGE_SHA256
    assert task.permissions.allowed_paths == ("src",)
    assert task.permissions.denied_paths == (".git",)


@pytest.mark.parametrize("version", ["runtime.v2", "v1", "", None])
def test_foreign_schema_version_is_rejected(version: object) -> None:
    payload = minimal_payload()
    if version is None:
        del payload["schemaVersion"]
    else:
        payload["schemaVersion"] = version

    with pytest.raises(WireError, match="schemaVersion"):
        parse_runner_task(payload)


def test_missing_required_field_names_the_field() -> None:
    payload = minimal_payload()
    del payload["idempotencyKey"]

    with pytest.raises(WireError, match="idempotencyKey is required"):
        parse_runner_task(payload)


def test_missing_nested_field_names_the_path() -> None:
    payload = minimal_payload()
    del payload["repository"]["baseRevision"]

    with pytest.raises(WireError, match="repository.baseRevision is required"):
        parse_runner_task(payload)


def test_malformed_uuid_is_rejected() -> None:
    payload = minimal_payload()
    payload["runId"] = "not-a-uuid"

    with pytest.raises(WireError, match="runId must be a UUID"):
        parse_runner_task(payload)


def test_malformed_timestamp_is_rejected() -> None:
    payload = minimal_payload()
    payload["issuedAt"] = "yesterday"

    with pytest.raises(WireError, match="issuedAt must be an ISO-8601 timestamp"):
        parse_runner_task(payload)


def test_naive_timestamp_is_rejected_by_the_contract_invariant() -> None:
    payload = minimal_payload()
    payload["issuedAt"] = "2026-08-02T12:00:00"

    with pytest.raises(WireError, match="issued_at must include a timezone"):
        parse_runner_task(payload)


def test_unknown_permission_mode_is_rejected() -> None:
    payload = minimal_payload()
    payload["permissions"]["mode"] = "yolo"

    with pytest.raises(WireError, match="permissions.mode"):
        parse_runner_task(payload)


def test_unversioned_content_hash_is_rejected() -> None:
    payload = minimal_payload()
    payload["contextBundle"]["contentHash"] = "latest"

    with pytest.raises(WireError, match="sha256"):
        parse_runner_task(payload)


def test_duplicate_tools_are_rejected() -> None:
    payload = minimal_payload()
    payload["permissions"]["allowedTools"] = ["git", "git"]

    with pytest.raises(WireError, match="unique"):
        parse_runner_task(payload)


def test_non_positive_attempt_is_rejected() -> None:
    payload = minimal_payload()
    payload["attempt"] = 0

    with pytest.raises(WireError, match="attempt must be positive"):
        parse_runner_task(payload)


def test_string_where_an_array_belongs_is_rejected() -> None:
    payload = minimal_payload()
    payload["testCommands"] = "pytest -q"

    with pytest.raises(WireError, match="testCommands must be an array of strings"):
        parse_runner_task(payload)


def test_non_string_array_item_names_its_index() -> None:
    payload = minimal_payload()
    payload["testCommands"] = ["pytest -q", 7]

    with pytest.raises(WireError, match=r"testCommands\[1\] must be a string"):
        parse_runner_task(payload)


def test_boolean_attempt_is_not_an_integer() -> None:
    payload = minimal_payload()
    payload["attempt"] = True

    with pytest.raises(WireError, match="attempt must be an integer"):
        parse_runner_task(payload)


def test_workspace_of_the_wrong_type_is_rejected() -> None:
    payload = minimal_payload()
    payload["workspace"] = "ws-1"

    with pytest.raises(WireError, match="workspace must be an object or null"):
        parse_runner_task(payload)


def test_non_object_payload_is_rejected() -> None:
    with pytest.raises(WireError, match="task payload must be an object"):
        parse_runner_task([])  # type: ignore[arg-type]


def test_wire_error_is_a_value_error() -> None:
    assert issubclass(WireError, ValueError)
