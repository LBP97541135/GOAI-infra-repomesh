#!/bin/bash
# higress-client.sh - Higress Console API wrapper for integration tests

_HIGRESS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_HIGRESS_LIB_DIR}/test-helpers.sh" 2>/dev/null || true

HIGRESS_COOKIE_FILE="/tmp/higress-test-cookie"

# ============================================================
# Authentication
# ============================================================

# Login to Higress Console
# Usage: higress_login <username> <password>
higress_login() {
    local username="$1"
    local password="$2"

    curl -sf -X POST "${TEST_CONSOLE_URL}/session/login" \
        -H 'Content-Type: application/json' \
        -c "${HIGRESS_COOKIE_FILE}" \
        -d '{"username":"'"${username}"'","password":"'"${password}"'"}'
}

# ============================================================
# Consumer Management
# ============================================================

# List all consumers
# Usage: higress_get_consumers
higress_get_consumers() {
    curl -sf "${TEST_CONSOLE_URL}/v1/consumers" \
        -b "${HIGRESS_COOKIE_FILE}"
}

# Get a specific consumer by name
# Usage: higress_get_consumer <name>
higress_get_consumer() {
    local name="$1"
    curl -sf "${TEST_CONSOLE_URL}/v1/consumers/${name}" \
        -b "${HIGRESS_COOKIE_FILE}"
}

# ============================================================
# Route Management
# ============================================================

# List all routes
# Usage: higress_get_routes
higress_get_routes() {
    curl -sf "${TEST_CONSOLE_URL}/v1/routes" \
        -b "${HIGRESS_COOKIE_FILE}"
}

# Get a specific route by name
# Usage: higress_get_route <name>
higress_get_route() {
    local name="$1"
    curl -sf "${TEST_CONSOLE_URL}/v1/routes/${name}" \
        -b "${HIGRESS_COOKIE_FILE}"
}

# ============================================================
# MCP Server Management
# ============================================================

# Get MCP server consumers
# Usage: higress_get_mcp_consumers <mcp_server_name>
higress_get_mcp_consumers() {
    local mcp_name="$1"
    curl -sf "${TEST_CONSOLE_URL}/v1/mcpServer/consumers?mcpServerName=${mcp_name}" \
        -b "${HIGRESS_COOKIE_FILE}"
}

# ============================================================
# AI Providers
# ============================================================

# List AI providers
# Usage: higress_get_ai_providers
higress_get_ai_providers() {
    curl -sf "${TEST_CONSOLE_URL}/v1/ai/providers" \
        -b "${HIGRESS_COOKIE_FILE}"
}
