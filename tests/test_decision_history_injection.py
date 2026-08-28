"""Phase 4b: similar historical decision chains injected into the confirmation
prompt — reference evidence for the RM, never a blocker.

Covers the injection pipeline end to end (contract decision-chain-v0.1 §6.5):
- the repository scope derived from the current candidates (slugs),
- the deterministic renderer,
- the fail-safe retrieval on ``DiscoveryChainService``,
- the ``DiscoveryPipeline.classify`` passthrough into the RM prompt,
- the ``DecisionHistoryFromChainStore`` adapter over decision_chain contracts.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from repomesh.modules.decision_chain.contracts import (
    DecisionChainSummaryView,
    DecisionStatus,
    DecisionStep,
)
from repomesh.modules.repository_intelligence.application.discovery_chain import (
    DiscoveryChainService,
    DiscoveryPipeline,
    DiscoveryTarget,
    _candidate_repository_ids,
    _format_history_context,
)
from repomesh.modules.repository_intelligence.domain import (
    AutoCard,
    RepositoryProfile,
)
from repomesh.modules.repository_intelligence.infrastructure import (
    DecisionHistoryFromChainStore,
)
from repomesh.modules.repository_intelligence.ports import SimilarDecisionSheet


def _profile(name: str = "ts-notification-service") -> RepositoryProfile:
    return RepositoryProfile(
        id=name,
        name=name,
        url=f"https://github.com/org/{name}",
        auto_card=AutoCard(
            top_dirs=("src/main/java",),
            deps=("spring-boot",),
            recent_commits=("fix email sending",),
            exposed_apis=("/api/v1/sendEmail",),
            low_signal=False,
        ),
    )


def _sheet(**overrides) -> SimilarDecisionSheet:
    base = dict(
        decision_id=uuid4(),
        project_id=uuid4(),
        step="confirmation",
        status="confirmed",
        affected_repository_ids=("ts-core-service",),
        payload_summary={
            "required": ["ts-core-service"],
            "effective_tiers": {"ts-core-service": "REQUIRED"},
        },
        business_time=datetime(2026, 8, 1, tzinfo=UTC),
    )
    base.update(overrides)
    return SimilarDecisionSheet(**base)


def _target() -> DiscoveryTarget:
    return DiscoveryTarget(
        snapshot_id=uuid4(),
        project_id=uuid4(),
        plan_version=1,
        requirement_text="fix email",
        discovery_version=1,
        discovery={},
    )


class _RecordingPort:
    """DecisionHistoryPort double: records the scope, returns sheets or raises."""

    def __init__(
        self,
        sheets: list[SimilarDecisionSheet] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._sheets = sheets or []
        self._error = error
        self.calls: list[
            tuple[UUID, UUID, tuple[str, ...], int, str | None]
        ] = []

    async def find_similar(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        repository_ids: tuple[str, ...],
        top_k: int = 5,
        query_text: str | None = None,
    ) -> list[SimilarDecisionSheet]:
        self.calls.append(
            (
                organization_id,
                project_id,
                tuple(repository_ids),
                top_k,
                query_text,
            )
        )
        if self._error is not None:
            raise self._error
        return list(self._sheets)


def _service(port: _RecordingPort | None) -> DiscoveryChainService:
    service = DiscoveryChainService(
        snapshots=None,
        directory=None,
        audit=None,
        pipeline=None,
    )
    service._decision_history = port
    return service


async def _org_stub(_project_id: UUID) -> UUID | None:
    return uuid4()


# ---------------------------------------------------------------------------
# Candidate scope (the slugs handed to the port)
# ---------------------------------------------------------------------------


class TestCandidateRepositoryIds:
    def test_deduplicates_and_keeps_first_seen_order(self) -> None:
        items = [
            {"repository_name": "a"},
            {"repository_name": "b"},
            {"repository_name": "a"},
        ]
        assert _candidate_repository_ids(items) == ("a", "b")

    def test_skips_blank_names(self) -> None:
        items = [
            {"repository_name": ""},
            {"repository_name": "   "},
            {"repository_name": "a"},
        ]
        assert _candidate_repository_ids(items) == ("a",)

    def test_empty_items(self) -> None:
        assert _candidate_repository_ids([]) == ()


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


class TestFormatHistoryContext:
    def test_empty_is_none(self) -> None:
        assert _format_history_context([]) is None

    def test_renders_each_sheet_deterministically(self) -> None:
        sheets = [
            _sheet(
                decision_id=UUID(int=1),
                business_time=datetime(2026, 8, 1, tzinfo=UTC),
            ),
            _sheet(
                decision_id=UUID(int=2),
                business_time=datetime(2026, 7, 15, tzinfo=UTC),
            ),
        ]
        text = _format_history_context(sheets)
        assert text is not None
        assert text.startswith("## Similar Historical Decisions")
        assert "1. confirmation / confirmed · 2026-08-01" in text
        assert "2. confirmation / confirmed · 2026-07-15" in text
        # Effective tier from the approval, not a bare slug.
        assert "ts-core-service (REQUIRED)" in text
        # Deterministic: same sheets, same bytes.
        assert text == _format_history_context(sheets)

    def test_tiers_fall_back_to_status_when_missing(self) -> None:
        sheet = _sheet(payload_summary={})
        text = _format_history_context([sheet])
        assert text is not None
        assert "ts-core-service (confirmed)" in text

    def test_non_dict_tiers_are_ignored(self) -> None:
        sheet = _sheet(payload_summary={"effective_tiers": "not-a-dict"})
        text = _format_history_context([sheet])
        assert text is not None
        assert "repos: ts-core-service" in text


# ---------------------------------------------------------------------------
# Fail-safe retrieval on DiscoveryChainService
# ---------------------------------------------------------------------------


class TestHistoryContextRetrieval:
    def test_no_port_returns_none(self) -> None:
        service = _service(port=None)
        assert (
            asyncio.run(
                service._history_context(_target(), [{"repository_name": "a"}])
            )
            is None
        )

    def test_empty_repository_scope_skips_the_port(self) -> None:
        port = _RecordingPort()
        service = _service(port=port)
        assert (
            asyncio.run(
                service._history_context(_target(), [{"repository_name": ""}])
            )
            is None
        )
        assert port.calls == []

    def test_unresolvable_organization_returns_none(self) -> None:
        port = _RecordingPort()
        service = _service(port=port)

        async def _no_org(_project_id: UUID) -> UUID | None:
            return None

        service._organization_of = _no_org
        assert (
            asyncio.run(
                service._history_context(_target(), [{"repository_name": "a"}])
            )
            is None
        )
        assert port.calls == []

    def test_port_failure_is_not_a_blocker(self) -> None:
        """A store failure yields no history — the classification proceeds
        exactly as before Phase 4b (enhancement, never a gate)."""
        port = _RecordingPort(error=RuntimeError("store down"))
        service = _service(port=port)
        service._organization_of = _org_stub
        assert (
            asyncio.run(
                service._history_context(_target(), [{"repository_name": "a"}])
            )
            is None
        )

    def test_sheets_render_into_context_with_scope_forwarded(self) -> None:
        port = _RecordingPort(sheets=[_sheet()])
        service = _service(port=port)
        service._organization_of = _org_stub
        target = _target()
        text = asyncio.run(
            service._history_context(
                target,
                [{"repository_name": "a"}, {"repository_name": "b"}],
                query_text=target.requirement_text,
            )
        )
        assert text is not None
        assert text.startswith("## Similar Historical Decisions")
        # Scope is forwarded as slugs, with the project and an org attached.
        assert len(port.calls) == 1
        org_id, project_id, repository_ids, top_k, query_text = port.calls[0]
        assert project_id == target.project_id
        assert repository_ids == ("a", "b")
        assert top_k == 5
        assert org_id is not None
        # The requirement in force rides along as the L3 semantic query text.
        assert query_text == target.requirement_text

    def test_organization_resolved_from_the_project(self) -> None:
        org_id = uuid4()
        port = _RecordingPort(sheets=[_sheet()])
        service = _service(port=port)

        async def _fixed_org(_project_id: UUID) -> UUID | None:
            return org_id

        service._organization_of = _fixed_org
        target = _target()
        asyncio.run(
            service._history_context(target, [{"repository_name": "a"}])
        )
        assert port.calls[0][0] == org_id


# ---------------------------------------------------------------------------
# DiscoveryPipeline.classify passthrough
# ---------------------------------------------------------------------------


class _FakeCatalog:
    def __init__(self, profiles: list[RepositoryProfile]) -> None:
        self._profiles = profiles

    async def list(self) -> list[RepositoryProfile]:
        return list(self._profiles)


class _StubLLM:
    def __init__(self) -> None:
        self.last_messages: list[dict[str, str]] | None = None

    def chat(
        self, messages: list[dict[str, str]], *, temperature: float = 0.0
    ) -> str:
        self.last_messages = messages
        return json.dumps(
            {
                "status": "REQUIRED",
                "confidence": 0.9,
                "reason": "handles email sending",
                "plan_summary": "fix email params",
                "missing_dependencies": [],
            }
        )


def _pipeline(llm: _StubLLM) -> DiscoveryPipeline:
    return DiscoveryPipeline(
        _FakeCatalog(
            [_profile("ts-notification-service"), _profile("ts-config-service")]
        ),
        llm,
        keyword_score_cap=0.99,
        confirmation_concurrency=1,
        confirmation_supplement_cap=0,
    )


class TestClassifyInjection:
    def test_history_context_reaches_the_rm_prompt(self) -> None:
        llm = _StubLLM()
        history = _format_history_context([_sheet()])
        summary = asyncio.run(
            _pipeline(llm).classify(
                "fix email",
                [
                    {
                        "repository_name": "ts-notification-service",
                        "rationale": "flagged",
                        "score": 0.9,
                    }
                ],
                history_context=history,
            )
        )
        assert "ts-notification-service" in summary.final_repos
        user = llm.last_messages[1]["content"]
        assert "## Similar Historical Decisions" in user
        assert "ts-core-service (REQUIRED)" in user
        assert user.index("## Similar Historical Decisions") < user.index("## Task")

    def test_without_history_context_no_history_section(self) -> None:
        llm = _StubLLM()
        asyncio.run(
            _pipeline(llm).classify(
                "fix email",
                [
                    {
                        "repository_name": "ts-notification-service",
                        "rationale": "flagged",
                        "score": 0.9,
                    }
                ],
            )
        )
        assert "## Similar Historical Decisions" not in llm.last_messages[1]["content"]


# ---------------------------------------------------------------------------
# Adapter over decision_chain contracts
# ---------------------------------------------------------------------------


class _StubSimilarity:
    """Structural stand-in for ``DecisionChainSimilarityService``."""

    def __init__(self, sheets: list[DecisionChainSummaryView]) -> None:
        self._sheets = sheets
        self.kwargs: dict = {}

    async def find_similar(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        same_repository_ids: tuple[str, ...] = (),
        top_k: int = 5,
    ) -> list[DecisionChainSummaryView]:
        self.kwargs = {
            "organization_id": organization_id,
            "project_id": project_id,
            "same_repository_ids": same_repository_ids,
            "top_k": top_k,
        }
        return list(self._sheets)


def _chain_summary(**overrides) -> DecisionChainSummaryView:
    base = dict(
        decision_id=uuid4(),
        project_id=uuid4(),
        organization_id=uuid4(),
        step=DecisionStep.CONFIRMATION,
        version=2,
        status=DecisionStatus.CONFIRMED,
        affected_repository_ids=["ts-core-service", "ts-config-service"],
        payload_summary={"effective_tiers": {"ts-core-service": "REQUIRED"}},
        business_time=datetime(2026, 8, 1, tzinfo=UTC),
    )
    base.update(overrides)
    return DecisionChainSummaryView(**base)


class TestDecisionHistoryFromChainStore:
    def test_maps_summaries_and_forwards_scope(self) -> None:
        summary = _chain_summary()
        similar = _StubSimilarity([summary])
        adapter = DecisionHistoryFromChainStore(similar)
        org_id, project_id = uuid4(), uuid4()
        sheets = asyncio.run(
            adapter.find_similar(
                organization_id=org_id,
                project_id=project_id,
                repository_ids=("ts-core-service",),
            )
        )
        assert similar.kwargs["organization_id"] == org_id
        assert similar.kwargs["project_id"] == project_id
        # The port's ``repository_ids`` are the producer's ``same_repository_ids``
        # — both carry repository slugs (§6.5).
        assert similar.kwargs["same_repository_ids"] == ("ts-core-service",)
        assert similar.kwargs["top_k"] == 5
        assert len(sheets) == 1
        sheet = sheets[0]
        assert sheet.decision_id == summary.decision_id
        assert sheet.project_id == summary.project_id
        assert sheet.step == "confirmation"
        assert sheet.status == "confirmed"
        assert sheet.affected_repository_ids == (
            "ts-core-service",
            "ts-config-service",
        )
        assert sheet.payload_summary == {
            "effective_tiers": {"ts-core-service": "REQUIRED"}
        }
        assert sheet.business_time == summary.business_time

    def test_top_k_is_forwarded(self) -> None:
        similar = _StubSimilarity([])
        adapter = DecisionHistoryFromChainStore(similar)
        asyncio.run(
            adapter.find_similar(
                organization_id=uuid4(),
                project_id=uuid4(),
                repository_ids=("a",),
                top_k=3,
            )
        )
        assert similar.kwargs["top_k"] == 3

    def test_empty_history_maps_to_empty_list(self) -> None:
        adapter = DecisionHistoryFromChainStore(_StubSimilarity([]))
        sheets = asyncio.run(
            adapter.find_similar(
                organization_id=uuid4(),
                project_id=uuid4(),
                repository_ids=(),
            )
        )
        assert sheets == []

    def test_accepts_and_ignores_the_l3_query_text_hook(self) -> None:
        """Port conformance: the structural adapter takes ``query_text`` and
        keeps ranking on repositories (L3 hook is semantic-adapters-only)."""
        similar = _StubSimilarity([_chain_summary()])
        adapter = DecisionHistoryFromChainStore(similar)
        sheets = asyncio.run(
            adapter.find_similar(
                organization_id=uuid4(),
                project_id=uuid4(),
                repository_ids=("ts-core-service",),
                query_text="fix email",
            )
        )
        assert len(sheets) == 1, "query_text 被忽略，命中不受影响"
