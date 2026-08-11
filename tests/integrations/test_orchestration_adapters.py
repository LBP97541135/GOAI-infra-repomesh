from types import SimpleNamespace
from uuid import uuid4

import pytest

from repomesh.integrations.orchestration import DeliveryStateAdapter
from repomesh.modules.delivery.contracts import RepositoryDeliveryStatus


class FakeDeliveryService:
    def __init__(self, change_sets: tuple[object, ...]) -> None:
        self._change_sets = change_sets
        self.requested_projects = []

    async def find_by_project(self, project_id):  # noqa: ANN001
        self.requested_projects.append(project_id)
        return self._change_sets


@pytest.mark.asyncio
async def test_delivery_state_adapter_projects_the_latest_merged_state() -> None:
    project_id = uuid4()
    repository_id = uuid4()
    service = FakeDeliveryService(
        (
            SimpleNamespace(
                repositories=(
                    SimpleNamespace(
                        repository_id=repository_id,
                        status=RepositoryDeliveryStatus.CI_PENDING,
                    ),
                )
            ),
            SimpleNamespace(
                repositories=(
                    SimpleNamespace(
                        repository_id=repository_id,
                        status=RepositoryDeliveryStatus.MERGED,
                    ),
                )
            ),
        )
    )

    states = await DeliveryStateAdapter(service).repository_states(project_id)

    assert service.requested_projects == [project_id]
    assert len(states) == 1
    assert states[0].repository_id == repository_id
    assert states[0].merged is True


@pytest.mark.asyncio
async def test_delivery_state_adapter_keeps_independent_repository_states() -> None:
    first_repository_id = uuid4()
    second_repository_id = uuid4()
    service = FakeDeliveryService(
        (
            SimpleNamespace(
                repositories=(
                    SimpleNamespace(
                        repository_id=first_repository_id,
                        status=RepositoryDeliveryStatus.MERGED,
                    ),
                    SimpleNamespace(
                        repository_id=second_repository_id,
                        status=RepositoryDeliveryStatus.REVIEW_PENDING,
                    ),
                )
            ),
        )
    )

    states = await DeliveryStateAdapter(service).repository_states(uuid4())

    assert {state.repository_id: state.merged for state in states} == {
        first_repository_id: True,
        second_repository_id: False,
    }
