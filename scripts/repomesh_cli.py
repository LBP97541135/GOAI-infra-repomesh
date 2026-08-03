#!/usr/bin/env python3
"""RepoMesh CLI — one-shot repository discovery.

Usage::

    # One-shot: user gives a sentence with URL + requirement
    python scripts/repomesh_cli.py run \\
        "在 https://gitlab.metaglobal.cn/orders/order-service 里加微信支付"

    # Local scan (debugging)
    python scripts/repomesh_cli.py scan /path/to/repo

Environment variables::

    GITLAB_TOKEN              — Required for GitLab API access
    GITHUB_TOKEN              — Required for GitHub API access
    DEEPSEEK_API_KEY          — Required for LLM-based discovery
    REPOMESH_DEEPSEEK_BASE_URL — Optional, defaults to https://api.deepseek.com/v1
    REPOMESH_DEEPSEEK_MODEL    — Optional, defaults to deepseek-chat
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Ensure the project's ``src`` package is importable when run directly.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = _PROJECT_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from repomesh.modules.repository_intelligence.application import (  # noqa: E402
    RepositoryDiscoveryService,
    make_llm_client,
    parse_user_input,
    scan_org,
    scan_repo,
)
from repomesh.modules.repository_intelligence.domain import (  # noqa: E402
    RepositoryProfile,
)
from repomesh.modules.repository_intelligence.infrastructure import (  # noqa: E402
    InMemoryRepositoryCatalog,
)
from repomesh.modules.repository_intelligence.infrastructure.cache import (  # noqa: E402
    OrgCache,
)
from repomesh.modules.repository_intelligence.infrastructure.platform import (  # noqa: E402
    Platform,
    UrlType,
    detect_platform,
    make_fetcher,
)

# ---------------------------------------------------------------------------
# Sub-command: run
# ---------------------------------------------------------------------------


async def cmd_run_async(args: argparse.Namespace) -> int:
    user_input: str = args.prompt

    # ① Parse input.
    try:
        parsed = parse_user_input(user_input)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"URL: {parsed.url}", file=sys.stderr)
    print(f"Requirement: {parsed.requirement}", file=sys.stderr)

    platform = detect_platform(parsed.url)

    # ② Determine URL type and fetch repo list.
    if platform is Platform.LOCAL:
        print(
            "Error: local paths are not supported by 'run'. "
            "Use 'scan' for local repos.",
            file=sys.stderr,
        )
        return 1

    token = os.environ.get(f"{platform.value.upper()}_TOKEN", "")
    if not token:
        env_var = f"{platform.value.upper()}_TOKEN"
        print(
            f"Error: {env_var} environment variable not set. "
            f"Set it to your {platform.value} access token.",
            file=sys.stderr,
        )
        return 1

    fetcher = make_fetcher(platform, **{f"{platform.value}_token": token})

    print(f"Identifying URL type on {platform.value}...", file=sys.stderr)
    url_type = await fetcher.identify(parsed.url)

    entry_repo_name: str | None = None
    group_url: str = parsed.url

    if url_type is UrlType.SINGLE_REPO:
        entry_repo_name = parsed.entry_repo_name
        print("Single repo detected. Finding parent group...", file=sys.stderr)
        parent = await fetcher.fetch_parent_group_url(parsed.url)
        if parent:
            group_url = parent
            print(f"Parent group: {group_url}", file=sys.stderr)
        else:
            print(
                "Warning: could not find parent group. "
                "Only this repo will be scanned.",
                file=sys.stderr,
            )
    elif url_type is UrlType.GROUP:
        print("Group/org detected.", file=sys.stderr)
    else:
        print(
            f"Error: could not identify {parsed.url} as a repo or group "
            f"on {platform.value}.",
            file=sys.stderr,
        )
        return 1

    # ③ Load or build cache.
    cache = OrgCache()
    profiles = cache.load(group_url)

    if profiles is not None:
        count = cache.get_repo_count(group_url)
        age = cache.get_age_hours(group_url)
        print(
            f"Using cached data: {count} repos "
            f"(age: {age:.1f}h)",
            file=sys.stderr,
        )
    else:
        print(f"Scanning all repos under {group_url}...", file=sys.stderr)
        profiles = await scan_org(
            group_url,
            fetcher,
            on_progress=lambda i, total, name: print(
                f"  [{i}/{total}] {name}", file=sys.stderr
            ),
        )
        if not profiles:
            print("No repositories found.", file=sys.stderr)
            return 1
        cache.save(group_url, profiles)
        print(f"Cached {len(profiles)} repos.", file=sys.stderr)

    # ④ LLM discovery.
    catalog = InMemoryRepositoryCatalog()
    for profile in profiles:
        catalog._profiles[profile.id] = profile  # noqa: SLF001

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    base_url = os.environ.get(
        "REPOMESH_DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
    )
    model = os.environ.get("REPOMESH_DEEPSEEK_MODEL", "deepseek-chat")
    llm_client = make_llm_client(api_key, base_url=base_url, model=model)

    if llm_client is None:
        print(
            "\n⚠ No DEEPSEEK_API_KEY set — using keyword-matching fallback.",
            file=sys.stderr,
        )
    else:
        print(f"\nUsing LLM: {model}", file=sys.stderr)

    service = RepositoryDiscoveryService(catalog, llm_client=llm_client)
    results = await service.discover(
        parsed.requirement,
        limit=args.limit,
        entry_point=entry_repo_name,
    )

    # ⑤ Output.
    _print_results(results, profiles)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    return asyncio.run(cmd_run_async(args))


def _print_results(results, profiles: list[RepositoryProfile]) -> None:
    if not results:
        print("\nNo matching repositories found.")
        return

    profiles_by_id = {p.id: p for p in profiles}
    low_signal_repos = [
        p.name for p in profiles if p.auto_card is not None and p.auto_card.low_signal
    ]

    print("\n" + "═" * 80)
    print("  Candidate repositories (sorted by confidence):")
    print("  " + "─" * 76)

    for evidence in results:
        profile = profiles_by_id.get(evidence.repository_id)
        name = profile.name if profile else "???"
        if evidence.is_entry_point:
            label = "确定"
        elif evidence.score >= 0.9:
            label = "高度相关"
        elif evidence.score >= 0.6:
            label = "可能相关"
        else:
            label = "低置信度"
        bar = "█" * int(evidence.score * 10)
        print(f"  [{evidence.score:.2f}] {name:40s} {label}  {bar}")
        print(f"         {evidence.rationale}")
        if evidence.is_entry_point:
            print("         ← 用户指定入口")
        print()

    if low_signal_repos:
        print("─" * 80)
        print("⚠ 以下仓库信息量不足，系统无法充分判断，请人工检查是否涉及：")
        for name in low_signal_repos:
            in_results = any(
                profiles_by_id.get(e.repository_id)
                and profiles_by_id[e.repository_id].name == name
                for e in results
            )
            tag = "已选" if in_results else "未选"
            print(f"    [?] {name:40s} ({tag})")
        print("─" * 80)

    entry_count = sum(1 for e in results if e.is_entry_point)
    print(
        f"\n{len(results)} candidate(s), "
        f"{entry_count} entry point(s), "
        f"{len(low_signal_repos)} low-signal repos."
    )


# ---------------------------------------------------------------------------
# Sub-command: scan (local, for debugging)
# ---------------------------------------------------------------------------


def cmd_scan(args: argparse.Namespace) -> int:
    repo_path = Path(args.repo_path).resolve()
    if not repo_path.is_dir():
        print(f"Error: not a directory: {repo_path}", file=sys.stderr)
        return 1

    card = scan_repo(repo_path)
    payload = {
        "top_dirs": list(card.top_dirs),
        "deps": list(card.deps),
        "recent_commits": list(card.recent_commits),
        "exposed_apis": list(card.exposed_apis),
        "low_signal": card.low_signal,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repomesh_cli",
        description="RepoMesh — one-shot repository discovery from a single sentence.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = sub.add_parser(
        "run",
        help="Give a sentence with a URL and requirement. "
        "The system does everything else.",
    )
    p_run.add_argument(
        "prompt",
        help='Your input, e.g. "在 https://gitlab.example.com/orders/order-service 里加微信支付"',
    )
    p_run.add_argument("--limit", type=int, default=10, help="Max candidates.")
    p_run.set_defaults(func=cmd_run)

    # scan
    p_scan = sub.add_parser("scan", help="Scan a local repo and print its AutoCard JSON.")
    p_scan.add_argument("repo_path", help="Path to the repository directory.")
    p_scan.set_defaults(func=cmd_scan)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
