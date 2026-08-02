import uvicorn
from fastapi import FastAPI

from repomesh.api.router import router
from repomesh.modules.repository_intelligence.in_memory import InMemoryRepositoryCatalog
from repomesh.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Multi-repository coding-agent orchestration infrastructure",
    )
    application.state.repository_catalog = InMemoryRepositoryCatalog()
    application.include_router(router)
    return application


app = create_app()


def run() -> None:
    uvicorn.run("repomesh.main:app", host="127.0.0.1", port=8000, reload=False)

