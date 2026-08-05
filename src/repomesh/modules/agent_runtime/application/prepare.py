from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from uuid import UUID

from repomesh.modules.agent_runtime.contracts import (
    CodingRunStatus,
    CodingRunView,
    PrepareCodingRunCommand,
    RegisterWorkspaceCommand,
    RunnerResultCandidate,
    RunnerResultValidation,
    SessionBindingView,
    WorkspaceView,
)
from repomesh.modules.agent_runtime.domain import (
    CodingRun,
    CodingRunConflict,
    CodingRunDenied,
    CodingRunNotFound,
    SessionBinding,
    Workspace,
)
from repomesh.modules.agent_runtime.ports.run_store import (
    CodingRunStore,
    SessionBindingStore,
    WorkspaceStore,
)
from repomesh.modules.agent_runtime.ports.runner_gateway import RunnerGateway
from repomesh.modules.context.contracts import ExecutionContextGrantReader
from repomesh.modules.specification.contracts import (
    BuildCodingAgentPackageCommand,
    CodingAgentPackageBuilder,
)


class RegisterWorkspace:
    def __init__(self, store: WorkspaceStore, workspace_root: Path) -> None:
        self._store = store
        self._workspace_root = workspace_root.resolve()

    async def execute(self, command: RegisterWorkspaceCommand) -> WorkspaceView:
        path = Path(command.path).resolve()
        if path == self._workspace_root or not path.is_relative_to(self._workspace_root):
            raise CodingRunDenied("workspace_outside_configured_root")
        workspace = Workspace(
            organization_id=command.organization_id,
            project_id=command.project_id,
            repository_id=command.repository_id,
            task_id=command.task_id,
            path=str(path),
            base_sha=command.base_sha,
        )
        await self._store.add(workspace)
        return workspace.to_view()


class PrepareCodingRun:
    def __init__(
        self,
        packages: CodingAgentPackageBuilder,
        grants: ExecutionContextGrantReader,
        workspaces: WorkspaceStore,
        runs: CodingRunStore,
    ) -> None:
        self._packages = packages
        self._grants = grants
        self._workspaces = workspaces
        self._runs = runs

    async def execute(self, command: PrepareCodingRunCommand) -> CodingRunView:
        package = await self._packages.execute(
            BuildCodingAgentPackageCommand(
                organization_id=command.organization_id,
                project_id=command.project_id,
                repository_id=command.repository_id,
                task_id=command.task_id,
                worker_agent_id=command.worker_agent_id,
            )
        )
        grant = await self._grants.get_grant(
            command.context_bundle_id,
            run_id=command.run_id,
            agent_id=command.worker_agent_id,
        )
        workspace = await self._workspaces.get(command.workspace_id)
        if workspace is None or not isinstance(workspace, Workspace):
            raise CodingRunNotFound(f"workspace not found: {command.workspace_id}")
        if (
            workspace.organization_id,
            workspace.project_id,
            workspace.repository_id,
            workspace.task_id,
        ) != (
            command.organization_id,
            command.project_id,
            command.repository_id,
            command.task_id,
        ):
            raise CodingRunDenied("workspace_binding_mismatch")
        if (
            grant.project_id != command.project_id
            or grant.repository_id != command.repository_id
            or grant.agent_id != command.worker_agent_id
            or grant.run_id != command.run_id
        ):
            raise CodingRunDenied("context_grant_binding_mismatch")
        if datetime.now(UTC) >= grant.expires_at:
            raise CodingRunDenied("context_grant_expired")
        if any(pattern not in grant.allowed_paths for pattern in package.allowed_paths):
            raise CodingRunDenied("task_spec_exceeds_context_grant")

        run = CodingRun(
            id=command.run_id,
            organization_id=command.organization_id,
            project_id=command.project_id,
            repository_id=command.repository_id,
            task_id=command.task_id,
            worker_agent_id=command.worker_agent_id,
            adapter_id=command.adapter_id,
            workspace_id=workspace.id,
            context_bundle_id=grant.bundle_id,
            context_bundle_hash=grant.content_hash,
            coding_package_hash=package.content_hash,
            base_sha=workspace.base_sha,
            instruction=package.instruction,
            acceptance=package.acceptance,
            required_tests=package.test_commands,
            allowed_tools=grant.allowed_tools,
            allowed_paths=package.allowed_paths,
            denied_paths=grant.denied_paths,
            network_policy=grant.network_policy,
            attempt=command.attempt,
        )
        await self._runs.prepare(
            run,
            workspace.bind(run.id),
            expected_workspace_revision=workspace.revision,
        )
        return run.to_view()


