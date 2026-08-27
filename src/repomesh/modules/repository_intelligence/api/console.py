"""Console-facing repository writes.

Deliberately *not* in ``repomesh.api.read_models``. That package is the read
side — projections assembled for the console's screens — and a scan that
registers repositories is a write against the table this module owns. It lives
with its producer.

Two things separate these endpoints from their native siblings in
``router.py``. Neither is the work itself, which is the same code reached
through the same helpers.

*What the caller may say.* The console's bodies carry no credential fields (see
:class:`ConsoleOrgScanRequest`); tokens come from the server's env.

*How long the caller waits.* A forty-repository organization is minutes of
outbound calls, and a browser holding a request open for that is a request that
dies on the first proxy timeout with the scan still running and nothing to show
for it. So the console's scans return 202 with a task id and are polled.

The task registry is an in-process dict, with everything that implies — see
:class:`ScanTaskRecord`. It is honest rather than durable, which is the right
trade while the only consumer is a page the user is looking at, and the wrong
one the moment anything needs to reason about scans it did not start.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException

from repomesh.modules.repository_intelligence.application import ScanRegistration

from .models import (
    ConsoleOrgScanRequest,
    ConsoleRepoScanRequest,
    ScanTaskView,
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

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/console", tags=["console-repositories"])

#: How many finished scans to keep. A registry that only grows is a leak, and
#: the console never looks further back than the scan it just started.
_MAX_REMEMBERED_TASKS = 100


@dataclass(slots=True)
class ScanTaskRecord:
    """One console scan, alive only in this process.

    Three properties the console is told about rather than left to discover:

    *It does not survive a restart.* No table, no outbox — a dict. Polling a
    task the API has forgotten gets a 404 that says why.

    *The scan itself is idempotent.* Registration skips names already in the
    catalog, so running the same scan again is safe. That is what makes the
    next property affordable.

    *The only retry is the whole scan.* Individual failures are counted, not
    listed, and there is no per-repository retry to offer. Listing them would
    mean handing the caller whatever the outbound request saw, which is the
    disclosure this codebase already closed once.
    """

    id: UUID
    kind: str
    url: str
    status: str = "running"
    total: int = 0
    scanned: int = 0
    last_scanned_repository: str | None = None
    registered: int = 0
    skipped: int = 0
    failed: int = 0
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    #: Strong reference to the running coroutine. Without it the event loop is
    #: free to garbage-collect a task nobody awaits, and the scan stops halfway
    #: with the record still reading "running".
    runner: asyncio.Task | None = None


_SCAN_TASKS: dict[UUID, ScanTaskRecord] = {}


def _forget_old_tasks() -> None:
    """Drop the oldest finished records once the registry outgrows its cap."""

    excess = len(_SCAN_TASKS) - _MAX_REMEMBERED_TASKS
    if excess <= 0:
        return
    finished = [record for record in _SCAN_TASKS.values() if record.status != "running"]
    finished.sort(key=lambda record: record.finished_at or record.started_at)
    for record in finished[:excess]:
        _SCAN_TASKS.pop(record.id, None)


def _as_view(record: ScanTaskRecord) -> ScanTaskView:
    return ScanTaskView(
        task_id=record.id,
        kind=record.kind,
        url=record.url,
        status=record.status,
        total=record.total,
        scanned=record.scanned,
        last_scanned_repository=record.last_scanned_repository,
        registered=record.registered,
        skipped=record.skipped,
        failed=record.failed,
        error=record.error,
        started_at=record.started_at,
        finished_at=record.finished_at,
    )


def _progress_reporter(record: ScanTaskRecord) -> Callable[[int, int, str], None]:
    """Feed a scan's per-repository progress into its task record."""

    def report(completed: int, total: int, repository: str) -> None:
        record.scanned = completed
        record.total = total
        record.last_scanned_repository = repository

    return report


async def _scan_with_fetcher(
    fetcher: object,
    scan: Callable[[], Awaitable[ScanRegistration]],
) -> ScanRegistration:
    """Run a background scan and always release the fetcher's HTTP client.

    The fetcher is built in the request handler but lives as long as the
    background task; without this the pooled client would stay open after
    the scan finished, leaking connections for the life of the API process.
    """

    try:
        return await scan()
    finally:
        await fetcher.aclose()  # type: ignore[attr-defined]


