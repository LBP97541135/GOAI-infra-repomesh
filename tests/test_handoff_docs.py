"""Tests for repository handoff documents (仓库对接文档).

Covers content building, the service lifecycle (generate → decide →
supersede), the PostgreSQL-backed store (against the SQLite test database),
and the PlanExecutionBridge integration on both the materialize and the
replan path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.change_orchestration import (
    PlanExecutionBridge,
    StartedExecutionPlan,
)
from repomesh.modules.project.contracts import ProjectAgentTopologyView
from repomesh.modules.repository_intelligence.application.confirmation import (
    ConfirmationResult,
    ConfirmationSummary,
    RepositoryPlan,
)
from repomesh.modules.repository_intelligence.application.handoff_docs import (
    HandoffDoc,
    HandoffDocError,
    HandoffDocService,
    HandoffDocStatus,
    build_doc_content,
    render_markdown,
)
from repomesh.modules.repository_intelligence.application.plan_integration import (
    ContractSpec,
    IntegratedPlan,
    TaskNode,
)
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.specification.contracts import (
    SpecificationVersionView,
    SpecificationView,
)
from repomesh.modules.specification.domain import SpecificationStatus
from repomesh.modules.task_orchestration.contracts import (
    ExecutionPlanStatus,
    ExecutionPlanView,
)

PROJECT_ID = UUID("11111111-1111-1111-1111-111111111111")
LEADER_ID = UUID("22222222-2222-2222-2222-222222222222")
ORG_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _make_plan() -> IntegratedPlan:
    return IntegratedPlan(
        engineering_spec="Unify the payment retry pipeline across services.",
        contracts=[
            ContractSpec(
                producer="ts-payment-service",
                consumer="ts-order-service",
                interface="POST /payments/retry",
                agreement="order calls payment with the same idempotency key",
            ),
            ContractSpec(
                producer="ts-notification-service",
                consumer="ts-payment-service",
                interface="POST /notifications/send",
                agreement="payment notifies the user on terminal payment states",
            ),
        ],
        task_dag=[
            TaskNode(
                repository="ts-notification-service",
                instruction="expose the send endpoint",
                depends_on=(),
                parallelizable_with=("ts-order-service",),
            ),
            TaskNode(
                repository="ts-payment-service",
                instruction="add the retry endpoint",
                depends_on=("ts-notification-service",),
                parallelizable_with=(),
            ),
            TaskNode(
                repository="ts-order-service",
                instruction="call the new retry endpoint",
                depends_on=("ts-payment-service",),
                parallelizable_with=("ts-notification-service",),
            ),
        ],
        execution_batches=[["ts-notification-service", "ts-order-service"], ["ts-payment-service"]],
    )


# ---------------------------------------------------------------------------
# Content building
# ---------------------------------------------------------------------------


def test_build_doc_content_roles_and_batches() -> None:
    content = build_doc_content(
        repository="ts-payment-service",
        plan_version=1,
        requirement="make payments retryable",
        plan=_make_plan(),
    )

    assert content["plan_version"] == 1
    assert content["requirement"] == "make payments retryable"
    # Producer of the retry endpoint, consumer of the notification endpoint.
    assert content["interfaces"]["produced"] == [
        {
            "interface": "POST /payments/retry",
            "consumer": "ts-order-service",
            "agreement": "order calls payment with the same idempotency key",
        }
    ]
    assert content["interfaces"]["consumed"] == [
        {
            "interface": "POST /notifications/send",
            "producer": "ts-notification-service",
            "agreement": "payment notifies the user on terminal payment states",
        }
    ]
    assert content["related_repositories"] == ["ts-notification-service", "ts-order-service"]
    # payment runs in the second batch.
    assert content["adjustment"]["execution_batch"] == 2
    assert content["adjustment"]["instruction"] == "add the retry endpoint"


def test_build_doc_content_merges_confirmation_details() -> None:
    content = build_doc_content(
        repository="ts-order-service",
        plan_version=2,
        requirement="make payments retryable",
        plan=_make_plan(),
        details={
            "summary": "add the retry call with an idempotency key",
            "changed_apis": ("POST /payments/retry",),
            "changed_modules": ("order",),
            "risk": "high",
        },
    )
    adjustment = content["adjustment"]
    assert adjustment["summary"] == "add the retry call with an idempotency key"
    assert adjustment["changed_apis"] == ["POST /payments/retry"]
    assert adjustment["changed_modules"] == ["order"]
    assert adjustment["risk"] == "high"


def test_render_markdown_contains_decision_block() -> None:
    doc = HandoffDoc(
        id=uuid4(),
        project_id=PROJECT_ID,
        plan_version=1,
        repository="ts-order-service",
        status=HandoffDocStatus.PENDING,
        content=build_doc_content(
            repository="ts-order-service",
            plan_version=1,
            requirement="make payments retryable",
            plan=_make_plan(),
        ),
        created_at=datetime.now(UTC),
        created_by_agent_id=LEADER_ID,
    )
    markdown = render_markdown(doc)
    assert "# 对接文档：ts-order-service" in markdown
    assert "## 人工确认" in markdown
    assert "待确认" in markdown
    assert "POST /payments/retry" in markdown


# ---------------------------------------------------------------------------
# Service (in-memory store)
# ---------------------------------------------------------------------------


class FakeHandoffDocStore:
    """In-memory :class:`HandoffDocStore` port implementation."""

    def __init__(self) -> None:
        self._docs: dict[UUID, HandoffDoc] = {}

    async def save(self, doc: HandoffDoc) -> HandoffDoc:
        self._docs[doc.id] = doc
        return doc

    async def get(self, doc_id: UUID) -> HandoffDoc | None:
        return self._docs.get(doc_id)

    async def list_docs(
        self,
        *,
        project_id: UUID,
        plan_version: int | None = None,
        repository: str | None = None,
        status: HandoffDocStatus | None = None,
    ) -> list[HandoffDoc]:
        docs = [d for d in self._docs.values() if d.project_id == project_id]
        if plan_version is not None:
            docs = [d for d in docs if d.plan_version == plan_version]
        if repository is not None:
            docs = [d for d in docs if d.repository == repository]
        if status is not None:
            docs = [d for d in docs if d.status is status]
        return sorted(docs, key=lambda d: (-d.plan_version, d.repository))

    async def supersede_for_repos(
        self,
        *,
        project_id: UUID,
        repositories: Sequence[str],
        superseded_by_version: int,
    ) -> int:
        count = 0
        for doc_id, doc in list(self._docs.items()):
            if (
                doc.project_id == project_id
                and doc.repository in repositories
                and doc.status is not HandoffDocStatus.SUPERSEDED
                and doc.plan_version != superseded_by_version
            ):
                self._docs[doc_id] = HandoffDoc(
                    id=doc.id,
                    project_id=doc.project_id,
                    plan_version=doc.plan_version,
                    repository=doc.repository,
                    status=HandoffDocStatus.SUPERSEDED,
                    content=doc.content,
                    created_at=doc.created_at,
                    created_by_agent_id=doc.created_by_agent_id,
                    decided_by_agent_id=doc.decided_by_agent_id,
                    decision_reason=doc.decision_reason,
                    superseded_by_version=superseded_by_version,
                )
                count += 1
        return count


async def test_generate_for_plan_creates_one_pending_doc_per_repo() -> None:
    store = FakeHandoffDocStore()
    service = HandoffDocService(store)

    docs = await service.generate_for_plan(
        project_id=PROJECT_ID,
        plan_version=1,
        plan=_make_plan(),
        requirement="make payments retryable",
        created_by_agent_id=LEADER_ID,
    )

    assert [d.repository for d in docs] == [
        "ts-notification-service",
        "ts-payment-service",
        "ts-order-service",
    ]
    assert all(d.status is HandoffDocStatus.PENDING for d in docs)
    assert all(d.plan_version == 1 for d in docs)
    assert all(d.created_by_agent_id == LEADER_ID for d in docs)
    assert all(d.content["adjustment"]["execution_batch"] for d in docs)


async def test_generate_for_plan_scoped_to_repositories_supersedes_old_docs() -> None:
    store = FakeHandoffDocStore()
    service = HandoffDocService(store)
    await service.generate_for_plan(
        project_id=PROJECT_ID,
        plan_version=1,
        plan=_make_plan(),
        requirement="make payments retryable",
        created_by_agent_id=LEADER_ID,
    )

    docs_v2 = await service.generate_for_plan(
        project_id=PROJECT_ID,
        plan_version=2,
        plan=_make_plan(),
        requirement="make payments retryable",
        created_by_agent_id=LEADER_ID,
        repositories=["ts-payment-service"],
    )

    assert [d.repository for d in docs_v2] == ["ts-payment-service"]
    assert all(d.plan_version == 2 for d in docs_v2)
    assert all(d.status is HandoffDocStatus.PENDING for d in docs_v2)

    all_docs = await service.list_docs(project_id=PROJECT_ID)
    by_repo: dict[str, HandoffDoc] = {d.repository: d for d in all_docs if d.plan_version == 1}
    assert by_repo["ts-payment-service"].status is HandoffDocStatus.SUPERSEDED
    assert by_repo["ts-payment-service"].superseded_by_version == 2
    # Unaffected repos keep their v1 documents untouched.
    assert by_repo["ts-order-service"].status is HandoffDocStatus.PENDING


async def test_decide_approves_pending_doc() -> None:
    store = FakeHandoffDocStore()
    service = HandoffDocService(store)
    (doc,) = await service.generate_for_plan(
        project_id=PROJECT_ID,
        plan_version=1,
        plan=_make_plan(),
        requirement="make payments retryable",
        created_by_agent_id=LEADER_ID,
        repositories=["ts-payment-service"],
    )
    owner = UUID("33333333-3333-3333-3333-333333333333")

    decided = await service.decide(
        doc_id=doc.id,
        approved=True,
        decided_by_agent_id=owner,
        reason="interface change looks fine",
    )

    assert decided.status is HandoffDocStatus.APPROVED
    assert decided.decision == "approved"
    assert decided.decided_by_agent_id == owner
    assert decided.decision_reason == "interface change looks fine"


async def test_decide_rejects_pending_doc() -> None:
    store = FakeHandoffDocStore()
    service = HandoffDocService(store)
    (doc,) = await service.generate_for_plan(
        project_id=PROJECT_ID,
        plan_version=1,
        plan=_make_plan(),
        requirement="make payments retryable",
        created_by_agent_id=LEADER_ID,
        repositories=["ts-payment-service"],
    )

    decided = await service.decide(
        doc_id=doc.id,
        approved=False,
        decided_by_agent_id=LEADER_ID,
        reason="breaks our SLA",
    )

    assert decided.status is HandoffDocStatus.REJECTED
    assert decided.decision == "rejected"


async def test_decide_raises_on_superseded_doc() -> None:
    store = FakeHandoffDocStore()
    service = HandoffDocService(store)
    (doc,) = await service.generate_for_plan(
        project_id=PROJECT_ID,
        plan_version=1,
        plan=_make_plan(),
        requirement="make payments retryable",
        created_by_agent_id=LEADER_ID,
        repositories=["ts-payment-service"],
    )
    await service.generate_for_plan(
        project_id=PROJECT_ID,
        plan_version=2,
        plan=_make_plan(),
        requirement="make payments retryable",
        created_by_agent_id=LEADER_ID,
        repositories=["ts-payment-service"],
    )

    with pytest.raises(HandoffDocError):
        await service.decide(
            doc_id=doc.id,
            approved=True,
            decided_by_agent_id=LEADER_ID,
        )


async def test_decide_raises_on_unknown_doc() -> None:
    service = HandoffDocService(FakeHandoffDocStore())
    with pytest.raises(HandoffDocError):
        await service.decide(
            doc_id=uuid4(),
            approved=True,
            decided_by_agent_id=LEADER_ID,
        )


# ---------------------------------------------------------------------------
# PostgreSQL-backed store (against the SQLite test database)
# ---------------------------------------------------------------------------


async def test_postgres_store_round_trip_and_filters(
    application_container: ApplicationContainer,
) -> None:
    store = application_container.handoff_doc_store()
    service = HandoffDocService(store)

    docs = await service.generate_for_plan(
        project_id=PROJECT_ID,
        plan_version=1,
        plan=_make_plan(),
        requirement="make payments retryable",
        created_by_agent_id=LEADER_ID,
    )

    # Upsert by id (decision) survives a round trip.
    await service.decide(
        doc_id=docs[0].id,
        approved=True,
        decided_by_agent_id=LEADER_ID,
        reason="ok",
    )
    fetched = await store.get(docs[0].id)
    assert fetched is not None
    assert fetched.status is HandoffDocStatus.APPROVED
    assert fetched.decided_by_agent_id == LEADER_ID
    assert fetched.decision_reason == "ok"

    # Filters.
    assert len(await service.list_docs(project_id=PROJECT_ID)) == 3
    assert len(await service.list_docs(project_id=PROJECT_ID, plan_version=1)) == 3
    assert len(
        await service.list_docs(
            project_id=PROJECT_ID, status=HandoffDocStatus.APPROVED
        )
    ) == 1
    assert len(
        await service.list_docs(
            project_id=PROJECT_ID, repository="ts-payment-service"
        )
    ) == 1

    # Supersede bulk-updates only the affected repos.
    count = await store.supersede_for_repos(
        project_id=PROJECT_ID,
        repositories=["ts-payment-service"],
        superseded_by_version=2,
    )
    assert count == 1
    payment_doc = next(d for d in docs if d.repository == "ts-payment-service")
    superseded = await store.get(payment_doc.id)
    assert superseded is not None
    assert superseded.status is HandoffDocStatus.SUPERSEDED
    assert superseded.superseded_by_version == 2


# ---------------------------------------------------------------------------
# Bridge integration
# ---------------------------------------------------------------------------


class RecordingHandoffDocGenerator:
    """Port implementation that records the bridge's handoff calls."""

    def __init__(self) -> None:
        self.generate_calls: list[dict] = []
        self.supersede_calls: list[dict] = []
        self._counter = 0

    async def generate_for_plan(
        self,
        *,
        project_id: UUID,
        plan_version: int,
        plan: IntegratedPlan,
        requirement: str,
        created_by_agent_id: UUID | None = None,
        repositories: Sequence[str] | None = None,
        details: Mapping[str, Mapping] | None = None,
    ) -> list[HandoffDoc]:
        self.generate_calls.append(
            {
                "project_id": project_id,
                "plan_version": plan_version,
                "plan": plan,
                "requirement": requirement,
                "created_by_agent_id": created_by_agent_id,
                "repositories": list(repositories) if repositories else None,
                "details": details,
            }
        )
        targets = list(repositories) if repositories else [
            t.repository for t in plan.task_dag
        ]
        docs = []
        for repository in targets:
            self._counter += 1
            docs.append(
                HandoffDoc(
                    id=UUID(int=self._counter),
                    project_id=project_id,
                    plan_version=plan_version,
                    repository=repository,
                    status=HandoffDocStatus.PENDING,
                    content={"repository": repository},
                    created_at=datetime.now(UTC),
                    created_by_agent_id=created_by_agent_id,
                )
            )
        return docs

    async def supersede_for_repos(
        self,
        *,
        project_id: UUID,
        repositories: Sequence[str],
        superseded_by_version: int,
    ) -> int:
        self.supersede_calls.append(
            {
                "project_id": project_id,
                "repositories": list(repositories),
                "superseded_by_version": superseded_by_version,
            }
        )
        return 0


