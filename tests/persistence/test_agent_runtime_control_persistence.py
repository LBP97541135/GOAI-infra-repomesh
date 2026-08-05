from uuid import uuid4

import pytest
import pytest_asyncio

from repomesh.modules.agent_runtime.contracts import WorkspaceStatus
from repomesh.modules.agent_runtime.domain import (
    CodingRun,
    CodingRunConflict,
    SessionBinding,
    Workspace,
)
from repomesh.modules.agent_runtime.infrastructure import PostgresAgentRuntimeStore
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS

HASH_A = f"sha256:{'a' * 64}"
HASH_B = f"sha256:{'b' * 64}"


@pytest_asyncio.fixture
async def database(tmp_path: object) -> Database:
    database_path = tmp_path.joinpath("repomesh-agent-runtime.db")
    instance = Database(
        f"sqlite+aiosqlite:///{database_path}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await instance.create_all_for_tests()
    yield instance
    await instance.dispose()


def build_run(workspace: Workspace, *, run_id=None) -> CodingRun:
    return CodingRun(
        id=run_id or uuid4(),
        organization_id=workspace.organization_id,
        project_id=workspace.project_id,
        repository_id=workspace.repository_id,
        task_id=workspace.task_id,
        worker_agent_id=uuid4(),
        adapter_id="claude-code",
        workspace_id=workspace.id,
        context_bundle_id=uuid4(),
        context_bundle_hash=HASH_A,
        coding_package_hash=HASH_B,
        base_sha=workspace.base_sha,
        instruction="Update pricing API",
        acceptance=("Pricing tests pass",),
        required_tests=("pytest tests/pricing",),
        allowed_tools=("read", "edit", "test"),
        allowed_paths=("src/pricing/**", "tests/pricing/**"),
        denied_paths=(".github/**",),
        network_policy=(),
    )


@pytest.mark.asyncio
async def test_runtime_control_records_round_trip(database: Database, tmp_path) -> None:
    store = PostgresAgentRuntimeStore(database)
    workspace = Workspace(
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_id=uuid4(),
        task_id=uuid4(),
        path=str(tmp_path.joinpath("worktree").resolve()),
        base_sha="abc123",
    )
    await store.add(workspace)
    run = build_run(workspace)
    bound = workspace.bind(run.id)
    await store.prepare(
        run,
        bound,
        expected_workspace_revision=workspace.revision,
    )
    binding = SessionBinding(
        run_id=run.id,
        task_id=run.task_id,
        adapter_id=run.adapter_id,
        workspace_id=run.workspace_id,
        context_bundle_hash=run.context_bundle_hash,
        coding_package_hash=run.coding_package_hash,
        native_session_id="session-42",
    )
    await store.add(binding)

    assert await store.get(workspace.id) == bound
    assert await store.get(run.id) == run
    assert await store.get_by_run(run.id) == binding


@pytest.mark.asyncio
async def test_prepare_conflict_rolls_back_workspace_binding(
    database: Database, tmp_path
) -> None:
    store = PostgresAgentRuntimeStore(database)
    first = Workspace(
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_id=uuid4(),
        task_id=uuid4(),
        path=str(tmp_path.joinpath("first").resolve()),
        base_sha="abc123",
    )
    second = Workspace(
        organization_id=first.organization_id,
        project_id=first.project_id,
        repository_id=first.repository_id,
        task_id=first.task_id,
        path=str(tmp_path.joinpath("second").resolve()),
        base_sha="abc123",
    )
    await store.add(first)
    await store.add(second)
    run = build_run(first)
    await store.prepare(run, first.bind(run.id), expected_workspace_revision=1)

    with pytest.raises(CodingRunConflict):
        await store.prepare(
            build_run(second, run_id=run.id),
            second.bind(run.id),
            expected_workspace_revision=1,
        )

    restored = await store.get(second.id)
    assert restored.status is WorkspaceStatus.READY
    assert restored.bound_run_id is None
