from uuid import UUID

from sqlalchemy import JSON, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence.base import Base

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class AgentPrincipalRecord(Base):
    __tablename__ = "agent_principals"
    __table_args__ = (
        UniqueConstraint(
            "agentteams_resource_kind",
            "agentteams_resource_name",
            name="uq_agent_principals_agentteams_resource",
        ),
        UniqueConstraint("idempotency_key", name="uq_agent_principals_idempotency_key"),
        UniqueConstraint("singleton_key", name="uq_agent_principals_singleton_key"),
        Index("ix_agent_principals_repository_role", "repository_id", "role"),
        {"schema": "agent_directory"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    role: Mapped[str] = mapped_column(String(50), index=True)
    leader_agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_directory.agent_principals.id", ondelete="RESTRICT"), index=True
    )
    singleton_key: Mapped[str | None] = mapped_column(String(200))
    repository_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    responsibility_paths: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list)
    agentteams_resource_kind: Mapped[str] = mapped_column(String(30))
    agentteams_resource_name: Mapped[str] = mapped_column(String(253))
    status: Mapped[str] = mapped_column(String(30), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_fingerprint: Mapped[str] = mapped_column(String(71))