class StubSpecService:
    def __init__(self) -> None:
        self._counter = 0

    async def create(self, command, *, idempotency_key):  # noqa: ANN001
        self._counter += 1
        return SpecificationView(
            id=UUID(int=self._counter),
            organization_id=command.organization_id,
            project_id=command.project_id,
            kind=command.kind,
            status=SpecificationStatus.DRAFT,
            title=command.title,
            repository_id=None,
            task_id=None,
            owner_agent_id=command.created_by_agent_id,
            revision=1,
            current_version=SpecificationVersionView(
                id=UUID(int=self._counter + 10_000),
                specification_id=UUID(int=self._counter),
                version=1,
                content_hash="abc123",
                created_by_agent_id=command.created_by_agent_id,
            ),
        )


class StubPlanStarter:
    async def start_plan(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        created_by_agent_id: UUID,
        batches: Sequence[Sequence],
        idempotency_key: str,
    ) -> StartedExecutionPlan:
        return StartedExecutionPlan(
            plan=ExecutionPlanView(
                id=UUID(int=7777),
                organization_id=organization_id,
                project_id=project_id,
                status=ExecutionPlanStatus.IN_PROGRESS,
                current_batch_index=0,
                batches=tuple(tuple(b) for b in batches),
            ),
            tasks=(),
        )


