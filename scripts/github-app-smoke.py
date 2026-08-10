"""Verify GitHub App repository access without printing installation tokens."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from repomesh.integrations.scm.contracts import RepositoryRef, SCMProvider
from repomesh.integrations.scm.github import GitHubAdapter
from repomesh.integrations.scm.github_auth import (
    GitHubAppTokenProvider,
    private_key_file_loader,
)


async def verify(app_id: int, private_key: Path, repositories: list[str]) -> None:
    provider = GitHubAppTokenProvider(app_id, private_key_file_loader(private_key))
    adapter = GitHubAdapter(provider)
    client = httpx.AsyncClient(timeout=20)
    results = []
    try:
        for full_name in repositories:
            owner, name = full_name.split("/", 1)
            repository = RepositoryRef(SCMProvider.GITHUB, owner, name)
            token = await provider(repository)
            response = await client.get(
                f"https://api.github.com/repos/{owner}/{name}",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()
            payload = response.json()
            commit_response = await client.get(
                f"https://api.github.com/repos/{owner}/{name}/commits/{payload['default_branch']}",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            commit_response.raise_for_status()
            head_sha = commit_response.json()["sha"]
            checks = await adapter.list_check_runs(repository, head_sha)
            protection = await adapter.get_branch_protection(
                repository, payload["default_branch"]
            )
            results.append(
                {
                    "repository": full_name,
                    "accessible": True,
                    "private": bool(payload.get("private")),
                    "default_branch": payload.get("default_branch"),
                    "permissions": payload.get("permissions", {}),
                    "branch_protection": {
                        "required_checks": protection.required_checks,
                        "required_approvals": protection.required_approvals,
                        "dismisses_stale_reviews": protection.dismisses_stale_reviews,
                    },
                    "checks": [
                        {
                            "name": check.check_name,
                            "terminal": check.terminal,
                            "passed": check.passed,
                            "summary": check.summary,
                        }
                        for check in checks
                    ],
                }
            )
    finally:
        await adapter.close()
        await client.aclose()
        await provider.close()
    print(json.dumps(results, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-id", type=int, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("repositories", nargs="+")
    args = parser.parse_args()
    asyncio.run(verify(args.app_id, args.private_key, args.repositories))


if __name__ == "__main__":
    main()
