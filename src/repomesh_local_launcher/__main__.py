"""``python -m repomesh_local_launcher <config path>`` -- the only way to run it.

One argument, because the config file is the only input the launcher accepts and
a second way to supply any of it would be a second way to get it wrong. Binding
to ``127.0.0.1`` is not a setting for the same reason it is the security
boundary (FR-09): an address the config could change is an address a mistake
could open to the network. No TLS and no auth token -- there is no link here to
protect, and a secret on this disk to guard this disk would protect nothing.
"""

import sys
from pathlib import Path

import uvicorn

from .app import create_app
from .config import load_config
from .windows import WindowsMemberProcessPlane

LOOPBACK = "127.0.0.1"


def main() -> int:
    config = load_config(Path(sys.argv[1]))
    app = create_app(config, WindowsMemberProcessPlane(config))
    uvicorn.run(app, host=LOOPBACK, port=config.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
