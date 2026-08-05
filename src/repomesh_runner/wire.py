"""Parsing of ``runtime.v1`` wire payloads into Runner domain objects.

This module is the inverse of :meth:`repomesh_runner.contracts.RunnerTask.to_wire`. Validation is
hand written on purpose: the contract package ships JSON Schema for language-neutral consumers,
while the Python execution plane leans on the dataclass invariants in
:mod:`repomesh_runner.contracts` and only adds the shape checks those invariants cannot express.

Two leniencies are deliberate and compatible with the v1 additive-change rule:

* every optional key may be absent *or* explicitly ``null`` — a producer that has not yet learned
  about a field and one that emits it empty are treated identically;
* an absent string array is read as an empty tuple, which for the permission arrays is the
  restrictive direction.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from .contracts import (
    RUNTIME_SCHEMA_VERSION,
    ContextBundleRef,
    RepositoryCheckout,
    RunnerPermissionMode,
    RunnerPermissions,
    RunnerTask,
    WorkspaceAssignment,
)

__all__ = ["WireError", "parse_runner_task"]


class WireError(ValueError):
    """Raised when a payload does not satisfy the ``runtime.v1`` task envelope."""


def parse_runner_task(payload: Mapping[str, Any]) -> RunnerTask:
    """Build a :class:`RunnerTask` from its wire representation.

    Raises:
        WireError: the payload is not a ``runtime.v1`` task or a field is malformed. The message
            names the offending field path.
    """

    if not isinstance(payload, Mapping):
        raise WireError("task payload must be an object")

    schema_version = payload.get("schemaVersion")
    if schema_version != RUNTIME_SCHEMA_VERSION:
        raise WireError(f"schemaVersion must be {RUNTIME_SCHEMA_VERSION!r}, got {schema_version!r}")

    repository = _parse_repository(_object_field(payload, "repository", "repository"))
    context_bundle = _parse_context_bundle(_object_field(payload, "contextBundle", "contextBundle"))
    permissions = _parse_permissions(_object_field(payload, "permissions", "permissions"))
    workspace = _parse_workspace(payload.get("workspace"))

    return _build(
        "task",
        RunnerTask,
        organization_id=_uuid_field(payload, "organizationId", "organizationId"),
        project_id=_uuid_field(payload, "projectId", "projectId"),
        task_id=_uuid_field(payload, "taskId", "taskId"),
        run_id=_uuid_field(payload, "runId", "runId"),
        correlation_id=_uuid_field(payload, "correlationId", "correlationId"),
        attempt=_int_field(payload, "attempt", "attempt"),
        adapter_id=_string_field(payload, "adapterId", "adapterId"),
        instruction=_string_field(payload, "instruction", "instruction"),
        repository=repository,
        context_bundle=context_bundle,
        permissions=permissions,
        idempotency_key=_string_field(payload, "idempotencyKey", "idempotencyKey"),
        issued_at=_datetime_field(payload, "issuedAt", "issuedAt"),
        resume_session_id=_optional_string(payload, "resumeSessionId", "resumeSessionId"),
        credential_refs=_string_tuple(payload, "credentialRefs", "credentialRefs"),
        workspace=workspace,
        worker_agent_id=_optional_uuid(payload, "workerAgentId", "workerAgentId"),
        test_commands=_string_tuple(payload, "testCommands", "testCommands"),
    )


def _parse_repository(payload: Mapping[str, Any]) -> RepositoryCheckout:
    return _build(
        "repository",
        RepositoryCheckout,
        repository_id=_uuid_field(payload, "repositoryId", "repository.repositoryId"),
        url=_string_field(payload, "url", "repository.url"),
        base_revision=_string_field(payload, "baseRevision", "repository.baseRevision"),
    )


def _parse_context_bundle(payload: Mapping[str, Any]) -> ContextBundleRef:
    return _build(
        "contextBundle",
        ContextBundleRef,
        bundle_id=_uuid_field(payload, "bundleId", "contextBundle.bundleId"),
        version=_int_field(payload, "version", "contextBundle.version"),
        manifest_uri=_string_field(payload, "manifestUri", "contextBundle.manifestUri"),
        content_hash=_string_field(payload, "contentHash", "contextBundle.contentHash"),
        coding_package_hash=_optional_string(
            payload, "codingPackageHash", "contextBundle.codingPackageHash"
        ),
    )


def _parse_permissions(payload: Mapping[str, Any]) -> RunnerPermissions:
    raw_mode = payload.get("mode")
    if raw_mode is None:
        raise WireError("permissions.mode is required")
    try:
        mode = RunnerPermissionMode(raw_mode)
    except ValueError as error:
        raise WireError(f"permissions.mode {raw_mode!r} is not a known permission mode") from error

    return _build(
        "permissions",
        RunnerPermissions,
        mode=mode,
        allowed_tools=_string_tuple(payload, "allowedTools", "permissions.allowedTools"),
        disallowed_tools=_string_tuple(payload, "disallowedTools", "permissions.disallowedTools"),
        network_targets=_string_tuple(payload, "networkTargets", "permissions.networkTargets"),
        allowed_paths=_string_tuple(payload, "allowedPaths", "permissions.allowedPaths"),
        denied_paths=_string_tuple(payload, "deniedPaths", "permissions.deniedPaths"),
    )


def _parse_workspace(value: Any) -> WorkspaceAssignment | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise WireError("workspace must be an object or null")
    return _build(
        "workspace",
        WorkspaceAssignment,
        workspace_id=_string_field(value, "workspaceId", "workspace.workspaceId"),
        path=_string_field(value, "path", "workspace.path"),
        base_sha=_string_field(value, "baseSha", "workspace.baseSha"),
    )


def _build(field: str, factory: Any, **kwargs: Any) -> Any:
    """Construct a contract dataclass, translating its invariants into wire errors."""

    try:
        return factory(**kwargs)
    except WireError:
        raise
    except ValueError as error:
        raise WireError(f"{field}: {error}") from error


def _object_field(payload: Mapping[str, Any], key: str, field: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if value is None:
        raise WireError(f"{field} is required")
    if not isinstance(value, Mapping):
        raise WireError(f"{field} must be an object")
    return value


def _string_field(payload: Mapping[str, Any], key: str, field: str) -> str:
    value = payload.get(key)
    if value is None:
        raise WireError(f"{field} is required")
    if not isinstance(value, str):
        raise WireError(f"{field} must be a string")
    return value


def _optional_string(payload: Mapping[str, Any], key: str, field: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise WireError(f"{field} must be a string or null")
    return value


def _int_field(payload: Mapping[str, Any], key: str, field: str) -> int:
    value = payload.get(key)
    if value is None:
        raise WireError(f"{field} is required")
    if isinstance(value, bool) or not isinstance(value, int):
        raise WireError(f"{field} must be an integer")
    return value


def _uuid_field(payload: Mapping[str, Any], key: str, field: str) -> UUID:
    return _parse_uuid(_string_field(payload, key, field), field)


def _optional_uuid(payload: Mapping[str, Any], key: str, field: str) -> UUID | None:
    value = _optional_string(payload, key, field)
    if value is None:
        return None
    return _parse_uuid(value, field)


def _parse_uuid(value: str, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise WireError(f"{field} must be a UUID") from error


def _datetime_field(payload: Mapping[str, Any], key: str, field: str) -> datetime:
    raw = _string_field(payload, key, field)
    try:
        return datetime.fromisoformat(raw)
    except ValueError as error:
        raise WireError(f"{field} must be an ISO-8601 timestamp") from error


def _string_tuple(payload: Mapping[str, Any], key: str, field: str) -> tuple[str, ...]:
    value = payload.get(key)
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise WireError(f"{field} must be an array of strings")
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise WireError(f"{field}[{index}] must be a string")
    return tuple(value)
