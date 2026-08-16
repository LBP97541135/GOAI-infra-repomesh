import pytest

from repomesh.modules.identity_access import (
    LocalAccountConflict,
    LocalAccountService,
    LocalAccountValidationError,
    LocalAuthenticationError,
)
from repomesh.modules.identity_access.infrastructure import InMemoryLocalAccountStore


@pytest.mark.asyncio
async def test_bootstrap_login_authenticate_and_logout() -> None:
    service = LocalAccountService(InMemoryLocalAccountStore(), session_ttl_seconds=60)
    admin = await service.bootstrap_admin("Admin", "strong-password-123", "Administrator")
    assert admin.username == "admin"
    assert admin.is_admin

    token, authenticated = await service.login("ADMIN", "strong-password-123")
    assert authenticated.id == admin.id
    assert (await service.authenticate(token)).id == admin.id

    await service.logout(token)
    with pytest.raises(LocalAuthenticationError, match="invalid or expired"):
        await service.authenticate(token)


@pytest.mark.asyncio
async def test_only_admin_can_create_accounts() -> None:
    service = LocalAccountService(InMemoryLocalAccountStore())
    admin = await service.bootstrap_admin("admin", "strong-password-123", "Admin")
    reviewer = await service.create_account(
        admin, "reviewer", "another-password-123", "Reviewer"
    )
    assert not reviewer.is_admin
    with pytest.raises(LocalAuthenticationError, match="administrator") as refused:
        await service.create_account(
            reviewer, "other", "another-password-456", "Other"
        )
    # Stays the plain base type; the API maps that alone to 403.
    assert type(refused.value) is LocalAuthenticationError


@pytest.mark.asyncio
async def test_rejected_account_input_is_typed_as_validation_not_authentication() -> None:
    service = LocalAccountService(InMemoryLocalAccountStore())
    admin = await service.bootstrap_admin("admin", "strong-password-123", "Admin")

    with pytest.raises(LocalAccountValidationError, match="at least 12 characters"):
        await service.create_account(admin, "reviewer", "short", "Reviewer")
    with pytest.raises(LocalAccountValidationError, match="display name is required"):
        await service.create_account(admin, "reviewer", "another-password-123", "   ")
    with pytest.raises(LocalAccountValidationError, match="username format is invalid"):
        await service.create_account(admin, "no spaces allowed", "another-password-123", "Nope")


@pytest.mark.asyncio
async def test_taken_username_and_repeated_bootstrap_are_typed_as_conflicts() -> None:
    service = LocalAccountService(InMemoryLocalAccountStore())
    admin = await service.bootstrap_admin("admin", "strong-password-123", "Admin")
    await service.create_account(admin, "reviewer", "another-password-123", "Reviewer")

    with pytest.raises(LocalAccountConflict, match="username already exists"):
        await service.create_account(admin, "REVIEWER", "another-password-456", "Twin")
    with pytest.raises(LocalAccountConflict, match="bootstrap is already complete"):
        await service.bootstrap_admin("second", "strong-password-456", "Second")


@pytest.mark.asyncio
async def test_malformed_username_at_login_stays_an_authentication_failure() -> None:
    """The new subclasses must not change what ``login`` raises.

    ``login`` normalizes through the same validator, so if the validation type
    were a sibling of LocalAuthenticationError instead of a subclass, the login
    route would stop catching it and answer 500.
    """

    service = LocalAccountService(InMemoryLocalAccountStore())
    await service.bootstrap_admin("admin", "strong-password-123", "Admin")

    with pytest.raises(LocalAuthenticationError):
        await service.login("no spaces allowed", "strong-password-123")
