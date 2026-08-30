#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"
api_port="${REPOMESH_API_PORT:-8000}"
web_port="${REPOMESH_WEB_PORT:-5280}"
secret_dir="${REPOMESH_SECRETS_DIR:-.secrets}"
controller_container="${REPOMESH_AGENTTEAMS_CONTROLLER_CONTAINER:-agentteams-controller}"
export REPOMESH_SECRETS_DIR="${secret_dir}"

# Windows Git Bash (MSYS) rewrites arguments that look like absolute Unix paths
# into Windows paths before they reach the process: a `docker exec ... cat
# /var/run/agentteams/cli-token` would receive `C:/Git/var/run/...` inside the
# container and fail. That failure is silent here — a failed command
# substitution in an assignment does not trip `set -e` — so the token would be
# minted empty and the API would come up with no Matrix messenger (materialize
# then 503s). Disable the path rewrite; harmless on Linux/macOS where the var is
# ignored.
export MSYS_NO_PATHCONV=1

# Load the whole .env into the shell environment before anything downstream
# reads it. Without this, only the three variables dotenv_value() names below
# ever reach the AgentTeams installer subprocess — AGENTTEAMS_NON_INTERACTIVE=1,
# AGENTTEAMS_VERSION, AGENTTEAMS_MATRIX_APPSERVICE_ENABLED and friends in
# .env.example sit unread, so the "one-command" install falls into the
# installer's interactive prompts instead of running unattended. Load one
# assignment at a time so an explicit caller value always wins over `.env`.
if [[ -f .env ]]; then
  while IFS= read -r dotenv_line || [[ -n "${dotenv_line}" ]]; do
    [[ "${dotenv_line}" =~ ^[[:space:]]*$ || "${dotenv_line}" =~ ^[[:space:]]*# ]] && continue
    [[ "${dotenv_line}" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=.*$ ]] || continue
    dotenv_name="${BASH_REMATCH[1]}"
    if ! declare -p "${dotenv_name}" >/dev/null 2>&1; then
      set -a
      # shellcheck disable=SC1090
      source /dev/stdin <<<"${dotenv_line}"
      set +a
    fi
  done < .env
fi

# One product-level model connection feeds both processes. Component-specific
# variables remain supported as explicit advanced overrides.
dotenv_value() {
  local name="$1"
  local value="${!name:-}"
  if [[ -n "${value}" ]]; then
    printf '%s' "${value}"
  elif [[ -f .env ]]; then
    sed -n "s/^${name}=//p" .env | tail -n 1 | sed -e 's/^\(["'\'' ]\)//' -e 's/\(["'\'' ]\)$//'
  fi
}

export AGENTTEAMS_LLM_API_KEY="${AGENTTEAMS_LLM_API_KEY:-$(dotenv_value REPOMESH_MODEL_API_KEY)}"
export AGENTTEAMS_OPENAI_BASE_URL="${AGENTTEAMS_OPENAI_BASE_URL:-$(dotenv_value REPOMESH_MODEL_BASE_URL)}"
export AGENTTEAMS_DEFAULT_MODEL="${AGENTTEAMS_DEFAULT_MODEL:-$(dotenv_value REPOMESH_MODEL)}"

mkdir -p "${secret_dir}"
credential_key_file="${secret_dir}/platform-credentials.key"
if [[ -z "${REPOMESH_CREDENTIALS_ENCRYPTION_KEY:-}" && ! -f "${credential_key_file}" ]]; then
  openssl rand -base64 32 | tr '+/' '-_' | tr -d '\r\n' > "${credential_key_file}"
  printf '\n' >> "${credential_key_file}"
  chmod 600 "${credential_key_file}" 2>/dev/null || true
