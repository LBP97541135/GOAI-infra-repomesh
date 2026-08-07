import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from repomesh.integrations.agentteams.governed_assignment import (
    CreateGovernedWorkerTaskCommand,
)
from repomesh.modules.agent_runtime.contracts import (
    AssessAssignedWorkerTaskCommand,
    StartAssignedWorkerTaskCommand,
    WorkerPreflightDecision,
)
from repomesh.settings import get_settings

router = APIRouter(tags=["worker-mcp"])

START_TOOL_NAME = "start_assigned_task"
ASSESS_TOOL_NAME = "assess_assigned_task"
PREPARE_WORKER_TASK_TOOL_NAME = "prepare_governed_worker_task"


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
        return _result(request_id, {"tools": _tool_definitions()})
    if method != "tools/call":
        return _error(request_id, -32601, "method not found")
    params = body.get("params")
    if not isinstance(params, dict) or params.get("name") not in {
        START_TOOL_NAME,
        ASSESS_TOOL_NAME,
        PREPARE_WORKER_TASK_TOOL_NAME,
    }:
        return _error(request_id, -32602, "unknown tool")
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        return _error(request_id, -32602, "tool arguments must be an object")
    if params.get("name") == ASSESS_TOOL_NAME:
        return await _assess(request, request_id, arguments)
    if params.get("name") == PREPARE_WORKER_TASK_TOOL_NAME:
        return await _prepare_worker_task(request, request_id, arguments)
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


async def _assess(
    request: Request, request_id: object, arguments: dict[str, Any]
) -> dict[str, Any]:
    try:
        assessment = await request.app.state.container.worker_preflight_service().execute(
            AssessAssignedWorkerTaskCommand(
                task_id=UUID(str(arguments["task_id"])),
                worker_agent_id=UUID(str(arguments["worker_agent_id"])),
                decision=WorkerPreflightDecision(str(arguments["decision"])),
                spec_understood=bool(arguments["spec_understood"]),
                scope_sufficient=bool(arguments["scope_sufficient"]),
                tests_defined=bool(arguments["tests_defined"]),
                dependencies_ready=bool(arguments["dependencies_ready"]),
                notes=str(arguments["notes"]),
            )
        )
    except (KeyError, TypeError, ValueError) as error:
        return _result(
            request_id,
            {"content": [{"type": "text", "text": str(error)}], "isError": True},
        )
    payload = {
        "task_id": str(assessment.task_id),
        "worker_agent_id": str(assessment.worker_agent_id),
        "decision": assessment.decision.value,
        "revision": assessment.revision,
        "notes": assessment.notes,
    }
    return _result(
        request_id,
        {
            "content": [{"type": "text", "text": json.dumps(payload)}],
            "structuredContent": payload,
        },
    )


async def _prepare_worker_task(
    request: Request, request_id: object, arguments: dict[str, Any]
) -> dict[str, Any]:
    try:
        result = await request.app.state.container.governed_worker_task_service().execute(
            CreateGovernedWorkerTaskCommand(
                organization_id=UUID(str(arguments["organization_id"])),
                project_id=UUID(str(arguments["project_id"])),
                repository_id=UUID(str(arguments["repository_id"])),
                parent_task_id=UUID(str(arguments["parent_task_id"])),
                leader_agent_id=UUID(str(arguments["leader_agent_id"])),
                worker_agent_id=UUID(str(arguments["worker_agent_id"])),
                title=str(arguments["title"]),
                goal=str(arguments["goal"]),
                acceptance=tuple(str(item) for item in arguments["acceptance"]),
                constraints=tuple(str(item) for item in arguments.get("constraints") or ()),
                tests=tuple(str(item) for item in arguments.get("tests") or ()),
                dependencies=tuple(
                    str(item) for item in arguments.get("dependencies") or ()
                ),
                allowed_paths=tuple(
                    str(item) for item in arguments.get("allowed_paths") or ()
                ),
                interface_changes=tuple(
                    str(item) for item in arguments.get("interface_changes") or ()
                ),
            ),
            idempotency_key=str(arguments["idempotency_key"]),
        )
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        return _result(
            request_id,
            {"content": [{"type": "text", "text": str(error)}], "isError": True},
        )
    payload = {
        "task_id": str(result.task.id),
        "specification_id": str(result.specification_id),
        "worker_agent_id": str(result.task.assignee_agent_id),
        "execution_mode": result.task.execution_mode.value,
        "status": result.task.status.value,
    }
    return _result(
        request_id,
        {
            "content": [{"type": "text", "text": json.dumps(payload)}],
            "structuredContent": payload,
        },
    )


def _tool_definitions() -> list[dict[str, Any]]:
    return [{
        "name": START_TOOL_NAME,
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
    }, {
        "name": PREPARE_WORKER_TASK_TOOL_NAME,
        "description": (
            "Repository Leader only: create and freeze a Task Specification, then assign "
            "the governed task to a Worker."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "organization_id": {"type": "string", "format": "uuid"},
                "project_id": {"type": "string", "format": "uuid"},
                "repository_id": {"type": "string", "format": "uuid"},
                "parent_task_id": {"type": "string", "format": "uuid"},
                "leader_agent_id": {"type": "string", "format": "uuid"},
                "worker_agent_id": {"type": "string", "format": "uuid"},
                "title": {"type": "string", "minLength": 1},
                "goal": {"type": "string", "minLength": 1},
                "acceptance": {
                    "type": "array", "items": {"type": "string"}, "minItems": 1,
                },
                "constraints": {"type": "array", "items": {"type": "string"}},
                "tests": {"type": "array", "items": {"type": "string"}},
                "dependencies": {"type": "array", "items": {"type": "string"}},
                "allowed_paths": {"type": "array", "items": {"type": "string"}},
                "interface_changes": {
                    "type": "array", "items": {"type": "string"},
                },
                "idempotency_key": {"type": "string", "minLength": 1},
            },
            "required": [
                "organization_id", "project_id", "repository_id", "parent_task_id",
                "leader_agent_id", "worker_agent_id", "title", "goal", "acceptance",
                "idempotency_key",
            ],
            "additionalProperties": False,
        },
    }, {
        "name": ASSESS_TOOL_NAME,
        "description": (
            "Assess the assigned Task Specification, scope, tests, and dependencies before "
            "starting any coding execution."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "format": "uuid"},
                "worker_agent_id": {"type": "string", "format": "uuid"},
                "decision": {"type": "string", "enum": ["ready", "question", "blocked"]},
                "spec_understood": {"type": "boolean"},
                "scope_sufficient": {"type": "boolean"},
                "tests_defined": {"type": "boolean"},
                "dependencies_ready": {"type": "boolean"},
                "notes": {"type": "string", "minLength": 1},
            },
            "required": [
                "task_id", "worker_agent_id", "decision", "spec_understood",
                "scope_sufficient", "tests_defined", "dependencies_ready", "notes",
            ],
            "additionalProperties": False,
        },
    }]


def _result(request_id: object, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
