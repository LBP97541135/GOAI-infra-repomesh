"""GitHub rollback gateway: revert branches, revert PRs and replay safety.

No test here talks to GitHub: every HTTP call goes through an
``httpx.MockTransport`` and the Git data plane is a fake publisher.
"""

from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest

from repomesh.integrations.scm.contracts import SCMConflict
from repomesh.integrations.scm.github import GitHubAdapter
from repomesh.integrations.scm.recovery import (
    GovernedRecoveryActionHandler,
    RecoveryExecutionContext,
    RecoverySagaExecutor,
    RecoveryWaiting,
    RevertConflict,
)
from repomesh.integrations.scm.revert import (
    GitHubRevertDeliveryGateway,
    RevertBranch,
    RevertBranchRequest,
)
from repomesh.modules.delivery import DeliveryService, InMemoryChangeSetStore
from repomesh.modules.delivery.contracts import (
    CIObservationCommand,
    MergeObservationCommand,
    PlanRecoveryCommand,
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RecoveryActionKind,
    RecoveryTrigger,
    RepositoryCandidateInput,
)

CANDIDATE_SHA = "a" * 40
MERGE_SHA = "d" * 40
REVERT_SHA = "e" * 40


class Catalog:
    def __init__(self, repository_id: UUID) -> None:
        self._repository_id = repository_id

    async def get(self, repository_id: UUID):
        if repository_id != self._repository_id:
            return None
        return SimpleNamespace(id=repository_id, url="https://github.com/acme/pricing")


class Reverter:
    """Fake Git data plane: records requests, never runs Git."""

    def __init__(self, *, created: bool = True, conflict: bool = False) -> None:
        self.requests: list[RevertBranchRequest] = []
        self._created = created
        self._conflict = conflict

    async def ensure(self, request: RevertBranchRequest) -> RevertBranch:
        self.requests.append(request)
        if self._conflict:
            raise RevertConflict("git revert conflicts with main")
        return RevertBranch(request.branch_name, REVERT_SHA, created=self._created)


def pull_payload(
    *,
    number: int = 42,
    sha: str = CANDIDATE_SHA,
    state: str = "open",
    branch: str = "repomesh/pricing",
    draft: bool = False,
    mergeable: bool | None = True,
    merged: bool = False,
) -> dict:
    return {
        "number": number,
        "html_url": f"https://github.com/acme/pricing/pull/{number}",
        "state": state,
        "draft": draft,
        "merged_at": "2026-08-12T00:00:00Z" if merged else None,
        "merge_commit_sha": "f" * 40 if merged else None,
        "head": {"ref": branch, "sha": sha},
        "base": {"ref": "main", "sha": "b" * 40},
        "mergeable": mergeable,
    }


def check_runs_payload(
    *,
    name: str = "ci",
    status: str = "completed",
    conclusion: str = "success",
    sha: str = REVERT_SHA,
) -> dict:
    return {
        "check_runs": [
            {
                "id": 7,
                "name": name,
                "head_sha": sha,
                "status": status,
                "conclusion": conclusion,
                "output": {"summary": f"{name} {conclusion}"},
            }
        ]
    }


PROTECTED_MAIN = {
    "required_status_checks": {"contexts": ["ci"]},
    "required_pull_request_reviews": {"required_approving_review_count": 1},
}


def revert_gate_response(
    request: httpx.Request, *, checks: dict | None = None, protection: dict | None = None
) -> httpx.Response | None:
    """Answer the two reads the revert CI gate makes, or None if unrelated."""

    if request.url.path.endswith("/check-runs"):
        return httpx.Response(200, json=checks if checks is not None else check_runs_payload())
    if request.url.path.endswith("/protection"):
        return httpx.Response(
            200, json=protection if protection is not None else PROTECTED_MAIN
        )
    return None