class StubTopologyReader:
    def __init__(self, topology: ProjectAgentTopologyView) -> None:
        self._topology = topology

    async def get_view(self, project_id: UUID) -> ProjectAgentTopologyView:
        return self._topology


class StubCatalog:
    """Returns a list of RepositoryProfile with name → id mapping."""

    def __init__(self, name_to_id: dict[str, UUID]) -> None:
        self._name_to_id = name_to_id

    async def list(self) -> list:
        return [
            RepositoryProfile(name=name, url=f"https://github.com/test/{name}", id=rid)
            for name, rid in self._name_to_id.items()
        ]


class StubSuperseder:
    async def supersede(self, command, *, idempotency_key):  # noqa: ANN001
        return command


class EmptyProjectTaskReader:
    async def list_project_tasks(self, project_id):  # noqa: ANN001
        return ()


class StubIntegrationService:
    def __init__(self, plan: IntegratedPlan) -> None:
        self._plan = plan

    def integrate(self, requirement: str, summary: ConfirmationSummary) -> IntegratedPlan:
        return self._plan


def _make_topology(project_id: UUID) -> ProjectAgentTopologyView:
    return ProjectAgentTopologyView(
        id=uuid4(),
        organization_id=ORG_ID,
        project_id=project_id,
        organization_leader_id=LEADER_ID,
        repository_teams=(),
    )


