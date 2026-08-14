import asyncio
import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from repomesh.shared.domain import new_id


class LocalAuthenticationError(RuntimeError):
    """The caller is not who, or not what, they need to be.

    Reserved for authentication and authorization failures: bad credentials,
    an expired session, a disabled account, a non-administrator asking for an
    administrator action. Account *shaping* failures are the subclasses below.
    """


class LocalAccountValidationError(LocalAuthenticationError):
    """The submitted account fields cannot make an account.

    A subclass rather than a sibling on purpose: ``login`` normalizes the
    username through the same code path, and its handler catches the base
    class. As a subclass, a malformed username at login keeps returning the
    flat 401 it always returned; as a sibling it would escape that handler and
    become a 500.

    It does not change what login *says*. That handler passes ``str(error)``
    through, so a malformed username has always answered 401 with "username
    format is invalid" — before this split and after it. Whether login should
    distinguish a malformed username from an unknown one at all is a separate
    question this class does not settle.
    """


class LocalAccountConflict(LocalAuthenticationError):
    """The account store is already in a state that refuses this request.

    Username taken, bootstrap already done. Subclass for the same reason as
    ``LocalAccountValidationError``.
    """


@dataclass(frozen=True, slots=True)
class LocalHumanAccount:
    username: str
    display_name: str
    password_hash: str
    password_salt: str
    is_admin: bool = False
    active: bool = True
    id: UUID = field(default_factory=new_id)


@dataclass(frozen=True, slots=True)
class LocalHumanAccountView:
    id: UUID
    username: str
    display_name: str
    is_admin: bool
    active: bool


@dataclass(frozen=True, slots=True)
class LocalHumanSession:
    account_id: UUID
    token_hash: str
    expires_at: datetime
    id: UUID = field(default_factory=new_id)


class LocalAccountStore(Protocol):
    async def count(self) -> int: ...

    async def add_account(self, account: LocalHumanAccount) -> None: ...

    async def get_account(self, account_id: UUID) -> LocalHumanAccount | None: ...

    async def get_account_by_username(self, username: str) -> LocalHumanAccount | None: ...

    async def list_accounts(self) -> tuple[LocalHumanAccount, ...]: ...

    async def add_session(self, session: LocalHumanSession) -> None: ...

    async def get_session_by_token_hash(self, token_hash: str) -> LocalHumanSession | None: ...

    async def delete_session(self, token_hash: str) -> None: ...


class LocalAccountService:
    def __init__(self, store: LocalAccountStore, *, session_ttl_seconds: int = 28800) -> None:
        self._store = store
        self._session_ttl_seconds = session_ttl_seconds

    @property
    def session_ttl_seconds(self) -> int:
        return self._session_ttl_seconds

    async def bootstrap_admin(
        self, username: str, password: str, display_name: str
    ) -> LocalHumanAccountView:
        if await self._store.count() != 0:
            raise LocalAccountConflict("local account bootstrap is already complete")
        return await self._create(username, password, display_name, is_admin=True)

    async def create_account(
        self,
        actor: LocalHumanAccountView,
        username: str,
        password: str,
        display_name: str,
        *,
        is_admin: bool = False,
    ) -> LocalHumanAccountView:
        if not actor.is_admin or not actor.active:
            raise LocalAuthenticationError("local administrator permission is required")
        return await self._create(username, password, display_name, is_admin=is_admin)

    async def login(self, username: str, password: str) -> tuple[str, LocalHumanAccountView]:
        account = await self._store.get_account_by_username(self._normalize_username(username))
        if account is None or not account.active:
            raise LocalAuthenticationError("invalid username or password")
        valid = await asyncio.to_thread(
            self._verify_password, password, account.password_salt, account.password_hash
        )
        if not valid:
            raise LocalAuthenticationError("invalid username or password")
        token = secrets.token_urlsafe(32)
        await self._store.add_session(
            LocalHumanSession(
                account_id=account.id,
                token_hash=self._token_hash(token),
                expires_at=datetime.now(UTC) + timedelta(seconds=self._session_ttl_seconds),
            )
        )
        return token, self._view(account)

    async def authenticate(self, token: str) -> LocalHumanAccountView:
        session = await self._store.get_session_by_token_hash(self._token_hash(token))
        expires_at = None if session is None else session.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if session is None or expires_at is None or expires_at <= datetime.now(UTC):
            raise LocalAuthenticationError("local session is invalid or expired")
        account = await self._store.get_account(session.account_id)
        if account is None or not account.active:
            raise LocalAuthenticationError("local account is disabled")
        return self._view(account)

    async def get_account(self, account_id: UUID) -> LocalHumanAccountView | None:
        account = await self._store.get_account(account_id)
        return self._view(account) if account is not None else None

    async def list_accounts(self) -> tuple[LocalHumanAccountView, ...]:
        return tuple(self._view(account) for account in await self._store.list_accounts())

    async def logout(self, token: str) -> None:
        await self._store.delete_session(self._token_hash(token))

    async def _create(
        self, username: str, password: str, display_name: str, *, is_admin: bool
    ) -> LocalHumanAccountView:
        normalized = self._normalize_username(username)
        if len(password) < 12:
            raise LocalAccountValidationError("password must contain at least 12 characters")
        if not display_name.strip():
            raise LocalAccountValidationError("display name is required")
        if await self._store.get_account_by_username(normalized) is not None:
            raise LocalAccountConflict("username already exists")
        salt = secrets.token_hex(16)
        password_hash = await asyncio.to_thread(self._password_hash, password, salt)
        account = LocalHumanAccount(
            username=normalized,
            display_name=display_name.strip(),
            password_hash=password_hash,
            password_salt=salt,
            is_admin=is_admin,
        )
        await self._store.add_account(account)
        return self._view(account)

    @staticmethod
    def _normalize_username(username: str) -> str:
        normalized = username.strip().lower()
        if len(normalized) < 3 or not all(
            character.isalnum() or character in "._-" for character in normalized
        ):
            raise LocalAccountValidationError("username format is invalid")
        return normalized

    @staticmethod
    def _password_hash(password: str, salt: str) -> str:
        return hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32
        ).hex()

    @classmethod
    def _verify_password(cls, password: str, salt: str, expected: str) -> bool:
        return hmac.compare_digest(cls._password_hash(password, salt), expected)

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _view(account: LocalHumanAccount) -> LocalHumanAccountView:
        return LocalHumanAccountView(
            id=account.id,
            username=account.username,
            display_name=account.display_name,
            is_admin=account.is_admin,
            active=account.active,
        )
