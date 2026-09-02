#!/usr/bin/env bash
# AC-C2 fixture: plant two itest-* leftovers in the NEXT round's worktree the
# moment the platform creates it, before the recipe's opening sweep runs.
#
#   itest-stale-c2  -> mtime pushed back past the 24h TTL  (must be removed)
#   itest-fresh-c2  -> just created                        (must be kept)
#
# The worktree only exists once the round is dispatched, and the recipe runs
# in the test phase after the agent phase, so there is a window of at least
# the agent phase to plant them. Run this in the background, then dispatch.
#
# Usage: c2_plant_leftovers.sh <workspace-root>/w <repo-hash-dir> [timeout-s]
set -euo pipefail
ROOT="${1:?workspace w/ dir}"
REPO_DIR="${2:?repo hash dir name}"
TIMEOUT="${3:-900}"

known="$(ls -1 "$ROOT")"
echo "watching $ROOT for a new run dir (known: $(echo "$known" | wc -l))"
deadline=$(( $(date +%s) + TIMEOUT ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  for d in "$ROOT"/*; do
    name="$(basename "$d")"
    if ! grep -qx "$name" <<<"$known"; then
      wt="$d/$REPO_DIR"
      if [ -e "$wt/.git" ]; then
        mkdir -p "$wt/itest-stale-c2/nested" "$wt/itest-fresh-c2/nested"
        echo "stale leftover (fixture)" > "$wt/itest-stale-c2/nested/marker.txt"
        echo "fresh leftover (fixture)" > "$wt/itest-fresh-c2/nested/marker.txt"
        # contents first, then the root: writing inside a directory bumps its mtime.
        touch -d "30 hours ago" "$wt/itest-stale-c2/nested/marker.txt" "$wt/itest-stale-c2/nested" "$wt/itest-stale-c2"
        echo "planted in $wt at $(date -Is)"
        stat -c '%n %y' "$wt/itest-stale-c2" "$wt/itest-fresh-c2"
        exit 0
      fi
    fi
  done
  sleep 1
done
echo "timeout: no new worktree appeared" >&2
exit 1