def _make_bridge(recorder: RecordingHandoffDocGenerator) -> PlanExecutionBridge:
    return PlanExecutionBridge(
        specifications=StubSpecService(),
        plans=StubPlanStarter(),
        topologies=StubTopologyReader(_make_topology(PROJECT_ID)),
        catalog=StubCatalog(
            {
                "ts-payment-service": UUID("aaaa1111-1111-1111-1111-111111111111"),
                "ts-order-service": UUID("bbbb1111-1111-1111-1111-111111111111"),
            }
        ),
        handoff_docs=recorder,
        superseder=StubSuperseder(),
        task_reader=EmptyProjectTaskReader(),
    )


async def test_materialize_generates_handoff_docs() -> None:
    recorder = RecordingHandoffDocGenerator()
    bridge = _make_bridge(recorder)

    result = await bridge.materialize(
        plan=_make_plan(),
        requirement="make payments retryable",
        project_id=PROJECT_ID,
        leader_agent_id=LEADER_ID,
        idempotency_prefix="tt-001",
        repo_details={
            "ts-payment-service": {
                "changed_apis": ("POST /payments/retry",),
                "changed_modules": ("payments",),
                "risk": "high",
            }
        },
    )

    assert len(recorder.generate_calls) == 1
    call = recorder.generate_calls[0]
    assert call["plan_version"] == 1
    assert call["repositories"] is None  # every repo in the plan gets a doc
    assert call["details"]["ts-payment-service"]["risk"] == "high"
    assert len(result.handoff_doc_ids) == 3


