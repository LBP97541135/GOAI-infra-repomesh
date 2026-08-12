#!/usr/bin/env bash
# One-command development launcher for the delivery console (batch S-1/S-3/S-4).
#
# Stages, in order: compose postgres -> dependencies -> alembic -> API on 8100
# -> Vite on 5280 -> browser. Every stage probes first and skips whatever is
# already serving, so re-running the script is safe and cheap.
#
# Safety rules this script keeps, on purpose:
#   * it never restarts, migrates into, or stops anything it did not start;
#   * `alembic upgrade head` only runs against the database this script brought
#     up itself, or against a DSN the operator passed explicitly;
#   * a busy host port that is not ours aborts the run with instructions
#     instead of being adopted.
#
# Ports 8100 (API) and 5280 (Vite) are fixed by frontend/vite.config.ts and are
# not configurable here. The postgres host port is: REPOMESH_POSTGRES_PORT.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}" || exit 1

STATE_DIR="${REPO_ROOT}/.repomesh-dev"
API_PORT=8100
WEB_PORT=5280
DB_PORT="${REPOMESH_POSTGRES_PORT:-5432}"
ACTION_TOKEN="${REPOMESH_AGENT_ACTION_TOKEN:-console-dev-token}"
CONSOLE_URL="http://127.0.0.1:${WEB_PORT}"

if [[ -n "${REPOMESH_DATABASE_URL:-}" ]]; then
  DSN="${REPOMESH_DATABASE_URL}"
  DSN_EXPLICIT=1
else
  DSN="postgresql+asyncpg://repomesh:repomesh@127.0.0.1:${DB_PORT}/repomesh"
  DSN_EXPLICIT=0
fi

seed=0
open_browser=1
own_database=0
backend_skipped=0
warnings=()

usage() {
  cat <<'USAGE'
用法： scripts/dev-up.sh [选项]

  --seed         起好之后灌演示种子（scripts/seed-console-demo.py），首屏不是空态
  --no-browser   起好之后不自动打开浏览器
  -h, --help     显示本帮助

环境变量：
  REPOMESH_POSTGRES_PORT      compose postgres 映射到宿主的端口（默认 5432）
  REPOMESH_DATABASE_URL       复用你自己的数据库；设了它就不起 compose postgres，
                              迁移会写进这个库（请确认它属于本项目）
  REPOMESH_AGENT_ACTION_TOKEN 读模型接口的 Bearer token（默认 console-dev-token，
                              必须与 frontend/.env.development 的 VITE_API_TOKEN 一致）
USAGE
}

for argument in "$@"; do
  case "${argument}" in
    --seed) seed=1 ;;
    --no-browser) open_browser=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：${argument}（用 --help 看用法）" >&2; exit 2 ;;
  esac
done

step() { printf '\n== %s\n' "$*"; }
ok() { printf '   [OK]   %s\n' "$*"; }
skip() { printf '   [跳过] %s\n' "$*"; }
info() { printf '          %s\n' "$*"; }
warn() { printf '   [注意] %s\n' "$*"; warnings+=("$*"); }

fail() {
  printf '\n   [失败] %s\n' "$1" >&2
  shift
  for line in "$@"; do printf '          %s\n' "${line}" >&2; done
  printf '\n启动中止。已经起来的组件没有被回收，修好上面的问题后重跑本脚本即可（幂等）。\n' >&2
  exit 1
}

have() { command -v "$1" >/dev/null 2>&1; }

# True when something is listening on the given local TCP port.
port_busy() {
  (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1
}

# Same question, asked three times: a component that is up but momentarily
# reloading would otherwise be mistaken for a stranger holding its port, and
# that mistake aborts the run.
http_serving_settled() {
  local attempt
  for attempt in 1 2 3; do
    http_serving "$1" && return 0
    sleep 1
  done
  return 1
}

# True when the URL answers with any HTTP status (404 still proves "serving").
http_serving() {
  local code
  code="$(curl --silent --show-error --output /dev/null --max-time 3 --write-out '%{http_code}' "$1" 2>/dev/null)"
  [[ -n "${code}" && "${code}" != "000" ]]
}

open_url() {
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) cmd //c start "" "$1" >/dev/null 2>&1 ;;
    Darwin) open "$1" >/dev/null 2>&1 ;;
    *) have xdg-open && xdg-open "$1" >/dev/null 2>&1 ;;
  esac
}

