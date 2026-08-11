import pytest

from repomesh.modules.identity_access import LocalAccountService, LocalAuthenticationError
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
    with pytest.raises(LocalAuthenticationError, match="administrator"):
        await service.create_account(
            reviewer, "other", "another-password-456", "Other"
        )
