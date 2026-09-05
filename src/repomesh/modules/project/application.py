from dataclasses import dataclass
from uuid import UUID

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalReader,
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
    RepositoryAgentTeamProvisioner,
)
from repomesh.modules.project.contracts import (
    CodeAccessLevel,
    ConstructionMode,
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
from repomesh.modules.project.ports import ProjectTopologyStore, TopologyPolicyDraftStore
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


@dataclass(frozen=True, slots=True)
class CreateAutomaticProjectTopologyRequest:
    organization_id: UUID
    project_id: UUID
    repository_ids: tuple[UUID, ...]
    execution_mode: ProjectExecutionMode = ProjectExecutionMode.AUTO
    required_checkpoints: frozenset[ProjectCheckpoint] = frozenset()
    human_grants: tuple[HumanProjectGrantInput, ...] = ()


class CreateProjectAgentTopology:
    def __init__(
        self,
        directory: AgentPrincipalReader,
        store: ProjectTopologyStore,
        *,
        construction_mode: ConstructionMode = ConstructionMode.HOSTED_NATIVE,
    ) -> None:
        self._directory = directory
        self._store = store
        # The mode every team created here is written with (hosted-native
        # spec M7). A constructor argument rather than a request field on
        # purpose: the request is fingerprinted for idempotency, and a field
        # added to it would change the fingerprint of every replay of a
        # topology created before the column existed. The composition root
        # passes the deployment default (``settings.construction_mode_default``);
        # a per-repository choice made at onboarding has no persisted carrier
        # yet and reaches the row by the operator's one-time UPDATE (spec
        # §5.3.1).
        self._construction_mode = construction_mode

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
                    construction_mode=self._construction_mode,
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


class EnsureProjectAgentTopology:
    """Implements :class:`ProjectTopologyProvisioner` (see it for the why).

    A thin composition of two capabilities that already exist and had no
    caller between them: ``ProvisionRepositoryAgentTeam`` makes the principals,
    ``CreateProjectAgentTopology`` makes the topology out of them. The
    *decisions* this class owns are only these three:

    - a project that already has a topology is left alone, whatever it was
      asked for;
    - the organization is the acting leader's own, never a fresh one
      (``scripts/run_pipeline.py`` mints a new organization per run, which is
      right for a script bootstrapping from nothing and wrong for a console
      round inside a workspace that already exists);
    - one worker per repository, the smallest team that can be assigned a task.

    ``execution_mode`` and ``required_checkpoints`` are deliberately not
    decided here. A topology created on the way to work is not the place to
    decide a project's supervision policy; the admin face
    (``POST /projects/topologies``) still owns that.

    What that face now has is somewhere to leave the decision in advance, so
    these three fields — the two above plus ``human_grants`` — are no longer
    always the defaults: they are read from the project's
    :class:`~repomesh.modules.project.domain.TopologyPolicyDraft` when one
    exists. This class still decides nothing about supervision; it carries a
    decision across a gap that used to swallow it, because the policy is set
    minutes before there is a topology to hold it. A project with no draft
    takes the same defaults it always did, byte for byte — the request this
    class hands the creator is identical, fingerprint included, which is the
    whole of the backward compatibility story for the projects already
    materialized.
    """

    def __init__(
        self,
        store: ProjectTopologyStore,
        teams: RepositoryAgentTeamProvisioner,
        creator: CreateProjectAgentTopology,
        policy_drafts: TopologyPolicyDraftStore,
    ) -> None:
        self._store = store
        self._teams = teams
        self._creator = creator
        self._policy_drafts = policy_drafts

    async def ensure(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        organization_leader_id: UUID,
        repository_ids: tuple[UUID, ...],
        idempotency_key: str,
    ) -> ProjectAgentTopologyView:
        existing = await self._store.get(project_id)
        if existing is not None:
            return existing.to_view()
        if not repository_ids:
            raise ProjectTopologyViolation(
                "cannot create a project topology with no repositories"
            )

        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")

        # Read before provisioning: a store that cannot answer should cost
        # nothing, and provisioning mints agent principals that would outlive
        # the failure. A draft whose grant names a repository the plan later
        # dropped is *not* caught here — ``ProjectAgentTopology`` owns that
        # rule because only it has the teams to check against, and it refuses
        # loudly rather than dropping the grant (silently unwatching a project
        # that someone asked to watch).
        draft = await self._policy_drafts.get(project_id)

        assignments = []
        # Sorted so the same repository set produces the same team order, and
        # therefore the same command fingerprint on a replay.
        for repository_id in sorted(set(repository_ids), key=str):
            team = await self._teams.provision(
                organization_id=organization_id,
                organization_leader_id=organization_leader_id,
                repository_id=repository_id,
                idempotency_key=f"{key}:team:{repository_id.hex}",
            )
            assignments.append(
                RepositoryTeamAssignment(
                    repository_id=repository_id,
                    leader_agent_id=team.leader.id,
                    worker_agent_ids=tuple(worker.id for worker in team.workers),
                )
            )
        return await self._creator.execute(
            CreateProjectAgentTopologyRequest(
                organization_id=organization_id,
                project_id=project_id,
                organization_leader_id=organization_leader_id,
                repository_teams=tuple(assignments),
                # Spelled out even when there is no draft so the defaults are
                # visible next to the thing that overrides them. The values are
                # the field defaults, so the command fingerprint of a
                # draft-less project is unchanged and a replay still matches.
                execution_mode=(
                    draft.execution_mode if draft else ProjectExecutionMode.AUTO
                ),
                required_checkpoints=(
                    draft.required_checkpoints if draft else frozenset()
                ),
                # The draft already holds ``HumanProjectGrant``; it goes back
                # out through the request's input type rather than sneaking a
                # domain object past it, because the request type is what
                # ``CreateProjectAgentTopology`` publishes and one caller
                # bypassing it would make it a lie.
                human_grants=(
                    tuple(
                        HumanProjectGrantInput(
                            human_principal_id=grant.human_principal_id,
                            role=grant.role,
                            code_access=grant.code_access,
                            control_actions=grant.control_actions,
                            repository_id=grant.repository_id,
                            path_patterns=grant.path_patterns,
                        )
                        for grant in draft.human_grants
                    )
                    if draft
                    else ()
                ),
            ),
            idempotency_key=f"{key}:topology",
        )


class CreateAutomaticProjectTopology:
    """Resolve a project topology from the long-lived organization agent directory."""

    def __init__(
        self,
        directory: AgentPrincipalReader,
        creator: CreateProjectAgentTopology,
    ) -> None:
        self._directory = directory
        self._creator = creator

    async def execute(
        self,
        request: CreateAutomaticProjectTopologyRequest,
        *,
        idempotency_key: str,
    ) -> ProjectAgentTopologyView:
        if not request.repository_ids:
            raise ProjectTopologyViolation("at least one repository is required")
        if len(set(request.repository_ids)) != len(request.repository_ids):
            raise ProjectTopologyViolation("project repositories must be unique")

        principals = tuple(
            item
            for item in await self._directory.list_views()
            if item.organization_id == request.organization_id
            and item.status is AgentPrincipalStatus.ACTIVE
        )
        leaders = [
            item for item in principals if item.role is AgentRole.ORGANIZATION_LEADER
        ]
        if len(leaders) != 1:
            raise ProjectTopologyViolation(
                "organization requires exactly one active organization leader"
            )
        organization_leader = leaders[0]

        assignments = []
        for repository_id in request.repository_ids:
            repository_leaders = [
                item
                for item in principals
                if item.role is AgentRole.REPOSITORY_LEADER
                and item.repository_id == repository_id
                and item.leader_agent_id == organization_leader.id
            ]
            if len(repository_leaders) != 1:
                raise ProjectTopologyViolation(
                    f"repository {repository_id} requires exactly one active repository leader"
                )
            repository_leader = repository_leaders[0]
            workers = tuple(
                sorted(
                    (
                        item.id
                        for item in principals
                        if item.role is AgentRole.WORKER
                        and item.repository_id == repository_id
                        and item.leader_agent_id == repository_leader.id
                    ),
                    key=str,
                )
            )
            if not workers:
                raise ProjectTopologyViolation(
                    f"repository {repository_id} requires at least one active worker"
                )
            assignments.append(
                RepositoryTeamAssignment(
                    repository_id=repository_id,
                    leader_agent_id=repository_leader.id,
                    worker_agent_ids=workers,
                )
            )

        return await self._creator.execute(
            CreateProjectAgentTopologyRequest(
                organization_id=request.organization_id,
                project_id=request.project_id,
                organization_leader_id=organization_leader.id,
                repository_teams=tuple(assignments),
                execution_mode=request.execution_mode,
                required_checkpoints=request.required_checkpoints,
                human_grants=request.human_grants,
            ),
            idempotency_key=idempotency_key,
        )
