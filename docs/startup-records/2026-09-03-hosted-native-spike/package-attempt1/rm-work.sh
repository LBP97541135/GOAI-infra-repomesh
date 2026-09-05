#!/usr/bin/env bash
# rm-work.sh -- RepoMesh hosted-native construction helper (task package v2, wave-0 spike build 2026-09-03).
#
#   bash base/tools/rm-work.sh init     create the local workspace from base/base.bundle at the base commit
#   bash base/tools/rm-work.sh test     run the frozen test commands inside the workspace and record the results
#   bash base/tools/rm-work.sh bundle   write candidate/{candidate.bundle,candidate.diff,changes.json,evidence.json}
#   bash base/tools/rm-work.sh clean    delete the local workspace
#
# The workspace lives OUTSIDE the task directory (default /work/<attempt_id>) so that the task directory
# never contains a repository checkout, .git, dependencies or build caches.
# This script carries no credentials and never talks to the network.
set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
TASK_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PACKAGE="$TASK_DIR/base/package.json"
BUNDLE="$TASK_DIR/base/base.bundle"
CANDIDATE_DIR="$TASK_DIR/candidate"
WORK_ROOT="${RM_WORK_ROOT:-/work}"

die() { echo "rm-work: error: $*" >&2; exit 2; }
note() { echo "rm-work: $*"; }

[ -f "$PACKAGE" ] || die "base/package.json not found (expected at $PACKAGE)"
command -v git >/dev/null 2>&1 || die "git is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"

pkg() {
  python3 - "$PACKAGE" "$1" <<'PY'
import json, sys
node = json.load(open(sys.argv[1], encoding="utf-8"))
for part in sys.argv[2].split("."):
    node = node[part]
print("\n".join(str(item) for item in node) if isinstance(node, list) else node)
PY
}

ATTEMPT_ID="$(pkg attempt_id)"
BASE_SHA="$(pkg base_sha)"
[ "$(basename "$TASK_DIR")" = "$ATTEMPT_ID" ] || die "task directory name $(basename "$TASK_DIR") does not match attempt_id $ATTEMPT_ID"
WORK="$WORK_ROOT/$ATTEMPT_ID"
STATE_DIR="$WORK_ROOT/.rm-work-state/$ATTEMPT_ID"
RESULTS="$STATE_DIR/test-results.json"
TASK_REL="shared/tasks/$ATTEMPT_ID"

# Hash of the complete working tree (tracked + untracked, minus ignored), independent of commits.
worktree_hash() {
  local index="$STATE_DIR/index.tmp"
  rm -f "$index"
  GIT_INDEX_FILE="$index" git -C "$WORK" add -A . >/dev/null
  GIT_INDEX_FILE="$index" git -C "$WORK" write-tree
}

require_workspace() {
  [ -d "$WORK/.git" ] || die "workspace $WORK is not initialised; run: bash $SCRIPT_PATH init"
  mkdir -p "$STATE_DIR"
}

cmd_init() {
  mkdir -p "$WORK_ROOT" "$STATE_DIR"
  if [ -d "$WORK/.git" ]; then
    local changed
    changed="$(git -C "$WORK" status --porcelain | wc -l | tr -d ' ')"
    note "workspace already initialised: $WORK (HEAD $(git -C "$WORK" rev-parse --short HEAD), $changed changed paths)"
    return 0
  fi
  [ -f "$BUNDLE" ] || die "base bundle missing: $BUNDLE"
  git -c init.defaultBranch=main clone --quiet "$BUNDLE" "$WORK"
  git -C "$WORK" checkout --quiet -B work "$BASE_SHA"
  git -C "$WORK" config user.name "RepoMesh Worker"
  git -C "$WORK" config user.email "worker@repomesh.invalid"
  printf '__pycache__/\n*.pyc\n.pytest_cache/\nnode_modules/\n' >> "$WORK/.git/info/exclude"
  local head
  head="$(git -C "$WORK" rev-parse HEAD)"
  [ "$head" = "$BASE_SHA" ] || die "workspace HEAD $head is not the base commit $BASE_SHA"
  note "workspace ready: $WORK"
  note "base commit: $BASE_SHA"
  note "edit files under the workspace, then run: bash $SCRIPT_PATH test"
}