fi
platform_secret_file="${secret_dir}/platform.env"
touch "${platform_secret_file}"
ensure_secret() {
  local name="$1"
  local value="${!name:-}"
  if [[ -z "${value}" ]]; then
    value="$(sed -n "s/^${name}=//p" "${platform_secret_file}" | tail -n 1)"
  fi
  if [[ -z "${value}" ]]; then
    value="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\r\n')"
    printf '%s=%s\n' "${name}" "${value}" >> "${platform_secret_file}"
  fi
  export "${name}=${value}"
}
ensure_secret REPOMESH_RUNNER_CONTROL_TOKEN
ensure_secret REPOMESH_AGENT_ACTION_TOKEN
ensure_secret REPOMESH_MCP_GATEWAY_TOKEN
printf '%s\n' "${REPOMESH_AGENT_ACTION_TOKEN}" > "${secret_dir}/browser-action-token"
chmod 600 "${secret_dir}/browser-action-token" 2>/dev/null || true

install_agentteams=0
skip_backend=0
for argument in "$@"; do
  case "${argument}" in
    --install-agentteams) install_agentteams=1 ;;
    --skip-backend) skip_backend=1 ;;
    *) echo "unknown argument: ${argument}" >&2; exit 2 ;;
  esac
done

command -v docker >/dev/null || { echo "Docker is required." >&2; exit 1; }

docker compose up -d postgres

agentteams_ready=0
if docker exec "${controller_container}" curl -sf http://127.0.0.1:8090/healthz >/dev/null 2>&1; then
  agentteams_ready=1
fi
model_configured=0
if [[ -n "${AGENTTEAMS_LLM_API_KEY:-}" ]]; then
  model_configured=1
fi
if [[ "${install_agentteams}" == "1" || ( "${agentteams_ready}" != "1" && "${model_configured}" == "1" ) ]]; then
  if [[ "${agentteams_ready}" != "1" ]]; then
    echo "AgentTeams Controller is missing; installing it automatically."
  fi
  bash components/agentteams/install/agentteams-install.sh
  if docker exec "${controller_container}" curl -sf http://127.0.0.1:8090/healthz >/dev/null 2>&1; then
    agentteams_ready=1
  fi
elif [[ "${agentteams_ready}" != "1" ]]; then
  echo "Model credentials are not configured; starting the setup plane first."
  export REPOMESH_AGENTTEAMS_REQUIRED=false
  unset REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN
  unset REPOMESH_AGENTTEAMS_STORAGE_ENDPOINT
  unset REPOMESH_AGENTTEAMS_STORAGE_ACCESS_KEY
  unset REPOMESH_AGENTTEAMS_STORAGE_SECRET_KEY
  rm -f "${secret_dir}/platform-runtime.env"
fi

if [[ "${model_configured}" == "1" && "${agentteams_ready}" != "1" ]]; then
  echo "AgentTeams Controller is not ready." >&2
  echo "Automatic AgentTeams installation did not produce a healthy controller." >&2
  exit 1
fi

if [[ "${skip_backend}" == "1" ]]; then
  exit 0
fi

if [[ "${agentteams_ready}" == "1" && -z "${REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN:-}" ]]; then
  # `sh -c '...'` keeps the container-internal path out of MSYS's reach as a
  # second guard beyond MSYS_NO_PATHCONV above.
  REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN="$(docker exec "${controller_container}" sh -c 'cat /var/run/agentteams/cli-token' | tr -d '\r\n')"
  export REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN
  if [[ -z "${REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN}" ]]; then
    echo "Failed to read the AgentTeams controller token from agentteams-controller:/var/run/agentteams/cli-token." >&2
    echo "Without it the API cannot reach the controller. Check that the controller is up and the file exists." >&2
    exit 1
  fi
fi

