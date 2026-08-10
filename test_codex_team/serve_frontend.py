"""Dependency-free static server for the Todo frontend."""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Sequence


FRONTEND_DIRECTORY = (Path(__file__).resolve().parent / "frontend").resolve()


class FrontendRequestHandler(SimpleHTTPRequestHandler):
    """Serve JavaScript modules with MIME types accepted by strict browsers."""

    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".mjs": "text/javascript",
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve the Todo frontend on loopback.")
    parser.add_argument("--port", type=int, default=5173, help="listen port (default: 5173)")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    return args


def create_server(port: int) -> ThreadingHTTPServer:
    """Create a loopback-only server rooted at the task frontend directory."""

    handler = partial(FrontendRequestHandler, directory=str(FRONTEND_DIRECTORY))
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    with create_server(args.port) as server:
        print(
            f"Serving {FRONTEND_DIRECTORY} at http://localhost:{args.port}",
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
