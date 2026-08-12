from fastapi import APIRouter

from repomesh.api.health import router as health_router
from repomesh.api.human_control import router as human_control_router
from repomesh.api.read_models import grid_router, issues_router, rooms_router
from repomesh.api.read_models import router as delivery_read_model_router
from repomesh.api.scm_reconciliation import router as scm_reconciliation_router
from repomesh.api.scm_webhook import router as scm_webhook_router
from repomesh.api.worker_mcp import router as worker_mcp_router
from repomesh.modules.agent_runtime.api.router import router as agent_runtime_router
from repomesh.modules.delivery.api.deliveries import router as deliveries_router
from repomesh.modules.delivery.api.router import router as delivery_router
from repomesh.modules.identity_access.api import router as identity_console_router
from repomesh.modules.repository_intelligence.api.console import (
    router as console_repositories_router,
)
from repomesh.modules.repository_intelligence.api.discovery_chain import (
    router as issue_discovery_router,
)
from repomesh.modules.repository_intelligence.api.router import (
    router as repository_intelligence_router,
)

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(worker_mcp_router)
api_router.include_router(repository_intelligence_router, prefix="/api/v1")
# Shares the /console prefix with the read model's grid router and with
# identity_access; the paths are disjoint, and a console *write* belongs to the
# module that owns the rows rather than to the read-model package.
api_router.include_router(console_repositories_router, prefix="/api/v1")
# Contract v0.4 §4.1: the discovery chain's writes are native /issues paths,
# not a console face — the bodies carry no credential, which is the only thing
# the console face exists to prevent. Method- and path-disjoint from the read
# model's issues_router, which registers GETs only.
api_router.include_router(issue_discovery_router, prefix="/api/v1")
api_router.include_router(agent_runtime_router, prefix="/api/v1")
api_router.include_router(delivery_router, prefix="/api/v1")
api_router.include_router(human_control_router, prefix="/api/v1")
api_router.include_router(deliveries_router, prefix="/api/v1")
api_router.include_router(delivery_read_model_router, prefix="/api/v1")
api_router.include_router(issues_router, prefix="/api/v1")
api_router.include_router(rooms_router, prefix="/api/v1")
api_router.include_router(grid_router, prefix="/api/v1")
api_router.include_router(identity_console_router, prefix="/api/v1")
api_router.include_router(scm_webhook_router, prefix="/api/v1")
api_router.include_router(scm_reconciliation_router, prefix="/api/v1")
