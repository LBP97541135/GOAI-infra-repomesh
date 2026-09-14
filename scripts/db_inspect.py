"""RepoMesh database inspection (B1 baseline tool).

Read-only census of the fact-source PostgreSQL: per schema, per table —
exact row count, total size, newest write (max over the table's first
timestamp column, labeled), and one redacted sample row as compact JSON.
Empty tables surface as empty — the point of the tool is to make "which
data is real" a one-command answer, not a meeting.

Everything queries the standard catalogs (information_schema / pg_class),
so the report logic ports to any driver unchanged (the Go version copies
the SQL, not this file). Secrets hygiene: sample values under keys that
look credential-ish (password/token/secret/hash/salt/credential/encrypted)
are masked before they ever reach the report.

Usage:
    uv run python scripts/db_inspect.py                 # print to stdout
    uv run python scripts/db_inspect.py --out PATH.md   # also write a file
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import text  # noqa: E402

from repomesh.persistence import Database  # noqa: E402

_SENSITIVE = re.compile(
    r"password|passwd|secret|token|salt|credential|encrypted|api_key|"
    r"private_key|webhook|hash",
    re.IGNORECASE,
)
_TRUNCATE_VALUE = 80
_TRUNCATE_SAMPLE = 300


def _q(identifier: str) -> str:
    """Quote one identifier for safe interpolation (names are catalog-read)."""
    return '"' + identifier.replace('"', '""') + '"'


def _redact_sample(sample: dict) -> str:
    clean = {}
    for key, value in sample.items():
        if _SENSITIVE.search(str(key)):
            clean[str(key)] = "<redacted>"
            continue
        if isinstance(value, str) and len(value) > _TRUNCATE_VALUE:
            value = value[:_TRUNCATE_VALUE] + "…"
        clean[str(key)] = value
    text_form = json.dumps(clean, ensure_ascii=False, default=str)
    if len(text_form) > _TRUNCATE_SAMPLE:
        text_form = text_form[:_TRUNCATE_SAMPLE] + "…"
    return text_form


async def _census(database: Database) -> list[dict]:
    """One record per table: {schema, table, rows, size, newest, sample}."""
    records: list[dict] = []
    async with database.transaction() as session:
        schemas = (
            await session.scalars(
                text(
                    "SELECT nspname FROM pg_namespace "
                    "WHERE nspname NOT LIKE 'pg\\_%' AND nspname <> 'information_schema' "
                    "ORDER BY nspname"
                )
            )
        ).all()
        for schema in schemas:
            tables = (
                await session.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = :s AND table_type = 'BASE TABLE' "
                        "ORDER BY table_name"
                    ),
                    {"s": schema},
                )
            ).scalars()
            for table in tables:
                qualified = f"{_q(schema)}.{_q(table)}"
                rows = (
                    await session.scalar(text(f"SELECT count(*) FROM {qualified}"))
                    or 0
                )
                size = await session.scalar(
                    text(f"SELECT pg_total_relation_size({qualified!r})")
                )
                ts_column = await session.scalar(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = :s AND table_name = :t "
                        "AND data_type IN ('timestamp with time zone', "
                        "'timestamp without time zone', 'date') "
                        "ORDER BY ordinal_position LIMIT 1"
                    ),
                    {"s": schema, "t": table},
                )
                newest: str | None = None
                if rows and ts_column:
                    newest = await session.scalar(
                        text(
                            f"SELECT max({_q(ts_column)})::text FROM {qualified}"
                        )
                    )
                sample_raw = (
                    await session.scalar(
                        text(
                            f"SELECT to_jsonb(t) FROM {qualified} t LIMIT 1"
                        )
                    )
                    if rows
                    else None
                )
                records.append(
                    {
                        "schema": schema,
                        "table": table,
                        "rows": rows,
                        "size": int(size or 0),
                        "newest": newest,
                        "ts_column": ts_column if rows else None,
                        "sample": _redact_sample(sample_raw) if sample_raw else None,
                    }
                )
    return records


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{num_bytes}B"


def _render(records: list[dict], dsn_display: str, heads: list[str]) -> str:
    now = datetime.now()
    total_rows = sum(r["rows"] for r in records)
    empty = [f"{r['schema']}.{r['table']}" for r in records if r["rows"] == 0]
    lines = [
        "# RepoMesh 数据库基线报告",
        "",
        f"- 生成时间：{now:%Y-%m-%d %H:%M:%S}",
        f"- 数据源：`{dsn_display}`（只读巡检，未写任何业务表）",
        f"- Alembic head：{', '.join(heads) or '未取得'}",
        f"- 概览：{len({r['schema'] for r in records})} 个 schema，"
        f"{len(records)} 张表，合计 {total_rows} 行，"
        f"空表 {len(empty)} 张",
        "",
    ]
    by_schema: dict[str, list[dict]] = {}
    for record in records:
        by_schema.setdefault(record["schema"], []).append(record)
    for schema in sorted(by_schema):
        lines.append(f"## {schema}")
        lines.append("")
        lines.append("| 表 | 行数 | 大小 | 最近写入(按列) | 样本(脱敏) |")
        lines.append("| --- | ---: | ---: | --- | --- |")
        for r in by_schema[schema]:
            newest = (
                f"{r['newest'][:19]} ({r['ts_column']})"
                if r["newest"]
                else "—"
            )
            sample = f"`{r['sample']}`" if r["sample"] else "—"
            lines.append(
                f"| {r['table']} | {r['rows']} | {_human_size(r['size'])} "
                f"| {newest} | {sample} |"
            )
        lines.append("")
    if empty:
        lines.append("## 空表清单")
        lines.append("")
        for name in empty:
            lines.append(f"- {name}")
        lines.append("")
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=pathlib.Path, default=None, help="同时把报告写入该路径"
    )
    args = parser.parse_args()

    env: dict[str, str] = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key] = value
    url = env.get("REPOMESH_DATABASE_URL")
    if not url:
        raise SystemExit("REPOMESH_DATABASE_URL 未配置（.env）")
    dsn_display = re.sub(r"://[^@/]+@", "://***@", url)

    database = Database(url)
    async with database.transaction() as session:
        heads = (
            await session.scalars(
                text("SELECT version_num FROM public.alembic_version")
            )
        ).all()
        extensions = (
            await session.execute(
                text("SELECT extname, extversion FROM pg_extension ORDER BY extname")
            )
        ).all()
    records = await _census(database)
    report = _render(records, dsn_display, list(heads))
    extension_line = ", ".join(f"{name} {ver}" for name, ver in extensions)
    report = report.replace(
        "- Alembic head：",
        f"- 已装扩展：{extension_line}\n- Alembic head：",
    )

    print(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"[written] {args.out}", file=sys.stderr)
    await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
