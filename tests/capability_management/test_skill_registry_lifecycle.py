"""Skill registry lifecycle tests: transitions, evaluation gates, rollback, seeding.

The lifecycle is the demonstrable core of "Skill Registry 从设计走向运行":
register → evaluate → canary → promote, plus the two paths a reviewer will
ask to see — a failing evaluation on canary rolling the version back, and
promotion refused without canary-window evidence.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from repomesh.modules.capability_management.contracts import (
    SkillLifecycleRefused,
    SkillVersionStatus,
)
from repomesh.modules.capability_management.infrastructure import SkillRegistryService
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS


@pytest_asyncio.fixture
async def database(tmp_path: object) -> Database:
    instance = Database(
        f"sqlite+aiosqlite:///{tmp_path.joinpath('repomesh-capability.db')}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await instance.create_all_for_tests()
    yield instance
    await instance.dispose()


@pytest_asyncio.fixture
async def registry(database: Database) -> SkillRegistryService:
    return SkillRegistryService(database)


async def _registered(registry: SkillRegistryService, version: str = "1.1.0"):
    return await registry.register_version(
        skill_id="task-execution",
        version=version,
        local_path="capabilities/skills/task-execution/SKILL.md",
        content_hash="sha256:test",
        created_by="test",
    )


@pytest.mark.asyncio
async def test_register_starts_in_draft(registry: SkillRegistryService) -> None:
    record = await _registered(registry)

    assert record.status == SkillVersionStatus.DRAFT.value


@pytest.mark.asyncio
async def test_invalid_semver_refused(registry: SkillRegistryService) -> None:
    with pytest.raises(SkillLifecycleRefused) as refused:
        await registry.register_version(
            skill_id="task-execution",
            version="v1",
            local_path="x",
            content_hash="h",
            created_by="test",
        )
    assert refused.value.code == "invalid_version"


@pytest.mark.asyncio
async def test_duplicate_version_refused(registry: SkillRegistryService) -> None:
    await _registered(registry, "1.1.0")

    with pytest.raises(SkillLifecycleRefused) as refused:
        await _registered(registry, "1.1.0")
    assert refused.value.code == "duplicate_version"


@pytest.mark.asyncio
async def test_canary_requires_passing_evaluation(registry: SkillRegistryService) -> None:
    record = await _registered(registry)
    await registry.transition(record.id, SkillVersionStatus.EVALUATING, actor="test")

    with pytest.raises(SkillLifecycleRefused) as refused:
        await registry.transition(record.id, SkillVersionStatus.CANARY, actor="test")
    assert refused.value.code == "evaluation_gate_failed"


@pytest.mark.asyncio
async def test_promotion_requires_canary_window_evidence(registry: SkillRegistryService) -> None:
    record = await _registered(registry)
    await registry.transition(record.id, SkillVersionStatus.EVALUATING, actor="test")
    await registry.record_evaluation(
        version_id=record.id,
        scenario="decompose a two-repo plan",
        negative_case="cycle in the DAG",
        outcome=True,
        evidence="scenario log",
        evaluated_by="tester",
    )
    await registry.transition(record.id, SkillVersionStatus.CANARY, actor="test")

    # No evaluation recorded during the canary window yet.
    with pytest.raises(SkillLifecycleRefused) as refused:
        await registry.transition(record.id, SkillVersionStatus.PROMOTED, actor="test")
    assert refused.value.code == "evaluation_gate_failed"

    await registry.record_evaluation(
        version_id=record.id,
        scenario="canary org ran a real task",
        negative_case="worker blocked mid-plan",
        outcome=True,
        evidence="canary run link",
        evaluated_by="tester",
    )
    promoted = await registry.transition(record.id, SkillVersionStatus.PROMOTED, actor="test")
    assert promoted.status == SkillVersionStatus.PROMOTED.value


@pytest.mark.asyncio
async def test_failing_canary_evaluation_rolls_back(registry: SkillRegistryService) -> None:
    record = await _registered(registry)
    await registry.transition(record.id, SkillVersionStatus.EVALUATING, actor="test")
    await registry.record_evaluation(
        version_id=record.id,
        scenario="pass gate",
        negative_case="none",
        outcome=True,
        evidence="ok",
        evaluated_by="tester",
    )
    await registry.transition(record.id, SkillVersionStatus.CANARY, actor="test")

    await registry.record_evaluation(
        version_id=record.id,
        scenario="canary regression",
        negative_case="wrong worker dispatched",
        outcome=False,
        evidence="failure log",
        evaluated_by="tester",
    )

    resolved = await registry.resolve_current("task-execution", None)
    assert resolved is None, "a rolled-back canary must not resolve as current"


@pytest.mark.asyncio
async def test_rollback_is_terminal_for_the_version_number(
    registry: SkillRegistryService,
) -> None:
    record = await _registered(registry)
    await registry.transition(record.id, SkillVersionStatus.EVALUATING, actor="test")
    await registry.record_evaluation(
        version_id=record.id, scenario="s", negative_case="n", outcome=True,
        evidence="e", evaluated_by="t",
    )
    await registry.transition(record.id, SkillVersionStatus.CANARY, actor="test")
    await registry.rollback(record.id, actor="tester")

    rolled = await registry.resolve_current("task-execution", None)
    assert rolled is None
    with pytest.raises(SkillLifecycleRefused) as refused:
        await registry.transition(record.id, SkillVersionStatus.PROMOTED, actor="test")
    assert refused.value.code == "illegal_skill_transition"


@pytest.mark.asyncio
async def test_seed_promoted_is_idempotent_and_preserves_rollback(
    registry: SkillRegistryService,
) -> None:
    assert await registry.seed_promoted(
        skill_id="task-execution", local_path="capabilities/skills/task-execution/SKILL.md"
    )
    current = await registry.resolve_current("task-execution", None)
    assert current is not None and current.version == "1.0.0"

    # An operator rolls the seed back; the next boot must not re-promote it.
    await registry.rollback(current.id, actor="tester")
    assert not await registry.seed_promoted(
        skill_id="task-execution", local_path="capabilities/skills/task-execution/SKILL.md"
    )
    assert await registry.resolve_current("task-execution", None) is None


@pytest.mark.asyncio
async def test_snapshot_creation_is_idempotent(registry: SkillRegistryService) -> None:
    versions = ["task-execution@1.0.0", "self-test@1.0.0"]
    first = await registry.create_snapshot(organization_id=None, versions=versions)
    second = await registry.create_snapshot(organization_id=None, versions=versions)

    assert first.id == second.id
