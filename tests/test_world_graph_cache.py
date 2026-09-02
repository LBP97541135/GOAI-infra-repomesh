"""M1: world-layer dependency graph in-process cache.

验收（docs/chenwenhui/世界层依赖图快照缓存-对抗性审查与设计方案-2026-08-28.md §4.3）：
1. 第二次 ``repository_profiles()`` / ``confirmation_service()`` 调用不触发
   ``catalog.list()`` —— 进程内缓存命中，避免每次分类/计划全量读库。
2. 档案变更后 ``invalidate_world_graph()`` 使缓存失效，下次访问重建。
3. ``DependencyGraphService`` 构建时发出 ``graph_build`` 埋点日志
   （profile_count / edge_count / duration_ms），作为 M2 的数据门禁。
"""

import logging
from collections.abc import Sequence
from uuid import UUID

import pytest

from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.repository_intelligence.application import (
    DependencyGraphService,
)
from repomesh.modules.repository_intelligence.application.confirmation import (
    ConfirmationService,
)
from repomesh.modules.repository_intelligence.application.discovery import LLMClient
from repomesh.modules.repository_intelligence.domain import (
    AutoCard,
    RepositoryProfile,
)
from repomesh.modules.repository_intelligence.ports.catalog import RepositoryCatalog
from repomesh.shared.events import EventEnvelope

_LOGGER_NAME = "repomesh.modules.repository_intelligence.application.dependency_graph"


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class CountingCatalog:
    """RepositoryCatalog double that counts ``list`` calls."""

    def __init__(self, profiles: list[RepositoryProfile]) -> None:
        self._profiles = list(profiles)
        self.list_calls = 0

    async def add(
        self, profile: RepositoryProfile, *, events: Sequence[EventEnvelope] = ()
    ) -> None:
        self._profiles.append(profile)

    async def list(self) -> list[RepositoryProfile]:
        self.list_calls += 1
        return list(self._profiles)

    async def get(self, repository_id: UUID) -> RepositoryProfile | None:
        return next((p for p in self._profiles if p.id == repository_id), None)


class _FakeLLM:
    """Minimal LLMClient double; confirmation/planning never call it in these tests."""

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        return "{}"


def _make_profile(name: str) -> RepositoryProfile:
    return RepositoryProfile(
        id=name,
        name=name,
        url=f"https://github.com/org/{name}",
        auto_card=AutoCard(
            top_dirs=("src/main",),
            deps=(),
            recent_commits=("init",),
            exposed_apis=(),
            low_signal=False,
        ),
    )


def _make_container(catalog: RepositoryCatalog) -> ApplicationContainer:
    # Only ``repository_catalog`` is exercised by M1; the rest of the fields
    # exist for composition-root wiring and are never touched here.
    return ApplicationContainer(
        database=None,
        agent_directory=None,
        project_topology_store=None,
        repository_catalog=catalog,
        outbox_store=None,
        task_store=None,
        collaboration_message_store=None,
        context_store=None,
        specification_store=None,
        mock_coding_agent_factory=None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_repository_profiles_cached_until_invalidated() -> None:
    catalog = CountingCatalog([_make_profile("a"), _make_profile("b")])
    container = _make_container(catalog)

    first = await container.repository_profiles()
    assert catalog.list_calls == 1

    second = await container.repository_profiles()
    assert catalog.list_calls == 1  # cache hit: no second read
    assert second is first  # same object, not a fresh projection

    # A scan registered a new profile; the invalidation hook drops the cache
    # so the next call rebuilds from the catalog.
    await catalog.add(_make_profile("c"))
    container.invalidate_world_graph()

    third = await container.repository_profiles()
    assert catalog.list_calls == 2
    assert {p.name for p in third} == {"a", "b", "c"}


async def test_world_graph_shared_until_invalidated() -> None:
    catalog = CountingCatalog([_make_profile("a"), _make_profile("b")])
    container = _make_container(catalog)

    graph1 = await container.world_graph()
    assert graph1 is not None
    assert catalog.list_calls == 1

    graph2 = await container.world_graph()
    assert graph2 is graph1  # same in-process instance, no rebuild

    container.invalidate_world_graph()
    graph3 = await container.world_graph()
    assert graph3 is not graph1  # rebuilt after invalidation
    assert catalog.list_calls == 2


async def test_confirmation_service_does_not_reread_catalog() -> None:
    catalog = CountingCatalog([_make_profile("a"), _make_profile("b")])
    container = _make_container(catalog)
    llm: LLMClient = _FakeLLM()

    first = await container.confirmation_service(llm)
    assert isinstance(first, ConfirmationService)
    assert catalog.list_calls == 1

    second = await container.confirmation_service(llm)
    assert isinstance(second, ConfirmationService)
    assert catalog.list_calls == 1  # profiles and graph both cache hits


def test_graph_build_metric_emitted(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        DependencyGraphService([_make_profile("a"), _make_profile("b")])

    matching = [
        record
        for record in caplog.records
        if record.name == _LOGGER_NAME and record.getMessage().startswith("graph_build ")
    ]
    assert matching, "graph_build 埋点日志未出现"
    message = matching[0].getMessage()
    assert "profile_count=2" in message
    assert "edge_count=" in message
    assert "duration_ms=" in message
