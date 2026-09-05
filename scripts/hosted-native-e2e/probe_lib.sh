#!/usr/bin/env bash
# probe_lib.sh -- record one acceptance-script probe per scene.
# Usage: source probe_lib.sh; probe NN 'command string'   (the command string is echoed verbatim, then run with bash -c)
# Secrets: reference tokens through exported variables ($T, $MT) so the echoed command never contains a value.
OUT="${OUT:-D:/Project4work/GOAI-infra-repomesh/output/hosted-native-e2e/2026-09-03}"
export OUT; mkdir -p "$OUT"
export T="$(sed -n 's/^REPOMESH_AGENT_ACTION_TOKEN=//p' D:/Project4work/GOAI-infra-repomesh/.secrets/platform.env | tail -1)"
export MT="$(sed -n 's/^REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN=//p' D:/Project4work/GOAI-infra-repomesh/.secrets/platform-runtime.env | tail -1)"
export API="http://127.0.0.1:8000/api/v1"
export MX="http://127.0.0.1:18080/_matrix/client/v3"
export PSQL="docker exec goai-infra-repomesh-postgres-1 psql -U repomesh -d repomesh -At"
export MSYS_NO_PATHCONV=1
export PYTHONIOENCODING=utf-8

probe() {
  local nn="$1"; shift
  if [ -n "${ONLY:-}" ] && [ "$nn" != "$ONLY" ]; then return 0; fi
  local cmd="$*"
  local out="$OUT/$nn.txt"
  {
    echo "# scene $nn"
    echo "# started: $(date -u +%Y-%m-%dT%H:%M:%SZ) UTC / $(date +%H:%M:%S) local"
    echo "\$ $cmd"
  } > "$out"
  bash -c "$cmd" >> "$out" 2>&1
  local rc=$?
  echo "# exit: $rc  finished: $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$out"
  # never let a token leak into the record
  sed -i "s#$T#<action-token>#g; s#$MT#<matrix-admin-token>#g" "$out"
  cat "$out"
  return 0
}
