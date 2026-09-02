from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from repomesh.persistence import Database

from .database_test_team import (
    DatabaseTestHandoffStatus,
    DatabaseTestTeamEvidence,
    DatabaseTestTeamPlan,
)
from .infrastructure import DatabaseTestTeamHandoffRecord


class PostgresDatabaseTestTeamHandoffStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, key: str) -> DatabaseTestTeamPlan | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(DatabaseTestTeamHandoffRecord).where(
                    DatabaseTestTeamHandoffRecord.branch_validation_key == key
                )
            )
        return self._plan(record) if record else None

    async def put(self, key: str, plan: DatabaseTestTeamPlan) -> None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(DatabaseTestTeamHandoffRecord).where(
                    DatabaseTestTeamHandoffRecord.branch_validation_key == key
                )
            )
            if record is None:
                session.add(
                    DatabaseTestTeamHandoffRecord(
                        id=uuid4(), branch_validation_key=key, payload=self._payload(plan)
                    )
                )
            else:
                record.payload = self._payload(plan)

    async def put_evidence(self, key: str, evidence: DatabaseTestTeamEvidence) -> None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(DatabaseTestTeamHandoffRecord).where(
                    DatabaseTestTeamHandoffRecord.branch_validation_key == key
                )
            )
            if record is None:
                raise ValueError("handoff plan does not exist")
            record.evidence = {
                "plan_key": evidence.plan_key,
                "candidate_sha": evidence.candidate_sha,
                "completed_checks": list(evidence.completed_checks),
                "affected_tables": list(evidence.affected_tables),
                "evidence_ref": evidence.evidence_ref,
            }

    async def get_evidence(self, key: str) -> DatabaseTestTeamEvidence | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(DatabaseTestTeamHandoffRecord).where(
                    DatabaseTestTeamHandoffRecord.branch_validation_key == key
                )
            )
        if record is None or record.evidence is None:
            return None
        item = record.evidence
        return DatabaseTestTeamEvidence(
            plan_key=str(item["plan_key"]),
            candidate_sha=str(item["candidate_sha"]),
            completed_checks=tuple(item["completed_checks"]),
            affected_tables=tuple(item["affected_tables"]),
            evidence_ref=str(item["evidence_ref"]),
        )

    @staticmethod
    def _payload(plan: DatabaseTestTeamPlan) -> dict[str, object]:
        return {
            "task_id": plan.task_id,
            "organization_id": plan.organization_id,
            "project_id": plan.project_id,
            "repository_id": plan.repository_id,
            "candidate_sha": plan.candidate_sha,
            "test_team_repository_id": plan.test_team_repository_id,
            "required_checks": list(plan.required_checks),
            "affected_tables": list(plan.affected_tables),
            "evidence_prefix": plan.evidence_prefix,
            "branch_validation_key": plan.branch_validation_key,
            "status": plan.status.value,
        }

    @staticmethod
    def _plan(record: DatabaseTestTeamHandoffRecord) -> DatabaseTestTeamPlan:
        item = record.payload
        return DatabaseTestTeamPlan(
            task_id=str(item["task_id"]),
            organization_id=str(item["organization_id"]),
            project_id=str(item["project_id"]),
            repository_id=str(item["repository_id"]),
            candidate_sha=str(item["candidate_sha"]),
            test_team_repository_id=str(item["test_team_repository_id"]),
            required_checks=tuple(item["required_checks"]),
            affected_tables=tuple(item["affected_tables"]),
            evidence_prefix=str(item["evidence_prefix"]),
            branch_validation_key=str(item["branch_validation_key"]),
            status=DatabaseTestHandoffStatus(str(item["status"])),
        )