cmd_test() {
  require_workspace
  python3 - "$WORK" "$RESULTS" "$PACKAGE" <<'PY'
import json, subprocess, sys, time
from datetime import datetime, timezone
work, results_path, package_path = sys.argv[1:4]
package = json.load(open(package_path, encoding="utf-8"))
timeout = int(package.get("test_timeout_seconds", 600))
results = []
for command in package["test_commands"]:
    started = time.monotonic()
    try:
        proc = subprocess.run(command, shell=True, cwd=work, capture_output=True, text=True, timeout=timeout)
        code, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or ""
        code, out = 124, (raw.decode(errors="replace") if isinstance(raw, bytes) else raw) + f"\n[timeout after {timeout}s]"
    excerpt = "\n".join(out.strip().splitlines()[-40:])
    results.append({"command": command, "exit_code": code, "duration_seconds": round(time.monotonic() - started, 2), "excerpt": excerpt})
    print(f"$ {command}\n{excerpt}\n[exit {code}]\n")
json.dump({"ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "results": results}, open(results_path, "w", encoding="utf-8"), indent=2)
PY
  # Record the tree hash after the run so generated-but-ignored files do not invalidate it.
  local tree
  tree="$(worktree_hash)"
  python3 - "$RESULTS" "$tree" <<'PY'
import json, sys
path, tree = sys.argv[1:3]
data = json.load(open(path, encoding="utf-8"))
data["tree"] = tree
json.dump(data, open(path, "w", encoding="utf-8"), indent=2)
failed = [r for r in data["results"] if r["exit_code"] != 0]
if failed:
    print(f"rm-work: {len(failed)} of {len(data['results'])} test command(s) FAILED; fix the code and run test again")
    sys.exit(1)
print(f"rm-work: all tests passed ({len(data['results'])} command(s)); next: bash base/tools/rm-work.sh bundle")
PY
}

cmd_bundle() {
  require_workspace
  [ -f "$RESULTS" ] || die "no test results recorded; run: bash $SCRIPT_PATH test"
  local tree tested
  tree="$(worktree_hash)"
  tested="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("tree",""))' "$RESULTS")"
  [ "$tree" = "$tested" ] || die "the workspace changed after the last test run; run: bash $SCRIPT_PATH test"
  if python3 -c 'import json,sys; sys.exit(0 if all(r["exit_code"]==0 for r in json.load(open(sys.argv[1]))["results"]) else 1)' "$RESULTS"; then
    note "last test run: all passed"
  else
    note "WARNING: the last test run had failures; evidence.json will record the candidate as red"
  fi
  git -C "$WORK" add -A .
  if ! git -C "$WORK" diff --cached --quiet; then
    git -C "$WORK" commit --quiet -m "candidate for attempt $ATTEMPT_ID"
  fi
  local count
  count="$(git -C "$WORK" rev-list --count "$BASE_SHA..HEAD")"
  [ "$count" -gt 0 ] || die "the workspace has no changes relative to the base commit; nothing to bundle"
  if [ "$count" -gt 1 ]; then
    git -C "$WORK" reset --quiet --soft "$BASE_SHA"
    git -C "$WORK" commit --quiet -m "candidate for attempt $ATTEMPT_ID"
  fi
  local head parent
  head="$(git -C "$WORK" rev-parse HEAD)"
  parent="$(git -C "$WORK" rev-parse HEAD^)"
  [ "$parent" = "$BASE_SHA" ] || die "candidate parent $parent is not the base commit"
  rm -rf "$CANDIDATE_DIR"
  mkdir -p "$CANDIDATE_DIR"
  git -C "$WORK" bundle create "$CANDIDATE_DIR/candidate.bundle" "$BASE_SHA..refs/heads/work" >/dev/null 2>&1
  git -C "$WORK" diff "$BASE_SHA" HEAD > "$CANDIDATE_DIR/candidate.diff"
  python3 - "$WORK" "$CANDIDATE_DIR" "$RESULTS" "$ATTEMPT_ID" "$BASE_SHA" "$head" <<'PY'
import json, subprocess, sys
from datetime import datetime, timezone
work, out, results_path, attempt, base, head = sys.argv[1:7]
status = subprocess.run(["git", "-C", work, "diff", "--name-status", base, head], capture_output=True, text=True, check=True).stdout
changed = [{"status": p[0][:1], "path": p[-1]} for p in (l.split("\t") for l in status.splitlines()) if len(p) >= 2]
tests = json.load(open(results_path, encoding="utf-8"))
now = datetime.now(timezone.utc).isoformat(timespec="seconds")
json.dump({"attempt_id": attempt, "base_sha": base, "head_sha": head, "changed_files": changed},
          open(f"{out}/changes.json", "w", encoding="utf-8"), indent=2)
json.dump({"attempt_id": attempt, "base_sha": base, "head_sha": head, "tree": tests.get("tree"), "tests_ran_at": tests["ran_at"],
           "tests": [{"command": r["command"], "exit_code": r["exit_code"], "excerpt": r["excerpt"]} for r in tests["results"]],
           "produced_at": now}, open(f"{out}/evidence.json", "w", encoding="utf-8"), indent=2)
print(f"candidate head: {head} (parent {base})")
print("changed files: " + ", ".join(f"{c['status']} {c['path']}" for c in changed))
PY
  note "candidate written into the task directory:"
  ls -l "$CANDIDATE_DIR" | tail -n +2 | sed 's/^/    /'
  note "submit with taskflow submit_task using exactly these deliverables:"
  for f in candidate.bundle candidate.diff changes.json evidence.json; do
    echo "    $TASK_REL/candidate/$f"
  done
}

cmd_clean() {
  rm -rf "$WORK" "$STATE_DIR"
  note "removed workspace $WORK"
}

case "${1:-}" in
  init) cmd_init ;;
  test) cmd_test ;;
  bundle) cmd_bundle ;;
  clean) cmd_clean ;;
  *) echo "usage: bash $SCRIPT_PATH init|test|bundle|clean" >&2; exit 64 ;;
esac
