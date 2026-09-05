#!/usr/bin/env python3
"""Assemble a RepoMesh hosted-native task package (v2 layout) for the wave-0 spike.

Usage:
  build_package.py construction --attempt-id <uuid> --generation N --out <root>
      [--previous-note TEXT]
  build_package.py review --attempt-id <uuid> --review-of <construction attempt uuid>
      --candidate-dir <dir> --out <root>

The package directory is <root>/<attempt-id>/ and its name is the attempt id (spec D-8).
"""

import argparse
import hashlib
import json
import shutil
import string
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manifest(target: Path, kind: str, attempt_id: str) -> dict:
    files = sorted(
        p.relative_to(target).as_posix()
        for p in target.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    )
    digests = {name: f"sha256:{sha256_of(target / name)}" for name in files}
    sizes = {name: (target / name).stat().st_size for name in files}
    joined = "".join(f"{name}\0{digests[name]}\n" for name in files)
    manifest = {
        "schema": "repomesh.agentteams-task.v2",
        "kind": kind,
        "attempt_id": attempt_id,
        "files": files,
        "file_digests": digests,
        "file_sizes": sizes,
        "content_hash": f"sha256:{hashlib.sha256(joined.encode()).hexdigest()}",
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def render(template: Path, values: dict) -> str:
    return string.Template(template.read_text(encoding="utf-8")).substitute(values)


def build_construction(args) -> Path:
    target = Path(args.out) / args.attempt_id
    if target.exists():
        sys.exit(f"refusing to overwrite existing package dir {target}")
    (target / "base" / "tools").mkdir(parents=True)
    shutil.copyfile(HERE / "base.bundle", target / "base" / "base.bundle")
    shutil.copyfile(HERE / "rm-work.sh", target / "base" / "tools" / "rm-work.sh")
    package = {
        "schema": "repomesh.agentteams-task.v2/package",
        "kind": "construction",
        "attempt_id": args.attempt_id,
        "generation": args.generation,
        "task_id": CONFIG["task_id"],
        "repository": CONFIG["repository"],
        "repository_id": CONFIG["repository_id"],
        "organization_id": CONFIG["organization_id"],
        "base_sha": CONFIG["base_sha"],
        "budget_seconds": CONFIG["budget_seconds"],
        "test_commands": CONFIG["test_commands"],
        "test_timeout_seconds": CONFIG["test_timeout_seconds"],
        "allowed_paths": CONFIG["allowed_paths"],
        "denied_paths": CONFIG["denied_paths"],
        "helper": "base/tools/rm-work.sh",
        "workspace_root": "/work",
    }
    (target / "base" / "package.json").write_text(
        json.dumps(package, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    values = {
        "attempt_id": args.attempt_id,
        "generation": args.generation,
        "task_id": CONFIG["task_id"],
        "base_sha": CONFIG["base_sha"],
        "budget_minutes": CONFIG["budget_seconds"] // 60,
        "instruction": CONFIG["instruction"],
        "test_command": CONFIG["test_commands"][0],
    }
    spec = render(HERE / "spec_construction.md.tpl", values)
    if args.previous_note:
        spec += "\n## Note from the previous attempt\n\n" + args.previous_note.strip() + "\n"
    (target / "spec.md").write_text(spec, encoding="utf-8")
    meta = {
        "task_id": args.attempt_id,
        "project_id": CONFIG["project_id"],
        "task_title": (
            f"Implement multi-currency quote() for {CONFIG['repository']} "
            f"(attempt {args.generation})"
        ),
        "assigned_to": CONFIG["worker"],
        "room_id": CONFIG["team_room_id"],
        "status": "assigned",
        "depends_on": [],
        "repomesh": {
            "kind": "construction",
            "task_id": CONFIG["task_id"],
            "attempt_id": args.attempt_id,
            "generation": args.generation,
            "budget_seconds": CONFIG["budget_seconds"],
            "base_sha": CONFIG["base_sha"],
            "repository_id": CONFIG["repository_id"],
            "organization_id": CONFIG["organization_id"],
            "package": "base/package.json",
        },
    }
    (target / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_manifest(target, "construction", args.attempt_id)
    return target


def build_review(args) -> Path:
    target = Path(args.out) / args.attempt_id
    if target.exists():
        sys.exit(f"refusing to overwrite existing package dir {target}")
    candidate = Path(args.candidate_dir)
    (target / "review").mkdir(parents=True)
    for name in ("candidate.diff", "changes.json", "evidence.json"):
        shutil.copyfile(candidate / name, target / "review" / name)
    changes = json.loads((candidate / "changes.json").read_text(encoding="utf-8"))
    evidence = json.loads((candidate / "evidence.json").read_text(encoding="utf-8"))
    diff_text = (candidate / "candidate.diff").read_text(encoding="utf-8")
    tests_block = "\n".join(
        f"- `{t['command']}` -> exit {t['exit_code']}\n\n  ```text\n  "
        + "\n  ".join(t["excerpt"].splitlines()[-12:])
        + "\n  ```"
        for t in evidence["tests"]
    )
    values = {
        "attempt_id": args.attempt_id,
        "review_of": args.review_of,
        "task_id": CONFIG["task_id"],
        "base_sha": CONFIG["base_sha"],
        "head_sha": changes["head_sha"],
        "instruction": CONFIG["instruction"],
        "allowed_paths": ", ".join(f"`{p}`" for p in CONFIG["allowed_paths"]),
        "denied_paths": ", ".join(f"`{p}`" for p in CONFIG["denied_paths"]),
        "changed_files": "\n".join(
            f"- `{c['status']}` `{c['path']}`" for c in changes["changed_files"]
        ),
        "tests_block": tests_block,
        "diff": diff_text,
        "budget_minutes": CONFIG["review_budget_seconds"] // 60,
        "test_command": CONFIG["test_commands"][0],
    }
    (target / "spec.md").write_text(render(HERE / "spec_review.md.tpl", values), encoding="utf-8")
    meta = {
        "task_id": args.attempt_id,
        "project_id": CONFIG["project_id"],
        "task_title": (
            f"Review candidate {changes['head_sha'][:8]} for {CONFIG['repository']} "
            f"(attempt {args.review_of[:8]})"
        ),
        "assigned_to": CONFIG["leader"],
        "room_id": CONFIG["leader_room_id"],
        "status": "assigned",
        "depends_on": [],
        "repomesh": {
            "kind": "review",
            "task_id": CONFIG["task_id"],
            "attempt_id": args.attempt_id,
            "review_of": args.review_of,
            "budget_seconds": CONFIG["review_budget_seconds"],
            "base_sha": CONFIG["base_sha"],
            "head_sha": changes["head_sha"],
            "repository_id": CONFIG["repository_id"],
            "organization_id": CONFIG["organization_id"],
        },
    }
    (target / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_manifest(target, "review", args.attempt_id)
    return target


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="kind", required=True)
    c = sub.add_parser("construction")
    c.add_argument("--attempt-id", required=True)
    c.add_argument("--generation", type=int, default=1)
    c.add_argument("--out", required=True)
    c.add_argument("--previous-note", default=None)
    r = sub.add_parser("review")
    r.add_argument("--attempt-id", required=True)
    r.add_argument("--review-of", required=True)
    r.add_argument("--candidate-dir", required=True)
    r.add_argument("--out", required=True)
    args = ap.parse_args()
    target = build_construction(args) if args.kind == "construction" else build_review(args)
    print(target)
    for p in sorted(target.rglob("*")):
        if p.is_file():
            print(f"  {p.relative_to(target).as_posix()}  {p.stat().st_size} B")


if __name__ == "__main__":
    main()