mkdir -p "${STATE_DIR}"

printf 'RepoMesh 交付控制台 · 一键开发环境\n'
printf '仓库根：%s\n' "${REPO_ROOT}"
printf '数据库：%s%s\n' "${DSN}" "$([[ ${DSN_EXPLICIT} == 1 ]] && echo '（来自 REPOMESH_DATABASE_URL）')"

have curl || fail "找不到 curl。" "本脚本用 curl 做就绪探测。Git Bash / macOS / 主流 Linux 都自带；请先安装 curl 再重跑。"

# ---------------------------------------------------------------- 1. 后端
step "[1/4] 后端 API（127.0.0.1:${API_PORT}）"

if http_serving_settled "http://127.0.0.1:${API_PORT}/docs"; then
  backend_skipped=1
  skip "8100 已经在提供服务，跳过数据库、依赖安装、迁移与 uvicorn 启动。"
  info "（这一跳过是有意的：既有实例的库和迁移状态由起它的人负责。"
  info "  若你刚改了迁移，请先 scripts/dev-down.sh 再重跑本脚本。）"
elif port_busy "${API_PORT}"; then
  fail "${API_PORT} 端口被占用，但它不是 RepoMesh API（/docs 打不开）。" \
    "前端的开发代理写死打 127.0.0.1:${API_PORT}（frontend/vite.config.ts），这个端口不能换。" \
    "请先停掉占用 ${API_PORT} 的进程；Windows 上可用：netstat -ano | findstr :${API_PORT}"