class SubmitPreparedCodingRun:
    def __init__(self, runs: CodingRunStore, runner: RunnerGateway) -> None:
        self._runs = runs
        self._runner = runner

    async def execute(self, run_id: UUID) -> CodingRunView:
        run = await self._required(run_id)
        if run.status is CodingRunStatus.PREPARED:
            submitted = run.transition(CodingRunStatus.SUBMITTED)
            await self._runs.update(submitted, expected_revision=run.revision)
        elif run.status is CodingRunStatus.SUBMITTED:
            submitted = run
        else:
            raise CodingRunConflict("only prepared or submitted runs can be dispatched")
        await self._runner.submit(submitted.to_view())
        return submitted.to_view()

    async def _required(self, run_id: UUID) -> CodingRun:
        run = await self._runs.get(run_id)
        if run is None or not isinstance(run, CodingRun):
            raise CodingRunNotFound(f"coding run not found: {run_id}")
        return run


class BindCodingSession:
    def __init__(self, runs: CodingRunStore, bindings: SessionBindingStore) -> None:
        self._runs = runs
        self._bindings = bindings

    async def execute(
        self, run_id: UUID, native_session_id: str
    ) -> SessionBindingView:
        run = await self._runs.get(run_id)
        if run is None or not isinstance(run, CodingRun):
            raise CodingRunNotFound(f"coding run not found: {run_id}")
        existing = await self._bindings.get_by_run(run_id)
        if existing is not None:
            if existing.native_session_id != native_session_id.strip():
                raise CodingRunConflict("run already has another native session")
            return existing.to_view()
        updated = run.bind_session(native_session_id)
        binding = SessionBinding(
            run_id=run.id,
            task_id=run.task_id,
            adapter_id=run.adapter_id,
            workspace_id=run.workspace_id,
            context_bundle_hash=run.context_bundle_hash,
            coding_package_hash=run.coding_package_hash,
            native_session_id=native_session_id.strip(),
        )
        await self._bindings.add(binding)
        await self._runs.update(updated, expected_revision=run.revision)
        return binding.to_view()


class ValidateRunnerResult:
    async def execute(
        self, run: CodingRunView, result: RunnerResultCandidate
    ) -> RunnerResultValidation:
        reasons: list[str] = []
        if not result.succeeded:
            reasons.append("runner_reported_failure")
        if result.run_id != run.id:
            reasons.append("run_binding_mismatch")
        for value in result.changed_files:
            normalized = value.replace("\\", "/").lstrip("/")
            path = PurePosixPath(normalized)
            if not normalized or ".." in path.parts:
                reasons.append(f"invalid_changed_path:{value}")
                continue
            if any(fnmatchcase(normalized, pattern) for pattern in run.denied_paths):
                reasons.append(f"denied_path_changed:{normalized}")
            elif not any(
                fnmatchcase(normalized, pattern) for pattern in run.allowed_paths
            ):
                reasons.append(f"path_outside_grant:{normalized}")
        executed = {item.command: item.exit_code for item in result.test_results}
        for command in run.required_tests:
            if command not in executed:
                reasons.append(f"required_test_missing:{command}")
            elif executed[command] != 0:
                reasons.append(f"required_test_failed:{command}")
        if result.succeeded and not result.summary.strip():
            reasons.append("successful_result_requires_summary")
        return RunnerResultValidation(accepted=not reasons, reasons=tuple(reasons))
