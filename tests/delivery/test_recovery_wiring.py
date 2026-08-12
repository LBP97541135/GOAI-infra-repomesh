"""The composition root actually runs the rollback Saga.

The Saga is exercised through the real container so that a regression in the
wiring is visible, not only a regression in the gateway. Nothing here reaches
GitHub: the adapter sits on an ``httpx.MockTransport`` and every scenario stops
short of the Git data plane.
"""

import asyncio
from dataclasses import replace
from uuid import UUID, uuid4

import httpx
import pytest

from repomesh.bootstrap.container import ApplicationContainer
from repomesh.integrations.scm import (
    GovernedRecoveryActionHandler,
    RecoveryConflictTaskCreator,
    RecoverySagaExecutor,
)
from repomesh.integrations.scm.github import GitHubAdapter
from repomesh.integrations.scm.recovery import RecoveryExecutionContext, RevertConflict
from repomesh.modules.delivery import DeliveryService, InMemoryChangeSetStore
from repomesh.modules.delivery.contracts import (
    CIObservationCommand,
    PlanRecoveryCommand,
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RecoveryActionStatus,
    RecoveryTrigger,
    RepositoryCandidateInput,
)
from repomesh.modules.project.contracts import (
    ProjectAgentTopologyView,
    ProjectTeamRuntimeStatus,
    RepositoryTeamView,
)
from repomesh.modules.repository_intelligence.domain.models import RepositoryProfile
from repomesh.modules.task_orchestration.contracts import AssignTaskCommand, TaskStatus, TaskView

CANDIDATE_SHA = "a" * 40


class Tasks:
    """In-memory TaskAssignmentGateway that honours the idempotency key."""

    def __init__(self) -> None:
        self.keys: list[str] = []
        self.commands: list[AssignTaskCommand] = []
        self.by_key: dict[str, TaskView] = {}

    async def assign(self, command: AssignTaskCommand, *, idempotency_key: str) -> TaskView:
        self.keys.append(idempotency_key)
        self.commands.append(command)
        if idempotency_key not in self.by_key:
            self.by_key[idempotency_key] = TaskView(
                id=uuid4(),
                organization_id=command.organization_id,
                project_id=command.project_id,
                repository_id=command.repository_id,
                parent_task_id=command.parent_task_id,
                assigned_by_agent_id=command.assigned_by_agent_id,
                assignee_agent_id=command.assignee_agent_id,
                title=command.title,
                instruction=command.instruction,
                acceptance=command.acceptance,
                status=TaskStatus.ASSIGNED,
                result_summary=None,
                version=1,
            )
        return self.by_key[idempotency_key]


class Topology:
    def __init__(self, view: ProjectAgentTopologyView | None) -> None:
        self._view = view

    async def get_view(self, project_id: UUID) -> ProjectAgentTopologyView | None:
        return self._view


class ConflictingGateway:
    async def close_pull_request(self, context) -> str:
        raise RevertConflict("close conflicts")

    async def create_revert_pull_request(self, context) -> str:
        raise RevertConflict("git revert conflicts with main")

    async def merge_revert_pull_request(self, context) -> str:
        raise RevertConflict("merge conflicts")

    async def revalidate(self, context) -> str:
        return "revalidated"


def topology_view(
    project_id: UUID, repository_id: UUID, *, workers: tuple[UUID, ...]
) -> ProjectAgentTopologyView:
    return ProjectAgentTopologyView(
        id=uuid4(),
        organization_id=uuid4(),
        project_id=project_id,
        organization_leader_id=uuid4(),
        repository_teams=(
            RepositoryTeamView(
                id=uuid4(),
                project_id=project_id,
                repository_id=repository_id,
                leader_agent_id=uuid4(),
                worker_agent_ids=workers,
                agentteams_team_name="pricing",
                runtime_status=ProjectTeamRuntimeStatus.READY,
                room_id=None,
                leader_room_id=None,
            ),
        ),
    )


async def pr_open_change_set(service: DeliveryService, repository_id: UUID):
    """A candidate that reached PR-open but never merged, then needs rollback."""

    change_set = await service.prepare(
        PrepareChangeSetCommand(
            uuid4(),
            uuid4(),
            uuid4(),
            "Rollback delivery",
            uuid4(),
            (
                RepositoryCandidateInput(
                    repository_id,
                    uuid4(),
                    CANDIDATE_SHA,
                    "b" * 40,
                    "repomesh/pricing",
                ),
            ),
        ),
        idempotency_key=f"rollback-{repository_id}",
    )
    await service.observe_pull_request(
        PullRequestObservationCommand(
            change_set.id,
            repository_id,
            42,
            "https://github.com/acme/pricing/pull/42",
            CANDIDATE_SHA,
        )
    )
    await service.observe_ci(
        CIObservationCommand(change_set.id, repository_id, False, "ci", "failed")
    )
    return await service.plan_recovery(
        PlanRecoveryCommand(change_set.id, RecoveryTrigger.CI_FAILED, "CI failed")
    )


def test_recovery_conflict_gateway_needs_agentteams(
    application_container: ApplicationContainer,
) -> None:
    assert application_container.recovery_conflict_task_gateway() is None

    with_tasks = replace(application_container, task_report_gateway=Tasks())

    assert isinstance(with_tasks.recovery_conflict_task_gateway(), RecoveryConflictTaskCreator)


