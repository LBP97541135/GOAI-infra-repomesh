from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from cryptography.fernet import Fernet
from sqlalchemy import Boolean, DateTime, LargeBinary, String, delete, select
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence import Database
from repomesh.persistence.base import Base

from .crypto import get_credentials_fernet

MODEL_API_KEY = "model.api_key"
MODEL_BASE_URL = "model.base_url"
MODEL_NAME = "model.model_name"
GITHUB_APP_ID = "github_app.app_id"
GITHUB_PRIVATE_KEY = "github_app.private_key"
GITHUB_WEBHOOK_SECRET = "github_app.webhook_secret"

ALLOWED_KEYS = frozenset(
    {
        MODEL_API_KEY,
        MODEL_BASE_URL,
        MODEL_NAME,
        GITHUB_APP_ID,
        GITHUB_PRIVATE_KEY,
        GITHUB_WEBHOOK_SECRET,
    }
)


class PlatformCredentialRecord(Base):
    __tablename__ = "platform_credentials"
    __table_args__ = {"schema": "platform"}

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    is_encrypted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )


@dataclass(frozen=True, slots=True)
class StoredCredential:
    key: str
    value: str
    updated_at: datetime
    updated_by: UUID | None


class PostgresPlatformCredentialStore:
    def __init__(self, database: Database, fernet: Fernet | None = None) -> None:
        self._database = database
        self._fernet = fernet or get_credentials_fernet()

    async def put_many(self, values: dict[str, str], *, updated_by: UUID | None) -> None:
        unknown = values.keys() - ALLOWED_KEYS
        if unknown:
            raise ValueError(f"unsupported credential keys: {', '.join(sorted(unknown))}")
        now = datetime.now(UTC)
        async with self._database.transaction() as session:
            for key, value in values.items():
                record = await session.get(PlatformCredentialRecord, key)
                encrypted = self._fernet.encrypt(value.encode("utf-8"))
                if record is None:
                    session.add(
                        PlatformCredentialRecord(
                            key=key,
                            value_encrypted=encrypted,
                            is_encrypted=True,
                            updated_at=now,
                            updated_by=updated_by,
                        )
                    )
                else:
                    record.value_encrypted = encrypted
                    record.is_encrypted = True
                    record.updated_at = now
                    record.updated_by = updated_by

    async def get(self, key: str) -> StoredCredential | None:
        if key not in ALLOWED_KEYS:
            raise ValueError(f"unsupported credential key: {key}")
        async with self._database.transaction() as session:
            record = await session.get(PlatformCredentialRecord, key)
            return self._decrypt(record) if record is not None else None

    async def get_many(self, keys: set[str] | frozenset[str]) -> dict[str, StoredCredential]:
        unknown = keys - ALLOWED_KEYS
        if unknown:
            raise ValueError(f"unsupported credential keys: {', '.join(sorted(unknown))}")
        if not keys:
            return {}
        async with self._database.transaction() as session:
            records = (
                await session.execute(
                    select(PlatformCredentialRecord).where(PlatformCredentialRecord.key.in_(keys))
                )
            ).scalars()
            return {record.key: self._decrypt(record) for record in records}

    async def delete(self, key: str) -> bool:
        if key not in ALLOWED_KEYS:
            raise ValueError(f"unsupported credential key: {key}")
        async with self._database.transaction() as session:
            result = await session.execute(
                delete(PlatformCredentialRecord).where(PlatformCredentialRecord.key == key)
            )
            return bool(result.rowcount)

    def _decrypt(self, record: PlatformCredentialRecord) -> StoredCredential:
        if not record.is_encrypted:
            raise ValueError(f"credential {record.key} is not encrypted")
        return StoredCredential(
            key=record.key,
            value=self._fernet.decrypt(record.value_encrypted).decode("utf-8"),
            updated_at=record.updated_at,
            updated_by=record.updated_by,
        )


async def effective_credential(container, key: str, fallback: str | None) -> str | None:
    store_factory = getattr(container, "platform_credential_store", None)
    if store_factory is None:
        return fallback
    stored = await store_factory().get(key)
    return stored.value if stored is not None else fallback