if [[ "${agentteams_ready}" == "1" && -z "${REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN:-}" ]]; then
  agentteams_env="${AGENTTEAMS_ENV_FILE:-${HOME}/agentteams-manager.env}"
  admin_user=""
  admin_password=""
  if [[ -f "${agentteams_env}" ]]; then
    admin_user="$(sed -n 's/^AGENTTEAMS_ADMIN_USER=//p' "${agentteams_env}" | tail -n 1)"
    admin_password="$(sed -n 's/^AGENTTEAMS_ADMIN_PASSWORD=//p' "${agentteams_env}" | tail -n 1)"
  fi
  if [[ -n "${admin_user}" && -n "${admin_password}" ]]; then
    login_payload="$(printf '{"type":"m.login.password","identifier":{"type":"m.id.user","user":"%s"},"password":"%s"}' "${admin_user}" "${admin_password}")"
    REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN="$(docker exec "${controller_container}" curl -sf -X POST http://127.0.0.1:6167/_matrix/client/v3/login -H 'Content-Type: application/json' -d "${login_payload}" | sed -n 's/.*"access_token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
    export REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN
    if [[ -z "${REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN}" ]]; then
      echo "Matrix admin login returned no access_token." >&2
      echo "The API would start with no Matrix messenger, so materialize / task dispatch would 503." >&2
      echo "Check the admin credentials in ${agentteams_env} and that the Matrix homeserver is healthy." >&2
      exit 1
    fi
  else
    # Not fatal: the API can still serve the read model without a messenger. But
    # be loud, because materialize and task dispatch will 503 until this is set.
    echo "WARNING: no AgentTeams admin credentials found (${agentteams_env})." >&2
    echo "WARNING: starting the API without a Matrix messenger — materialize and task" >&2
    echo "WARNING: dispatch will return 503 until REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN is set." >&2
  fi
fi

# Task packages reach the worker through AgentTeams' MinIO (S3): the worker runs
# `mc mirror agentteams/<bucket>/teams/.../shared/tasks/...` to pull them. The API
# must therefore publish through the S3 object publisher, which the bootstrap only
# selects when endpoint + access key + secret key are all set. Left unset, it falls
# back to the disk publisher, whose plain files MinIO's S3 API does not serve — the
# worker's mirror then finds nothing and no task ever reaches an agent. Derive the
# endpoint (reachable on the shared agentteams-net) and MinIO root credentials from
# the manager env, mirroring the Matrix token injection above.
if [[ "${agentteams_ready}" == "1" && ( -z "${REPOMESH_AGENTTEAMS_STORAGE_ACCESS_KEY:-}" || -z "${REPOMESH_AGENTTEAMS_STORAGE_SECRET_KEY:-}" ) ]]; then
  agentteams_env="${AGENTTEAMS_ENV_FILE:-${HOME}/agentteams-manager.env}"
  minio_user=""
  minio_password=""
  if [[ -f "${agentteams_env}" ]]; then
    minio_user="$(sed -n 's/^AGENTTEAMS_MINIO_USER=//p' "${agentteams_env}" | tail -n 1)"
    minio_password="$(sed -n 's/^AGENTTEAMS_MINIO_PASSWORD=//p' "${agentteams_env}" | tail -n 1)"
  fi
  if [[ -n "${minio_user}" && -n "${minio_password}" ]]; then
    export REPOMESH_AGENTTEAMS_STORAGE_ENDPOINT="${REPOMESH_AGENTTEAMS_STORAGE_ENDPOINT:-http://agentteams-controller:9000}"
    export REPOMESH_AGENTTEAMS_STORAGE_ACCESS_KEY="${minio_user}"
    export REPOMESH_AGENTTEAMS_STORAGE_SECRET_KEY="${minio_password}"
  else
    # Not fatal: the API still serves the read model. But task dispatch will not
    # reach any worker, because the disk publisher's files are invisible over S3.
    echo "WARNING: no AgentTeams MinIO credentials found (${agentteams_env})." >&2
    echo "WARNING: the API will fall back to the disk task publisher, whose files the" >&2
    echo "WARNING: worker's S3 mirror cannot read — dispatched tasks never reach workers." >&2
  fi
