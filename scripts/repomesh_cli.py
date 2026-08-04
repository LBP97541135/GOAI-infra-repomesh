#!/usr/bin/env python3
"""RepoMesh CLI — requirement-driven repository discovery.

Usage::

    # Text requirement
    python scripts/repomesh_cli.py run \\
        -r "修复订票流程中微信支付回调超时的问题" \\
        https://gitlab.metaglobal.cn/orders/order-service

    # Requirement document (Markdown / plain text)
    python scripts/repomesh_cli.py run \\
        -f PRD.md \\
        https://gitlab.metaglobal.cn/orders/order-service

    # Non-interactive mode (CI/CD)
    python scripts/repomesh_cli.py run \\
        --no-interactive -r "加微信支付" \\
        https://gitlab.metaglobal.cn/

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
    RequirementAnalyzer,
    extract_entry_repo_name,
    load_requirement,
    make_llm_client,
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

#: Maximum rounds of interactive Q&A.
_MAX_INTERACTION_ROUNDS = 2


# ---------------------------------------------------------------------------
# Sub-command: run
# ---------------------------------------------------------------------------


async def cmd_run_async(args: argparse.Namespace) -> int:
    url: str | None = args.url

    # ① Load requirement (from text or file).
    try:
        requirement = load_requirement(args.requirement, args.requirement_file)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # ② If URL is missing, prompt the user for it.
    if not url:
        if args.no_interactive:
            print(
                "Error: URL is required but was not provided. "
                "Use: repomesh run <url> -r \"requirement\"",
                file=sys.stderr,
            )
            return 1
        print("⚠ 未提供仓库或组织 URL。", file=sys.stderr)
        print(
            "请输入目标 URL（仓库或组织地址），例如：\n"
            "  https://gitlab.example.com/orders/order-service\n"
            "  https://github.com/FudanSELab/train-ticket",
            file=sys.stderr,
        )
        try:
            url = input("URL > ").strip()
        except (EOFError, KeyboardInterrupt):
            url = ""
        if not url:
            print("Error: URL is required. Aborting.", file=sys.stderr)
            return 1

    print(f"URL: {url}", file=sys.stderr)
    print(f"Requirement: {requirement[:200]}...", file=sys.stderr)

    platform = detect_platform(url)

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

    # ② LLM setup (needed for both interaction and discovery).
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    base_url = os.environ.get(
        "REPOMESH_DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
    )
    model = os.environ.get("REPOMESH_DEEPSEEK_MODEL", "deepseek-chat")
    llm_client = make_llm_client(api_key, base_url=base_url, model=model)

    # ③ Requirement sufficiency check + interactive Q&A.
    extracted_keywords: list[str] = []
    if llm_client is not None and not args.no_interactive:
        analyzer = RequirementAnalyzer(llm_client)
        for round_num in range(_MAX_INTERACTION_ROUNDS):
            analysis = analyzer.analyze(requirement)
            extracted_keywords = analysis.extracted_keywords

            if analysis.sufficient:
                print(
                    f"\n需求信息充分（置信度 {analysis.confidence:.0%}），"
                    f"开始分析。",
                    file=sys.stderr,
                )
                break

            missing = ", ".join(analysis.missing_dimensions) or "信息不足"
            print(
                f"\n需求信息还不够明确（缺少：{missing}）",
                file=sys.stderr,
            )
            if round_num == _MAX_INTERACTION_ROUNDS - 1:
                print(
                    "已达到最大交互轮次，将使用现有信息继续分析。",
                    file=sys.stderr,
                )
                break

            for question in analysis.questions:
                print(f"\n❓ {question}")
                try:
                    answer = input("> ").strip()
                except (EOFError, KeyboardInterrupt):
                    answer = ""
                if answer:
                    requirement += f"\n\n[补充信息] {question}\n{answer}"
    elif llm_client is None:
        print(
            "\n⚠ No DEEPSEEK_API_KEY set — using keyword-matching fallback.",
            file=sys.stderr,
        )
    else:
        print("\n--no-interactive: 跳过需求充分度评估。", file=sys.stderr)

    # ④ Determine URL type and fetch repo list.
    print(f"\nIdentifying URL type on {platform.value}...", file=sys.stderr)
    url_type = await fetcher.identify(url)

    entry_repo_name: str | None = None
    group_url: str = url

    if url_type is UrlType.SINGLE_REPO:
        entry_repo_name = extract_entry_repo_name(url)
        print("Single repo detected. Finding parent group...", file=sys.stderr)
        parent = await fetcher.fetch_parent_group_url(url)
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
            f"Error: could not identify {url} as a repo or group "
            f"on {platform.value}.",
            file=sys.stderr,
        )
        return 1

    # ⑤ Load or build cache.
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

    # ⑥ LLM discovery.
    catalog = InMemoryRepositoryCatalog()
    for profile in profiles:
        catalog._profiles[profile.id] = profile  # noqa: SLF001

    if llm_client is not None:
        print(f"\nUsing LLM: {model}", file=sys.stderr)

    service = RepositoryDiscoveryService(catalog, llm_client=llm_client)
    results = await service.discover(
        requirement,
        limit=args.limit,
        entry_point=entry_repo_name,
        keywords=extracted_keywords or None,
    )

    # ⑦ Output.
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
        description="RepoMesh — requirement-driven repository discovery.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = sub.add_parser(
        "run",
        help="Provide a requirement and URL. "
        "The system finds which repos need changes.",
    )
    p_run.add_argument(
        "url",
        nargs="?",
        default=None,
        help="Repository or organization URL. "
        "If omitted, the system will prompt for it.",
    )
    # Requirement input: mutually exclusive, at least one required.
    req_group = p_run.add_mutually_exclusive_group(required=True)
    req_group.add_argument(
        "--requirement",
        "-r",
        help="Requirement text (a paragraph of business description).",
    )
    req_group.add_argument(
        "--requirement-file",
        "-f",
        help="Path to a requirement document (Markdown or plain text).",
    )
    p_run.add_argument(
        "--no-interactive",
        action="store_true",
        help="Skip interactive Q&A; analyse with whatever info is given.",
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
