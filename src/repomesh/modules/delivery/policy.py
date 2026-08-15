from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import JSON, Boolean, Integer, String, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence import Database
from repomesh.persistence.base import Base

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


@dataclass(frozen=True, slots=True)
class DeliveryPolicy:
    organization_id: UUID
    repository_id: UUID | None = None
    auto_merge: bool = False
    base_branch: str = "main"
    required_checks: tuple[str, ...] = ()
    required_approvals: int = 1
    contract_gate: bool = False
    add_label: bool = False

    def __post_init__(self) -> None:
        if not self.base_branch.strip():
            raise ValueError("base branch is required")
        if self.required_approvals < 0:
            raise ValueError("required approvals cannot be negative")


class DeliveryPolicyRecord(Base):
    __tablename__ = "delivery_policies"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "repository_scope",
            name="uq_delivery_policy_scope",
        ),
        {"schema": "delivery"},
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    repository_scope: Mapped[str] = mapped_column(String(36), primary_key=True)
    repository_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    auto_merge: Mapped[bool] = mapped_column(Boolean)
    base_branch: Mapped[str] = mapped_column(String(255))
    required_checks: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    required_approvals: Mapped[int] = mapped_column(Integer)
    contract_gate: Mapped[bool] = mapped_column(Boolean)
    add_label: Mapped[bool] = mapped_column(Boolean)


class PostgresDeliveryPolicyStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def put(self, policy: DeliveryPolicy) -> None:
        scope = str(policy.repository_id) if policy.repository_id else "*"
        async with self._database.transaction() as session:
            record = await session.get(
                DeliveryPolicyRecord,
                {"organization_id": policy.organization_id, "repository_scope": scope},
            )
            if record is None:
                session.add(self._record(policy, scope))
                return
            record.repository_id = policy.repository_id
            record.auto_merge = policy.auto_merge
            record.base_branch = policy.base_branch
            record.required_checks = list(policy.required_checks)
            record.required_approvals = policy.required_approvals
            record.contract_gate = policy.contract_gate
            record.add_label = policy.add_label

    async def resolve(
        self,
        organization_id: UUID,
        repository_id: UUID | None = None,
        *,
        fallback: DeliveryPolicy | None = None,
    ) -> DeliveryPolicy:
        scopes = [str(repository_id), "*"] if repository_id else ["*"]
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(DeliveryPolicyRecord).where(
                        DeliveryPolicyRecord.organization_id == organization_id,
                        DeliveryPolicyRecord.repository_scope.in_(scopes),
                    )
                )
            ).all()
        by_scope = {record.repository_scope: record for record in records}
        record = by_scope.get(str(repository_id)) or by_scope.get("*")
        if record is None:
            return fallback or DeliveryPolicy(organization_id=organization_id)
        return self._domain(record)

    @staticmethod
    def _record(policy: DeliveryPolicy, scope: str) -> DeliveryPolicyRecord:
        return DeliveryPolicyRecord(
            organization_id=policy.organization_id,
            repository_scope=scope,
            repository_id=policy.repository_id,
            auto_merge=policy.auto_merge,
            base_branch=policy.base_branch,
            required_checks=list(policy.required_checks),
            required_approvals=policy.required_approvals,
            contract_gate=policy.contract_gate,
            add_label=policy.add_label,
        )

    @staticmethod
    def _domain(record: DeliveryPolicyRecord) -> DeliveryPolicy:
        return DeliveryPolicy(
            organization_id=record.organization_id,
            repository_id=record.repository_id,
            auto_merge=record.auto_merge,
            base_branch=record.base_branch,
            required_checks=tuple(record.required_checks),
            required_approvals=record.required_approvals,
            contract_gate=record.contract_gate,
            add_label=record.add_label,
        )
