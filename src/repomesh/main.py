import uvicorn

from repomesh.bootstrap import create_app
from repomesh.modules.platform_config import load_runtime_environment
from repomesh.settings import get_settings

load_runtime_environment()
get_settings.cache_clear()
app = create_app()


def run() -> None:
    uvicorn.run("repomesh.main:app", host="127.0.0.1", port=8000, reload=False)
