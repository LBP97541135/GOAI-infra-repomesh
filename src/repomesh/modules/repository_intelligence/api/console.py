"""Console-facing repository writes.

Deliberately *not* in ``repomesh.api.read_models``. That package is the read
side — projections assembled for the console's screens — and a scan that
registers repositories is a write against the table this module owns. It lives
with its producer.

What separates these endpoints from their native siblings in ``router.py`` is
not the work they do but what they are allowed to be told. The console's bodies
carry no credential fields (see :class:`ConsoleOrgScanRequest`); the tokens come
from the server's env. Everything else — the SSRF allowlist, the silence about
what an outbound request saw, the skip-by-name idempotence — is the same code
path, reached through the same helpers.
"""

from fastapi import APIRouter, HTTPException

from .models import (
    ConsoleOrgScanRequest,
    ConsoleRepoScanRequest,
    OrgScanResult,
    RepoScanResult,
    RepositoryView,
)
from .router import (
    ACTION_TOKEN,
    CatalogDependency,
    ScanFailed,
    build_scan_fetcher,
    perform_org_scan,
    perform_repo_scan,
    require_single_repo_url,
)

router = APIRouter(prefix="/console", tags=["console-repositories"])


def server_scan_credentials() -> tuple[str, str]:
    """The scan credentials the console is allowed to use: the server's own.

    Returns ``(github_token, gitlab_token)``. Empty strings mean unauthenticated
    reads, which is enough for public repositories and is the honest default —
    an operator who wants private repositories in the console configures the
    env, and no browser ever holds the secret.
    """

    from repomesh.settings import get_settings  # noqa: PLC0415

    settings = get_settings()
    return settings.repository_scan_github_token, settings.repository_scan_gitlab_token


@router.post(
    "/repositories/scan-org", response_model=OrgScanResult, dependencies=[ACTION_TOKEN]
)
async def console_scan_organization(
    body: ConsoleOrgScanRequest, catalog: CatalogDependency
) -> OrgScanResult:
    """Scan an organization on behalf of the console.

    Authentication is the shared action token, the same one ``POST /issues``
    takes. That token names no subject and no workspace; a subject-carrying
    session ticket is the adopted backlog item this endpoint shares with every
    other write on the console face, and it is an architecture change, not
    something to improvise here.
    """

    github_token, gitlab_token = server_scan_credentials()
    url = str(body.org_url)
    fetcher = build_scan_fetcher(
        url,
        target="organization",
        github_token=github_token,
        gitlab_token=gitlab_token,
    )

    try:
        outcome = await perform_org_scan(url, fetcher, catalog, max_workers=body.max_workers)
    except ScanFailed as error:
        raise HTTPException(502, str(error)) from error

    return OrgScanResult(
        org_url=url,
        total_scanned=outcome.total_scanned,
        registered=len(outcome.registered),
        skipped=outcome.skipped,
        failed=outcome.failed,
        repositories=[RepositoryView.model_validate(p) for p in outcome.registered],
    )


@router.post(
    "/repositories/scan-repo", response_model=RepoScanResult, dependencies=[ACTION_TOKEN]
)
async def console_scan_repository(
    body: ConsoleRepoScanRequest, catalog: CatalogDependency
) -> RepoScanResult:
    """Scan one repository on behalf of the console.

    The console has a single URL box: it badges what the user pasted with
    ``GET /repositories/url-type`` and posts here or to ``scan-org`` on that
    verdict. The verdict is re-checked server-side rather than trusted.
    """

    github_token, gitlab_token = server_scan_credentials()
    url = str(body.repo_url).rstrip("/")
    require_single_repo_url(url)
    fetcher = build_scan_fetcher(
        url,
        target="repository",
        github_token=github_token,
        gitlab_token=gitlab_token,
    )

    try:
        outcome = await perform_repo_scan(url, fetcher, catalog)
    except ScanFailed as error:
        raise HTTPException(502, str(error)) from error

    return RepoScanResult(
        repo_url=url,
        total_scanned=outcome.total_scanned,
        registered=len(outcome.registered),
        skipped=outcome.skipped,
        failed=outcome.failed,
        repositories=[RepositoryView.model_validate(p) for p in outcome.registered],
    )
