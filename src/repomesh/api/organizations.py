"""First-class organizations: the system of record behind ``organization_id``.

Historically ``organization_id`` was a bare UUID minted in the browser and never
backed by a record. This module makes an organization a real entity with a name,
slug, SCM binding and default policy, so downstream flows can resolve a genuine
organization instead of trusting an arbitrary UUID.
"""
import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence.base import Base

ScmProvider = Literal["github", "gitlab"]

router = APIRouter(prefix="/api/v1/organizations", tags=["organizations"])


class OrganizationRecord(Base):
    __tablename__ = "organizations"
    __table_args__ = ({"schema": "platform"},)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    scm_provider: Mapped[str] = mapped_column(String(20))
    scm_organization_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    default_worker_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


_SLUG_ALLOWED = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(default=None, max_length=120)
    scm_provider: ScmProvider
    scm_organization_url: HttpUrl | None = None
    default_model: str | None = Field(default=None, min_length=1, max_length=100)
    default_worker_count: int = Field(default=1, ge=1, le=20)


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    scm_provider: ScmProvider | None = None
    scm_organization_url: HttpUrl | None = None
    default_model: str | None = Field(default=None, min_length=1, max_length=100)
    default_worker_count: int | None = Field(default=None, ge=1, le=20)


def _view(record: OrganizationRecord) -> dict:
    return {
        "id": str(record.id),
        "name": record.name,
        "slug": record.slug,
        "scm_provider": record.scm_provider,
        "scm_organization_url": record.scm_organization_url,
        "default_model": record.default_model,
        "default_worker_count": record.default_worker_count,
        "created_at": record.created_at,
    }


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


async def _require_admin(request: Request):
    actor = await _authenticated_account(request)
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="local administrator permission is required")
    return actor


async def load_organization(session, organization_id: UUID) -> OrganizationRecord | None:
    """Fetch an organization within an existing session; used by setup flows."""
    return await session.get(OrganizationRecord, organization_id)


@router.post("", status_code=201)
async def create_organization(body: OrganizationCreate, request: Request) -> dict:
    await _require_admin(request)
    slug = body.slug.strip().lower() if body.slug else _slugify(body.name)
    if not slug or not _SLUG_ALLOWED.match(slug):
        raise HTTPException(
            status_code=422,
            detail="slug must be lower-case alphanumeric with dashes",
        )
    now = datetime.now(UTC)
    record = OrganizationRecord(
        id=uuid4(),
        name=body.name.strip(),
        slug=slug,
        scm_provider=body.scm_provider,
        scm_organization_url=str(body.scm_organization_url) if body.scm_organization_url else None,
        default_model=body.default_model,
        default_worker_count=body.default_worker_count,
        created_at=now,
    )
    async with request.app.state.container.database.transaction() as session:
        existing = await session.scalar(
            select(OrganizationRecord).where(OrganizationRecord.slug == slug)
        )
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=f"organization slug '{slug}' already exists",
            )
        session.add(record)
    return _view(record)


@router.get("")
async def list_organizations(request: Request) -> list[dict]:
    await _authenticated_account(request)
    async with request.app.state.container.database.transaction() as session:
        records = (
            await session.scalars(
                select(OrganizationRecord).order_by(OrganizationRecord.created_at.asc())
            )
        ).all()
    return [_view(record) for record in records]


@router.get("/{organization_id}")
async def get_organization(organization_id: UUID, request: Request) -> dict:
    await _authenticated_account(request)
    async with request.app.state.container.database.transaction() as session:
        record = await session.get(OrganizationRecord, organization_id)
    if record is None:
        raise HTTPException(status_code=404, detail="organization does not exist")
    return _view(record)


@router.patch("/{organization_id}")
async def update_organization(
    organization_id: UUID, body: OrganizationUpdate, request: Request
) -> dict:
    await _require_admin(request)
    async with request.app.state.container.database.transaction() as session:
        record = await session.get(OrganizationRecord, organization_id)
        if record is None:
            raise HTTPException(status_code=404, detail="organization does not exist")
        if body.name is not None:
            record.name = body.name.strip()
        if body.scm_provider is not None:
            record.scm_provider = body.scm_provider
        if body.scm_organization_url is not None:
            record.scm_organization_url = str(body.scm_organization_url)
        if body.default_model is not None:
            record.default_model = body.default_model
        if body.default_worker_count is not None:
            record.default_worker_count = body.default_worker_count
        view = _view(record)
    return view