else
  # 1a. Postgres
  if [[ "${DSN_EXPLICIT}" == "1" ]]; then
    skip "已显式指定 REPOMESH_DATABASE_URL，不起 compose postgres。"
    info "迁移与后端都会连这个库，请确认它属于本项目。"
  else
    have docker || fail "找不到 docker。" \
      "需要 Docker Desktop（Windows/macOS）或 docker engine（Linux）来起 Postgres。" \
      "也可以自带数据库：REPOMESH_DATABASE_URL=postgresql+asyncpg://user:pw@host:port/db scripts/dev-up.sh"
    docker info >/dev/null 2>&1 || fail "Docker 装了但没跑起来。" \
      "请先启动 Docker Desktop（或 systemctl start docker），等它完全就绪后重跑本脚本。"

    if docker compose ps --services --status running 2>/dev/null | grep -qx postgres; then
      own_database=1
      skip "compose 的 postgres 容器已在运行。"
    elif port_busy "${DB_PORT}"; then
      fail "宿主 ${DB_PORT} 端口已被占用，但占用它的不是本项目 compose 的 postgres 容器。" \
        "脚本不会对不是自己起的数据库执行迁移——谱系不符的库会被 alembic 改坏。" \
        "三选一：" \
        "  1) 给本项目的库换端口： REPOMESH_POSTGRES_PORT=5433 scripts/dev-up.sh" \
        "  2) 复用你已有的库（确认它属于本项目，脚本会对它 alembic upgrade head）：" \
        "     REPOMESH_DATABASE_URL=postgresql+asyncpg://repomesh:repomesh@127.0.0.1:${DB_PORT}/repomesh scripts/dev-up.sh" \
        "  3) 停掉占用 ${DB_PORT} 的服务后重跑"
    else
      info "起 compose postgres（宿主端口 ${DB_PORT}）…"
      REPOMESH_POSTGRES_PORT="${DB_PORT}" docker compose up -d postgres >/dev/null 2>&1 \
        || fail "docker compose up -d postgres 失败。" \
          "看一眼原因： docker compose up postgres" \
          "常见原因：宿主端口 ${DB_PORT} 被占（换 REPOMESH_POSTGRES_PORT）、镜像拉不下来。"
      own_database=1
      : >"${STATE_DIR}/postgres.started"
      ok "postgres 容器已启动。"
    fi

    if [[ "${own_database}" == "1" ]]; then
      info "等数据库接受连接…"
      ready=0
      for _ in $(seq 1 30); do
        if docker compose exec -T postgres pg_isready -U repomesh -d repomesh >/dev/null 2>&1; then
          ready=1
          break
        fi
        sleep 2
      done
      [[ "${ready}" == "1" ]] || fail "60 秒内数据库没有就绪。" \
        "看容器日志： docker compose logs --tail 50 postgres" \
        "若卷里是旧的、口令不同的数据目录，可以整个项目删掉重来（会清数据）： docker compose down -v"
      ok "数据库已就绪。"
    fi
  fi

  # 1b. 依赖
  have uv || fail "找不到 uv。" \
    "安装： https://docs.astral.sh/uv/getting-started/installation/" \
    "Windows： powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\"" \
    "或者走全 Docker 方案，宿主只需要 Docker： docker compose --profile console up -d"
  info "同步 Python 依赖（uv sync --extra dev）…"
  uv sync --extra dev || fail "uv sync --extra dev 失败。" \
    "多半是网络或 Python 版本问题；把上面的报错原文贴出来定位。"
  ok "Python 依赖就绪。"

  # 1c. 迁移
  info "执行数据库迁移（alembic upgrade head → ${DSN}）…"
  REPOMESH_DATABASE_URL="${DSN}" uv run alembic upgrade head || fail "alembic upgrade head 失败。" \
    "常见原因：" \
    "  * DSN 指向的库不是本项目的库（迁移谱系不符）——换一个空库再试；" \
    "  * 库还没起来或口令不对——用 psql 手工连一次 ${DSN} 验证；" \
    "  * 本地有没提交的迁移冲突——uv run alembic heads 看是不是多头。" \
    "脚本不会自动重试，也不会绕过失败继续起服务。"
  ok "迁移已到 head。"

  # 1d. uvicorn
  info "启动 API（uvicorn，127.0.0.1:${API_PORT}）…"
  REPOMESH_DATABASE_URL="${DSN}" REPOMESH_AGENT_ACTION_TOKEN="${ACTION_TOKEN}" \
    nohup uv run uvicorn repomesh.main:app --host 127.0.0.1 --port "${API_PORT}" \
    >"${STATE_DIR}/backend.log" 2>&1 &
  echo $! >"${STATE_DIR}/backend.pid"

  serving=0
  for _ in $(seq 1 40); do
    if http_serving "http://127.0.0.1:${API_PORT}/docs"; then
      serving=1
      break
    fi
    sleep 2
  done
  if [[ "${serving}" != "1" ]]; then
    tail -n 30 "${STATE_DIR}/backend.log" >&2 2>/dev/null
    rm -f "${STATE_DIR}/backend.pid"
    fail "80 秒内 API 没有在 ${API_PORT} 上提供服务（判据是 /docs 可达，不是 /health/ready）。" \
      "完整日志： ${STATE_DIR}/backend.log" \
      "常见原因：端口被抢、DSN 连不上、依赖装了一半。"
  fi
  ok "API 已就绪： http://127.0.0.1:${API_PORT}/docs"
fi

# ---------------------------------------------------------------- 2. 种子
step "[2/4] 演示数据"
if [[ "${seed}" != "1" ]]; then
  skip "未加 --seed，不灌演示数据（新库首屏会是空态，属正常）。"
elif [[ "${own_database}" != "1" && "${DSN_EXPLICIT}" != "1" ]]; then
  if [[ "${backend_skipped}" == "1" ]]; then
    warn "跳过灌种子：后端是既有实例，脚本无从确认它连的是哪个库，不敢往默认 DSN 写。"
  else
    warn "跳过灌种子：本次运行没有起数据库，脚本不往不是自己起的库写数据。"
  fi
  info "确认之后显式指定同一个库再灌："
  info "  REPOMESH_DATABASE_URL=<后端在用的 DSN> uv run python scripts/seed-console-demo.py --database-url <同一个 DSN>"
