import uvicorn

from repomesh.bootstrap import create_app

app = create_app()


def run() -> None:
    uvicorn.run("repomesh.main:app", host="127.0.0.1", port=8000, reload=False)
