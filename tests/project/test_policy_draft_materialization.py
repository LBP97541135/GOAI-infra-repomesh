"""What ``EnsureProjectAgentTopology`` does with a policy draft, and without one.

Two paths, and the second is the one that matters most. Reading the draft is the
feature; leaving a draft-less project exactly as it was is the promise that the
projects already materialized are not touched by this change. A test that only
covered the first would let "no draft" quietly acquire a policy of its own.

Nothing here needs a database: the store is a protocol with three methods, and a
fake that answers ``None`` is a more precise statement of "this project has no
draft" than an empty table is.
"""

from uuid import UUID, uuid4

import pytest

from repomesh.modules.agent_directory.application import (
    CreateAgent,
    CreateAgentRequest,
    ProvisionRepositoryAgentTeam,
)
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.agent_directory.infrastructure import InMemoryAgentDirectory
from repomesh.modules.project import (
    CreateProjectAgentTopology,
    EnsureProjectAgentTopology,
)
from repomesh.modules.project.contracts import (
    CodeAccessLevel,
    HumanControlAction,
    HumanProjectRole,
    ProjectCheckpoint,
    ProjectExecutionMode,
)
from repomesh.modules.project.domain import HumanProjectGrant, TopologyPolicyDraft
from repomesh.modules.project.infrastructure import InMemoryProjectTopologyStore


class FakeTopologyPolicyDraftStore:
    """The three-method port, backed by a dict. ``get`` is the only one used here."""

    def __init__(self, *drafts: TopologyPolicyDraft) -> None:
        self.reads: list[UUID] = []
        self._drafts = {draft.project_id: draft for draft in drafts}

    async def get(self, project_id: UUID) -> TopologyPolicyDraft | None:
        self.reads.append(project_id)
        return self._drafts.get(project_id)

    async def upsert(self, draft: TopologyPolicyDraft) -> TopologyPolicyDraft:
        self._drafts[draft.project_id] = draft
        return draft

    async def delete(self, project_id: UUID) -> bool:
        return self._drafts.pop(project_id, None) is not None


async def _organization(directory: InMemoryAgentDirectory) -> tuple[UUID, UUID]:
    organization_id = uuid4()
    leader = await CreateAgent(directory).execute(
        CreateAgentRequest(
            organization_id=organization_id,
            role=AgentRole.ORGANIZATION_LEADER,
            agentteams_resource_name="policy-draft-org-leader",
        ),
        idempotency_key="policy-draft-org-leader",
    )
    return organization_id, leader.principal.id


def _ensure(drafts: FakeTopologyPolicyDraftStore, directory: InMemoryAgentDirectory):
    store = InMemoryProjectTopologyStore()
    return EnsureProjectAgentTopology(
        store,
        ProvisionRepositoryAgentTeam(directory),
        CreateProjectAgentTopology(directory, store),
        drafts,
    )


@pytest.mark.asyncio
async def test_materialized_topology_takes_its_policy_from_the_draft() -> None:
    directory = InMemoryAgentDirectory()
    organization_id, organization_leader_id = await _organization(directory)
    project_id = uuid4()
    repository_id = uuid4()
    human_principal_id = uuid4()
    grant = HumanProjectGrant(
        human_principal_id=human_principal_id,
        role=HumanProjectRole.PROJECT_SUPERVISOR,
        code_access=CodeAccessLevel.READ,
        control_actions=frozenset(
            {
                HumanControlAction.VIEW_DECISIONS,
                HumanControlAction.APPROVE_CHECKPOINT,
                HumanControlAction.REQUEST_CHANGES,
            }
        ),
    )
    checkpoints = frozenset(
        {ProjectCheckpoint.REPOSITORY_SCOPE, ProjectCheckpoint.DELIVERY}
    )
    drafts = FakeTopologyPolicyDraftStore(
        TopologyPolicyDraft(
            project_id=project_id,
            created_by=uuid4(),
            execution_mode=ProjectExecutionMode.SUPERVISED,
            required_checkpoints=checkpoints,
            human_grants=(grant,),
        )
    )

    topology = await _ensure(drafts, directory).ensure(
        organization_id=organization_id,
        project_id=project_id,
        organization_leader_id=organization_leader_id,
        repository_ids=(repository_id,),
        idempotency_key=f"project:{project_id}:topology",
    )

    assert drafts.reads == [project_id]
    assert topology.execution_mode is ProjectExecutionMode.SUPERVISED
    assert topology.required_checkpoints == checkpoints
    assert len(topology.human_grants) == 1
    carried = topology.human_grants[0]
    assert carried.human_principal_id == human_principal_id
    assert carried.role is HumanProjectRole.PROJECT_SUPERVISOR
    assert carried.code_access is CodeAccessLevel.READ
    assert carried.control_actions == grant.control_actions
    assert carried.repository_id is None


@pytest.mark.asyncio
async def test_project_without_a_draft_keeps_the_defaults_it_always_had() -> None:
    """The backward-compatibility lock.

    Every project materialized before drafts existed went through this path, and
    the three fields it produced are these three values. The store is still
    consulted — the read is unconditional — and answering ``None`` has to leave
    the result identical to the day there was nothing to consult.
    """

    directory = InMemoryAgentDirectory()
    organization_id, organization_leader_id = await _organization(directory)
    project_id = uuid4()
    drafts = FakeTopologyPolicyDraftStore()

    topology = await _ensure(drafts, directory).ensure(
        organization_id=organization_id,
        project_id=project_id,
        organization_leader_id=organization_leader_id,
        repository_ids=(uuid4(),),
        idempotency_key=f"project:{project_id}:topology",
    )

    assert drafts.reads == [project_id]
    assert topology.execution_mode is ProjectExecutionMode.AUTO
    assert topology.required_checkpoints == frozenset()
    assert topology.human_grants == ()
