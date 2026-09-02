from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from repomesh.modules.delivery.contracts import (
    CIObservationCommand,
    MergeObservationCommand,
    PlanRecoveryCommand,
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RecordRecoveryActionCommand,
    RepositoryCandidateInput,
    ReviewObservationCommand,
)
from repomesh.modules.delivery.policy import DeliveryPolicy
from repomesh.settings import get_settings

from .models import (
    ChangeSetCreate,
    CIObservationCreate,
    DeliveryPolicyUpdate,
    MergeObservationCreate,
    PullRequestObservationCreate,
    RecoveryActionUpdate,
    RecoveryPlanCreate,
    ReviewObservationCreate,
)

router = APIRouter(prefix="/delivery", tags=["delivery"])


async def _human(request: Request):
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


async def _administrator(request: Request):
    actor = await _human(request)
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="local administrator permission is required")
    return actor


@router.put("/organizations/{organization_id}/policy")
async def put_organization_policy(
    organization_id: UUID,
    body: DeliveryPolicyUpdate,
    request: Request,
) -> dict:
    await _administrator(request)
    policy = DeliveryPolicy(organization_id=organization_id, **body.model_dump())
    await request.app.state.container.delivery_policy_store().put(policy)
    return asdict(policy)


@router.put("/organizations/{organization_id}/repositories/{repository_id}/policy")
async def put_repository_policy(
    organization_id: UUID,
    repository_id: UUID,
    body: DeliveryPolicyUpdate,
    request: Request,
) -> dict:
    await _administrator(request)
    policy = DeliveryPolicy(
        organization_id=organization_id,
        repository_id=repository_id,
        **body.model_dump(),
    )
    await request.app.state.container.delivery_policy_store().put(policy)
    return asdict(policy)


@router.get("/organizations/{organization_id}/policy")
async def get_delivery_policy(
    organization_id: UUID,
    request: Request,
    repository_id: UUID | None = None,
) -> dict:
    await _human(request)
    policy = await request.app.state.container.delivery_policy_store().resolve(
        organization_id,
        repository_id,
        fallback=request.app.state.container.default_delivery_policy(organization_id),
    )
    return asdict(policy)


def _service(request: Request):
    expected = get_settings().agent_action_token
    if not expected:
        raise HTTPException(status_code=503, detail="delivery API authentication is not configured")
    if request.headers.get("Authorization") != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid delivery API credentials")
    return request.app.state.container.delivery_service()


@router.post("/change-sets")
async def prepare_change_set(body: ChangeSetCreate, request: Request) -> dict:
    result = await _service(request).prepare(
        PrepareChangeSetCommand(
            organization_id=body.organization_id,
            project_id=body.project_id,
            created_by_agent_id=body.created_by_agent_id,
            title=body.title,
            validation_snapshot_id=body.validation_snapshot_id,
            candidates=tuple(
                RepositoryCandidateInput(
                    repository_id=item.repository_id,
                    task_id=item.task_id,
                    commit_sha=item.commit_sha,
                    base_sha=item.base_sha,
                    branch_name=item.branch_name,
                    depends_on=tuple(item.depends_on),
                    required_checks=tuple(item.required_checks),
                    required_approvals=item.required_approvals,
                )
                for item in body.candidates
            ),
        ),
        idempotency_key=body.idempotency_key,
    )
    return asdict(result)


@router.get("/change-sets/{change_set_id}")
async def get_change_set(change_set_id: UUID, request: Request) -> dict:
    return asdict(await _service(request).get(change_set_id))


@router.get("/change-sets/{change_set_id}/conflicts")
async def get_change_set_conflicts(change_set_id: UUID, request: Request) -> list[dict]:
    _service(request)
    cases = await request.app.state.container.delivery_conflict_case_store().list_for_change_set(
        change_set_id
    )
    return [asdict(case) for case in cases]


@router.get("/change-sets/{change_set_id}/repositories/{repository_id}/merge-gate")
async def evaluate_merge_gate(change_set_id: UUID, repository_id: UUID, request: Request) -> dict:
    return asdict(await _service(request).evaluate_merge_gate(change_set_id, repository_id))


@router.post("/change-sets/{change_set_id}/pull-requests")
async def observe_pull_request(
    change_set_id: UUID, body: PullRequestObservationCreate, request: Request
) -> dict:
    return asdict(
        await _service(request).observe_pull_request(
            PullRequestObservationCommand(change_set_id=change_set_id, **body.model_dump())
        )
    )


@router.post("/change-sets/{change_set_id}/ci")
async def observe_ci(change_set_id: UUID, body: CIObservationCreate, request: Request) -> dict:
    return asdict(
        await _service(request).observe_ci(
            CIObservationCommand(change_set_id=change_set_id, **body.model_dump())
        )
    )


@router.post("/change-sets/{change_set_id}/reviews")
async def observe_review(
    change_set_id: UUID, body: ReviewObservationCreate, request: Request
) -> dict:
    return asdict(
        await _service(request).observe_review(
            ReviewObservationCommand(change_set_id=change_set_id, **body.model_dump())
        )
    )


@router.post("/change-sets/{change_set_id}/merges")
async def observe_merge(
    change_set_id: UUID, body: MergeObservationCreate, request: Request
) -> dict:
    return asdict(
        await _service(request).observe_merge(
            MergeObservationCommand(change_set_id=change_set_id, **body.model_dump())
        )
    )


@router.post("/change-sets/{change_set_id}/recovery-plans")
async def plan_recovery(change_set_id: UUID, body: RecoveryPlanCreate, request: Request) -> dict:
    return asdict(
        await _service(request).plan_recovery(
            PlanRecoveryCommand(change_set_id=change_set_id, **body.model_dump())
        )
    )


@router.post("/change-sets/{change_set_id}/recovery-plans/{recovery_plan_id}/actions/{action_id}")
async def record_recovery_action(
    change_set_id: UUID,
    recovery_plan_id: UUID,
    action_id: UUID,
    body: RecoveryActionUpdate,
    request: Request,
) -> dict:
    return asdict(
        await _service(request).record_recovery_action(
            RecordRecoveryActionCommand(
                change_set_id=change_set_id,
                recovery_plan_id=recovery_plan_id,
                action_id=action_id,
                status=body.status,
                detail=body.detail,
            )
        )
    )
