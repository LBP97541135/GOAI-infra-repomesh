#!/usr/bin/env bash
# Answer a copaw Tool Guard prompt with "/approve" as @admin, @mentioning the member (group rooms require a mention).
# MSYS_NO_PATHCONV=1 is essential: Git Bash would otherwise rewrite "/approve" into "D:/Git/approve" and DENY the tool.
# Usage: approve.sh team|leader agt-worker-dfb8a4cda6f7
set -euo pipefail
export MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8
S="$(cygpath -m "$(cd "$(dirname "$0")" && pwd)")"
cd D:/Project4work/GOAI-infra-repomesh
python "$S/send_room.py" --room "$1" --to "$2" --text '/approve' --bare | tee -a output/hosted-native-e2e/2026-09-03/spike/sent.jsonl
