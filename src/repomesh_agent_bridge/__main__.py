"""``python -m repomesh_agent_bridge`` — the same entry point as the console script."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
