import os
from uuid import uuid4

import pytest

from repomesh.modules.agent_directory.application import CreateAgent, CreateAgentRequest
from repomesh.modules.agent_directory.application.repository_team import (
    ProvisionRepositoryAgentTeam,
)
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.agent_directory.infrastructure import PostgresAgentDirectory
from repomesh.modules.capability_management.contracts import (
    CROSS_REPO_TEST_TEAM_PROFILE,
)
from repomesh.modules.project import (
    CreateProjectAgentTopology,
    EnsureProjectAgentTopology,
)
from repomesh.modules.project.infrastructure import (
    PostgresProjectTopologyStore,
    PostgresTopologyPolicyDraftStore,
)
from repomesh.modules.repository_intelligence.application import RegisterRepository
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.repository_intelligence.infrastructure import PostgresRepositoryCatalog
from repomesh.persistence import Database
from repomesh.persistence.outbox import OutboxStore

POSTGRES_URL = os.getenv("REPOMESH_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="REPOMESH_TEST_POSTGRES_URL is not configured"),
]


@pytest.mark.asyncio
async def test_postgres_repository_transaction_and_outbox() -> None:
    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    catalog = PostgresRepositoryCatalog(database)
    outbox = OutboxStore(database)
    unique = uuid4().hex
    profile = RepositoryProfile(
        name=f"integration-{unique}",
        url=f"https://github.com/example/{unique}",
        description="PostgreSQL integration test",
    )
    try:
        await RegisterRepository(catalog).execute(profile)

        assert await catalog.get(profile.id) == profile
        updated = await catalog.update_verification(
            profile.id,
            test_commands=("python scripts/run_tests.py",),
            test_paths=("tests/**",),
        )
        assert updated is not None
        assert updated.test_commands == ("python scripts/run_tests.py",)
        assert updated.test_paths == ("tests/**",)
        assert await catalog.get(profile.id) == updated
        assert any(
            message.payload.get("url") == profile.url
            for message in await outbox.pending()
        )

        profiled = await catalog.update_capability_profile(
            profile.id, capability_profile="cross-repo-test-team"
        )
        assert profiled is not None
        assert profiled.capability_profile == "cross-repo-test-team"
        assert (await catalog.get(profile.id)).capability_profile == (
            "cross-repo-test-team"
        )
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_postgres_test_team_supply_chain_converges_by_id() -> None:
    """Group A's postgres tier (spec S-5): the supply chain on the real driver.

    The in-memory tier walks the materialize endpoint over sqlite; what only
    a real PostgreSQL proves is the two ends this line actually added or
    leans on there: ``list()`` surfacing ``capability_profile`` through the
    real column — the read ``_ensure_topology``'s append rule filters on —
    and the provisioner converging instead of rebuilding, over the real
    stores. Identity is asserted by id, never by count (AC-A3's discipline);
    membership is asserted on this test's own rows only, because the database
    is shared and other runs leave profiled repositories behind.
    """

    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    catalog = PostgresRepositoryCatalog(database)
    directory = PostgresAgentDirectory(database)
    store = PostgresProjectTopologyStore(database)
    unique = uuid4().hex
    organization_id = uuid4()
    project_id = uuid4()
    sibling_project_id = uuid4()
    try:
        plan_repo = RepositoryProfile(
            name=f"itg-plan-{unique}",
            url=f"https://github.com/example/itg-plan-{unique}",
        )
        test_repo = RepositoryProfile(
            name=f"itg-test-{unique}",
            url=f"https://github.com/example/itg-test-{unique}",
            capability_profile=CROSS_REPO_TEST_TEAM_PROFILE,
        )
        await RegisterRepository(catalog).execute(plan_repo)
        await RegisterRepository(catalog).execute(test_repo)

        # The read side of the switch: the append rule works off ``list()``,
        # so the profile has to surface there, not only on ``get()``.
        by_id = {row.id: row.capability_profile for row in await catalog.list()}
        assert by_id[test_repo.id] == CROSS_REPO_TEST_TEAM_PROFILE
        assert by_id[plan_repo.id] is None

        leader = await CreateAgent(directory).execute(
            CreateAgentRequest(
                organization_id=organization_id,
                role=AgentRole.ORGANIZATION_LEADER,
                agentteams_resource_name=f"itg-org-{unique}",
            ),
            idempotency_key=f"itg-org-{unique}",
        )
        provisioner = EnsureProjectAgentTopology(
            store,
            ProvisionRepositoryAgentTeam(directory),
            CreateProjectAgentTopology(directory, store),
            PostgresTopologyPolicyDraftStore(database),
        )

        first = await provisioner.ensure(
            organization_id=organization_id,
            project_id=project_id,
            organization_leader_id=leader.principal.id,
            repository_ids=(plan_repo.id, test_repo.id),
            idempotency_key=f"itg-round-{unique}",
        )
        team = next(
            item
            for item in first.repository_teams
            if item.repository_id == test_repo.id
        )

        # Same key, same project: the early exit returns the built topology.
        replayed = await provisioner.ensure(
            organization_id=organization_id,
            project_id=project_id,
            organization_leader_id=leader.principal.id,
            repository_ids=(plan_repo.id, test_repo.id),
            idempotency_key=f"itg-round-{unique}",
        )
        replayed_team = next(
            item
            for item in replayed.repository_teams
            if item.repository_id == test_repo.id
        )
        assert replayed_team.id == team.id
        assert replayed_team.agentteams_team_name == team.agentteams_team_name

        # A second project over the same repositories converges on the same
        # principals — the repository leader is a directory singleton, and a
        # rebuild would have violated it rather than reused it.
        sibling = await provisioner.ensure(
            organization_id=organization_id,
            project_id=sibling_project_id,
            organization_leader_id=leader.principal.id,
            repository_ids=(plan_repo.id, test_repo.id),
            idempotency_key=f"itg-sibling-{unique}",
        )
        sibling_team = next(
            item
            for item in sibling.repository_teams
            if item.repository_id == test_repo.id
        )
        assert sibling_team.leader_agent_id == team.leader_agent_id
        assert set(sibling_team.worker_agent_ids) == set(team.worker_agent_ids)
    finally:
        await database.dispose()
