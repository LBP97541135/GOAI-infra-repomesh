from dataclasses import dataclass
from uuid import UUID

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalReader,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.project.contracts import (
    CodeAccessLevel,
    HumanControlAction,
    HumanProjectRole,
    ProjectAgentTopologyView,
    ProjectCheckpoint,
    ProjectExecutionMode,
)
from repomesh.modules.project.domain import (
    HumanProjectGrant,
    ProjectAgentTopology,
    ProjectTopologyConflict,
    ProjectTopologyViolation,
    RepositoryTeam,
)
from repomesh.modules.project.ports import ProjectTopologyStore
from repomesh.shared.idempotency import command_fingerprint


@dataclass(frozen=True, slots=True)
class RepositoryTeamAssignment:
    repository_id: UUID
    leader_agent_id: UUID
    worker_agent_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class HumanProjectGrantInput:
    human_principal_id: UUID
    role: HumanProjectRole
    code_access: CodeAccessLevel
    control_actions: frozenset[HumanControlAction]
    repository_id: UUID | None = None
    path_patterns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CreateProjectAgentTopologyRequest:
    organization_id: UUID
    project_id: UUID
    organization_leader_id: UUID
    repository_teams: tuple[RepositoryTeamAssignment, ...]
    execution_mode: ProjectExecutionMode = ProjectExecutionMode.AUTO
    required_checkpoints: frozenset[ProjectCheckpoint] = frozenset()
    human_grants: tuple[HumanProjectGrantInput, ...] = ()


class CreateProjectAgentTopology:
    def __init__(self, directory: AgentPrincipalReader, store: ProjectTopologyStore) -> None:
        self._directory = directory
        self._store = store

    async def execute(
        self, request: CreateProjectAgentTopologyRequest, *, idempotency_key: str
    ) -> ProjectAgentTopologyView:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")
        fingerprint = command_fingerprint(request)
        if existing := await self._store.get_by_idempotency_key(key):
            topology, existing_fingerprint = existing
            if fingerprint != existing_fingerprint:
                raise ProjectTopologyConflict(
                    "idempotency key was used for a different project topology"
                )
            return topology.to_view()

        organization_leader = await self._required_agent(request.organization_leader_id)
        self._assert_agent(
            organization_leader,
            role=AgentRole.ORGANIZATION_LEADER,
            organization_id=request.organization_id,
        )
        teams = []
        for assignment in request.repository_teams:
            leader = await self._required_agent(assignment.leader_agent_id)
            self._assert_agent(
                leader,
                role=AgentRole.REPOSITORY_LEADER,
                organization_id=request.organization_id,
                repository_id=assignment.repository_id,
                leader_agent_id=request.organization_leader_id,
            )
            for worker_id in assignment.worker_agent_ids:
                worker = await self._required_agent(worker_id)
                self._assert_agent(
                    worker,
                    role=AgentRole.WORKER,
                    organization_id=request.organization_id,
                    repository_id=assignment.repository_id,
                    leader_agent_id=assignment.leader_agent_id,
                )
            teams.append(
                RepositoryTeam(
                    project_id=request.project_id,
                    repository_id=assignment.repository_id,
                    leader_agent_id=assignment.leader_agent_id,
                    worker_agent_ids=assignment.worker_agent_ids,
                )
            )
        topology = ProjectAgentTopology(
            organization_id=request.organization_id,
            project_id=request.project_id,
            organization_leader_id=request.organization_leader_id,
            repository_teams=tuple(teams),
            execution_mode=request.execution_mode,
            required_checkpoints=request.required_checkpoints,
            human_grants=tuple(
                HumanProjectGrant(
                    human_principal_id=grant.human_principal_id,
                    role=grant.role,
                    code_access=grant.code_access,
                    control_actions=grant.control_actions,
                    repository_id=grant.repository_id,
                    path_patterns=grant.path_patterns,
                )
                for grant in request.human_grants
            ),
        )
        await self._store.add(
            topology,
            idempotency_key=key,
            request_fingerprint=fingerprint,
        )
        return topology.to_view()

    async def _required_agent(self, agent_id: UUID) -> AgentPrincipalView:
        profile = await self._directory.get_view(agent_id)
        if profile is None:
            raise ProjectTopologyViolation(f"agent does not exist: {agent_id}")
        return profile

    @staticmethod
    def _assert_agent(
        profile: AgentPrincipalView,
        *,
        role: AgentRole,
        organization_id: UUID,
        repository_id: UUID | None = None,
        leader_agent_id: UUID | None = None,
    ) -> None:
        if profile.role is not role:
            raise ProjectTopologyViolation(
                f"agent {profile.id} must have role {role.value}"
            )
        if profile.organization_id != organization_id:
            raise ProjectTopologyViolation(f"agent {profile.id} belongs to another organization")
        if repository_id is not None and profile.repository_id != repository_id:
            raise ProjectTopologyViolation(f"agent {profile.id} belongs to another repository")
        if leader_agent_id is not None and profile.leader_agent_id != leader_agent_id:
            raise ProjectTopologyViolation(f"agent {profile.id} belongs to another leader")