async def _drive(
    record: ScanTaskRecord, scan: Callable[[], Awaitable[ScanRegistration]]
) -> None:
    """Run a scan to completion and record the outcome, whatever it is.

    Nothing may escape: this coroutine has no caller to catch for it, and an
    exception that got out would leave the record reading "running" forever
    while the console polled a scan that had already died.
    """

    try:
        outcome = await scan()
    except ScanFailed as error:
        record.status = "failed"
        record.error = str(error)
    except Exception:
        _logger.exception("scan task %s crashed for %s", record.id, record.url)
        record.status = "failed"
        record.error = "scan failed"
    else:
        record.status = "succeeded"
        record.total = outcome.total_scanned
        record.scanned = outcome.total_scanned
        record.registered = len(outcome.registered)
        record.skipped = outcome.skipped
        record.failed = outcome.failed
    finally:
        record.finished_at = datetime.now(UTC)


def _start(
    *,
    kind: str,
    url: str,
    total: int,
    scan: Callable[[ScanTaskRecord], Awaitable[ScanRegistration]],
) -> ScanTaskRecord:
    """Register a task and set it running in the background.

    Everything that can be refused — a local path, a host off the allowlist, a
    group URL sent to the single-repo scan — has already been refused by the
    time this is called. A 202 means the request was acceptable, not that the
    scan will succeed.
    """

    record = ScanTaskRecord(id=uuid4(), kind=kind, url=url, total=total)
    _SCAN_TASKS[record.id] = record
    _forget_old_tasks()
    # ``scan`` takes the record because a scan reports progress *into* it, and
    # the record cannot exist before the id it is keyed by.
    record.runner = asyncio.create_task(_drive(record, lambda: scan(record)))
    return record


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
    "/repositories/scan-org",
    response_model=ScanTaskView,
    status_code=202,
    dependencies=[ACTION_TOKEN],
)
async def console_scan_organization(
    body: ConsoleOrgScanRequest, catalog: CatalogDependency
) -> ScanTaskView:
    """Start an organization scan; poll ``scan-tasks/{id}`` for the counts.

    202, not 200: the response says the scan was accepted and started, and
    nothing about whether it will find anything. The refusals that *can* be
    decided up front still happen up front and still come back as 400.

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
    max_workers = body.max_workers

    record = _start(
        kind="organization",
        url=url,
        # Unknown until the platform lists the org; the console shows "n of ?"
        # rather than a total invented before anyone counted.
        total=0,
        scan=lambda task: _scan_with_fetcher(
            fetcher,
            lambda: perform_org_scan(
                url,
                fetcher,
                catalog,
                max_workers=max_workers,
                on_progress=_progress_reporter(task),
            ),
        ),
    )
    return _as_view(record)


@router.post(
    "/repositories/scan-repo",
    response_model=ScanTaskView,
    status_code=202,
    dependencies=[ACTION_TOKEN],
)
async def console_scan_repository(
    body: ConsoleRepoScanRequest, catalog: CatalogDependency
) -> ScanTaskView:
    """Start a single-repository scan; poll ``scan-tasks/{id}`` for the counts.

    The console has a single URL box: it badges what the user pasted with
    ``GET /repositories/url-type`` and posts here or to ``scan-org`` on that
    verdict. The verdict is re-checked server-side rather than trusted.
    """

    github_token, gitlab_token = server_scan_credentials()
    url = str(body.repo_url).rstrip("/")
    fetcher = build_scan_fetcher(
        url,
        target="repository",
        github_token=github_token,
        gitlab_token=gitlab_token,
    )
    await require_single_repo_url(url, fetcher)

    record = _start(
        kind="repository",
        url=url,
        total=1,
        scan=lambda _task: _scan_with_fetcher(
            fetcher,
            lambda: perform_repo_scan(url, fetcher, catalog),
        ),
    )
    return _as_view(record)


@router.get(
    "/repositories/scan-tasks/{task_id}",
    response_model=ScanTaskView,
    dependencies=[ACTION_TOKEN],
)
async def get_scan_task(task_id: UUID) -> ScanTaskView:
    """Report a scan's progress, or its result.

    While ``status`` is ``running`` the counts are partial — ``scanned`` of
    ``total`` moves, and the registration counts stay at zero until the scan
    finishes, because nothing is written until every repository has been read.

    A 404 here is ambiguous on purpose-free terms: the task never existed, or
    this process was restarted and forgot it. The detail says so, so the
    console can tell the user "the scan was lost, run it again" — which is
    safe, because re-scanning skips what is already in the catalog.
    """

    record = _SCAN_TASKS.get(task_id)
    if record is None:
        raise HTTPException(
            404,
            "scan task not found — scan state lives in this process only and is lost "
            "on restart; re-running the scan is safe and skips what is already registered",
        )
    return _as_view(record)
