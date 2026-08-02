from fastapi import APIRouter, HTTPException, Request, status

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(request: Request) -> dict[str, str]:
    if not await request.app.state.container.database.is_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "dependency": "postgresql"},
        )
    return {"status": "ready"}