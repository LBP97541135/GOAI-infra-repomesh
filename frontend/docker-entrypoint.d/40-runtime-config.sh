#!/bin/sh
set -eu

token="${REPOMESH_BROWSER_API_TOKEN:-}"
if [ -z "${token}" ] && [ -f "${REPOMESH_BROWSER_API_TOKEN_FILE:-}" ]; then
  token="$(tr -d '\r\n' < "${REPOMESH_BROWSER_API_TOKEN_FILE}")"
fi
encoded_token="$(printf '%s' "${token}" | base64 | tr -d '\r\n')"
printf 'window.__REPOMESH_CONFIG__ = { apiToken: atob("%s") };\n' "${encoded_token}" \
  > /usr/share/nginx/html/runtime-config.js