async def delivered_change_set(*, merged: bool):
    """One ChangeSet whose single candidate is merged (or only PR-open)."""

    repository_id = uuid4()
    service = DeliveryService(InMemoryChangeSetStore())
    change_set = await service.prepare(
        PrepareChangeSetCommand(
            uuid4(),
            uuid4(),
            uuid4(),
            "Rollback delivery",
            uuid4(),
            (
                RepositoryCandidateInput(
                    repository_id,
                    uuid4(),
                    CANDIDATE_SHA,
                    "b" * 40,
                    "repomesh/pricing",
                ),
            ),
        ),
        idempotency_key="rollback-delivery",
    )
    await service.observe_pull_request(
        PullRequestObservationCommand(
            change_set.id,
            repository_id,
            42,
            "https://github.com/acme/pricing/pull/42",
            CANDIDATE_SHA,
        )
    )
    await service.observe_ci(
        CIObservationCommand(change_set.id, repository_id, True, "ci", "passed")
    )
    if merged:
        await service.observe_merge(
            MergeObservationCommand(change_set.id, repository_id, MERGE_SHA)
        )
    recovered = await service.plan_recovery(
        PlanRecoveryCommand(
            change_set.id,
            RecoveryTrigger.PARTIAL_MERGE,
            "downstream delivery failed",
        )
    )
    return service, recovered, repository_id


def context_for(change_set, kind: RecoveryActionKind) -> RecoveryExecutionContext:
    plan = change_set.recovery_plans[-1]
    action = next(item for item in plan.actions if item.kind is kind)
    return RecoveryExecutionContext(change_set, action)


def build_gateway(repository_id: UUID, handler, reverter=None):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = GitHubAdapter(lambda repo: "installation-token", client=client)
    gateway = GitHubRevertDeliveryGateway(
        Catalog(repository_id), adapter, reverter or Reverter()
    )
    return gateway, client


def revert_branch_of(change_set, repository_id: UUID) -> str:
    return (
        f"repomesh/revert/{str(change_set.id)[:8]}/"
        f"{str(repository_id)[:8]}-{MERGE_SHA[:12]}"
    )


@pytest.mark.asyncio
async def test_closing_an_already_closed_pull_request_issues_no_write() -> None:
    _, change_set, repository_id = await delivered_change_set(merged=False)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=pull_payload(state="closed"))

    gateway, client = build_gateway(repository_id, handler)

    detail = await gateway.close_pull_request(
        context_for(change_set, RecoveryActionKind.CLOSE_PULL_REQUEST)
    )

    assert [request.method for request in requests] == ["GET"]
    assert "#42 is closed" in detail
    await client.aclose()


