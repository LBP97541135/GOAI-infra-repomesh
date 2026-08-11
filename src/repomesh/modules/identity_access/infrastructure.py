from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, String, delete, func, select
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.modules.identity_access.local_accounts import (
    LocalAccountStore,
    LocalHumanAccount,
    LocalHumanSession,
)
from repomesh.persistence import Database
from repomesh.persistence.base import Base


class LocalHumanAccountRecord(Base):
    __tablename__ = "local_human_accounts"
    __table_args__ = ({"schema": "identity_access"},)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(64))
    password_salt: Mapped[str] = mapped_column(String(32))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class LocalHumanSessionRecord(Base):
    __tablename__ = "local_human_sessions"
    __table_args__ = ({"schema": "identity_access"},)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class InMemoryLocalAccountStore(LocalAccountStore):
    def __init__(self) -> None:
        self.accounts: dict[UUID, LocalHumanAccount] = {}
        self.sessions: dict[str, LocalHumanSession] = {}

    async def count(self) -> int:
        return len(self.accounts)

    async def add_account(self, account: LocalHumanAccount) -> None:
        self.accounts[account.id] = account

    async def get_account(self, account_id: UUID) -> LocalHumanAccount | None:
        return self.accounts.get(account_id)

    async def get_account_by_username(self, username: str) -> LocalHumanAccount | None:
        return next((item for item in self.accounts.values() if item.username == username), None)

    async def list_accounts(self) -> tuple[LocalHumanAccount, ...]:
        return tuple(sorted(self.accounts.values(), key=lambda item: item.username))

    async def add_session(self, session: LocalHumanSession) -> None:
        self.sessions[session.token_hash] = session

    async def get_session_by_token_hash(self, token_hash: str) -> LocalHumanSession | None:
        return self.sessions.get(token_hash)

    async def delete_session(self, token_hash: str) -> None:
        self.sessions.pop(token_hash, None)


class PostgresLocalAccountStore(LocalAccountStore):
    def __init__(self, database: Database) -> None:
        self._database = database

    async def count(self) -> int:
        async with self._database.transaction() as session:
            return int(await session.scalar(select(func.count(LocalHumanAccountRecord.id))) or 0)

    async def add_account(self, account: LocalHumanAccount) -> None:
        async with self._database.transaction() as session:
            session.add(
                LocalHumanAccountRecord(
                    id=account.id,
                    username=account.username,
                    display_name=account.display_name,
                    password_hash=account.password_hash,
                    password_salt=account.password_salt,
                    is_admin=account.is_admin,
                    active=account.active,
                )
            )

    async def get_account(self, account_id: UUID) -> LocalHumanAccount | None:
        async with self._database.transaction() as session:
            record = await session.get(LocalHumanAccountRecord, account_id)
        return self._account(record)

    async def get_account_by_username(self, username: str) -> LocalHumanAccount | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(LocalHumanAccountRecord).where(
                    LocalHumanAccountRecord.username == username
                )
            )
        return self._account(record)

    async def list_accounts(self) -> tuple[LocalHumanAccount, ...]:
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(LocalHumanAccountRecord).order_by(LocalHumanAccountRecord.username)
                )
            ).all()
        return tuple(item for record in records if (item := self._account(record)) is not None)

    async def add_session(self, session_value: LocalHumanSession) -> None:
        async with self._database.transaction() as session:
            session.add(
                LocalHumanSessionRecord(
                    id=session_value.id,
                    account_id=session_value.account_id,
                    token_hash=session_value.token_hash,
                    expires_at=session_value.expires_at,
                )
            )

    async def get_session_by_token_hash(self, token_hash: str) -> LocalHumanSession | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(LocalHumanSessionRecord).where(
                    LocalHumanSessionRecord.token_hash == token_hash
                )
            )
        if record is None:
            return None
        return LocalHumanSession(
            id=record.id,
            account_id=record.account_id,
            token_hash=record.token_hash,
            expires_at=record.expires_at,
        )

    async def delete_session(self, token_hash: str) -> None:
        async with self._database.transaction() as session:
            await session.execute(
                delete(LocalHumanSessionRecord).where(
                    LocalHumanSessionRecord.token_hash == token_hash
                )
            )

    @staticmethod
    def _account(record: LocalHumanAccountRecord | None) -> LocalHumanAccount | None:
        if record is None:
            return None
        return LocalHumanAccount(
            id=record.id,
            username=record.username,
            display_name=record.display_name,
            password_hash=record.password_hash,
            password_salt=record.password_salt,
            is_admin=record.is_admin,
            active=record.active,
        )
