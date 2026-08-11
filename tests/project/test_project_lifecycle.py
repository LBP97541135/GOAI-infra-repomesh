from uuid import uuid4

import pytest

from repomesh.modules.project import (
    CodeAccessLevel,
    ControlProjectCommand,
    HumanControlAction,
    HumanProjectRole,
    ProjectLifecycleService,
    ProjectOperationalStatus,
)
from repomesh.modules.project.domain import (
    HumanProjectGrant,
    ProjectAgentTopology,
    ProjectTopologyViolation,
    RepositoryTeam,
)
from repomesh.modules.project.infrastructure import InMemoryProjectTopologyStore


async def lifecycle_fixture():
    project_id = uuid4()
    human_id = uuid4()
    store = InMemoryProjectTopologyStore()
    topology = ProjectAgentTopology(
        organization_id=uuid4(),
        project_id=project_id,
        organization_leader_id=uuid4(),
        repository_teams=(
            RepositoryTeam(
                project_id=project_id,
                repository_id=uuid4(),
                leader_agent_id=uuid4(),
                worker_agent_ids=(uuid4(),),
            ),
        ),
        human_grants=(
            HumanProjectGrant(
                human_principal_id=human_id,
                role=HumanProjectRole.PROJECT_SUPERVISOR,
                code_access=CodeAccessLevel.READ,
                control_actions=frozenset(
                    {
                        HumanControlAction.PAUSE_PROJECT,
                        HumanControlAction.RESUME_PROJECT,
                        HumanControlAction.CANCEL_PROJECT,
                    }
                ),
            ),
        ),
    )
    await store.add(topology, idempotency_key="lifecycle", request_fingerprint="v1")
    return project_id, human_id, store


@pytest.mark.asyncio
async def test_pause_resume_and_cancel_are_persisted() -> None:
    project_id, human_id, store = await lifecycle_fixture()
    service = ProjectLifecycleService(store)
    paused = await service.control(
        ControlProjectCommand(project_id, human_id, HumanControlAction.PAUSE_PROJECT)
    )
    assert paused.operational_status is ProjectOperationalStatus.PAUSED
    resumed = await service.control(
        ControlProjectCommand(project_id, human_id, HumanControlAction.RESUME_PROJECT)
    )
    assert resumed.operational_status is ProjectOperationalStatus.ACTIVE
    cancelled = await service.control(
        ControlProjectCommand(project_id, human_id, HumanControlAction.CANCEL_PROJECT)
    )
    assert cancelled.operational_status is ProjectOperationalStatus.CANCELLED
    with pytest.raises(ProjectTopologyViolation, match="cannot be resumed"):
        await service.control(
            ControlProjectCommand(project_id, human_id, HumanControlAction.RESUME_PROJECT)
        )


@pytest.mark.asyncio
async def test_ungranted_human_cannot_pause_project() -> None:
    project_id, _, store = await lifecycle_fixture()
    with pytest.raises(ProjectTopologyViolation, match="membership_denied"):
        await ProjectLifecycleService(store).control(
            ControlProjectCommand(
                project_id, uuid4(), HumanControlAction.PAUSE_PROJECT
            )
        )
