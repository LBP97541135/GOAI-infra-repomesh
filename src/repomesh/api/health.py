from fastapi import APIRouter, HTTPException, Request, status

router = APIRouter(tags=["health"])


def _not_ready(dependency: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"status": "not_ready", "dependency": dependency},
    )


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(request: Request) -> dict[str, str]:
    container = request.app.state.container
    if not await container.database.is_ready():
        raise _not_ready("postgresql")
    if not await container.is_agentteams_ready():
        raise _not_ready("agentteams")
    return {"status": "ready"}
