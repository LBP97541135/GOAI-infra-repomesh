"""``python -m repomesh_runner`` — the container entrypoint."""

import sys

from .main import run

if __name__ == "__main__":
    sys.exit(run())