@pytest.mark.asyncio
async def test_container_saga_drives_a_rollback_action_through_github(
    application_container: ApplicationContainer,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "number": 42,
                "html_url": "https://github.com/acme/pricing/pull/42",
                "state": "closed" if request.method == "PATCH" else "open",
                "draft": False,
                "merged_at": None,
                "head": {"ref": "repomesh/pricing", "sha": CANDIDATE_SHA},
                "base": {"ref": "main", "sha": "b" * 40},
                "mergeable": True,
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    container = replace(
        application_container,
        scm_adapter=GitHubAdapter(lambda repo: "installation-token", client=client),
    )
    profile = RepositoryProfile(name="pricing", url="https://github.com/acme/pricing")
    await container.repository_catalog.add(profile)
    service = container.delivery_service()
    change_set = await pr_open_change_set(service, profile.id)

    saga = container.recovery_saga_executor()
    await saga.run_once()

    current = await service.get(change_set.id)
    action = current.recovery_plans[-1].actions[0]
    assert action.status is RecoveryActionStatus.SUCCEEDED
    assert "#42 is closed" in action.detail
    assert [request.method for request in requests] == ["GET", "PATCH"]
    await client.aclose()


@pytest.mark.asyncio
async def test_revert_conflict_opens_one_worker_task_and_holds_the_saga() -> None:
    repository_id = uuid4()
    service = DeliveryService(InMemoryChangeSetStore())
    change_set = await pr_open_change_set(service, repository_id)
    worker_id = uuid4()
    tasks = Tasks()
    conflicts = RecoveryConflictTaskCreator(
        tasks,
        Topology(topology_view(change_set.project_id, repository_id, workers=(worker_id,))),
    )
    saga = RecoverySagaExecutor(
        service, GovernedRecoveryActionHandler(ConflictingGateway()), conflicts
    )

    await saga.run_once()
    await saga.run_once()

    parked = await service.get(change_set.id)
    actions = parked.recovery_plans[-1].actions
    assert actions[0].status is RecoveryActionStatus.WAITING_WORKER
    # The rollback never runs ahead of an unresolved conflict.
    assert actions[1].status is RecoveryActionStatus.PENDING
    # A parked action is not re-executed, so the Worker is asked exactly once.
    assert len(tasks.keys) == 1
    assert tasks.keys[0] == f"revert-conflict:{change_set.id}:{actions[0].id}"
    assert tasks.commands[0].assignee_agent_id == worker_id
    assert "Conflict evidence: close conflicts" in tasks.commands[0].instruction
    assert str(next(iter(tasks.by_key.values())).id) in actions[0].detail


@pytest.mark.asyncio
async def test_revert_conflict_without_a_worker_is_refused() -> None:
    repository_id = uuid4()
    service = DeliveryService(InMemoryChangeSetStore())
    change_set = await pr_open_change_set(service, repository_id)
    conflicts = RecoveryConflictTaskCreator(
        Tasks(),
        Topology(topology_view(change_set.project_id, repository_id, workers=())),
    )
    saga = RecoverySagaExecutor(
        service, GovernedRecoveryActionHandler(ConflictingGateway()), conflicts
    )

    with pytest.raises(ValueError, match="no Worker to resolve a revert conflict"):
        await saga.run_once()


@pytest.mark.asyncio
async def test_revert_conflict_without_a_topology_is_refused() -> None:
    repository_id = uuid4()
    service = DeliveryService(InMemoryChangeSetStore())
    await pr_open_change_set(service, repository_id)
    conflicts = RecoveryConflictTaskCreator(Tasks(), Topology(None))
    saga = RecoverySagaExecutor(
        service, GovernedRecoveryActionHandler(ConflictingGateway()), conflicts
    )

    with pytest.raises(ValueError, match="project topology is unavailable"):
        await saga.run_once()


@pytest.mark.asyncio
async def test_saga_background_loop_starts_and_stops_cleanly() -> None:
    repository_id = uuid4()
    service = DeliveryService(InMemoryChangeSetStore())
    change_set = await pr_open_change_set(service, repository_id)
    tasks = Tasks()
    conflicts = RecoveryConflictTaskCreator(
        tasks,
        Topology(topology_view(change_set.project_id, repository_id, workers=(uuid4(),))),
    )
    saga = RecoverySagaExecutor(
        service,
        GovernedRecoveryActionHandler(ConflictingGateway()),
        conflicts,
        interval_seconds=0.01,
    )

    await saga.start()
    await asyncio.sleep(0.05)
    await saga.close()

    parked = await service.get(change_set.id)
    assert parked.recovery_plans[-1].actions[0].status is RecoveryActionStatus.WAITING_WORKER
    assert len(set(tasks.keys)) == 1


@pytest.mark.asyncio
async def test_replayed_conflict_reuses_the_same_worker_task() -> None:
    """The Saga can die between assigning the task and recording waiting_worker.

    On restart the same action conflicts again, and the Worker must not receive
    a second copy of the repair.
    """

    repository_id = uuid4()
    service = DeliveryService(InMemoryChangeSetStore())
    change_set = await pr_open_change_set(service, repository_id)
    tasks = Tasks()
    conflicts = RecoveryConflictTaskCreator(
        tasks,
        Topology(topology_view(change_set.project_id, repository_id, workers=(uuid4(),))),
    )
    context = RecoveryExecutionContext(change_set, change_set.recovery_plans[-1].actions[0])

    first = await conflicts.create_for_conflict(context, "git revert conflicts with main")
    second = await conflicts.create_for_conflict(context, "git revert conflicts with main")

    assert first == second
    assert len(set(tasks.keys)) == 1
