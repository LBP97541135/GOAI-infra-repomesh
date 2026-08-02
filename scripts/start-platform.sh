#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

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