@pytest.mark.asyncio
async def test_closing_a_remotely_merged_pull_request_demands_a_revert() -> None:
    _, change_set, repository_id = await delivered_change_set(merged=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pull_payload(state="closed", merged=True))

    gateway, client = build_gateway(repository_id, handler)

    with pytest.raises(SCMConflict, match="merged remotely"):
        await gateway.close_pull_request(
            context_for(change_set, RecoveryActionKind.CLOSE_PULL_REQUEST)
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_revert_pull_request_is_opened_from_a_deterministic_branch() -> None:
    import json

    _, change_set, repository_id = await delivered_change_set(merged=True)
    branch = revert_branch_of(change_set, repository_id)
    requests: list[httpx.Request] = []
    responses = iter(
        [
            httpx.Response(200, json=[]),
            httpx.Response(201, json=pull_payload(number=43, sha=REVERT_SHA, branch=branch)),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return next(responses)

    reverter = Reverter(created=True)
    gateway, client = build_gateway(repository_id, handler, reverter)

    detail = await gateway.create_revert_pull_request(
        context_for(change_set, RecoveryActionKind.CREATE_REVERT_PULL_REQUEST)
    )

    assert reverter.requests[0].merge_sha == MERGE_SHA
    assert reverter.requests[0].branch_name == branch
    body = json.loads(requests[1].content)
    assert body["head"] == branch
    assert body["base"] == "main"
    assert body["draft"] is False
    assert "published branch" in detail and "#43" in detail
    await client.aclose()


@pytest.mark.asyncio
async def test_replayed_revert_reuses_the_published_branch_and_pull_request() -> None:
    _, change_set, repository_id = await delivered_change_set(merged=True)
    branch = revert_branch_of(change_set, repository_id)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json=[pull_payload(number=43, sha=REVERT_SHA, branch=branch)]
        )

    reverter = Reverter(created=False)
    gateway, client = build_gateway(repository_id, handler, reverter)

    detail = await gateway.create_revert_pull_request(
        context_for(change_set, RecoveryActionKind.CREATE_REVERT_PULL_REQUEST)
    )

    assert [request.method for request in requests] == ["GET"]
    assert "reused branch" in detail
    await client.aclose()


@pytest.mark.asyncio
async def test_git_revert_conflict_reaches_the_saga_as_a_revert_conflict() -> None:
    _, change_set, repository_id = await delivered_change_set(merged=True)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never called
        raise AssertionError("a conflicting revert must not reach GitHub")

    gateway, client = build_gateway(repository_id, handler, Reverter(conflict=True))

    with pytest.raises(RevertConflict):
        await gateway.create_revert_pull_request(
            context_for(change_set, RecoveryActionKind.CREATE_REVERT_PULL_REQUEST)
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_merging_an_already_merged_revert_issues_no_second_merge() -> None:
    _, change_set, repository_id = await delivered_change_set(merged=True)
    branch = revert_branch_of(change_set, repository_id)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                pull_payload(
                    number=43, sha=REVERT_SHA, branch=branch, state="closed", merged=True
                )
            ],
        )

    gateway, client = build_gateway(repository_id, handler)

    detail = await gateway.merge_revert_pull_request(
        context_for(change_set, RecoveryActionKind.MERGE_REVERT_PULL_REQUEST)
    )

    assert [request.method for request in requests] == ["GET"]
    assert "already merged" in detail
    await client.aclose()


@pytest.mark.asyncio
async def test_merging_a_revert_guards_on_the_revert_head_sha() -> None:
    _, change_set, repository_id = await delivered_change_set(merged=True)
    branch = revert_branch_of(change_set, repository_id)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "PUT":
            return httpx.Response(200, json={"merged": True, "sha": "c" * 40, "message": "ok"})
        gate = revert_gate_response(request)
        if gate is not None:
            return gate
        return httpx.Response(
            200, json=[pull_payload(number=43, sha=REVERT_SHA, branch=branch)]
        )

    gateway, client = build_gateway(repository_id, handler)

    detail = await gateway.merge_revert_pull_request(
        context_for(change_set, RecoveryActionKind.MERGE_REVERT_PULL_REQUEST)
    )

    assert [request.method for request in requests] == ["GET", "GET", "GET", "PUT"]
    assert f'"sha":"{REVERT_SHA}"'.encode() in requests[-1].content
    assert requests[-1].url.path.endswith("/pulls/43/merge")
    assert "rolled back" in detail
    await client.aclose()


@pytest.mark.asyncio
async def test_unmergeable_revert_pull_request_becomes_a_revert_conflict() -> None:
    _, change_set, repository_id = await delivered_change_set(merged=True)
    branch = revert_branch_of(change_set, repository_id)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[pull_payload(number=43, sha=REVERT_SHA, branch=branch, mergeable=False)],
        )

    gateway, client = build_gateway(repository_id, handler)

    with pytest.raises(RevertConflict, match="#43"):
        await gateway.merge_revert_pull_request(
            context_for(change_set, RecoveryActionKind.MERGE_REVERT_PULL_REQUEST)
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_revert_is_not_merged_while_its_own_checks_are_still_running() -> None:
    _, change_set, repository_id = await delivered_change_set(merged=True)
    branch = revert_branch_of(change_set, repository_id)
    writes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            writes.append(request.method)
        gate = revert_gate_response(
            request, checks=check_runs_payload(status="in_progress", conclusion="")
        )
        if gate is not None:
            return gate
        return httpx.Response(
            200, json=[pull_payload(number=43, sha=REVERT_SHA, branch=branch)]
        )

    gateway, client = build_gateway(repository_id, handler)

    with pytest.raises(RecoveryWaiting, match="checks running"):
        await gateway.merge_revert_pull_request(
            context_for(change_set, RecoveryActionKind.MERGE_REVERT_PULL_REQUEST)
        )
    assert writes == []
    await client.aclose()


@pytest.mark.asyncio
async def test_revert_waits_when_a_required_check_has_not_reported_at_all() -> None:
    """An empty check list is a wait only because protection demands a check."""

    _, change_set, repository_id = await delivered_change_set(merged=True)
    branch = revert_branch_of(change_set, repository_id)

    def handler(request: httpx.Request) -> httpx.Response:
        gate = revert_gate_response(request, checks={"check_runs": []})
        if gate is not None:
            return gate
        return httpx.Response(
            200, json=[pull_payload(number=43, sha=REVERT_SHA, branch=branch)]
        )

    gateway, client = build_gateway(repository_id, handler)

    with pytest.raises(RecoveryWaiting, match="missing required checks"):
        await gateway.merge_revert_pull_request(
            context_for(change_set, RecoveryActionKind.MERGE_REVERT_PULL_REQUEST)
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_revert_whose_own_checks_failed_is_a_conflict_not_a_wait() -> None:
    _, change_set, repository_id = await delivered_change_set(merged=True)
    branch = revert_branch_of(change_set, repository_id)
    writes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            writes.append(request.method)
        gate = revert_gate_response(request, checks=check_runs_payload(conclusion="failure"))
        if gate is not None:
            return gate
        return httpx.Response(
            200, json=[pull_payload(number=43, sha=REVERT_SHA, branch=branch)]
        )

    gateway, client = build_gateway(repository_id, handler)

    with pytest.raises(SCMConflict, match="failed its own checks"):
        await gateway.merge_revert_pull_request(
            context_for(change_set, RecoveryActionKind.MERGE_REVERT_PULL_REQUEST)
        )
    assert writes == []
    await client.aclose()


@pytest.mark.asyncio
async def test_unprotected_base_branch_does_not_strand_a_rollback() -> None:
    """No protection and no checks means nothing to wait for, so the revert merges."""

    _, change_set, repository_id = await delivered_change_set(merged=True)
    branch = revert_branch_of(change_set, repository_id)
    merged_revert = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal merged_revert
        if request.method == "PUT":
            merged_revert = True
            return httpx.Response(200, json={"merged": True, "sha": "c" * 40, "message": "ok"})
        if request.url.path.endswith("/protection"):
            return httpx.Response(404, json={"message": "Branch not protected"})
        if request.url.path.endswith("/check-runs"):
            return httpx.Response(200, json={"check_runs": []})
        return httpx.Response(
            200, json=[pull_payload(number=43, sha=REVERT_SHA, branch=branch)]
        )

    gateway, client = build_gateway(repository_id, handler)

    detail = await gateway.merge_revert_pull_request(
        context_for(change_set, RecoveryActionKind.MERGE_REVERT_PULL_REQUEST)
    )

    assert merged_revert
    assert "rolled back" in detail
    await client.aclose()


@pytest.mark.asyncio
async def test_saga_retries_a_waiting_revert_instead_of_failing_the_plan() -> None:
    """The gate and the Saga agree: a running check parks nothing permanently."""

    service, change_set, repository_id = await delivered_change_set(merged=True)
    branch = revert_branch_of(change_set, repository_id)
    ci_done = False
    merged_revert = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal merged_revert
        if request.method == "PUT":
            merged_revert = True
            return httpx.Response(200, json={"merged": True, "sha": "c" * 40, "message": "ok"})
        gate = revert_gate_response(
            request,
            checks=check_runs_payload()
            if ci_done
            else check_runs_payload(status="in_progress", conclusion=""),
        )
        if gate is not None:
            return gate
        if request.url.params.get("head") == f"acme:{branch}":
            if merged_revert:
                return httpx.Response(
                    200,
                    json=[
                        pull_payload(
                            number=43,
                            sha=REVERT_SHA,
                            branch=branch,
                            state="closed",
                            merged=True,
                        )
                    ],
                )
            return httpx.Response(
                200, json=[pull_payload(number=43, sha=REVERT_SHA, branch=branch)]
            )
        return httpx.Response(200, json=[])

    gateway, client = build_gateway(repository_id, handler, Reverter(created=True))
    saga = RecoverySagaExecutor(service, GovernedRecoveryActionHandler(gateway))

    await saga.run_once()  # opens the revert PR
    await saga.run_once()  # checks still running -> waits, does not merge
    waiting = await service.get(change_set.id)
    merge_action = waiting.recovery_plans[-1].actions[1]
    assert merge_action.status.value == "pending"
    assert waiting.status.value == "compensating"
    assert not merged_revert

    ci_done = True
    await saga.run_once()  # same action retried, now green
    await saga.run_once()  # revalidate
    completed = await service.get(change_set.id)
    assert merged_revert
    actions = completed.recovery_plans[-1].actions
    assert all(action.status.value == "succeeded" for action in actions)
    assert completed.status.value == "compensated"
    await client.aclose()


@pytest.mark.asyncio
async def test_revalidation_confirms_the_revert_landed() -> None:
    _, change_set, repository_id = await delivered_change_set(merged=True)
    branch = revert_branch_of(change_set, repository_id)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                pull_payload(
                    number=43, sha=REVERT_SHA, branch=branch, state="closed", merged=True
                )
            ],
        )

    gateway, client = build_gateway(repository_id, handler)

    detail = await gateway.revalidate(
        context_for(change_set, RecoveryActionKind.REVALIDATE_CHANGESET)
    )

    assert "reverted by PR #43" in detail
    await client.aclose()


