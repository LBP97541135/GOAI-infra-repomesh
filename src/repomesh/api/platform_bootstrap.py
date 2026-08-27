
from fastapi import APIRouter, HTTPException, Request

from repomesh.modules.platform_config import (
    BootstrapOperation,
    BootstrapPhase,
    BootstrapState,
    BootstrapTransitionError,
)

router = APIRouter(prefix="/api/v1/setup/bootstrap", tags=["platform-bootstrap"])

_PHASE_MESSAGES = {
    BootstrapPhase.WAITING_FOR_MODEL: "Waiting for the model connection",
    BootstrapPhase.INSTALLING_AGENTTEAMS: "Installing the AgentTeams execution plane",
    BootstrapPhase.VERIFYING_CONTROLLER: "Verifying the AgentTeams Controller",
    BootstrapPhase.CONFIGURING_MATRIX: "Configuring Matrix",
    BootstrapPhase.CONFIGURING_STORAGE: "Configuring object storage",
    BootstrapPhase.WRITING_RUNTIME_CONFIG: "Writing RepoMesh runtime configuration",
    BootstrapPhase.RESTARTING_API: "Restarting the RepoMesh API",
    BootstrapPhase.VERIFYING_PLATFORM: "Verifying the complete platform",
    BootstrapPhase.COMPLETE: "Execution plane is ready",
}


async def _admin(request: Request):
    authorization = request.headers.get("Authorization", "")
    token = (
        authorization.removeprefix("Bearer ").strip()
        if authorization.startswith("Bearer ")
        else request.cookies.get("repomesh_session")
    )
    if not token:
        raise HTTPException(status_code=401, detail="local authentication is required")
    try:
        actor = await request.app.state.container.local_account_service().authenticate(token)
    except Exception as error:
        raise HTTPException(status_code=401, detail="invalid local session") from error
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="local administrator permission is required")
    return actor


def bootstrap_view(operation: BootstrapOperation | None) -> dict:
    if operation is None:
        return {
            "operation_id": None,
            "state": "idle",
            "phase": BootstrapPhase.WAITING_FOR_MODEL.value,
            "attempt": 0,
            "retryable": False,
            "error_code": None,
            "error_detail": None,
            "message": "Waiting for the model connection",
            "updated_at": None,
        }
    return {
        "operation_id": str(operation.id),
        "state": operation.state.value,
        "phase": operation.phase.value,
        "attempt": operation.attempt,
        "retryable": operation.state is BootstrapState.RETRYABLE_FAILURE,
        "error_code": operation.error_code.value if operation.error_code is not None else None,
        "error_detail": operation.error_detail,
        "message": _PHASE_MESSAGES[operation.phase],
        "updated_at": operation.updated_at.isoformat(),
    }


@router.get("")
async def get_bootstrap_status(request: Request) -> dict:
    await _admin(request)
    operation = await request.app.state.container.bootstrap_operation_store().latest()
    return bootstrap_view(operation)


@router.post("/retry")
async def retry_bootstrap(request: Request) -> dict:
    await _admin(request)
    store = request.app.state.container.bootstrap_operation_store()
    operation = await store.latest()
    if operation is None:
        raise HTTPException(status_code=404, detail="bootstrap operation does not exist")
    try:
        retried = await store.retry(operation.id)
    except BootstrapTransitionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return bootstrap_view(retried)
