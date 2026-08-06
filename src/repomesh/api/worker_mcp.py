import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from repomesh.modules.agent_runtime.contracts import StartAssignedWorkerTaskCommand
from repomesh.settings import get_settings

router = APIRouter(tags=["worker-mcp"])

TOOL_NAME = "start_assigned_task"


@router.post("/api/v1/mcp/worker")
async def worker_mcp(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    _authorize(request)
    request_id = body.get("id")
    method = body.get("method")
    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "repomesh-task-control", "version": "0.1.0"},
            },
        )
    if method == "notifications/initialized":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": [_tool_definition()]})
    if method != "tools/call":
        return _error(request_id, -32601, "method not found")
    params = body.get("params")
    if not isinstance(params, dict) or params.get("name") != TOOL_NAME:
        return _error(request_id, -32602, "unknown tool")
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        return _error(request_id, -32602, "tool arguments must be an object")
    try:
        started = await request.app.state.container.worker_execution_service().execute(
            StartAssignedWorkerTaskCommand(
                task_id=UUID(str(arguments["task_id"])),
                worker_agent_id=UUID(str(arguments["worker_agent_id"])),
                adapter_id=str(arguments.get("adapter_id") or "claude-code"),
                base_revision=str(arguments.get("base_revision") or "main"),
                task_features=frozenset(arguments.get("task_features") or ()),
            )
        )
    except (KeyError, TypeError, ValueError) as error:
        return _result(
            request_id,
            {"content": [{"type": "text", "text": str(error)}], "isError": True},
        )
    workspace = started.task.workspace
    payload = {
        "task_id": str(started.task.task_id),
        "run_id": str(started.task.run_id),
        "status": started.status.value,
        "workspace_id": workspace.workspace_id if workspace else None,
        "base_sha": workspace.base_sha if workspace else None,
    }
    return _result(
        request_id,
        {
            "content": [{"type": "text", "text": json.dumps(payload)}],
            "structuredContent": payload,
        },
    )


def _authorize(request: Request) -> None:
    settings = get_settings()
    if settings.direct_worker_mcp_enabled:
        if settings.environment not in {"development", "test"}:
            raise HTTPException(
                status_code=503,
                detail="direct Worker MCP is forbidden outside development and test",
            )
        return
    action_token_valid = bool(
        settings.agent_action_token
        and request.headers.get("Authorization")
        == f"Bearer {settings.agent_action_token}"
    )
    gateway_token_valid = bool(
        settings.mcp_gateway_token
        and (
            request.headers.get("X-RepoMesh-Gateway-Token") == settings.mcp_gateway_token
            or request.headers.get("Authorization")
            == f"Bearer {settings.mcp_gateway_token}"
        )
    )
    if not settings.agent_action_token and not settings.mcp_gateway_token:
        raise HTTPException(status_code=503, detail="MCP authentication is not configured")
    if not action_token_valid and not gateway_token_valid:
        raise HTTPException(status_code=401, detail="invalid MCP credentials")


def _tool_definition() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": "Start the current RepoMesh task assigned to this Worker.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "format": "uuid"},
                "worker_agent_id": {"type": "string", "format": "uuid"},
                "adapter_id": {"type": "string", "default": "claude-code"},
                "base_revision": {"type": "string", "default": "main"},
                "task_features": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["task_id", "worker_agent_id"],
            "additionalProperties": False,
        },
    }


def _result(request_id: object, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
