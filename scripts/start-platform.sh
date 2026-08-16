#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

# Windows Git Bash (MSYS) rewrites arguments that look like absolute Unix paths
# into Windows paths before they reach the process: a `docker exec ... cat
# /var/run/agentteams/cli-token` would receive `C:/Git/var/run/...` inside the
# container and fail. That failure is silent here — a failed command
# substitution in an assignment does not trip `set -e` — so the token would be
# minted empty and the API would come up with no Matrix messenger (materialize
# then 503s). Disable the path rewrite; harmless on Linux/macOS where the var is
# ignored.
export MSYS_NO_PATHCONV=1

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

mkdir -p .secrets
platform_secret_file=".secrets/platform.env"
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
command -v curl >/dev/null || { echo "curl is required." >&2; exit 1; }

docker compose up -d postgres

if [[ "${install_agentteams}" == "1" ]]; then
  bash components/agentteams/install/agentteams-install.sh
fi

if ! docker exec agentteams-controller curl -sf http://127.0.0.1:8090/healthz >/dev/null; then
  echo "AgentTeams Controller is not ready." >&2
  echo "Run this script with --install-agentteams." >&2
  exit 1
fi

if [[ "${skip_backend}" == "1" ]]; then
  exit 0
fi

if [[ -z "${REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN:-}" ]]; then
  # `sh -c '...'` keeps the container-internal path out of MSYS's reach as a
  # second guard beyond MSYS_NO_PATHCONV above.
  REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN="$(docker exec agentteams-controller sh -c 'cat /var/run/agentteams/cli-token' | tr -d '\r\n')"
  export REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN
  if [[ -z "${REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN}" ]]; then
    echo "Failed to read the AgentTeams controller token from agentteams-controller:/var/run/agentteams/cli-token." >&2
    echo "Without it the API cannot reach the controller. Check that the controller is up and the file exists." >&2
    exit 1
  fi
fi

if [[ -z "${REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN:-}" ]]; then
  agentteams_env="${AGENTTEAMS_ENV_FILE:-${HOME}/agentteams-manager.env}"
  admin_user=""
  admin_password=""
  if [[ -f "${agentteams_env}" ]]; then
    admin_user="$(sed -n 's/^AGENTTEAMS_ADMIN_USER=//p' "${agentteams_env}" | tail -n 1)"
    admin_password="$(sed -n 's/^AGENTTEAMS_ADMIN_PASSWORD=//p' "${agentteams_env}" | tail -n 1)"
  fi
  if [[ -n "${admin_user}" && -n "${admin_password}" ]]; then
    login_payload="$(printf '{"type":"m.login.password","identifier":{"type":"m.id.user","user":"%s"},"password":"%s"}' "${admin_user}" "${admin_password}")"
    REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN="$(docker exec agentteams-controller curl -sf -X POST http://127.0.0.1:6167/_matrix/client/v3/login -H 'Content-Type: application/json' -d "${login_payload}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("access_token", ""))')"
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
if [[ -z "${REPOMESH_AGENTTEAMS_STORAGE_ACCESS_KEY:-}" || -z "${REPOMESH_AGENTTEAMS_STORAGE_SECRET_KEY:-}" ]]; then
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

docker compose --profile platform up -d --build api

ready=0
for _ in $(seq 1 30); do
  if curl --fail --silent --max-time 3 http://127.0.0.1:8000/health/ready >/dev/null; then
    ready=1
    break
  fi
  sleep 2
done

if [[ "${ready}" != "1" ]]; then
  docker compose --profile platform logs --tail 100 api
  echo "RepoMesh API did not become ready at http://127.0.0.1:8000." >&2
  exit 1
fi

echo "RepoMesh is ready at http://127.0.0.1:8000/docs"
