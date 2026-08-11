from uuid import uuid4

import pytest

from repomesh.modules.delivery.application import DeliveryService
from repomesh.modules.delivery.contracts import (
    CIObservationCommand,
    ContractView,
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RepositoryCandidateInput,
)
from repomesh.modules.delivery.infrastructure import InMemoryChangeSetStore


class FakeContractCatalog:
    def __init__(self, contracts=()):
        self._contracts = tuple(contracts)
        self.queried_projects = []

    async def contracts_for_project(self, project_id):
        self.queried_projects.append(project_id)
        return self._contracts


def contract(producer, consumer, *, planned=False):
    return ContractView(
        producer=producer,
        consumer=consumer,
        interface="GET /prices",
        status="frozen",
        consumer_planned=planned,
    )


def producer_command(producer, *extra):
    return PrepareChangeSetCommand(
        organization_id=uuid4(),
        project_id=uuid4(),
        created_by_agent_id=uuid4(),
        title="Contract delivery",
        validation_snapshot_id=uuid4(),
        candidates=(
            RepositoryCandidateInput(
                repository_id=producer,
                task_id=uuid4(),
                commit_sha="a" * 40,
                base_sha="1" * 40,
                branch_name="repomesh/producer",
            ),
            *extra,
        ),
    )


async def make_ready(service, command):
    view = await service.prepare(command, idempotency_key=str(uuid4()))
    for index, candidate in enumerate(command.candidates):
        number = 70 + index
        await service.observe_pull_request(
            PullRequestObservationCommand(
                view.id,
                candidate.repository_id,
                number,
                f"https://example.test/pulls/{number}",
                candidate.commit_sha,
            )
        )
        await service.observe_ci(
            CIObservationCommand(
                view.id,
                candidate.repository_id,
                True,
                f"ci-{number}",
                "passed",
            )
        )
    return view


@pytest.mark.asyncio
async def test_contract_gate_refuses_producer_without_consumer_candidate() -> None:
    producer = uuid4()
    consumer = uuid4()
    catalog = FakeContractCatalog((contract(producer, consumer),))
    service = DeliveryService(InMemoryChangeSetStore(), contract_catalog=catalog)
    view = await make_ready(service, producer_command(producer))

    decision = await service.evaluate_merge_gate(view.id, producer)

    assert not decision.allowed
    assert "contract change is missing a consumer adapter candidate" in decision.reasons
    assert catalog.queried_projects == [view.project_id]


@pytest.mark.asyncio
async def test_contract_gate_allows_consumer_with_planned_adapter_task() -> None:
    producer = uuid4()
    consumer = uuid4()
    catalog = FakeContractCatalog((contract(producer, consumer, planned=True),))
    service = DeliveryService(InMemoryChangeSetStore(), contract_catalog=catalog)
    view = await make_ready(service, producer_command(producer))

    decision = await service.evaluate_merge_gate(view.id, producer)

    assert decision.allowed
    assert decision.reasons == ()


@pytest.mark.asyncio
async def test_contract_gate_allows_consumer_already_in_changeset() -> None:
    producer = uuid4()
    consumer = uuid4()
    command = producer_command(
        producer,
        RepositoryCandidateInput(
            repository_id=consumer,
            task_id=uuid4(),
            commit_sha="b" * 40,
            base_sha="2" * 40,
            branch_name="repomesh/consumer",
            depends_on=(producer,),
        ),
    )
    catalog = FakeContractCatalog((contract(producer, consumer),))
    service = DeliveryService(InMemoryChangeSetStore(), contract_catalog=catalog)
    view = await make_ready(service, command)

    producer_decision = await service.evaluate_merge_gate(view.id, producer)

    assert producer_decision.allowed
    assert producer_decision.reasons == ()


@pytest.mark.asyncio
async def test_contract_gate_off_when_catalog_absent() -> None:
    producer = uuid4()
    service = DeliveryService(InMemoryChangeSetStore())
    view = await make_ready(service, producer_command(producer))

    decision = await service.evaluate_merge_gate(view.id, producer)

    assert decision.allowed
    assert decision.reasons == ()


@pytest.mark.asyncio
async def test_contract_gate_no_contracts_leaves_behavior_unchanged() -> None:
    producer = uuid4()
    catalog = FakeContractCatalog()
    service = DeliveryService(InMemoryChangeSetStore(), contract_catalog=catalog)
    view = await make_ready(service, producer_command(producer))

    decision = await service.evaluate_merge_gate(view.id, producer)

    assert decision.allowed
    assert decision.reasons == ()


@pytest.mark.asyncio
async def test_contract_gate_ignores_repositories_that_are_not_producers() -> None:
    repository = uuid4()
    unrelated_producer = uuid4()
    consumer = uuid4()
    catalog = FakeContractCatalog((contract(unrelated_producer, consumer),))
    service = DeliveryService(InMemoryChangeSetStore(), contract_catalog=catalog)
    view = await make_ready(service, producer_command(repository))

    decision = await service.evaluate_merge_gate(view.id, repository)

    assert decision.allowed
    assert decision.reasons == ()
