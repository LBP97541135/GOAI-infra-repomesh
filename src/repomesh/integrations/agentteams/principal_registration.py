from dataclasses import dataclass, replace

from repomesh.modules.agent_directory.application import CreateAgent, CreateAgentRequest
from repomesh.modules.agent_directory.contracts import AgentCreated, AgentRole
from repomesh.modules.agent_directory.ports import AgentDirectory
from repomesh.modules.agent_runtime.ports.agent_team import (
    AgentTeamControlPlane,
    ManagerProjection,
    McpServerProjection,
    WorkerProjection,
)


@dataclass(frozen=True, slots=True)
class RegisterNativeAgentRequest:
    principal: CreateAgentRequest
    manager: ManagerProjection | None = None
    worker: WorkerProjection | None = None

    def __post_init__(self) -> None:
        if (self.manager is None) == (self.worker is None):
            raise ValueError("exactly one native Manager or Worker projection is required")
        projection = self.manager or self.worker
        if projection is None or projection.name != self.principal.agentteams_resource_name:
            raise ValueError("native resource name must match the principal binding")
        if self.principal.role is AgentRole.ORGANIZATION_LEADER and self.manager is None:
            raise ValueError("organization leader requires a native AgentTeams Manager")
        if self.principal.role is not AgentRole.ORGANIZATION_LEADER and self.worker is None:
            raise ValueError("repository leaders and workers require native AgentTeams Workers")


def with_task_control(
    worker: WorkerProjection, task_control_url: str | None
) -> WorkerProjection:
    """Give a worker projection the RepoMesh task-control MCP server.

    Module level rather than a method because two callers need the *same*
    projection: this module registers an agent as it is created, and the
    console's materialize projects agents that already exist. The controller
    compares an existing worker's ``mcpServers`` field for field, so a second
    caller that spelled this differently would turn a re-registration into a
    409 conflict.
    """

    if not task_control_url:
        return worker
    servers = tuple(
        server for server in worker.mcp_servers if server.name != "repomesh-task-control"
    )
    return replace(
        worker,
        mcp_servers=(
            McpServerProjection("repomesh-task-control", task_control_url),
            *servers,
        ),
    )


class RegisterNativeAgent:
    """Ensure an AgentTeams resource, then register only its RepoMesh business binding."""

    def __init__(
        self,
        control_plane: AgentTeamControlPlane,
        directory: AgentDirectory,
        worker_task_control_url: str | None = None,
    ) -> None:
        self._control_plane = control_plane
        self._register = CreateAgent(directory)
        self._worker_task_control_url = worker_task_control_url

    async def execute(
        self,
        request: RegisterNativeAgentRequest,
        *,
        idempotency_key: str,
    ) -> AgentCreated:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")
        if request.manager is not None:
            await self._control_plane.ensure_manager(
                request.manager,
                idempotency_key=f"{key}:agentteams",
            )
        elif request.worker is not None:
            worker = self._with_task_control(request.worker)
            await self._control_plane.ensure_worker(
                worker,
                idempotency_key=f"{key}:agentteams",
            )
        return await self._register.execute(
            request.principal,
            idempotency_key=f"{key}:principal",
        )

    def _with_task_control(self, worker: WorkerProjection) -> WorkerProjection:
        return with_task_control(worker, self._worker_task_control_url)
