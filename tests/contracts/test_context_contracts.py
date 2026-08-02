from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from repomesh.modules.context.contracts import (
    ContextAccessRecorded,
    ContextAccessResult,
    ContextAction,
    ContextBundlePublished,
    ContextBundleRef,
    ContextObjectType,
    ContextScope,
)


def test_context_contract_enums_keep_the_documented_wire_values() -> None:
    assert [scope.value for scope in ContextScope] == [
        "organization",
        "project_shared",
        "team_private",
        "task_private",
        "run_private",
        "secret",
    ]
    assert [action.value for action in ContextAction] == [
        "discover",
        "read",
        "mount",
        "publish",
        "approve",
        "export",
    ]
    assert ContextObjectType.ENGINEERING_SPEC.value == "engineering_spec"


def test_context_public_events_are_immutable_provider_neutral_values() -> None:
    project_id = uuid4()
    version_id = uuid4()
    bundle = ContextBundleRef(
        bundle_id=uuid4(),
        run_id=uuid4(),
        task_spec_version_id=uuid4(),
        agent_id=uuid4(),
        content_hash=f"sha256:{'d' * 64}",
        item_version_ids=(version_id,),
        required_read_version_ids=(version_id,),
    )
    published = ContextBundlePublished(project_id=project_id, bundle=bundle)
    access = ContextAccessRecorded(
        project_id=project_id,
        run_id=bundle.run_id,
        agent_id=bundle.agent_id,
        version_id=version_id,
        path="context/project/spec.md",
        content_hash=f"sha256:{'d' * 64}",
        result=ContextAccessResult.ALLOWED,
    )

    assert published.bundle.content_hash == access.content_hash
    with pytest.raises(FrozenInstanceError):
        published.project_id = uuid4()  # type: ignore[misc]
