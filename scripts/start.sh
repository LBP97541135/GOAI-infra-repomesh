#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"
secret_dir="${REPOMESH_SECRETS_DIR:-.secrets}"
mkdir -p "${secret_dir}"
export REPOMESH_SECRETS_DIR="${secret_dir}"
startup_env="${secret_dir}/startup.env"
if [[ -f "${startup_env}" ]]; then
  while IFS= read -r line || [[ -n "${line}" ]]; do
    [[ "${line}" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
    name="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    if [[ -z "${!name:-}" ]]; then
      export "${name}=${value}"
    fi
  done < "${startup_env}"
fi

port_available() {
  local port="$1"
  ! (echo >/dev/tcp/127.0.0.1/"${port}") >/dev/null 2>&1
}

select_port() {
  local first="$1"
  local last="$2"
  local port
  for port in $(seq "${first}" "${last}"); do
    if port_available "${port}"; then
      printf '%s' "${port}"
      return 0
    fi
  done
  echo "No available port in range ${first}-${last}." >&2
  return 1
}

command -v docker >/dev/null || {
  echo "Docker is required. Install and start Docker, then rerun this launcher." >&2
  exit 1
}
docker info >/dev/null 2>&1 || {
  echo "Docker is installed but not running. Start it, then rerun this launcher." >&2
  exit 1
}

export REPOMESH_POSTGRES_PORT="${REPOMESH_POSTGRES_PORT:-$(select_port 5432 5442)}"
export REPOMESH_API_PORT="${REPOMESH_API_PORT:-$(select_port 8000 8010)}"
export REPOMESH_WEB_PORT="${REPOMESH_WEB_PORT:-$(select_port 5280 5290)}"
cat >"${startup_env}" <<EOF
REPOMESH_POSTGRES_PORT=${REPOMESH_POSTGRES_PORT}
REPOMESH_API_PORT=${REPOMESH_API_PORT}
REPOMESH_WEB_PORT=${REPOMESH_WEB_PORT}
EOF

"${repo_root}/scripts/start-platform.sh"

console_url="http://127.0.0.1:${REPOMESH_WEB_PORT}"
if command -v open >/dev/null; then
  open "${console_url}"
elif command -v xdg-open >/dev/null; then
  xdg-open "${console_url}" >/dev/null 2>&1 || true
fi
echo "Open RepoMesh: ${console_url}"