async def test_replan_regenerates_docs_for_affected_repos() -> None:
    recorder = RecordingHandoffDocGenerator()
    summary = ConfirmationSummary(
        required=[
            ConfirmationResult(
                repository="ts-payment-service",
                status="REQUIRED",
                confidence=1.0,
                plan_summary="add the retry endpoint",
                plan=RepositoryPlan(
                    changed_apis=("POST /payments/retry",),
                    changed_modules=("payments",),
                    risk="high",
                ),
            )
        ],
        maybe=[],
        excluded=[],
    )
    bridge = _make_bridge(recorder)

    result = await bridge.replan(
        project_id=PROJECT_ID,
        leader_agent_id=LEADER_ID,
        feedback="payment retry is failing under load",
        change_source_repo="ts-payment-service",
        plan_version=1,
        requirement="make payments retryable",
        idempotency_prefix="tt-001-replan",
        all_repos=["ts-payment-service", "ts-order-service"],
        integration_service=StubIntegrationService(_make_plan()),
        confirmation_summary=summary,
        graph=None,
    )

    assert len(recorder.supersede_calls) == 0  # supersede happens inside the service
    assert len(recorder.generate_calls) == 1
    call = recorder.generate_calls[0]
    assert call["plan_version"] == 2
    assert call["repositories"] == ["ts-payment-service"]
    assert call["details"]["ts-payment-service"]["changed_apis"] == (
        "POST /payments/retry",
    )
    assert result.handoff_doc_ids == [UUID(int=1)]


async def test_replan_without_new_plan_supersedes_only() -> None:
    recorder = RecordingHandoffDocGenerator()
    bridge = _make_bridge(recorder)

    result = await bridge.replan(
        project_id=PROJECT_ID,
        leader_agent_id=LEADER_ID,
        feedback="payment retry is failing under load",
        change_source_repo="ts-payment-service",
        plan_version=1,
        requirement="make payments retryable",
        idempotency_prefix="tt-001-replan",
        all_repos=["ts-payment-service", "ts-order-service"],
        integration_service=None,
        confirmation_summary=None,
        graph=None,
    )

    assert len(recorder.generate_calls) == 0
    assert len(recorder.supersede_calls) == 1
    assert recorder.supersede_calls[0]["superseded_by_version"] == 2
    assert recorder.supersede_calls[0]["repositories"] == ["ts-payment-service"]
    assert result.handoff_doc_ids == []
