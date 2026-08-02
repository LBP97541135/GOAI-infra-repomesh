from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from repomesh.modules.context.contracts import (
    ContextAction,
    ContextObjectType,
    ContextScope,
)
from repomesh.modules.context.domain import (
    ContextBundle,
    ContextBundleItem,
    ContextObject,
    PermissionLayer,
    PermissionRequest,
    evaluate_permission,
)

CONTENT_HASH = f"sha256:{'a' * 64}"


def test_context_object_requires_project_outside_organization_scope() -> None:
    with pytest.raises(ValueError, match="project_id"):
        ContextObject(
            organization_id=uuid4(),
            object_type=ContextObjectType.CONTRACT,
            scope=ContextScope.PROJECT_SHARED,
            owner_subject="repository-manager",
            title="Orders API",
        )


def test_bundle_is_immutable_and_hashes_its_exact_inputs() -> None:
    item = ContextBundleItem(
        context_object_id=uuid4(),
        version_id=uuid4(),
        content_hash=CONTENT_HASH,
        mount_path="context/contracts/orders.md",
        required_read=True,
    )
    bundle = ContextBundle.create(
        project_id=uuid4(),
        run_id=uuid4(),
        task_spec_version_id=uuid4(),
        agent_id=uuid4(),
        role="worker",
        repository_id=uuid4(),
        base_sha="abc123",
        workspace_id=uuid4(),
        items=(item,),
        allowed_tools=("pytest",),
        allowed_paths=("src/**",),
        denied_paths=(".github/**",),
        network_policy=(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    assert bundle.content_hash.startswith("sha256:")
    with pytest.raises(FrozenInstanceError):
        bundle.role = "manager"  # type: ignore[misc]
    with pytest.raises(ValueError, match="content_hash"):
        ContextBundle(
            project_id=bundle.project_id,
            run_id=bundle.run_id,
            task_spec_version_id=bundle.task_spec_version_id,
            agent_id=bundle.agent_id,
            role=bundle.role,
            repository_id=bundle.repository_id,
            base_sha=bundle.base_sha,
            workspace_id=bundle.workspace_id,
            items=bundle.items,
            allowed_tools=("pytest", "git"),
            allowed_paths=bundle.allowed_paths,
            denied_paths=bundle.denied_paths,
            network_policy=bundle.network_policy,
            expires_at=bundle.expires_at,
            content_hash=bundle.content_hash,
            id=bundle.id,
            created_at=bundle.created_at,
        )


def test_context_mount_path_rejects_parent_traversal() -> None:
    with pytest.raises(ValueError, match="relative paths"):
        ContextBundleItem(
            context_object_id=uuid4(),
            version_id=uuid4(),
            content_hash=CONTENT_HASH,
            mount_path="context/../secrets.txt",
        )


def test_permission_is_intersection_of_all_layers_minus_explicit_deny() -> None:
    context_object_id = uuid4()
    repository_id = uuid4()
    request = PermissionRequest(
        action=ContextAction.MOUNT,
        scope=ContextScope.PROJECT_SHARED,
        context_object_id=context_object_id,
        repository_id=repository_id,
        path="context/contracts/orders.md",
    )
    common = {
        "actions": frozenset({ContextAction.MOUNT, ContextAction.READ}),
        "scopes": frozenset({ContextScope.PROJECT_SHARED}),
        "repository_ids": frozenset({repository_id}),
        "path_patterns": ("context/**",),
    }
    layers = tuple(
        PermissionLayer(name=name, **common)
        for name in ("agent_policy", "project_membership", "task_spec", "run_delegation")
    )

    assert evaluate_permission(request, layers=layers).allowed

    restricted_task = PermissionLayer(
        name="task_spec",
        actions=frozenset({ContextAction.READ}),
        scopes=frozenset({ContextScope.PROJECT_SHARED}),
    )
    denied = evaluate_permission(request, layers=(*layers[:2], restricted_task, layers[3]))
    assert not denied.allowed
    assert denied.reason == "task_spec:action"

    explicit_deny = PermissionLayer(name="security_policy", **common)
    denied = evaluate_permission(request, layers=layers, explicit_denies=(explicit_deny,))
    assert denied.reason == "explicit_deny:security_policy"