@pytest.mark.asyncio
async def test_revalidation_refuses_a_changeset_that_is_still_merged() -> None:
    _, change_set, repository_id = await delivered_change_set(merged=True)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    gateway, client = build_gateway(repository_id, handler)

    with pytest.raises(SCMConflict, match="still carries merge"):
        await gateway.revalidate(
            context_for(change_set, RecoveryActionKind.REVALIDATE_CHANGESET)
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_revalidation_refuses_a_pull_request_that_is_still_open() -> None:
    _, change_set, repository_id = await delivered_change_set(merged=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pull_payload(state="open"))

    gateway, client = build_gateway(repository_id, handler)

    with pytest.raises(SCMConflict, match="still open"):
        await gateway.revalidate(
            context_for(change_set, RecoveryActionKind.REVALIDATE_CHANGESET)
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_saga_rolls_a_merged_changeset_back_through_github() -> None:
    """End to end: the Saga drives all three GitHub rollback actions in order."""

    service, change_set, repository_id = await delivered_change_set(merged=True)
    branch = revert_branch_of(change_set, repository_id)
    merged_revert = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal merged_revert
        if request.method == "PUT":
            merged_revert = True
            return httpx.Response(200, json={"merged": True, "sha": "c" * 40, "message": "ok"})
        gate = revert_gate_response(request)
        if gate is not None:
            return gate
        if request.url.params.get("head") == f"acme:{branch}":
            if merged_revert:
                return httpx.Response(
                    200,
                    json=[
                        pull_payload(
                            number=43,
                            sha=REVERT_SHA,
                            branch=branch,
                            state="closed",
                            merged=True,
                        )
                    ],
                )
            return httpx.Response(
                200, json=[pull_payload(number=43, sha=REVERT_SHA, branch=branch)]
            )
        return httpx.Response(200, json=[])

    gateway, client = build_gateway(repository_id, handler, Reverter(created=True))
    saga = RecoverySagaExecutor(service, GovernedRecoveryActionHandler(gateway))

    await saga.run_once()
    await saga.run_once()
    await saga.run_once()

    completed = await service.get(change_set.id)
    actions = completed.recovery_plans[-1].actions
    assert [action.kind for action in actions] == [
        RecoveryActionKind.CREATE_REVERT_PULL_REQUEST,
        RecoveryActionKind.MERGE_REVERT_PULL_REQUEST,
        RecoveryActionKind.REVALIDATE_CHANGESET,
    ]
    assert all(action.status.value == "succeeded" for action in actions)
    assert completed.status.value == "compensated"
    assert merged_revert
    await client.aclose()


@pytest.mark.asyncio
async def test_repeated_saga_passes_never_merge_the_revert_twice() -> None:
    """Replay safety: a fully rolled back ChangeSet issues no further writes."""

    service, change_set, repository_id = await delivered_change_set(merged=True)
    branch = revert_branch_of(change_set, repository_id)
    writes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            writes.append(f"{request.method} {request.url.path}")
        if request.url.params.get("head") == f"acme:{branch}":
            return httpx.Response(
                200,
                json=[
                    pull_payload(
                        number=43, sha=REVERT_SHA, branch=branch, state="closed", merged=True
                    )
                ],
            )
        return httpx.Response(200, json=[])

    gateway, client = build_gateway(repository_id, handler, Reverter(created=False))
    context = context_for(change_set, RecoveryActionKind.MERGE_REVERT_PULL_REQUEST)

    for _ in range(3):
        await gateway.create_revert_pull_request(
            context_for(change_set, RecoveryActionKind.CREATE_REVERT_PULL_REQUEST)
        )
        await gateway.merge_revert_pull_request(context)
        await gateway.revalidate(
            context_for(change_set, RecoveryActionKind.REVALIDATE_CHANGESET)
        )

    assert writes == []
    await client.aclose()
