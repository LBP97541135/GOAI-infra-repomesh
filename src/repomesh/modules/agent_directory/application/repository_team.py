from dataclasses import dataclass
from uuid import UUID

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentRole,
    RepositoryAgentTeamCreated,
)
from repomesh.modules.agent_directory.domain import AgentHierarchyViolation
from repomesh.modules.agent_directory.ports import AgentDirectory

from .create import CreateAgent, CreateAgentRequest


@dataclass(frozen=True, slots=True)
class CreateRepositoryAgentTeamRequest:
    organization_id: UUID
    organization_leader_id: UUID
    repository_id: UUID
    leader_agentteams_resource_name: str
    worker_agentteams_resource_names: tuple[str, ...]
    leader_responsibility_paths: tuple[str, ...] = ("**",)
    worker_responsibility_paths: tuple[str, ...] = ("**",)

    def __post_init__(self) -> None:
        if not 1 <= len(self.worker_agentteams_resource_names) <= 20:
            raise ValueError("worker resources must contain between 1 and 20 names")
        resources = (
            self.leader_agentteams_resource_name,
            *self.worker_agentteams_resource_names,
        )
        if any(not name.strip() for name in resources):
            raise ValueError("AgentTeams resource names are required")
        if len(set(resources)) != len(resources):
            raise ValueError("AgentTeams resource names must be unique")


class CreateRepositoryAgentTeam:
    """Register business principals for an existing AgentTeams repository team."""

    def __init__(self, directory: AgentDirectory) -> None:
        self._create = CreateAgent(directory)

    async def execute(
        self,
        request: CreateRepositoryAgentTeamRequest,
        *,
        idempotency_key: str,
    ) -> RepositoryAgentTeamCreated:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")
        leader = await self._create.execute(
            CreateAgentRequest(
                organization_id=request.organization_id,
                role=AgentRole.REPOSITORY_LEADER,
                agentteams_resource_name=request.leader_agentteams_resource_name,
                leader_agent_id=request.organization_leader_id,
                repository_id=request.repository_id,
                responsibility_paths=request.leader_responsibility_paths,
            ),
            idempotency_key=f"{key}:leader",
        )
        workers = []
        for index, resource_name in enumerate(
            request.worker_agentteams_resource_names, start=1
        ):
            worker = await self._create.execute(
                CreateAgentRequest(
                    organization_id=request.organization_id,
                    role=AgentRole.WORKER,
                    agentteams_resource_name=resource_name,
                    leader_agent_id=leader.principal.id,
                    repository_id=request.repository_id,
                    responsibility_paths=request.worker_responsibility_paths,
                ),
                idempotency_key=f"{key}:worker:{index:02d}",
            )
            workers.append(worker.principal)
        return RepositoryAgentTeamCreated(
            repository_id=request.repository_id,
            leader=leader.principal,
            workers=tuple(workers),
        )


def _leader_resource_name(repository_id: UUID) -> str:
    return f"agt-leader-{repository_id.hex[:12]}"


def _worker_resource_name(repository_id: UUID) -> str:
    return f"agt-worker-{repository_id.hex[:12]}"


class ProvisionRepositoryAgentTeam:
    """Ensure a repository has the leader-and-worker pair a topology needs.

    Implements :class:`RepositoryAgentTeamProvisioner`. The difference from
    :class:`CreateRepositoryAgentTeam` is the whole point: *create* is what an
    operator does once, deliberately, with names they chose; *ensure* is what a
    console round does on its way to work, and it must converge on the team a
    repository already has rather than fail.

    Convergence is not an optimisation here, it is correctness. A repository
    leader is a directory singleton — ``CreateAgent`` derives
    ``singleton_key = "repository:{id}:leader"`` — so the second project to
    touch a repository would hit ``AgentAlreadyExists`` and strand the round.
    Two projects sharing a repository is ordinary.

    Names are derived from the repository id (``agt-leader-<hex12>``), the
    scheme ``scripts/run_pipeline.py`` already uses, so a repository ends up
    with one name whichever path provisioned it.
    """

    def __init__(self, directory: AgentDirectory) -> None:
        self._directory = directory
        self._create = CreateAgent(directory)
        self._team = CreateRepositoryAgentTeam(directory)

    async def provision(
        self,
        *,
        organization_id: UUID,
        organization_leader_id: UUID,
        repository_id: UUID,
        idempotency_key: str,
    ) -> RepositoryAgentTeamCreated:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")

        principals = await self._directory.list()
        leader = next(
            (
                principal
                for principal in principals
                if principal.role is AgentRole.REPOSITORY_LEADER
                and principal.repository_id == repository_id
                and principal.status is AgentPrincipalStatus.ACTIVE
            ),
            None,
        )
        if leader is None:
            return await self._team.execute(
                CreateRepositoryAgentTeamRequest(
                    organization_id=organization_id,
                    organization_leader_id=organization_leader_id,
                    repository_id=repository_id,
                    leader_agentteams_resource_name=_leader_resource_name(repository_id),
                    worker_agentteams_resource_names=(
                        _worker_resource_name(repository_id),
                    ),
                ),
                idempotency_key=key,
            )

        # Reuse. Both mismatches below are refusals rather than repairs: a
        # topology built on them would be rejected by CreateProjectAgentTopology
        # anyway, and saying which agent is wrong beats a generic violation
        # raised three layers later.
        if leader.organization_id != organization_id:
            raise AgentHierarchyViolation(
                f"repository {repository_id} already has a leader in another "
                f"organization ({leader.organization_id}); it cannot join this "
                "project's topology"
            )
        if leader.leader_agent_id != organization_leader_id:
            raise AgentHierarchyViolation(
                f"repository {repository_id} already has leader {leader.id}, which "
                f"reports to {leader.leader_agent_id} rather than to organization "
                f"leader {organization_leader_id}"
            )

        workers = tuple(
            principal.to_view()
            for principal in principals
            if principal.role is AgentRole.WORKER
            and principal.leader_agent_id == leader.id
            and principal.status is AgentPrincipalStatus.ACTIVE
        )
        if not workers:
            # A leader with no worker cannot be assigned a task. Workers carry
            # no singleton key, so this adds one instead of converging.
            created = await self._create.execute(
                CreateAgentRequest(
                    organization_id=organization_id,
                    role=AgentRole.WORKER,
                    agentteams_resource_name=_worker_resource_name(repository_id),
                    leader_agent_id=leader.id,
                    repository_id=repository_id,
                    responsibility_paths=("**",),
                ),
                idempotency_key=f"{key}:worker:01",
            )
            workers = (created.principal,)

        return RepositoryAgentTeamCreated(
            repository_id=repository_id,
            leader=leader.to_view(),
            workers=workers,
        )
