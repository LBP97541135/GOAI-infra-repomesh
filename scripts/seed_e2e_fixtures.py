"""Seed the local git fixtures the one-shot e2e stack clones from.

The compose stack's repositories are registered with URLs that are *paths
inside the api container* (``/runner-workspaces/fixtures/<name>``): the
platform's GitWorktreeManager clones them with ``git clone --mirror`` exactly
as it would clone a remote, and the bind mount
``./.repomesh-workspaces -> /runner-workspaces`` makes the same tree visible to
the runner container at ``/workspace``. This script creates those fixture
repositories on the host, idempotently: an existing repository with a ``main``
branch is left untouched, so re-running a dev stack never rewrites history.

Each fixture is a small real codebase — module plus passing tests — because the
execution plane clones it, materializes a worktree, and runs the coding agent
inside it; an empty repository would make every step after the clone a no-op.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

FIXTURES: dict[str, dict[str, str]] = {
    "checkout-pricing-api": {
        "src/pricing.py": '''"""Checkout pricing domain (e2e fixture)."""


def calculate_total(lines, coupon=None):
    """Total in cents for ``lines`` of (unit_price, quantity) pairs."""
    subtotal = sum(unit_price * quantity for unit_price, quantity in lines)
    if coupon == "SAVE10":
        return int(round(subtotal * 0.9))
    return subtotal
''',
        "tests/test_pricing.py": '''from src.pricing import calculate_total


def test_empty_cart_totals_zero():
    assert calculate_total([]) == 0


def test_line_totals_are_summed():
    assert calculate_total([(100, 2), (250, 1)]) == 450


def test_coupon_discounts_ten_percent():
    assert calculate_total([(100, 10)], coupon="SAVE10") == 900
''',
        "README.md": "checkout-pricing-api — RepoMesh local e2e fixture (pricing domain).\n",
    },
    "checkout-web": {
        "src/checkout_view.py": '''"""Checkout view model (e2e fixture)."""

from src.pricing import calculate_total


def checkout_lines(lines, coupon=None):
    """View model the checkout page renders for one cart."""
    return {
        "lines": [{"unit_price": p, "quantity": q} for p, q in lines],
        "total": calculate_total(lines, coupon=coupon),
    }
''',
        "tests/test_checkout_view.py": '''from src.checkout_view import checkout_lines


def test_view_model_carries_lines_and_total():
    view = checkout_lines([(100, 2)])
    assert view["lines"] == [{"unit_price": 100, "quantity": 2}]
    assert view["total"] == 200
''',
        "README.md": "checkout-web — RepoMesh local e2e fixture (checkout view).\n",
    },
}


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def seed(workspace_root: Path) -> list[str]:
    created = []
    for name, files in FIXTURES.items():
        repository = workspace_root / "fixtures" / name
        if (repository / ".git").exists():
            print(f"[keep] {repository} already seeded")
            continue
        repository.mkdir(parents=True, exist_ok=True)
        _git(repository, "init", "-b", "main")
        (repository / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
        for relative, content in files.items():
            path = repository / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        _git(repository, "add", ".")
        _git(repository, "-c", "user.name=repomesh-e2e", "-c", "user.email=e2e@repomesh.local",
             "commit", "-m", "Seed local e2e fixture")
        created.append(str(repository))
        print(f"[seed] {repository}")
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace-root",
        default=".repomesh-workspaces",
        help="Host workspace root bind-mounted into the stack (default: .repomesh-workspaces)",
    )
    arguments = parser.parse_args()
    if sys.platform == "win32":
        print(
            "note: run this from Git Bash / WSL, or ensure `git` is on PATH\n"
            "      (the fixtures must exist before the platform clones them)",
        )
    seed(Path(arguments.workspace_root))
    print("\nRepository URLs to register on the platform (container-side paths):")
    for name in FIXTURES:
        print(f"  /runner-workspaces/fixtures/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
