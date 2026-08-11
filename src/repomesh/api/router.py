from fastapi import APIRouter

from repomesh.api.health import router as health_router
from repomesh.api.human_control import router as human_control_router
from repomesh.api.read_models import issues_router
from repomesh.api.read_models import router as delivery_read_model_router
from repomesh.api.scm_reconciliation import router as scm_reconciliation_router
from repomesh.api.scm_webhook import router as scm_webhook_router
from repomesh.api.worker_mcp import router as worker_mcp_router
from repomesh.modules.agent_runtime.api.router import router as agent_runtime_router
from repomesh.modules.delivery.api.deliveries import router as deliveries_router
from repomesh.modules.delivery.api.router import router as delivery_router
from repomesh.modules.repository_intelligence.api.router import (
    router as repository_intelligence_router,
)

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(worker_mcp_router)
api_router.include_router(repository_intelligence_router, prefix="/api/v1")
api_router.include_router(agent_runtime_router, prefix="/api/v1")
api_router.include_router(delivery_router, prefix="/api/v1")
api_router.include_router(human_control_router, prefix="/api/v1")
api_router.include_router(deliveries_router, prefix="/api/v1")
api_router.include_router(delivery_read_model_router, prefix="/api/v1")
api_router.include_router(issues_router, prefix="/api/v1")
api_router.include_router(scm_webhook_router, prefix="/api/v1")
api_router.include_router(scm_reconciliation_router, prefix="/api/v1")
