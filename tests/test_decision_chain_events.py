"""decision-chain v0.1 —— 五个决策节点在同一条链上的贯通测试。

§2.1 把一条决策链定义为五个节点：classification → confirmation →
integration → task → pr，全部挂在同一个 project_id（E1 根）与
organization_id（L1）下。本文件验证五个节点各自发射契约 §3.2 事件，
且事件携带一致的 L1/L2 身份。

前三个节点走真实 API + Postgres（与 tests/api/test_issue_discovery 同一种
驱动方式）；后两个节点用内存 fakes 驱动 TaskOrchestrator / DeliveryService
——它们的事件发射点已在模块测试中单独覆盖，这里断言的是"链"。
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from uuid import UUID, uuid4

from api.test_issue_discovery import (
    ANALYSIS_OK,
    CANDIDATES,
    HEADERS,
    INTEGRATION,
    ScriptedLLM,
    _configure,
    _confirmation,
    _create_issue,
    _seed,
    _walk_to_classification,
)
from fastapi.testclient import TestClient
from sqlalchemy import select

from repomesh.bootstrap.app import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.delivery.application import DeliveryService
from repomesh.modules.delivery.contracts import (
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RepositoryCandidateInput,
)
from repomesh.modules.delivery.infrastructure import (
    InMemoryChangeSetStore,
    InMemoryDeliveryAuditLog,
)
from repomesh.modules.project.contracts import (
    ProjectAgentTopologyView,
    ProjectTeamRuntimeStatus,
    RepositoryTeamView,
)
from repomesh.modules.task_orchestration.application import TaskOrchestrator
from repomesh.modules.task_orchestration.contracts import AssignTaskCommand
from repomesh.modules.task_orchestration.infrastructure import InMemoryTaskStore
from repomesh.persistence.models.platform import AuditEventRecord
from repomesh.settings import get_settings
from repomesh.shared.events import ActorType, EventEnvelope

APPROVAL_REASON = "范围合理"


@contextmanager
def _discovery_session(container: ApplicationContainer, leader_id: UUID):
    """Walk one issue to a plan; the three discovery events land in Postgres.

    Yields ``(project_id, evidence_version)``. The approval key is replayed
    before the yield so the test can assert the event emitted exactly once.
    """

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = _walk_to_classification(client, leader_id, issue_id)
        evidence = chain.read()["classification_evidence_version"]
        body = {
            "decided_by_agent_id": str(leader_id),
            "idempotency_key": "chain-approval-01",
            "decision": "approved",
            "reason": APPROVAL_REASON,
            "evidence_version": evidence,
        }
        approved = client.post(
            f"/api/v1/issues/{issue_id}/discovery/approval",
            json=body,
            headers=HEADERS,
        )
        assert approved.status_code == 200, approved.text
        assert chain.run("plan")["status"] == "succeeded"
        # 同一 approval 键重放：approve 提前 return，不产生第二个事件。
        replayed = client.post(
            f"/api/v1/issues/{issue_id}/discovery/approval",
            json=body,
            headers=HEADERS,
        )
        assert replayed.status_code == 200, replayed.text
        yield UUID(issue_id), evidence


def _fetch_events(
    container: ApplicationContainer, event_type: str
) -> list[AuditEventRecord]:
    async def fetch() -> list[AuditEventRecord]:
        async with container.database.transaction() as session:
            result = await session.execute(
                select(AuditEventRecord)
                .where(AuditEventRecord.event_type == event_type)
                .order_by(AuditEventRecord.occurred_at)
            )
            return list(result.scalars())

    return asyncio.run(fetch())


class _FakeAgentDirectory:
    def __init__(self, principals: tuple[AgentPrincipalView, ...]) -> None:
        self._by_id = {principal.id: principal for principal in principals}

    async def get_view(self, agent_id: UUID) -> AgentPrincipalView | None:
        return self._by_id.get(agent_id)


class _FakeTopologies:
    def __init__(self, topology: ProjectAgentTopologyView) -> None:
        self._topology = topology

    async def get_view(self, project_id: UUID) -> ProjectAgentTopologyView | None:
        return self._topology if project_id == self._topology.project_id else None


class _RecordingCollaboration:
    def __init__(self) -> None:
        self.sent: list[tuple[object, str]] = []

    async def send(self, command, *, idempotency_key: str) -> None:
        self.sent.append((command, idempotency_key))


def _plan_task(
    organization_id: UUID, project_id: UUID, leader_id: UUID
) -> tuple[EventEnvelope, UUID]:
    """Node 4: assign one task under the chain's identity; return (event, task_id).

    Assigns to a repository leader so no worker publication is involved. The
    same key is replayed once: TasksPlanned must be emitted exactly once.
    """

    repository_id = uuid4()
    repo_leader_id = uuid4()
    directory = _FakeAgentDirectory(
        (
            AgentPrincipalView(
                id=leader_id,
                organization_id=organization_id,
                role=AgentRole.ORGANIZATION_LEADER,
                leader_agent_id=None,
                repository_id=None,
                responsibility_paths=(),
                agentteams_resource_name="chain-org-leader",
                status=AgentPrincipalStatus.ACTIVE,
            ),
            AgentPrincipalView(
                id=repo_leader_id,
                organization_id=organization_id,
                role=AgentRole.REPOSITORY_LEADER,
                leader_agent_id=leader_id,
                repository_id=repository_id,
                responsibility_paths=("src/**",),
                agentteams_resource_name="chain-repo-leader",
                status=AgentPrincipalStatus.ACTIVE,
            ),
        )
    )
    topologies = _FakeTopologies(
        ProjectAgentTopologyView(
            id=uuid4(),
            organization_id=organization_id,
            project_id=project_id,
            organization_leader_id=leader_id,
            repository_teams=(
                RepositoryTeamView(
                    id=uuid4(),
                    project_id=project_id,
                    repository_id=repository_id,
                    leader_agent_id=repo_leader_id,
                    worker_agent_ids=(),
                    agentteams_team_name="chain-team",
                    runtime_status=ProjectTeamRuntimeStatus.READY,
                    room_id=None,
                    leader_room_id=None,
                ),
            ),
        )
    )
    audit = InMemoryDeliveryAuditLog()
    orchestrator = TaskOrchestrator(
        directory,
        topologies,
        InMemoryTaskStore(),
        _RecordingCollaboration(),
        audit=audit,
    )
    command = AssignTaskCommand(
        organization_id=organization_id,
        project_id=project_id,
        repository_id=repository_id,
        assigned_by_agent_id=leader_id,
        assignee_agent_id=repo_leader_id,
        title="改通知模板",
        instruction="按集成决策调整通知模板",
        acceptance=("通过测试",),
    )
    created = asyncio.run(orchestrator.assign(command, idempotency_key="chain-task-key"))
    asyncio.run(orchestrator.assign(command, idempotency_key="chain-task-key"))  # replay
    assert len(audit.events) == 1, "replaying the assignment key must not re-emit"
    return audit.events[0], created.id


def _observe_pr(
    organization_id: UUID,
    project_id: UUID,
    leader_id: UUID,
    task_id: UUID,
) -> tuple[tuple[EventEnvelope, EventEnvelope], UUID, UUID]:
    """Node 5: observe PRs for one ChangeSet; return (events, change_set_id, repository_id).

    PR 42 is observed twice — the replay must stay silent — and PR 43 then
    produces a second event, one node per PR.
    """

    repository_id = uuid4()
    head_sha = "a" * 40
    audit = InMemoryDeliveryAuditLog()
    service = DeliveryService(InMemoryChangeSetStore(), audit=audit)
    change_set = asyncio.run(
        service.prepare(
            PrepareChangeSetCommand(
                organization_id=organization_id,
                project_id=project_id,
                created_by_agent_id=leader_id,
                title="订单通知修复",
                validation_snapshot_id=None,
                candidates=(
                    RepositoryCandidateInput(
                        repository_id=repository_id,
                        task_id=task_id,
                        commit_sha=head_sha,
                        base_sha="b" * 40,
                        branch_name="fix/notify",
                    ),
                ),
            ),
            idempotency_key="chain-delivery-key",
        )
    )

    def observe(number: int) -> None:
        asyncio.run(
            service.observe_pull_request(
                PullRequestObservationCommand(
                    change_set_id=change_set.id,
                    repository_id=repository_id,
                    pull_request_number=number,
                    pull_request_url=f"https://github.com/acme/ts-notify/pull/{number}",
                    head_sha=head_sha,
                )
            )
        )

    observe(42)
    observe(42)  # same PR → replay, no second event
    assert len(audit.events) == 1, "re-observing the same PR must not re-emit"
    observe(43)  # a new PR is a new node
    assert len(audit.events) == 2
    return (audit.events[0], audit.events[1]), change_set.id, repository_id


def test_decision_chain_emits_five_events_on_one_project(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """The five decision nodes share one project_id and one organization_id."""

    _configure(monkeypatch)
    organization_id, leader_id, _, _ = _seed(application_container)
    container = replace(
        application_container,
        llm_client=ScriptedLLM(
            ANALYSIS_OK,
            CANDIDATES,
            _confirmation("REQUIRED"),
            _confirmation("MAYBE"),
            INTEGRATION,
        ),
    )
    try:
        with _discovery_session(container, leader_id) as (project_id, evidence):
            classified, = _fetch_events(container, "ClassificationDecided")
            confirmed, = _fetch_events(container, "ConfirmationDecided")
            integrated, = _fetch_events(container, "IntegrationDecided")
            tasks_planned, task_id = _plan_task(organization_id, project_id, leader_id)
            (observed_first, _), change_set_id, repository_id = _observe_pr(
                organization_id, project_id, leader_id, task_id
            )
            events = (classified, confirmed, integrated, tasks_planned, observed_first)

            # 链不变式（§2.1）：五个节点共享一个 L1、一个 L2。
            for event in events:
                assert event.organization_id == organization_id
                assert event.project_id == project_id
            assert len({event.event_id for event in events}) == 5

            # §3.2 — ClassificationDecided
            assert classified.event_type == "ClassificationDecided"
            assert classified.actor_type == ActorType.AGENT
            assert classified.actor_id == str(leader_id)
            assert classified.aggregate_type == "Project"
            assert classified.aggregate_id == project_id
            tiering = classified.payload["classification"]
            assert tiering["required"] == ["ts-notify"]
            assert tiering["maybe"] == ["ts-order"]
            assert tiering["excluded"] == []
            assert tiering["effective_tiers"] == {
                "ts-notify": "REQUIRED",
                "ts-order": "MAYBE",
            }
            assert tiering["evidence_version"] == evidence
            assert tiering["supplemented_repository_ids"] == []
            assert classified.payload["affected_repository_ids"] == ["ts-notify", "ts-order"]

            # §3.2 — ConfirmationDecided（会话内已重放 approval 键）
            assert confirmed.event_type == "ConfirmationDecided"
            assert confirmed.actor_id == str(leader_id)
            assert confirmed.payload["approval"] == {
                "state": "approved",
                "decided_by_agent_id": str(leader_id),
                "reason": APPROVAL_REASON,
            }
            assert confirmed.payload["evidence_version"] == evidence
            assert confirmed.payload["adjustments"] == []
            assert confirmed.payload["affected_repository_ids"] == ["ts-notify", "ts-order"]
            assert len(_fetch_events(container, "ConfirmationDecided")) == 1

            # §3.2 — IntegrationDecided
            assert integrated.event_type == "IntegrationDecided"
            assert integrated.payload["execution_batches"] == [
                {"index": 0, "repository_ids": ["ts-notify"]},
                {"index": 1, "repository_ids": ["ts-order"]},
            ]
            assert integrated.payload["contracts"] == [
                "ts-notify->ts-order:POST /api/v1/notify"
            ]
            assert integrated.payload["affected_repository_ids"] == ["ts-notify", "ts-order"]

            # §3.2 — TasksPlanned
            assert tasks_planned.event_type == "TasksPlanned"
            assert tasks_planned.actor_type == ActorType.AGENT
            assert tasks_planned.actor_id == str(leader_id)
            assert tasks_planned.aggregate_type == "Task"
            assert tasks_planned.aggregate_id == task_id
            assert tasks_planned.task_id == task_id
            assert tasks_planned.payload["upstream_step"] == "integration"
            assert tasks_planned.payload["task"]["task_id"] == str(task_id)
            assert tasks_planned.payload["task"]["parent_task_id"] is None

            # §3.2 — PullRequestObserved
            assert observed_first.event_type == "PullRequestObserved"
            assert observed_first.actor_type == ActorType.SERVICE
            assert observed_first.aggregate_type == "ChangeSet"
            assert observed_first.aggregate_id == change_set_id
            assert observed_first.payload["change_set_id"] == str(change_set_id)
            assert observed_first.payload["repository_id"] == str(repository_id)
            assert observed_first.payload["pull_request_number"] == 42
            assert observed_first.payload["task_ids"] == [str(task_id)]

            # 三个 discovery 节点按链序落库。
            assert classified.occurred_at <= confirmed.occurred_at <= integrated.occurred_at
    finally:
        get_settings.cache_clear()


def test_tasks_planned_carries_the_chain_identity_and_emits_once() -> None:
    """Node 4 in isolation: envelope, payload shape, and replay silence."""

    organization_id, project_id, leader_id = uuid4(), uuid4(), uuid4()
    event, task_id = _plan_task(organization_id, project_id, leader_id)

    assert event.event_type == "TasksPlanned"
    assert event.organization_id == organization_id
    assert event.project_id == project_id
    assert event.actor_type == ActorType.AGENT
    assert event.actor_id == str(leader_id)
    assert event.aggregate_type == "Task"
    assert event.aggregate_id == task_id
    assert event.task_id == task_id
    assert event.payload["schema_version"] == 1
    assert event.payload["upstream_step"] == "integration"
    task = event.payload["task"]
    assert task["task_id"] == str(task_id)
    assert task["parent_task_id"] is None
    assert task["title"] == "改通知模板"


def test_pull_request_observed_is_emitted_once_per_pr_with_changeset_identity() -> None:
    """Node 5 in isolation: E9 反查（org/project 来自 ChangeSet）与重放语义."""

    organization_id, project_id, leader_id, task_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    (first, second), change_set_id, repository_id = _observe_pr(
        organization_id, project_id, leader_id, task_id
    )

    assert first.event_type == "PullRequestObserved"
    assert first.organization_id == organization_id
    assert first.project_id == project_id
    assert first.actor_type == ActorType.SERVICE
    assert first.actor_id == str(leader_id)
    assert first.aggregate_type == "ChangeSet"
    assert first.aggregate_id == change_set_id
    assert first.payload["schema_version"] == 1
    assert first.payload["change_set_id"] == str(change_set_id)
    assert first.payload["repository_id"] == str(repository_id)
    assert first.payload["pull_request_number"] == 42
    assert first.payload["pull_request_url"] == "https://github.com/acme/ts-notify/pull/42"
    assert first.payload["task_ids"] == [str(task_id)]

    assert second.payload["pull_request_number"] == 43
    assert second.organization_id == organization_id
    assert second.project_id == project_id
    assert second.event_id != first.event_id
