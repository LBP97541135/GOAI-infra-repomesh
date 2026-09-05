#!/usr/bin/env bash
# Render every recorded probe transcript to HTML and screenshot it with headless Chrome at 1440x900 (dark).
# Usage: shoot_terminal.sh <report-name>   e.g. 2026-09-03-hosted-native-e2e-baseline
set -u
SC="$(cygpath -m "$(cd "$(dirname "$0")" && pwd)")"
REPORT="${1:-2026-09-03-hosted-native-e2e-baseline}"
ROOT=D:/Project4work/GOAI-infra-repomesh
OUT="$ROOT/output/hosted-native-e2e/2026-09-03"
SHOTS="$ROOT/docs/startup-records/$REPORT"
CHROME="/c/Program Files/Google/Chrome/Application/chrome.exe"
mkdir -p "$OUT/html" "$SHOTS"
export MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8

# scene -> screenshot file name (NN_页面_状态.png); terminal evidence is marked 终端
declare -A NAME=(
  [01]="01_终端_setup-status.png"
  [03]="03_终端_console-repositories.png"
  [05]="05_终端_agt-get-workers.png"
  [06]="06_终端_docker-ps-worker容器.png"
  [07]="07_终端_POST-issues与列表.png"
  [08]="08_终端_plan-snapshot批次.png"
  [11]="11_终端_mc-ls任务包与房间派单.png"
  [11s]="11_终端_密钥探针零命中.png"
  [13]="13_终端_result-md-BLOCKED.png"
  [19]="19_终端_trace-sessions.png"
  [20]="20_终端_observe-issues.png"
  [21]="21_终端_log-entries-ERROR.png"
  [22]="22_终端_alert-rules.png"
  [28]="28_演练F_终端_告警联动.png"
  [30]="30_终端_终态账面.png"
)

for f in "$OUT"/*.txt; do
  n="$(basename "$f" .txt)"
  target="${NAME[$n]:-}"
  [ -n "$target" ] || continue
  python "$SC/render_probe_html.py" "$f" "$OUT/html/$n.html" "baseline probe · scene $n" >/dev/null
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --window-size=1440,900 --force-dark-mode \
    --screenshot="$SHOTS/$target" "file:///$OUT/html/$n.html" >/dev/null 2>&1
  echo "$n -> $target ($(stat -c %s "$SHOTS/$target" 2>/dev/null || echo missing) bytes)"
done
