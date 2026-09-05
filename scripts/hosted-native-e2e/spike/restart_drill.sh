#!/usr/bin/env bash
# Restart drill for attempt 2: approve `init`, wait until the worker has edited files and asks to run `test`,
# then `docker restart` the worker container mid-construction. Records the workspace state before and after.
# Usage: restart_drill.sh <attempt2-id>
set -u
export MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8
S="$(cygpath -m "$(cd "$(dirname "$0")" && pwd)")"
A2="$1"
W=agentteams-worker-agt-worker-dfb8a4cda6f7
L=D:/Project4work/GOAI-infra-repomesh/output/hosted-native-e2e/2026-09-03/spike
DRILL="$L/restart_drill.log"
say() { echo "$(date -u +%H:%M:%S) $*" | tee -a "$DRILL"; }

snapshot() {
  docker exec $W sh -c "echo 'container uptime:'; cat /proc/uptime | cut -d' ' -f1; echo '/work:'; ls /work; \
    if [ -d /work/$A2/.git ]; then echo 'attempt2 HEAD:' \$(git -C /work/$A2 rev-parse --short HEAD); echo 'attempt2 status:'; git -C /work/$A2 status --short; git -C /work/$A2 diff --stat | tail -1; else echo 'attempt2 workspace missing'; fi; \
    echo 'local task dirs:'; ls /root/.copaw-worker/agt-worker-dfb8a4cda6f7/.copaw/workspaces/default/shared/tasks/; \
    echo 'attempt2 local files:'; ls /root/.copaw-worker/agt-worker-dfb8a4cda6f7/.copaw/workspaces/default/shared/tasks/$A2/ 2>&1" 2>&1 | sed 's/^/    /' | tee -a "$DRILL"
}

say "drill start attempt2=$A2"
say "approving init"
bash "$S/approve.sh" team agt-worker-dfb8a4cda6f7 >/dev/null
deadline=$(( $(date +%s) + 600 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  # the room watcher writes team-room messages into watch.log; look for a fresh test prompt
  if grep "Waiting for approval" "$L/watch.log" | grep "$A2" | grep -q "rm-work.sh test"; then
    say "test prompt seen; worker has edited files and wants to run test -> restarting container NOW"
    say "--- state before restart"; snapshot
    t0=$(date +%s)
    docker restart $W >/dev/null && say "docker restart returned after $(( $(date +%s) - t0 ))s"
    sleep 8
    say "--- state after restart"; snapshot
    docker ps --filter name=$W --format '{{.Names}} {{.Status}}' | tee -a "$DRILL"
    exit 0
  fi
  sleep 4
done
say "no test prompt within 10 minutes; drill not executed"
exit 1
