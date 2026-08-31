import hashlib
from uuid import uuid4

import pytest

from repomesh.modules.capability_management import (
    PostgresSkillRegistry,
    SkillEvaluationInput,
    SkillRegistryConflict,
    SkillReleaseChannel,
)
from repomesh.modules.capability_management.presets import SKILLS
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS


@pytest.fixture
async def registry(tmp_path):
    wrapper = tmp_path / "capabilities" / "skills" / "task-execution" / "SKILL.md"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("# Task execution v1\n", encoding="utf-8")
    database = Database(
        f"sqlite+aiosqlite:///{tmp_path / 'skill-registry.db'}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await database.create_all_for_tests()
    store = PostgresSkillRegistry(database, tmp_path)
    definition = SKILLS["task-execution"]
    await store.bootstrap_definition(definition, tmp_path)
    try:
        yield store, definition, wrapper
    finally:
        await database.dispose()


def _evaluation(*, passed=True):
    return SkillEvaluationInput(
        dataset_id="cross-repo-suite",
        dataset_version="2026-08-28",
        completion_rate=0.95 if passed else 0.60,
        test_pass_rate=0.96 if passed else 0.70,
        human_rework_rate=0.10 if passed else 0.40,
        tool_error_rate=0.05 if passed else 0.30,
        average_tokens=1200,
        average_duration_ms=45000,
    )


@pytest.mark.asyncio
async def test_canary_assignment_is_stable_and_failed_health_rolls_back(registry) -> None:
    store, definition, wrapper = registry
    digest = f"sha256:{hashlib.sha256(wrapper.read_bytes()).hexdigest()}"
    version_id = await store.create_version(
        definition.id, "2.0.0", digest, definition.local_path or ""
    )
    assert await store.evaluate(version_id, _evaluation()) is True
    await store.release(version_id, SkillReleaseChannel.CANARY, traffic_percent=25)

    assignments = [
        await store.resolve(definition, uuid4()) for _ in range(100)
    ]
    canary_count = sum(item.version == "2.0.0" for item in assignments)
    assert 10 <= canary_count <= 40

    task_id = uuid4()
    first = await store.resolve(definition, task_id)
    replay = await store.resolve(definition, task_id)
    assert replay.assignment_id == first.assignment_id
    assert replay.version == first.version

    assert await store.evaluate(version_id, _evaluation(passed=False)) is False
    after_rollback = await store.resolve(definition, uuid4())
    assert after_rollback.version == "1.0.0"
    history = await store.skill_history(definition.id)
    assert next(v for v in history["versions"] if v["version"] == "2.0.0")["state"] == (
        "rolled_back"
    )
    assert next(r for r in history["releases"] if r["channel"] == "canary")["status"] == (
        "rolled_back"
    )


@pytest.mark.asyncio
async def test_registry_rejects_unreviewed_path_and_wrong_hash(registry, tmp_path) -> None:
    store, definition, wrapper = registry
    digest = f"sha256:{hashlib.sha256(wrapper.read_bytes()).hexdigest()}"
    with pytest.raises(SkillRegistryConflict, match="outside the reviewed root"):
        await store.create_version(definition.id, "2.0.0", digest, "../outside.md")
    with pytest.raises(SkillRegistryConflict, match="content hash"):
        await store.create_version(
            definition.id,
            "2.0.0",
            "sha256:" + "0" * 64,
            definition.local_path or "",
        )


@pytest.mark.asyncio
async def test_passing_canary_promotes_to_stable_and_deprecates_v1(registry) -> None:
    store, definition, wrapper = registry
    digest = f"sha256:{hashlib.sha256(wrapper.read_bytes()).hexdigest()}"
    version_id = await store.create_version(
        definition.id, "2.0.0", digest, definition.local_path or ""
    )
    await store.evaluate(version_id, _evaluation())
    await store.release(version_id, SkillReleaseChannel.CANARY, traffic_percent=10)
    await store.evaluate(version_id, _evaluation())
    await store.release(version_id, SkillReleaseChannel.STABLE)

    assigned = await store.resolve(definition, uuid4())
    assert assigned.version == "2.0.0"
    history = await store.skill_history(definition.id)
    assert next(v for v in history["versions"] if v["version"] == "1.0.0")["state"] == (
        "deprecated"
    )
    assert [r for r in history["releases"] if r["status"] == "active"] == [
        next(
            r
            for r in history["releases"]
            if r["channel"] == "stable" and r["version_id"] == version_id
        )
    ]
