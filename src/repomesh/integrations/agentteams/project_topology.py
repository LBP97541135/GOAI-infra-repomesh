from dataclasses import dataclass, replace
from uuid import UUID

from repomesh.modules.agent_directory.contracts import AgentPrincipalReader
from repomesh.modules.agent_runtime.ports.agent_team import (
    AgentTeamControlPlane,
    TeamMemberProjection,
    TeamProjection,
    TeamRole,
)
from repomesh.modules.project.contracts import (
    ProjectAgentTopologyView,
    ProjectTeamRuntimeStatus,
)
from repomesh.modules.project.domain import ProjectTopologyViolation, RepositoryTeam
from repomesh.modules.project.ports import ProjectTopologyStore


@dataclass(frozen=True, slots=True)
class _LeaderAdoption:
    """What one look at a repository leader's worker document settles.

    A pair rather than two return values because the two facts have to come
    from the *same* observation to be consistent with each other: the Team a
    leader belongs to and whether that leader has a container are both read out
    of one document, and splitting them into two reads would let a team be
    adopted under a name seen at one moment and a mode seen at another.
    """

    team_name: str
    external_leader: bool


class ReconcileProjectAgentTopology:
    """Create AgentTeams Teams from already registered native Manager/Worker resources.

    Repository-scoped, since A-8 (contract §8.7.2). A Team owns its members
    exclusively and a repository's principals are directory singletons, so
    every project touching a repository shares one Team and its two rooms. The
    name this asks for is therefore derived from the repository, and — because
    the rows minted before A-8 each carry a name of their own — the reconcile
    *asks the controller first* rather than trusting the row.
    """

    def __init__(
        self,
        directory: AgentPrincipalReader,
        store: ProjectTopologyStore,
        control_plane: AgentTeamControlPlane,
    ) -> None:
        self._directory = directory
        self._store = store
        self._control_plane = control_plane

    async def execute(self, project_id: UUID) -> ProjectAgentTopologyView:
        topology = await self._store.get(project_id)
        if topology is None:
            raise ProjectTopologyViolation(f"project topology does not exist: {project_id}")
        if await self._directory.get_view(topology.organization_leader_id) is None:
            raise ProjectTopologyViolation("organization leader binding does not exist")
        reconciled_teams = []
        for team in topology.repository_teams:
            members = []
            for agent_id, role in (
                (team.leader_agent_id, TeamRole.LEADER),
                *((worker_id, TeamRole.WORKER) for worker_id in team.worker_agent_ids),
            ):
                principal = await self._directory.get_view(agent_id)
                if principal is None:
                    raise ProjectTopologyViolation(f"agent binding does not exist: {agent_id}")
                members.append(
                    TeamMemberProjection(principal.agentteams_resource_name, role)
                )
            adoption = await self._adopt_leader(team, leader=members[0].name)
            name = adoption.team_name
            runtime_team = await self._control_plane.ensure_team(
                TeamProjection(
                    name=name,
                    members=tuple(members),
                    description=(
                        f"RepoMesh project {project_id} repository {team.repository_id}"
                    ),
                ),
                idempotency_key=f"project:{project_id}:repository:{team.repository_id}:team",
            )
            reconciled_teams.append(
                team.with_runtime(
                    status=(
                        ProjectTeamRuntimeStatus.READY
                        if runtime_team.phase.lower() in {"ready", "active", "running"}
                        else ProjectTeamRuntimeStatus.PENDING
                    ),
                    room_id=runtime_team.team_room_id,
                    leader_room_id=runtime_team.leader_room_id,
                    agentteams_team_name=name,
                ).with_adopted_leader(external=adoption.external_leader)
            )
        topology = replace(topology, repository_teams=tuple(reconciled_teams))
        await self._store.save(topology)
        return topology.to_view()

    async def _adopt_leader(self, team: RepositoryTeam, *, leader: str) -> "_LeaderAdoption":
        """Where this repository's Team already lives, and who leads it from where.

        Two facts, one read, because the controller answers both in the same
        document and the read was already being made.

        Adoption, not surgery (A-8). The row's stored name is deliberately not
        consulted: before A-8 it was minted from the row's own id, so three
        issues on one repository carried three different names for a resource
        that can only exist once. Trusting it is what produced the deadlock —
        the first issue to project got the Team, and every later issue asked
        the controller for a second Team over the same leader and got
        ``400 ... is already a member of Team ...`` forever.

        So the controller is asked instead, through the one read that answers
        it without provoking that refusal: a worker document names its Team
        (``GET /api/v1/workers/{name}``, verified on the deployed v1.2.0
        controller). The repository leader is the anchor because it is the one
        principal a repository's Team must contain, and it is a directory
        singleton — whatever Team holds it *is* this repository's Team,
        whatever it happens to be called.

        Falling through to the canonical repository name covers the two cases
        with nothing to adopt: a repository nobody has projected yet, and a
        leader the controller has not seen. Both then converge on the same
        name from any project, which is what stops a fresh row from repeating
        the defect.

        One GET per team per projection. Not cached: the whole point is to
        observe a Team another project may have created since.

        *Who leads it from where* is the second fact, and it is the whole of
        PR 5.5B's activation path (adjudication D-2). The same worker document
        carries ``containerManaged``, so a leader whose container the controller
        does not own is an external Repository Leader — one with a Bridge
        serving it, provisioned through the route PR 5.5A added and confirmed by
        the same preflight. A team whose leader runs outside the cluster is a
        team whose leader can plan for itself, which is what ``leader`` mode
        means, so adoption of the identity and activation of the mode are one
        decision taken from one observation.

        Reusing this read rather than adding one is not only cheaper. A second
        lookup could answer differently from this one — the two would be
        separated by ``ensure_team`` — and a team could then be adopted under a
        Team name observed at one instant and a mode observed at another.

        ``container_managed is False`` and not a truthiness test: the field is
        ``None`` when the answer did not come from a controller that knows it,
        and unknown must never be read as external. Every other case — no worker
        document, a managed leader, an answer without the field — is "no
        external leader observed this pass", which the domain's one-way latch
        deliberately does not treat as a reason to demote anybody.
        """

        canonical = RepositoryTeam.canonical_agentteams_team_name(team.repository_id)
        existing = await self._control_plane.get_worker(leader)
        return _LeaderAdoption(
            team_name=(existing.team if existing else None) or canonical,
            external_leader=existing is not None and existing.container_managed is False,
        )
