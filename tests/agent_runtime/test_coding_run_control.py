from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from repomesh.modules.agent_runtime.application import (
    BindCodingSession,
    PrepareCodingRun,
    RegisterWorkspace,
    SubmitPreparedCodingRun,
    ValidateRunnerResult,
)
from repomesh.modules.agent_runtime.contracts import (
    CodingRunStatus,
    CommandExecutionResult,
    PrepareCodingRunCommand,
    RegisterWorkspaceCommand,
    RunnerResultCandidate,
    WorkspaceStatus,
)
from repomesh.modules.agent_runtime.domain import CodingRunConflict, CodingRunDenied
from repomesh.modules.agent_runtime.infrastructure import InMemoryAgentRuntimeStore
from repomesh.modules.agent_runtime.ports.runner_gateway import MockRunnerGateway
from repomesh.modules.context.contracts import ExecutionContextGrant
from repomesh.modules.specification.contracts import CodingAgentPackage

HASH_A = f"sha256:{'a' * 64}"
HASH_B = f"sha256:{'b' * 64}"


class StubPackageBuilder:
    def __init__(self, package: CodingAgentPackage) -> None:
        self.package = package

    async def execute(self, command) -> CodingAgentPackage:
        return self.package


class StubGrantReader:
    def __init__(self, grant: ExecutionContextGrant) -> None:
        self.grant = grant

    async def get_grant(
        self, bundle_id: UUID, *, run_id: UUID, agent_id: UUID
    ) -> ExecutionContextGrant:
        return self.grant


async def prepared_scenario(tmp_path):
    organization_id = uuid4()
    project_id = uuid4()
    repository_id = uuid4()
    task_id = uuid4()
    worker_id = uuid4()
    run_id = uuid4()
    bundle_id = uuid4()
    store = InMemoryAgentRuntimeStore()
    workspace = await RegisterWorkspace(store, tmp_path).execute(
        RegisterWorkspaceCommand(
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository_id,
            task_id=task_id,
            path=str(tmp_path.joinpath("worktree").resolve()),
            base_sha="abc123",
        )
    )
    package = CodingAgentPackage(
        project_id=project_id,
        repository_id=repository_id,
        task_id=task_id,
        worker_agent_id=worker_id,
        instruction="Update pricing API",
        acceptance=("Old clients remain compatible",),
        constraints=("Do not remove old fields",),
        dependencies=("pricing-contract v2.3",),
        interface_changes=("Add nullable price field",),
        allowed_paths=("src/pricing/**", "tests/pricing/**"),
        test_commands=("pytest tests/pricing",),
        context_files=(),
        content_hash=HASH_A,
    )
    grant = ExecutionContextGrant(
        bundle_id=bundle_id,
        project_id=project_id,
        run_id=run_id,
        agent_id=worker_id,
        repository_id=repository_id,
        allowed_tools=("read", "edit", "test"),
        allowed_paths=("src/pricing/**", "tests/pricing/**"),
        denied_paths=("src/pricing/secrets/**", ".github/**"),
        network_policy=(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        content_hash=HASH_B,
    )
    command = PrepareCodingRunCommand(
        organization_id=organization_id,
        project_id=project_id,
        repository_id=repository_id,
        task_id=task_id,
        worker_agent_id=worker_id,
        adapter_id="claude-code",
        workspace_id=workspace.id,
        context_bundle_id=bundle_id,
        run_id=run_id,
    )
    service = PrepareCodingRun(
        StubPackageBuilder(package), StubGrantReader(grant), store, store
    )
    return store, service, command, workspace, grant


@pytest.mark.asyncio
async def test_prepare_binds_workspace_and_persists_internal_run(tmp_path) -> None:
    store, service, command, workspace, _ = await prepared_scenario(tmp_path)
    run = await service.execute(command)

    bound = await store.get(workspace.id)
    assert run.status is CodingRunStatus.PREPARED
    assert run.coding_package_hash == HASH_A
    assert run.context_bundle_hash == HASH_B
    assert bound.status is WorkspaceStatus.BOUND
    assert bound.bound_run_id == run.id


@pytest.mark.asyncio
async def test_prepare_rejects_spec_paths_outside_context_grant(tmp_path) -> None:
    store, _, command, _, grant = await prepared_scenario(tmp_path)
    restricted = replace(grant, allowed_paths=("src/pricing/**",))
    package = CodingAgentPackage(
        project_id=command.project_id,
        repository_id=command.repository_id,
        task_id=command.task_id,
        worker_agent_id=command.worker_agent_id,
        instruction="Update pricing API",
        acceptance=("Tests pass",),
        constraints=(),
        dependencies=(),
        interface_changes=(),
        allowed_paths=("src/pricing/**", "tests/pricing/**"),
        test_commands=(),
        context_files=(),
        content_hash=HASH_A,
    )
    service = PrepareCodingRun(
        StubPackageBuilder(package), StubGrantReader(restricted), store, store
    )

    with pytest.raises(CodingRunDenied, match="exceeds_context_grant"):
        await service.execute(command)


@pytest.mark.asyncio
async def test_submit_and_session_binding_are_runner_contract_independent(tmp_path) -> None:
    store, service, command, _, _ = await prepared_scenario(tmp_path)
    prepared = await service.execute(command)
    runner = MockRunnerGateway()
    submitted = await SubmitPreparedCodingRun(store, runner).execute(prepared.id)
    binding = await BindCodingSession(store, store).execute(prepared.id, "session-42")
    replay = await BindCodingSession(store, store).execute(prepared.id, "session-42")

    assert submitted.status is CodingRunStatus.SUBMITTED
    assert runner.submitted == [submitted]
    assert binding == replay
    with pytest.raises(CodingRunConflict, match="another native session"):
        await BindCodingSession(store, store).execute(prepared.id, "session-99")


@pytest.mark.asyncio
async def test_result_validator_rejects_path_and_test_violations(tmp_path) -> None:
    _, service, command, _, _ = await prepared_scenario(tmp_path)
    run = await service.execute(command)
    validator = ValidateRunnerResult()
    valid = await validator.execute(
        run,
        RunnerResultCandidate(
            run_id=run.id,
            succeeded=True,
            summary="Updated pricing API",
            changed_files=("src/pricing/api.py", "tests/pricing/test_api.py"),
            test_results=(CommandExecutionResult("pytest tests/pricing", 0),),
        ),
    )
    invalid = await validator.execute(
        run,
        RunnerResultCandidate(
            run_id=run.id,
            succeeded=True,
            summary="Updated everything",
            changed_files=(".github/workflows/release.yml", "src/checkout/api.py"),
            test_results=(CommandExecutionResult("pytest tests/pricing", 1),),
        ),
    )

    assert valid.accepted
    assert not invalid.accepted
    assert "denied_path_changed:.github/workflows/release.yml" in invalid.reasons
    assert "path_outside_grant:src/checkout/api.py" in invalid.reasons
    assert "required_test_failed:pytest tests/pricing" in invalid.reasons
