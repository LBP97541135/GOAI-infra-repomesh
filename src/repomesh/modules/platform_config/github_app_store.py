"""Durable state for the GitHub App manifest flow.

Two tables, deliberately separate from ``platform_credentials``:

``github_app_manifest_states``
    The one-time tokens that tie a wizard click to the redirect GitHub sends
    back. They used to live in a module-level dict, which was wrong in a way
    that destroyed data: saving the model credentials restarts this process on
    purpose (``_restart_receipt`` / the bootstrap ``RESTARTING_API`` phase), and
    a restart mid-flow dropped the state while GitHub had *already* created the
    App — leaving an orphan App whose private key is never re-issued.

``github_app_registrations``
    Slug, owner and installation id. None of these are secrets, so they stay
    out of the Fernet-encrypted credential table: the unauthenticated
    ``/setup/status`` poller reads them, and putting them behind the encryption
    key would make a key rotation take the status endpoint down with it.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, String, delete, select, update
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence import Database
from repomesh.persistence.base import Base

#: GitHub's own limit on the manifest flow is one hour; anything shorter just
#: invents a second, stricter deadline that only we know about.
CREATE_STATE_TTL_SECONDS = 3600
#: The install hop is soft-verified (the App JWT is the real gate), so this is
#: only about garbage collection, not security.
INSTALL_STATE_TTL_SECONDS = 86400

MANIFEST_STATE_CREATE = "create"
MANIFEST_STATE_INSTALL = "install"


def state_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class GitHubAppManifestStateRecord(Base):
    __tablename__ = "github_app_manifest_states"
    __table_args__ = {"schema": "platform"}

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    #: Only the digest is stored — a leaked database row must not be replayable
    #: against GitHub's redirect.
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    requested_name: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_owner: Mapped[str | None] = mapped_column(String(64))
    requested_origin: Mapped[str] = mapped_column(String(256), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GitHubAppRegistrationRecord(Base):
    __tablename__ = "github_app_registrations"
    __table_args__ = {"schema": "platform"}

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    app_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_login: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_type: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_owner_login: Mapped[str | None] = mapped_column(String(64))
    installation_id: Mapped[int | None] = mapped_column(BigInteger)
    installation_account_login: Mapped[str | None] = mapped_column(String(64))
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(frozen=True, slots=True)
class ManifestState:
    id: UUID
    kind: str
    requested_by: UUID | None
    requested_name: str
    requested_owner: str | None
    requested_origin: str


@dataclass(frozen=True, slots=True)
class GitHubAppRegistration:
    app_id: int
    slug: str
    owner_login: str
    owner_type: str
    requested_owner_login: str | None
    installation_id: int | None
    installation_account_login: str | None
    installed_at: datetime | None

    @property
    def installed(self) -> bool:
        return self.installed_at is not None


class PostgresGitHubAppManifestStateStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def issue(
        self,
        *,
        kind: str,
        requested_name: str,
        requested_origin: str,
        requested_owner: str | None = None,
        requested_by: UUID | None = None,
        ttl_seconds: int = CREATE_STATE_TTL_SECONDS,
    ) -> str:
        """Mint a state token and return it in the clear — the caller hands it
        to GitHub, and only the digest survives here."""
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        async with self._database.transaction() as session:
            await session.execute(
                delete(GitHubAppManifestStateRecord).where(
                    GitHubAppManifestStateRecord.expires_at < now
                )
            )
            session.add(
                GitHubAppManifestStateRecord(
                    id=uuid4(),
                    state_hash=state_token_hash(token),
                    kind=kind,
                    requested_by=requested_by,
                    requested_name=requested_name,
                    requested_owner=requested_owner,
                    requested_origin=requested_origin,
                    claimed_at=None,
                    consumed_at=None,
                    expires_at=now + timedelta(seconds=ttl_seconds),
                    created_at=now,
                )
            )
        return token

    async def claim(self, token: str, *, kind: str) -> ManifestState | None:
        """Atomically take ownership of an unclaimed, unexpired state.

        Claiming *before* the code exchange is what keeps two concurrent
        callbacks (browser refresh, link prefetch, a TLS proxy re-fetching the
        redirect) from both POSTing the same one-time code — the loser sees
        ``None`` and renders the expired page without having touched GitHub.
        The window it opens, where a claimed state is stuck if the exchange
        never reached GitHub, is closed by :meth:`release`.
        """
        if not token:
            return None
        token_hash = state_token_hash(token)
        now = datetime.now(UTC)
        async with self._database.transaction() as session:
            result = await session.execute(
                update(GitHubAppManifestStateRecord)
                .where(
                    GitHubAppManifestStateRecord.state_hash == token_hash,
                    GitHubAppManifestStateRecord.kind == kind,
                    GitHubAppManifestStateRecord.claimed_at.is_(None),
                    GitHubAppManifestStateRecord.consumed_at.is_(None),
                    GitHubAppManifestStateRecord.expires_at > now,
                )
                .values(claimed_at=now)
            )
            if result.rowcount != 1:
                return None
            record = await session.scalar(
                select(GitHubAppManifestStateRecord).where(
                    GitHubAppManifestStateRecord.state_hash == token_hash
                )
            )
            if record is None:  # pragma: no cover - the UPDATE just matched it
                return None
            return ManifestState(
                id=record.id,
                kind=record.kind,
                requested_by=record.requested_by,
                requested_name=record.requested_name,
                requested_owner=record.requested_owner,
                requested_origin=record.requested_origin,
            )

    async def release(self, state_id: UUID) -> None:
        """Undo a claim so the user can simply reload the callback URL.

        Only for transport-level failures, where GitHub most likely never saw
        the code. An HTTP 4xx means the code *was* spent and must not be
        retried.
        """
        async with self._database.transaction() as session:
            await session.execute(
                update(GitHubAppManifestStateRecord)
                .where(
                    GitHubAppManifestStateRecord.id == state_id,
                    GitHubAppManifestStateRecord.consumed_at.is_(None),
                )
                .values(claimed_at=None)
            )

    async def finalize(self, state_id: UUID) -> None:
        """Burn the state for good — called only after the credentials landed."""
        async with self._database.transaction() as session:
            await session.execute(
                update(GitHubAppManifestStateRecord)
                .where(GitHubAppManifestStateRecord.id == state_id)
                .values(consumed_at=datetime.now(UTC))
            )


class PostgresGitHubAppRegistrationStore:
    """The single App this deployment delivers through.

    One row at a time by construction: ``upsert`` clears any predecessor, so
    ``current()`` never has to guess which of several rows the status endpoint
    means.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def current(self) -> GitHubAppRegistration | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(GitHubAppRegistrationRecord)
                .order_by(GitHubAppRegistrationRecord.updated_at.desc())
                .limit(1)
            )
            return self._domain(record) if record is not None else None

    async def upsert(
        self,
        *,
        app_id: int,
        slug: str,
        owner_login: str,
        owner_type: str,
        requested_owner_login: str | None,
        installation_id: int | None = None,
        installation_account_login: str | None = None,
    ) -> GitHubAppRegistration:
        now = datetime.now(UTC)
        async with self._database.transaction() as session:
            await session.execute(
                delete(GitHubAppRegistrationRecord).where(
                    GitHubAppRegistrationRecord.app_id != app_id
                )
            )
            record = await session.scalar(
                select(GitHubAppRegistrationRecord).where(
                    GitHubAppRegistrationRecord.app_id == app_id
                )
            )
            if record is None:
                record = GitHubAppRegistrationRecord(
                    id=uuid4(),
                    app_id=app_id,
                    slug=slug,
                    owner_login=owner_login,
                    owner_type=owner_type,
                    requested_owner_login=requested_owner_login,
                    installation_id=installation_id,
                    installation_account_login=installation_account_login,
                    installed_at=now if installation_id is not None else None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
                await session.flush()
            else:
                record.slug = slug
                record.owner_login = owner_login
                record.owner_type = owner_type
                record.requested_owner_login = requested_owner_login
                if installation_id is not None:
                    record.installation_id = installation_id
                    record.installation_account_login = installation_account_login
                    record.installed_at = now
                record.updated_at = now
            return self._domain(record)

    async def mark_installed(
        self,
        *,
        app_id: int,
        installation_id: int,
        installation_account_login: str | None,
    ) -> GitHubAppRegistration | None:
        now = datetime.now(UTC)
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(GitHubAppRegistrationRecord).where(
                    GitHubAppRegistrationRecord.app_id == app_id
                )
            )
            if record is None:
                return None
            record.installation_id = installation_id
            record.installation_account_login = installation_account_login
            record.installed_at = now
            record.updated_at = now
            return self._domain(record)

    @staticmethod
    def _domain(record: GitHubAppRegistrationRecord) -> GitHubAppRegistration:
        installed_at = record.installed_at
        if installed_at is not None and installed_at.tzinfo is None:
            # SQLite hands back naive datetimes where Postgres does not; without
            # this the two backends disagree about what the same row means.
            installed_at = installed_at.replace(tzinfo=UTC)
        return GitHubAppRegistration(
            app_id=record.app_id,
            slug=record.slug,
            owner_login=record.owner_login,
            owner_type=record.owner_type,
            requested_owner_login=record.requested_owner_login,
            installation_id=record.installation_id,
            installation_account_login=record.installation_account_login,
            installed_at=installed_at,
        )
