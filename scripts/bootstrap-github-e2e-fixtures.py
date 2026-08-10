"""Install deterministic two-repository fixtures through a GitHub App token."""

import argparse
import asyncio
import base64
from pathlib import Path

import httpx

from repomesh.integrations.scm.contracts import RepositoryRef, SCMProvider
from repomesh.integrations.scm.github_auth import (
    GitHubAppTokenProvider,
    private_key_file_loader,
)

WORKFLOW = """name: unit-tests
on:
  push:
    branches: [main, "repomesh/**"]
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m unittest discover -s tests -v
"""

FIXTURES = {
    "repomesh-e2e-api": {
        "src/pricing.py": '''"""Pricing API used by the RepoMesh delivery acceptance test."""


def calculate_quote(subtotal: int, shipping: int) -> dict[str, int]:
    """Return a checkout quote; orders of at least 100 receive a 10-unit discount."""
    discount = 10 if subtotal >= 100 else 0
    return {
        "subtotal": subtotal,
        "shipping": shipping,
        "total": subtotal + shipping - discount,
    }
''',
        "tests/test_pricing.py": '''import unittest

from src.pricing import calculate_quote


class PricingTest(unittest.TestCase):
    def test_regular_order(self):
        self.assertEqual(
            calculate_quote(80, 5),
            {"subtotal": 80, "shipping": 5, "total": 85},
        )


if __name__ == "__main__":
    unittest.main()
''',
        ".github/workflows/unit-tests.yml": WORKFLOW,
    },
    "repomesh-e2e-client": {
        "src/checkout_view.py": '''"""Checkout view adapter for RepoMesh acceptance tests."""


def checkout_lines(quote: dict[str, int]) -> list[str]:
    return [
        f"Subtotal: {quote['subtotal']}",
        f"Shipping: {quote['shipping']}",
        f"Total: {quote['total']}",
    ]
''',
        "tests/test_checkout_view.py": '''import unittest

from src.checkout_view import checkout_lines


class CheckoutViewTest(unittest.TestCase):
    def test_regular_quote(self):
        self.assertEqual(
            checkout_lines({"subtotal": 80, "shipping": 5, "total": 85}),
            ["Subtotal: 80", "Shipping: 5", "Total: 85"],
        )


if __name__ == "__main__":
    unittest.main()
''',
        ".github/workflows/unit-tests.yml": WORKFLOW,
    },
}


async def request(client, method, url, token, **kwargs):
    response = await client.request(
        method,
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        **kwargs,
    )
    if response.is_error:
        try:
            detail = response.json().get("message", response.text)
        except ValueError:
            detail = response.text
        raise RuntimeError(f"GitHub {method} {url} failed ({response.status_code}): {detail}")
    return response.json()


async def install_repository(client, provider, owner, name, files):
    repository = RepositoryRef(SCMProvider.GITHUB, owner, name)
    token = await provider(repository)
    api = f"https://api.github.com/repos/{owner}/{name}/git"
    ref = await request(client, "GET", f"{api}/ref/heads/main", token)
    parent = ref["object"]["sha"]
    commit = await request(client, "GET", f"{api}/commits/{parent}", token)
    entries = []
    for path, content in files.items():
        blob = await request(
            client,
            "POST",
            f"{api}/blobs",
            token,
            json={
                "content": base64.b64encode(content.encode()).decode(),
                "encoding": "base64",
            },
        )
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree = await request(
        client,
        "POST",
        f"{api}/trees",
        token,
        json={"base_tree": commit["tree"]["sha"], "tree": entries},
    )
    candidate = await request(
        client,
        "POST",
        f"{api}/commits",
        token,
        json={
            "message": "test: install RepoMesh delivery fixture",
            "tree": tree["sha"],
            "parents": [parent],
        },
    )
    await request(
        client,
        "PATCH",
        f"{api}/refs/heads/main",
        token,
        json={"sha": candidate["sha"], "force": False},
    )
    return candidate["sha"]


async def run(args):
    provider = GitHubAppTokenProvider(
        args.app_id, private_key_file_loader(args.private_key)
    )
    client = httpx.AsyncClient(timeout=30)
    try:
        for name, files in FIXTURES.items():
            if args.without_workflows:
                files = {
                    path: content
                    for path, content in files.items()
                    if not path.startswith(".github/")
                }
            sha = await install_repository(client, provider, args.owner, name, files)
            print(f"{args.owner}/{name} {sha}")
    finally:
        await client.aclose()
        await provider.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-id", type=int, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--without-workflows", action="store_true")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
