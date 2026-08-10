from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen


TASK_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TASK_ROOT))

import serve_frontend  # noqa: E402


class FrontendServerTests(unittest.TestCase):
    def test_default_and_override_ports(self) -> None:
        self.assertEqual(serve_frontend.parse_args([]).port, 5173)
        self.assertEqual(serve_frontend.parse_args(["--port", "35173"]).port, 35173)

    def test_actual_http_mime_and_directory_boundary(self) -> None:
        server = serve_frontend.create_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            for filename in ("app.js", "app.mjs"):
                with self.subTest(filename=filename):
                    with urlopen(f"{base_url}/{filename}", timeout=3) as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.headers.get_content_type(), "text/javascript")

            with self.assertRaises(HTTPError) as caught:
                urlopen(f"{base_url}/%2e%2e/README.md", timeout=3)
            self.assertEqual(caught.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
