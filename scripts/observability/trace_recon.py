#!/usr/bin/env python3
"""M0 侦察：列出并分析 Agent 会话文件结构（只读）。

用途：冻结 CoPaw 会话 JSON 的字段映射（见
docs/推理轨迹-M0-侦察记录-2026-08-15.md），为 trace 解析器提供输入基线。
本脚本只读，不写对象、不改任何文件。

用法：

  # 本地直读（REPOMESH_AGENTTEAMS_STORAGE_ROOT 指向的目录，与 MinIO 对象同构）
  python scripts/observability/trace_recon.py --root .agentteams-storage
  python scripts/observability/trace_recon.py --root .agentteams-storage --dump-first

  # MinIO（凭据来自 REPOMESH_AGENTTEAMS_STORAGE_* 的约定，可显式传入）
  python scripts/observability/trace_recon.py \
      --endpoint 127.0.0.1:9000 --access-key minio --secret-key minioadmin \
      --bucket agentteams-storage

对象路径约定（AgentTeams 侧已核实）：
  agents/{name}/.copaw/workspaces/default/sessions/{session_id}.json

输出：每个会话的 key / 大小 / 顶层键 / memory.content 概况 / 消息 role 与内容块
类型统计。未发现任何会话时退出码 1。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# 与 Agent 侧 push_loop 的 key 布局一致
SESSION_KEY_SUFFIX = ".copaw/workspaces/default/sessions/"


def _is_session_key(key: str) -> bool:
    parts = key.split("/")
    return len(parts) >= 6 and key.endswith(".json") and SESSION_KEY_SUFFIX in key


def _agent_name_from_key(key: str) -> str:
    parts = key.split("/")
    return parts[1] if len(parts) > 1 and parts[0] == "agents" else "?"


def discover_local(root: Path) -> list[tuple[str, str]]:
    """扫描本地存储目录，返回 [(key, 绝对路径)]。"""
    found: list[tuple[str, str]] = []
    if not root.is_dir():
        return found
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix != ".json":
            continue
        rel = path.relative_to(root).as_posix()
        if _is_session_key(rel):
            found.append((rel, str(path)))
    return sorted(found)


def discover_minio(
    endpoint: str, access_key: str, secret_key: str, bucket: str
) -> list[tuple[str, bytes]]:
    """扫描 MinIO bucket，返回 [(key, 对象内容)]。"""
    from minio import Minio

    secure = endpoint.startswith("https://")
    host = endpoint.removeprefix("https://").removeprefix("http://").rstrip("/")
    client = Minio(host, access_key=access_key, secret_key=secret_key, secure=secure)
    if not client.bucket_exists(bucket):
        raise SystemExit(f"bucket 不存在：{bucket}")
    found: list[tuple[str, bytes]] = []
    for item in client.list_objects(bucket, prefix="agents/", recursive=True):
        if _is_session_key(item.object_name):
            found.append((item.object_name, client.get_object(bucket, item.object_name).read()))
    return found


def _shape(value: object, depth: int = 0) -> str:
    """给一个值的类型形状：dict 显示键清单，list 显示长度，其余显示类型。"""
    if isinstance(value, dict):
        if depth >= 2:
            return f"dict[{len(value)}]"
        keys = ", ".join(sorted(value.keys())[:12])
        more = "…" if len(value) > 12 else ""
        return f"dict{{{keys}{more}}}"
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, str):
        return f"str[{len(value)}]"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return type(value).__name__
    return type(value).__name__


def summarize(data: object, key: str) -> dict[str, object]:
    """提取一个会话文件的结构摘要（只读不落盘）。"""
    summary: dict[str, object] = {"key": key}
    if not isinstance(data, dict):
        summary["top_level"] = f"NOT_A_DICT({type(data).__name__})"
        return summary

    summary["top_level_keys"] = sorted(data.keys())
    agent = data.get("agent")
    if not isinstance(agent, dict):
        summary["agent"] = f"NOT_A_DICT({type(agent).__name__})"
        return summary

    summary["agent_keys"] = sorted(agent.keys())
    summary["agent_name"] = agent.get("name")
    memory = agent.get("memory")
    content: list[object] = []
    if isinstance(memory, dict) and isinstance(memory.get("content"), list):
        content = memory["content"]

    roles: Counter[str] = Counter()
    block_types: Counter[str] = Counter()
    metadata_keys: Counter[str] = Counter()
    parsed = 0
    skipped = 0
    for entry in content:
        if not (isinstance(entry, list) and len(entry) >= 1 and isinstance(entry[0], dict)):
            skipped += 1
            continue
        msg = entry[0]
        parsed += 1
        role = msg.get("role")
        if isinstance(role, str):
            roles[role] += 1
        blocks = msg.get("content")
        if isinstance(blocks, list):
            for block in blocks:
                if isinstance(block, dict) and isinstance(block.get("type"), str):
                    block_types[block["type"]] += 1
        meta = msg.get("metadata")
        if isinstance(meta, dict):
            for mkey in meta:
                metadata_keys[mkey] += 1

    summary["memory_content_len"] = len(content)
    summary["parsed_msgs"] = parsed
    summary["skipped_msgs"] = skipped
    summary["roles"] = dict(roles)
    summary["block_types"] = dict(block_types)
    summary["metadata_keys"] = dict(metadata_keys)
    return summary


def print_summary(summary: dict[str, object]) -> None:
    key = summary["key"]
    if "top_level_keys" not in summary:
        print(f"  {key}\n    顶层非 dict，跳过")
        return
    print(f"  {key}")
    print(f"    top_level={summary['top_level_keys']}")
    if "agent_keys" not in summary:
        print("    agent 非 dict，跳过")
        return
    print(f"    agent={summary['agent_keys']}")
    print(f"    agent.name={summary['agent_name']!r}")
    print(f"    memory.content 长度={summary['memory_content_len']} "
          f"(解析 {summary['parsed_msgs']} 条，跳过 {summary['skipped_msgs']} 条)")
    print(f"    roles={summary['roles']}")
    print(f"    block_types={summary['block_types']}")
    print(f"    metadata_keys={summary['metadata_keys']}")


def dump_first(data: object, key: str) -> None:
    print(f"\n── 完整样例：{key} ──")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:8000])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M0 会话结构侦察（只读）")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--root", type=Path, help="本地存储根目录（STORAGE_ROOT）")
    src.add_argument(
        "--endpoint", help="MinIO endpoint（配 --access-key/--secret-key）"
    )
    parser.add_argument("--access-key", default="")
    parser.add_argument("--secret-key", default="")
    parser.add_argument("--bucket", default="agentteams-storage")
    parser.add_argument("--dump-first", action="store_true", help="打印第一个会话完整 JSON")
    args = parser.parse_args(argv)

    if args.root is not None:
        found = discover_local(args.root)
        items: list[tuple[str, object]] = []
        for key, path in found:
            try:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                print(f"  {key}\n    JSON 解析失败：{exc}", file=sys.stderr)
                continue
            items.append((key, data))
    else:
        found = discover_minio(args.endpoint, args.access_key, args.secret_key, args.bucket)
        items = []
        for key, raw in found:
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                print(f"  {key}\n    JSON 解析失败：{exc}", file=sys.stderr)
                continue
            items.append((key, data))

    if not items:
        where = (
            f"本地目录 {args.root}"
            if args.root is not None
            else f"MinIO {args.endpoint}/{args.bucket}"
        )
        print(f"未发现会话文件（{where}）。\n"
              "确认 Agent 已运行并把会话推入存储；会话路径约定：\n"
              "  agents/{{name}}/.copaw/workspaces/default/sessions/*.json")
        return 1

    print(f"发现 {len(items)} 个会话文件：")
    for key, data in items:
        print_summary(summarize(data, key))
    if args.dump_first:
        dump_first(items[0][1], items[0][0])

    counts: Counter[str] = Counter()
    for _, data in items:
        summary = summarize(data, "")
        if "block_types" in summary:
            for btype, n in summary["block_types"].items():
                counts[btype] += n
    if counts:
        print(f"\n全部会话的块类型合计：{dict(counts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
