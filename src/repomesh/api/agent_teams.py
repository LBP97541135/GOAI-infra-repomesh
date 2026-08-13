"""Persist AgentTeams Teams composed during setup and onboarding.

The AgentTeams control plane is the runtime authority for a Team, but until now
RepoMesh kept no record of the Teams it asked that control plane to compose. The
operator-chosen name and description, the membership snapshot and the idempotency
key all lived only in the Go runtime, so after a restart RepoMesh could only
*infer* Teams from the agent directory and had to synthesise a display name.

This module makes a composed Team a first-class RepoMesh record. Both the manual
``POST /agent-teams`` flow and repository onboarding upsert a row here (keyed by
the unique AgentTeams Team name), and ``GET /agent-teams`` lists them with the
real name/description so the UI no longer has to reconstruct them.
"""
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import JSON, DateTime, String, Text, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence.base import Base

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")

router = APIRouter(prefix="/api/v1/agent-teams", tags=["agent-teams"])


class AgentTeamRecord(Base):
    __tablename__ = "agent_teams"
    __table_args__ = (
        UniqueConstraint(
            "agentteams_team_name", name="uq_agent_teams_agentteams_team_name"
        ),
        {"schema": "agent_directory"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    name: Mapped[str] = mapped_column(String(253))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    leader_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    member_agent_ids: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list)
    agentteams_team_name: Mapped[str] = mapped_column(String(253))
    repository_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _view(record: AgentTeamRecord) -> dict:
    return {
        "id": str(record.id),
        "organization_id": str(record.organization_id),
        "name": record.name,
        "description": record.description,
        "leader_agent_id": str(record.leader_agent_id),
        "member_agent_ids": list(record.member_agent_ids or []),
        "agentteams_team_name": record.agentteams_team_name,
        "repository_id": str(record.repository_id) if record.repository_id else None,
        "created_at": record.created_at,
    }


async def persist_agent_team(
    database,
    *,
    organization_id: UUID,
    name: str,
    description: str | None,
    leader_agent_id: UUID,
    member_agent_ids: list[UUID],
    agentteams_team_name: str,
    repository_id: UUID | None,
    idempotency_key: str,
) -> None:
    """Upsert the RepoMesh record for a composed Team, keyed by its Team name.

    Idempotent by ``agentteams_team_name`` so onboarding retries and Team reuse
    refresh the membership snapshot rather than creating duplicates.
    """
    now = datetime.now(UTC)
    member_ids = [str(member) for member in member_agent_ids]
    async with database.transaction() as session:
        existing = await session.scalar(
            select(AgentTeamRecord).where(
                AgentTeamRecord.agentteams_team_name == agentteams_team_name
            )
        )
        if existing is None:
            session.add(
                AgentTeamRecord(
                    id=uuid4(),
                    organization_id=organization_id,
                    name=name,
                    description=description,
                    leader_agent_id=leader_agent_id,
                    member_agent_ids=member_ids,
                    agentteams_team_name=agentteams_team_name,
                    repository_id=repository_id,
                    idempotency_key=idempotency_key,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            existing.organization_id = organization_id
            existing.name = name
            existing.description = description
            existing.leader_agent_id = leader_agent_id
            existing.member_agent_ids = member_ids
            existing.repository_id = repository_id
            existing.idempotency_key = idempotency_key
            existing.updated_at = now


async def _authenticated_account(request: Request):
    authorization = request.headers.get("Authorization", "")
    token = (
        authorization.removeprefix("Bearer ").strip()
        if authorization.startswith("Bearer ")
        else request.cookies.get("repomesh_session")
    )
    if not token:
        raise HTTPException(status_code=401, detail="local authentication is required")
    try:
        return await request.app.state.container.local_account_service().authenticate(token)
    except Exception as error:
        raise HTTPException(status_code=401, detail="invalid local session") from error


@router.get("")
async def list_agent_teams(request: Request) -> list[dict]:
    await _authenticated_account(request)
    async with request.app.state.container.database.transaction() as session:
        records = (
            await session.scalars(
                select(AgentTeamRecord).order_by(AgentTeamRecord.created_at.asc())
            )
        ).all()
    return [_view(record) for record in records]