else
  info "灌演示种子（目标库 ${DSN}）…"
  if uv run python scripts/seed-console-demo.py --database-url "${DSN}"; then
    ok "演示数据已写入。"
  else
    warn "种子脚本失败——控制台仍可用，只是首屏是空态。"
    info "半写状态无法靠重跑修复（脚本自己会报 SeedIncomplete）：换一个空库重来最省事。"
  fi
fi

# ---------------------------------------------------------------- 3. 前端
step "[3/4] 前端开发服务器（127.0.0.1:${WEB_PORT}）"
if http_serving_settled "${CONSOLE_URL}/"; then
  skip "${WEB_PORT} 已经在提供服务，跳过 npm install 与 vite 启动。"
elif port_busy "${WEB_PORT}"; then
  fail "${WEB_PORT} 端口被占用，但打不开页面。" \
    "vite 配了 strictPort，端口不能自动顺延，也不能换（README 里的地址写死 ${WEB_PORT}）。" \
    "请先停掉占用它的进程： netstat -ano | findstr :${WEB_PORT}"
else
  have npm || fail "找不到 npm。" \
    "装 Node.js 20+： https://nodejs.org/" \
    "或者走全 Docker 方案： docker compose --profile console up -d"
  if [[ -d "${REPO_ROOT}/frontend/node_modules" ]]; then
    skip "frontend/node_modules 已存在，跳过 npm install（要更新依赖请手工 npm install）。"
  else
    info "安装前端依赖（npm install）…"
    (cd "${REPO_ROOT}/frontend" && npm install) || fail "npm install 失败。" \
      "把上面的报错原文贴出来定位；国内网络常见是 registry 超时。"
    ok "前端依赖就绪。"
  fi

  info "启动 vite…"
  (cd "${REPO_ROOT}/frontend" && nohup npm run dev >"${STATE_DIR}/frontend.log" 2>&1 & echo $! >"${STATE_DIR}/frontend.pid")

  serving=0
  for _ in $(seq 1 30); do
    if http_serving "${CONSOLE_URL}/"; then
      serving=1
      break
    fi
    sleep 2
  done
  if [[ "${serving}" != "1" ]]; then
    tail -n 30 "${STATE_DIR}/frontend.log" >&2 2>/dev/null
    rm -f "${STATE_DIR}/frontend.pid"
    fail "60 秒内前端没有在 ${WEB_PORT} 上提供服务。" \
      "完整日志： ${STATE_DIR}/frontend.log"
  fi
  ok "前端已就绪。"
fi

# ---------------------------------------------------------------- 4. 打开
step "[4/4] 打开控制台"
if [[ "${open_browser}" == "1" ]]; then
  open_url "${CONSOLE_URL}" && ok "已尝试用默认浏览器打开 ${CONSOLE_URL}" || warn "没能自动打开浏览器，手工访问下面的地址即可。"
else
  skip "加了 --no-browser，不自动打开。"
fi

cat <<SUMMARY

------------------------------------------------------------------
控制台： ${CONSOLE_URL}
接口文档： http://127.0.0.1:${API_PORT}/docs

首次使用：在登录页点「首次部署？初始化管理员」创建本地管理员账号
          （POST /auth/bootstrap 只认第一次；之后一律走登录）。
数据源：  默认就是真实数据（打 8100 后端）；演示夹具要显式加 ?source=replay。

日志： ${STATE_DIR}/backend.log · ${STATE_DIR}/frontend.log
收摊： scripts/dev-down.sh（只停本脚本起的组件，且逐项问你）
------------------------------------------------------------------
SUMMARY

if [[ "${#warnings[@]}" -gt 0 ]]; then
  printf '\n有 %d 条注意事项：\n' "${#warnings[@]}"
  for line in "${warnings[@]}"; do printf '  * %s\n' "${line}"; done
fi
