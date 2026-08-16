#!/usr/bin/env bash
# Symmetric teardown for scripts/dev-up.sh (batch S-1).
#
# Only components this repository's dev-up started are considered, and each one
# is confirmed before it is stopped. State lives in .repomesh-dev/: a pid file
# per process, a marker file for the postgres container. No state file, no
# action - a service you started yourself is never touched.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}" || exit 1

STATE_DIR="${REPO_ROOT}/.repomesh-dev"
assume_yes=0

usage() {
  cat <<'USAGE'
用法： scripts/dev-down.sh [选项]

  -y, --yes    不逐项询问，直接停（仍然只停本仓 dev-up 起的组件）
  -h, --help   显示本帮助

只会动 .repomesh-dev/ 里记录过的组件：
  backend.pid      dev-up 起的 uvicorn（8100）
  frontend.pid     dev-up 起的 vite（5280）
  postgres.started dev-up 起的 compose postgres 容器
没有记录的组件一律跳过——你自己起的服务不在本脚本的管辖范围内。
USAGE
}

for argument in "$@"; do
  case "${argument}" in
    -y|--yes) assume_yes=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：${argument}（用 --help 看用法）" >&2; exit 2 ;;
  esac
done

ok() { printf '   [OK]   %s\n' "$*"; }
skip() { printf '   [跳过] %s\n' "$*"; }
info() { printf '          %s\n' "$*"; }

confirm() {
  [[ "${assume_yes}" == "1" ]] && return 0
  local answer=""
  printf '   停止 %s？[y/N] ' "$1"
  read -r answer </dev/tty 2>/dev/null || read -r answer || answer=""
  [[ "${answer}" == "y" || "${answer}" == "Y" ]]
}

alive() { kill -0 "$1" >/dev/null 2>&1; }

stop_pid() {
  local pid="$1"
  if command -v taskkill >/dev/null 2>&1; then
    # Windows: uv/npm wrap the real server in a child process; kill the tree.
    taskkill //PID "${pid}" //T //F >/dev/null 2>&1 && return 0
  fi
  # Elsewhere: the recorded pid is `uv run` / `npm run`, and the server it
  # spawned would outlive it and keep holding the port.
  command -v pkill >/dev/null 2>&1 && pkill -P "${pid}" >/dev/null 2>&1
  kill "${pid}" >/dev/null 2>&1 || return 1
  for _ in $(seq 1 10); do
    alive "${pid}" || return 0
    sleep 1
  done
  kill -9 "${pid}" >/dev/null 2>&1
  ! alive "${pid}"
}

stop_process() {
  local label="$1" file="${STATE_DIR}/$2" pid=""
  if [[ ! -f "${file}" ]]; then
    skip "没有 ${label} 的记录，不动它（若它在跑，那不是本脚本起的）。"
    return
  fi
  pid="$(cat "${file}" 2>/dev/null)"
  if [[ -z "${pid}" ]] || ! alive "${pid}"; then
    skip "${label}（PID ${pid:-空}）已经不在了，清掉记录。"
    rm -f "${file}"
    return
  fi
  if ! confirm "${label}（PID ${pid}）"; then
    skip "${label} 保留。"
    return
  fi
  if stop_pid "${pid}"; then
    rm -f "${file}"
    ok "${label} 已停止。"
  else
    printf '   [失败] 没能停掉 %s（PID %s）。\n' "${label}" "${pid}" >&2
    info "手工处理： Windows  taskkill /PID ${pid} /T /F"
    info "           Linux/macOS  kill -9 ${pid}"
  fi
}

printf 'RepoMesh 交付控制台 · 收摊\n'
printf '状态目录：%s\n\n' "${STATE_DIR}"

stop_process "前端 vite（5280）" frontend.pid
stop_process "后端 uvicorn（8100）" backend.pid

if [[ -f "${STATE_DIR}/postgres.started" ]]; then
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    if confirm "compose 的 postgres 容器（数据卷保留）"; then
      if docker compose stop postgres >/dev/null 2>&1; then
        rm -f "${STATE_DIR}/postgres.started"
        ok "postgres 容器已停止（卷 repomesh-postgres 保留，下次 dev-up 数据还在）。"
        info "要连数据一起删： docker compose down -v"
      else
        printf '   [失败] docker compose stop postgres 没成功。\n' >&2
        info "看一眼： docker compose ps"
      fi
    else
      skip "postgres 保留。"
    fi
  else
    skip "Docker 没在跑，postgres 记录保留，等 Docker 起来再收。"
  fi
else
  skip "没有 postgres 的记录，不动任何数据库容器。"
fi

printf '\n收摊结束。日志留在 %s，重来一次： scripts/dev-up.sh\n' "${STATE_DIR}"
