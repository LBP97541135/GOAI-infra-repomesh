import asyncio
import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete

from repomesh.modules.capability_management.presets import SKILLS
from repomesh.modules.capability_management.registry import (
    PostgresSkillRegistry,
    SkillAssignmentRecord,
    SkillEvaluationRecord,
    SkillRecord,
    SkillReleaseRecord,
    SkillVersionRecord,
)
from repomesh.persistence import Database

POSTGRES_URL = os.getenv("REPOMESH_TEST_DATABASE_URL") or os.getenv(
    "REPOMESH_TEST_POSTGRES_URL"
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="PostgreSQL test URL is not configured"),
]


@pytest.mark.asyncio
async def test_postgres_concurrent_seed_and_assignment_converge() -> None:
    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    registry = PostgresSkillRegistry(database, Path.cwd())
    skill_id = f"registry-concurrency-{uuid4()}"
    definition = replace(SKILLS["task-execution"], id=skill_id)
    task_id = uuid4()
    try:
        await asyncio.gather(
            *(registry.bootstrap_definition(definition, Path.cwd()) for _ in range(16))
        )
        assignments = await asyncio.gather(
            *(registry.resolve(definition, task_id) for _ in range(16))
        )
        assert len({item.assignment_id for item in assignments}) == 1
        assert {item.version for item in assignments} == {"1.0.0"}
    finally:
        async with database.transaction() as session:
            await session.execute(
                delete(SkillAssignmentRecord).where(
                    SkillAssignmentRecord.skill_id == skill_id
                )
            )
            version_ids = (
                await session.scalars(
                    SkillVersionRecord.__table__.select()
                    .with_only_columns(SkillVersionRecord.id)
                    .where(SkillVersionRecord.skill_id == skill_id)
                )
            ).all()
            if version_ids:
                await session.execute(
                    delete(SkillEvaluationRecord).where(
                        SkillEvaluationRecord.version_id.in_(version_ids)
                    )
                )
            await session.execute(
                delete(SkillReleaseRecord).where(SkillReleaseRecord.skill_id == skill_id)
            )
            await session.execute(
                delete(SkillVersionRecord).where(SkillVersionRecord.skill_id == skill_id)
            )
            await session.execute(delete(SkillRecord).where(SkillRecord.id == skill_id))
        await database.dispose()
