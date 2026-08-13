#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

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
  REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN="$(docker exec agentteams-controller cat /var/run/agentteams/cli-token)"
  export REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN
fi

if [[ -z "${REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN:-}" ]]; then
  agentteams_env="${AGENTTEAMS_ENV_FILE:-${HOME}/agentteams-manager.env}"
  if [[ -f "${agentteams_env}" ]]; then
    admin_user="$(sed -n 's/^AGENTTEAMS_ADMIN_USER=//p' "${agentteams_env}" | tail -n 1)"
    admin_password="$(sed -n 's/^AGENTTEAMS_ADMIN_PASSWORD=//p' "${agentteams_env}" | tail -n 1)"
    if [[ -n "${admin_user}" && -n "${admin_password}" ]]; then
      login_payload="$(printf '{"type":"m.login.password","identifier":{"type":"m.id.user","user":"%s"},"password":"%s"}' "${admin_user}" "${admin_password}")"
      REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN="$(docker exec agentteams-controller curl -sf -X POST http://127.0.0.1:6167/_matrix/client/v3/login -H 'Content-Type: application/json' -d "${login_payload}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("access_token", ""))')"
      export REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN
    fi
  fi
fi

docker compose --profile platform up -d --build api web

ready=0
for _ in $(seq 1 30); do
  if curl --fail --silent --max-time 3 http://127.0.0.1:5173/health/ready >/dev/null; then
    ready=1
    break
  fi
  sleep 2
done

if [[ "${ready}" != "1" ]]; then
  docker compose --profile platform logs --tail 100 api web
  echo "RepoMesh did not become ready at http://127.0.0.1:5173." >&2
  exit 1
fi

echo "RepoMesh is ready at http://127.0.0.1:5173"
echo "API documentation is available at http://127.0.0.1:5173/docs"
