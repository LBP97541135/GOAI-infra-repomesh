"""Seed the topology a live ``/bridge/materialize`` run needs.

The materialize endpoint takes a project and an Organization Leader as given: it turns an
integrated plan into Specs and an execution plan, it does not create the organization that owns
them. Project intake has no HTTP entry point yet, so a live run of the batch loop needs this
script first.

It provisions two repository teams on purpose. One repository proves the chain end to end; two
repositories in two batches are what prove the batch gate, which is the part of the loop that
only exists since the plan-execution work.

Everything is keyed by ``SCENARIO_KEY`` and idempotent: re-running reuses the same organization,
project and teams instead of stacking duplicates.
"""

import asyncio
import json
import os
from uuid import uuid4

from repomesh.bootstrap.app import build_default_container
from repomesh.modules.agent_directory.application import (
    CreateAgent,
    CreateAgentRequest,
    CreateRepositoryAgentTeam,
    CreateRepositoryAgentTeamRequest,
)
from repomesh.modules.agent_directory.contracts import AgentCreated, AgentRole
from repomesh.modules.project.contracts import ProjectTeamRuntimeStatus
from repomesh.modules.project.domain import ProjectAgentTopology, RepositoryTeam
from repomesh.modules.repository_intelligence.application import RegisterRepository
from repomesh.modules.repository_intelligence.domain import AutoCard, RepositoryProfile

SCENARIO_KEY = os.environ.get("SCENARIO_KEY", "two-repo-plan-20260807")

REPOSITORIES = (
    {
        "name": "checkout-live",
        "url": "/runner-workspaces/fixtures/live-checkout-e2e-20260807",
        "description": "Checkout pricing fixture",
        "top_dirs": ("src/checkout_fixture", "tests"),
        "apis": ("calculate_total",),
        "team": "repomesh-checkout-team",
        "leader": "repomesh-checkout-leader",
        "workers": ("repomesh-checkout-worker-01",),
        "paths": ("src/checkout_fixture/pricing.py", "tests/**", "scripts/run_tests.py"),
        "room_id": "!17HBaqnghYlqQgL7f6:matrix-local.agentteams.io:18080",
        "leader_room_id": "!Gjy2GkhLoJwFbJz3eb:matrix-local.agentteams.io:18080",
    },
    {
        "name": "billing-live",
        "url": "/runner-workspaces/fixtures/live-billing-e2e-20260807",
        "description": "Billing invoice fixture",
        "top_dirs": ("src/billing_fixture", "tests"),
        "apis": ("invoice_total",),
        "team": "repomesh-billing-team",
        "leader": "repomesh-billing-leader",
        "workers": ("repomesh-billing-worker-01",),
        "paths": ("src/billing_fixture/invoice.py", "tests/**", "scripts/run_tests.py"),
        "room_id": "!MgsARaf0pvMMuZkT8g:matrix-local.agentteams.io:18080",
        "leader_room_id": "!YpTtCe0vSBlq3wdC2J:matrix-local.agentteams.io:18080",
    },
)


async def main() -> None:
    container = build_default_container()
    if container.agent_team_messenger is None:
        raise RuntimeError(
            "Matrix messenger is not configured; the plan would be materialized without "
            "notifying any Worker"
        )
    try:
        organization_id = uuid4()
        project_id = uuid4()

        # The AgentTeams Manager "default" is a global singleton and the directory enforces one
        # principal per AgentTeams resource, so an earlier live run on this machine already owns
        # the Organization Leader. Reuse it instead of colliding on that binding; REUSE_LEADER_KEY
        # names it, and a machine without one falls through and creates its own.
        leader_key = f"{SCENARIO_KEY}:organization-leader"
        candidate_keys = [leader_key]
        if reuse_key := os.environ.get("REUSE_LEADER_KEY"):
            candidate_keys.append(reuse_key)
        existing_leader = None
        for key in candidate_keys:
            if found := await container.agent_directory.get_by_idempotency_key(key):
                existing_leader, leader_key = found, key
                break
        if existing_leader:
            organization_id = existing_leader[0].organization_id
            organization_leader = AgentCreated(existing_leader[0].to_view(), leader_key)
        else:
            organization_leader = await CreateAgent(container.agent_directory).execute(
                CreateAgentRequest(
                    organization_id=organization_id,
                    role=AgentRole.ORGANIZATION_LEADER,
                    agentteams_resource_name="default",
                ),
                idempotency_key=leader_key,
            )

        reuse_team_keys = json.loads(os.environ.get("REUSE_TEAM_KEYS", "{}"))
        catalog = {item.url: item for item in await container.repository_catalog.list()}
        teams = []
        for spec in REPOSITORIES:
            repository = catalog.get(spec["url"]) or RepositoryProfile(
                name=spec["name"],
                url=spec["url"],
                description=spec["description"],
                topics=("live-e2e",),
                languages=("Python",),
                auto_card=AutoCard(
                    top_dirs=spec["top_dirs"],
                    exposed_apis=spec["apis"],
                ),
            )
            if spec["url"] not in catalog:
                await RegisterRepository(container.repository_catalog).execute(repository)
            # An AgentTeams resource can back only one business principal, so a team an earlier
            # live run already registered must be reused under its original key rather than
            # registered again under this scenario's key.
            team_key = reuse_team_keys.get(
                spec["name"], f"{SCENARIO_KEY}:{spec['name']}:team"
            )
            team = await CreateRepositoryAgentTeam(container.agent_directory).execute(
                CreateRepositoryAgentTeamRequest(
                    organization_id=organization_id,
                    organization_leader_id=organization_leader.principal.id,
                    repository_id=repository.id,
                    leader_agentteams_resource_name=spec["leader"],
                    worker_agentteams_resource_names=spec["workers"],
                    worker_responsibility_paths=spec["paths"],
                ),
                idempotency_key=team_key,
            )
            teams.append((spec, repository, team))

        topology_key = f"{SCENARIO_KEY}:topology"
        existing_topology = await container.project_topology_store.get_by_idempotency_key(
            topology_key
        )
        if existing_topology:
            topology = existing_topology[0]
            project_id = topology.project_id
        else:
            topology = ProjectAgentTopology(
                organization_id=organization_id,
                project_id=project_id,
                organization_leader_id=organization_leader.principal.id,
                repository_teams=tuple(
                    RepositoryTeam(
                        project_id=project_id,
                        repository_id=repository.id,
                        leader_agent_id=team.leader.id,
                        worker_agent_ids=(team.workers[0].id,),
                        agentteams_team_name=spec["team"],
                        runtime_status=ProjectTeamRuntimeStatus.READY,
                        room_id=spec["room_id"],
                        leader_room_id=spec["leader_room_id"],
                    )
                    for spec, repository, team in teams
                ),
            )
            await container.project_topology_store.add(
                topology,
                idempotency_key=topology_key,
                request_fingerprint=f"sha256:{'0' * 64}",
            )

        print(
            json.dumps(
                {
                    "scenario_key": SCENARIO_KEY,
                    "organization_id": str(organization_id),
                    "project_id": str(project_id),
                    "leader_agent_id": str(organization_leader.principal.id),
                    "repositories": [
                        {
                            # The catalog owns the name the plan's task_dag must use: an
                            # earlier run may already have registered this URL under its own.
                            "name": repository.name,
                            "repository_id": str(repository.id),
                            "leader_agent_id": str(team.leader.id),
                            "worker_agent_id": str(team.workers[0].id),
                        }
                        for spec, repository, team in teams
                    ],
                },
                indent=2,
            )
        )
    finally:
        await container.close()


if __name__ == "__main__":
    asyncio.run(main())