fi

agentteams_env="${AGENTTEAMS_ENV_FILE:-${HOME}/agentteams-manager.env}"
if [[ "${agentteams_ready}" == "1" && -f "${agentteams_env}" ]]; then
  cp "${agentteams_env}" "${secret_dir}/agentteams-manager.env"
  chmod 600 "${secret_dir}/agentteams-manager.env" 2>/dev/null || true
fi
if [[ "${agentteams_ready}" == "1" && -n "${REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN:-}" && -n "${REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN:-}" && -n "${REPOMESH_AGENTTEAMS_STORAGE_ACCESS_KEY:-}" && -n "${REPOMESH_AGENTTEAMS_STORAGE_SECRET_KEY:-}" ]]; then
  runtime_tmp="${secret_dir}/platform-runtime.env.tmp"
  cat >"${runtime_tmp}" <<EOF
REPOMESH_AGENTTEAMS_REQUIRED=true
REPOMESH_AGENTTEAMS_CONTROLLER_URL=http://agentteams-controller:8090
REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN=${REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN}
REPOMESH_AGENTTEAMS_MATRIX_URL=http://agentteams-controller:6167
REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN=${REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN}
REPOMESH_AGENTTEAMS_STORAGE_ENDPOINT=http://agentteams-controller:9000
REPOMESH_AGENTTEAMS_STORAGE_ACCESS_KEY=${REPOMESH_AGENTTEAMS_STORAGE_ACCESS_KEY}
REPOMESH_AGENTTEAMS_STORAGE_SECRET_KEY=${REPOMESH_AGENTTEAMS_STORAGE_SECRET_KEY}
REPOMESH_AGENTTEAMS_STORAGE_BUCKET=agentteams-storage
EOF
  chmod 600 "${runtime_tmp}" 2>/dev/null || true
  mv -f "${runtime_tmp}" "${secret_dir}/platform-runtime.env"
fi

docker compose --profile platform up -d --build api web bootstrap

ready=0
api_container="$(docker compose --profile platform ps -q api)"
for _ in $(seq 1 30); do
  if [[ -n "${api_container}" && "$(docker inspect --format '{{.State.Health.Status}}' "${api_container}" 2>/dev/null)" == "healthy" ]]; then
    ready=1
    break
  fi
  sleep 2
done

if [[ "${ready}" != "1" ]]; then
  docker compose --profile platform logs --tail 100 api
  echo "RepoMesh API did not become ready at http://127.0.0.1:${api_port}." >&2
  exit 1
fi

web_ready=0
web_container="$(docker compose --profile platform ps -q web)"
for _ in $(seq 1 30); do
  if [[ -n "${web_container}" && "$(docker inspect --format '{{.State.Health.Status}}' "${web_container}" 2>/dev/null)" == "healthy" ]]; then
    web_ready=1
    break
  fi
  sleep 2
done
if [[ "${web_ready}" != "1" ]]; then
  docker compose --profile platform logs --tail 100 web
  echo "RepoMesh console did not become ready at http://127.0.0.1:${web_port}." >&2
  exit 1
fi

bootstrap_ready=0
bootstrap_container="$(docker compose --profile platform ps -q bootstrap)"
for _ in $(seq 1 30); do
  if [[ -n "${bootstrap_container}" && "$(docker inspect --format '{{.State.Health.Status}}' "${bootstrap_container}" 2>/dev/null)" == "healthy" ]]; then
    bootstrap_ready=1
    break
  fi
  sleep 2
done
if [[ "${bootstrap_ready}" != "1" ]]; then
  docker compose --profile platform logs --tail 100 bootstrap
  echo "RepoMesh bootstrap reconciler did not become ready." >&2
  exit 1
fi

echo "RepoMesh is ready at http://127.0.0.1:${api_port}/docs"
echo "RepoMesh console is ready at http://127.0.0.1:${web_port}"
