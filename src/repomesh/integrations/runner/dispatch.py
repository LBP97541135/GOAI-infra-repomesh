from repomesh.integrations.workspace import GitWorktreeManager
from repomesh.modules.agent_runtime.contracts import DispatchWorkerTaskCommand
from repomesh.modules.capability_management import ResolveAgentCapabilities
from repomesh.modules.context.application import GetExecutionContextGrant
from repomesh.modules.repository_intelligence.ports.catalog import RepositoryCatalog
from repomesh.modules.specification import (
    BuildCodingAgentPackage,
    BuildCodingAgentPackageCommand,
)
from repomesh_runner.contracts import RunnerPermissionMode, RunnerTask

from .context_materializer import RunnerContextMaterializer
from .gateway import RunnerControlGateway
from .task_projection import RunnerTaskProjectionRequest, RunnerTaskProjector


class DispatchWorkerTask:
    """Build, mount and durably enqueue one governed Worker execution."""

    def __init__(
        self,
        packages: BuildCodingAgentPackage,
        grants: GetExecutionContextGrant,
        capabilities: ResolveAgentCapabilities,
        repositories: RepositoryCatalog,
        workspaces: GitWorktreeManager,
        materializer: RunnerContextMaterializer,
        gateway: RunnerControlGateway,
        projector: RunnerTaskProjector | None = None,
    ) -> None:
        self._packages = packages
        self._grants = grants
        self._capabilities = capabilities
        self._repositories = repositories
        self._workspaces = workspaces
        self._materializer = materializer
        self._gateway = gateway
        self._projector = projector or RunnerTaskProjector()

    async def execute(self, command: DispatchWorkerTaskCommand) -> RunnerTask:
        repository = await self._repositories.get(command.repository_id)
        if repository is None:
            raise ValueError(f"repository not found: {command.repository_id}")
        package = await self._packages.execute(
            BuildCodingAgentPackageCommand(
                organization_id=command.organization_id,
                project_id=command.project_id,
                repository_id=command.repository_id,
                task_id=command.task_id,
                worker_agent_id=command.worker_agent_id,
            )
        )
        grant = await self._grants.execute(
            command.bundle_id,
            run_id=command.run_id,
            agent_id=command.worker_agent_id,
        )
        capabilities = await self._capabilities.execute(
            command.worker_agent_id, task_features=command.task_features
        )
        workspace = await self._workspaces.prepare(
            repository_id=command.repository_id,
            repository_url=repository.url,
            base_revision=command.base_revision,
            run_id=command.run_id,
            workspace_id=grant.workspace_id,
        )
        if grant.base_sha is not None and grant.base_sha != workspace.base_sha:
            if workspace.created:
                await self._workspaces.release(workspace)
            raise ValueError("workspace base SHA does not match the immutable context grant")
        try:
            manifest = workspace.path / ".repomesh" / "context" / "manifest.json"
            runner_task = self._projector.project(
                RunnerTaskProjectionRequest(
                    package=package,
                    context_grant=grant,
                    capabilities=capabilities,
                    organization_id=command.organization_id,
                    run_id=command.run_id,
                    correlation_id=command.correlation_id,
                    adapter_id=command.adapter_id,
                    repository_url=repository.url,
                    base_revision=command.base_revision,
                    workspace_id=workspace.workspace_id,
                    workspace_path=workspace.path,
                    base_sha=workspace.base_sha,
                    context_manifest_uri=manifest.resolve().as_uri(),
                    attempt=command.attempt,
                    permission_mode=RunnerPermissionMode(command.permission_mode),
                    resume_session_id=command.resume_session_id,
                    credential_refs=command.credential_refs,
                )
            )
            self._materializer.materialize(runner_task, package, capabilities)
            await self._gateway.enqueue(runner_task)
            return runner_task
        except Exception:
            if workspace.created:
                await self._workspaces.release(workspace)
            raise
