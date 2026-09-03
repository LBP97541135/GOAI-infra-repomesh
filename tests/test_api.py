import asyncio
from dataclasses import replace
from datetime import datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from repomesh.bootstrap import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.agent_directory.application import (
    CreateAgent,
    CreateAgentRequest,
    CreateRepositoryAgentTeam,
    CreateRepositoryAgentTeamRequest,
)
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.agent_runtime.ports.agent_team import TeamRuntimeRef
from repomesh.settings import get_settings


def test_health(application_container: ApplicationContainer) -> None:
    with TestClient(create_app(application_container)) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}


def test_first_run_setup_status_and_coding_agent_probe(
    application_container: ApplicationContainer,
) -> None:
    with TestClient(create_app(application_container)) as client:
        status = client.get("/api/v1/setup/status")
        assert status.status_code == 200
        assert status.json()["counts"] == {
            "accounts": 0,
            "agents": 0,
            "repositories": 0,
        }
        assert "administrator" in status.json()["next_actions"]
        dependencies = {item["id"]: item for item in status.json()["dependencies"]}
        assert dependencies["database"] == {
            "id": "database",
            "state": "ready",
            "owner": "system",
            "remediation": "automatic",
            "required": True,
            "message": "managed by the RepoMesh product launcher",
        }
        assert dependencies["agentteams"]["state"] == "missing"
        assert dependencies["model"]["owner"] == "user"
        assert dependencies["model"]["state"] in {"ready", "waiting_for_user"}
        assert dependencies["github_app"]["state"] in {"ready", "optional"}
        assert dependencies["repositories"]["state"] == "pending_onboarding"
        assert dependencies["repositories"]["required"] is False

        probes = client.get("/api/v1/setup/coding-agents")
        assert probes.status_code == 200
        adapters = {item["adapter_id"]: item for item in probes.json()["adapters"]}
        assert {"claude-code", "codex", "kimi"}.issubset(adapters)
        assert "auth_status" in adapters["codex"]


def test_local_account_bootstrap_login_and_session_authentication(
    application_container: ApplicationContainer,
) -> None:
    with TestClient(create_app(application_container)) as client:
        created = client.post(
            "/api/v1/auth/bootstrap",
            json={
                "username": "admin",
                "password": "strong-password-123",
                "display_name": "Administrator",
            },
        )
        assert created.status_code == 201
        assert created.json()["is_admin"] is True

        duplicate = client.post(
            "/api/v1/auth/bootstrap",
            json={
                "username": "other",
                "password": "strong-password-456",
                "display_name": "Other",
            },
        )
        assert duplicate.status_code == 409

        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "strong-password-123"},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        authenticated = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert authenticated.status_code == 200
        assert authenticated.json()["username"] == "admin"
        assert client.get("/api/v1/auth/me").status_code == 200

        logged_out = client.post(
            "/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}
        )
        assert logged_out.status_code == 204
        assert (
            client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code
            == 401
        )


def test_account_creation_separates_bad_input_conflict_and_permission(
    application_container: ApplicationContainer,
) -> None:
    """One LocalAuthenticationError used to become 403 for all three.

    The form on the other end has to know whether to highlight a field, say
    the name is taken, or say the operator may not do this at all.
    """

    with TestClient(create_app(application_container)) as client:
        client.post(
            "/api/v1/auth/bootstrap",
            json={
                "username": "admin",
                "password": "strong-password-123",
                "display_name": "Administrator",
            },
        )
        admin_headers = {
            "Authorization": "Bearer "
            + client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "strong-password-123"},
            ).json()["access_token"]
        }

        short_password = client.post(
            "/api/v1/auth/accounts",
            headers=admin_headers,
            json={"username": "reviewer", "password": "short", "display_name": "Reviewer"},
        )
        assert short_password.status_code == 422
        assert short_password.json()["detail"] == "password must contain at least 12 characters"

        blank_display_name = client.post(
            "/api/v1/auth/accounts",
            headers=admin_headers,
            json={"username": "reviewer", "password": "reviewer-password-123", "display_name": " "},
        )
        assert blank_display_name.status_code == 422

        created = client.post(
            "/api/v1/auth/accounts",
            headers=admin_headers,
            json={
                "username": "reviewer",
                "password": "reviewer-password-123",
                "display_name": "Reviewer",
            },
        )
        assert created.status_code == 201

        duplicate = client.post(
            "/api/v1/auth/accounts",
            headers=admin_headers,
            json={
                "username": "REVIEWER",
                "password": "reviewer-password-456",
                "display_name": "Twin",
            },
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"] == "username already exists"

        reviewer_headers = {
            "Authorization": "Bearer "
            + client.post(
                "/api/v1/auth/login",
                json={"username": "reviewer", "password": "reviewer-password-123"},
            ).json()["access_token"]
        }
        forbidden = client.post(
            "/api/v1/auth/accounts",
            headers=reviewer_headers,
            json={
                "username": "other",
                "password": "another-password-123",
                "display_name": "Other",
            },
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"] == "local administrator permission is required"


def test_bootstrap_rejects_weak_password_as_input_not_conflict(
    application_container: ApplicationContainer,
) -> None:
    with TestClient(create_app(application_container)) as client:
        weak = client.post(
            "/api/v1/auth/bootstrap",
            json={"username": "admin", "password": "short", "display_name": "Administrator"},
        )
        assert weak.status_code == 422
        assert weak.json()["detail"] == "password must contain at least 12 characters"


def test_login_with_malformed_username_still_answers_401(
    application_container: ApplicationContainer,
) -> None:
    """Regression pin for the error-typing split.

    ``login`` normalizes the username through the same validator that now
    raises LocalAccountValidationError. Because that type subclasses
    LocalAuthenticationError, the login handler still catches it and answers
    401 — a sibling type would have escaped the handler and become a 500.
    """

    with TestClient(create_app(application_container)) as client:
        client.post(
            "/api/v1/auth/bootstrap",
            json={
                "username": "admin",
                "password": "strong-password-123",
                "display_name": "Administrator",
            },
        )
        refused = client.post(
            "/api/v1/auth/login",
            json={"username": "no spaces allowed", "password": "strong-password-123"},
        )
        assert refused.status_code == 401
        assert refused.json()["detail"] == "username format is invalid"


def test_admin_can_compose_agentteams_team_from_existing_agents(
    application_container: ApplicationContainer,
) -> None:
    organization_id = uuid4()
    repository_id = uuid4()

    async def create_agents():
        organization_leader = await CreateAgent(application_container.agent_directory).execute(
            CreateAgentRequest(
                organization_id=organization_id,
                role=AgentRole.ORGANIZATION_LEADER,
                agentteams_resource_name="manual-team-org-leader",
            ),
            idempotency_key="manual-team-org-leader",
        )
        return await CreateRepositoryAgentTeam(application_container.agent_directory).execute(
            CreateRepositoryAgentTeamRequest(
                organization_id=organization_id,
                organization_leader_id=organization_leader.principal.id,
                repository_id=repository_id,
                leader_agentteams_resource_name="manual-team-repo-leader",
                worker_agentteams_resource_names=("manual-team-worker",),
            ),
            idempotency_key="manual-team-agents",
        )

    team_agents = asyncio.run(create_agents())

    class ControlPlane:
        projection = None

        async def ensure_team(self, projection, *, idempotency_key: str):
            self.projection = projection
            return TeamRuntimeRef(
                name=projection.name,
                phase="Ready",
                team_room_id="!manual:matrix.local",
                leader_room_id="!leader:matrix.local",
                leader_name=projection.members[0].name,
                ready_workers=len(projection.members) - 1,
                total_workers=len(projection.members) - 1,
            )

    control_plane = ControlPlane()
    container = replace(application_container, agent_team_control_plane=control_plane)
    with TestClient(create_app(container)) as client:
        client.post(
            "/api/v1/auth/bootstrap",
            json={"username": "admin", "password": "strong-password-123", "display_name": "Admin"},
        )
        client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "strong-password-123"},
        )
        response = client.post(
            "/api/v1/agent-teams",
            json={
                "organization_id": str(organization_id),
                "name": "saleor_backend",
                "description": "Saleor backend team",
                "leader_agent_id": str(team_agents.leader.id),
                "member_agent_ids": [str(team_agents.workers[0].id)],
                "idempotency_key": "manual-team:saleor-backend",
            },
        )

    assert response.status_code == 201
    assert response.json()["team"]["name"] == "saleor_backend"
    assert response.json()["team"]["phase"] == "Ready"
    assert response.json()["leader"]["id"] == str(team_agents.leader.id)
    assert response.json()["members"][0]["id"] == str(team_agents.workers[0].id)
    assert [member.name for member in control_plane.projection.members] == [
        "manual-team-repo-leader",
        "manual-team-worker",
    ]


def test_authenticated_project_mode_and_checkpoint_decision_api(
    application_container: ApplicationContainer,
) -> None:
    organization_id = uuid4()
    repository_id = uuid4()
    project_id = uuid4()

    async def agents():
        leader = await CreateAgent(application_container.agent_directory).execute(
            CreateAgentRequest(
                organization_id=organization_id,
                role=AgentRole.ORGANIZATION_LEADER,
                agentteams_resource_name="human-api-org-leader",
            ),
            idempotency_key="human-api-org-leader",
        )
        team = await CreateRepositoryAgentTeam(application_container.agent_directory).execute(
            CreateRepositoryAgentTeamRequest(
                organization_id=organization_id,
                organization_leader_id=leader.principal.id,
                repository_id=repository_id,
                leader_agentteams_resource_name="human-api-repo-leader",
                worker_agentteams_resource_names=("human-api-worker",),
            ),
            idempotency_key="human-api-repo-team",
        )
        return leader.principal, team

    organization_leader, team = asyncio.run(agents())
    with TestClient(create_app(application_container)) as client:
        client.post(
            "/api/v1/auth/bootstrap",
            json={
                "username": "admin",
                "password": "strong-password-123",
                "display_name": "Administrator",
            },
        )
        admin_login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "strong-password-123"},
        ).json()
        admin_headers = {"Authorization": f"Bearer {admin_login['access_token']}"}
        reviewer = client.post(
            "/api/v1/auth/accounts",
            headers=admin_headers,
            json={
                "username": "reviewer",
                "password": "reviewer-password-123",
                "display_name": "Reviewer",
            },
        ).json()
        created = client.post(
            "/api/v1/projects/topologies",
            headers=admin_headers,
            json={
                "organization_id": str(organization_id),
                "project_id": str(project_id),
                "organization_leader_id": str(organization_leader.id),
                "repository_teams": [
                    {
                        "repository_id": str(repository_id),
                        "leader_agent_id": str(team.leader.id),
                        "worker_agent_ids": [str(team.workers[0].id)],
                    }
                ],
                "execution_mode": "supervised",
                "required_checkpoints": ["execution"],
                "human_grants": [
                    {
                        "human_principal_id": reviewer["id"],
                        "role": "project_supervisor",
                        "code_access": "read",
                        "control_actions": [
                            "approve_checkpoint",
                            "request_changes",
                            "pause_project",
                            "resume_project",
                            "cancel_project",
                        ],
                        "repository_id": None,
                        "path_patterns": [],
                    }
                ],
                "idempotency_key": "human-api-project",
            },
        )
        assert created.status_code == 201
        assert created.json()["execution_mode"] == "supervised"

        reviewer_login = client.post(
            "/api/v1/auth/login",
            json={"username": "reviewer", "password": "reviewer-password-123"},
        ).json()
        pending_gate = client.get(
            f"/api/v1/projects/{project_id}/checkpoint-gate",
            headers=admin_headers,
            params={
                "checkpoint": "execution",
                "evidence_version": "task:example:v1",
                "repository_id": str(repository_id),
            },
        )
        assert pending_gate.json()["reason"] == "human_checkpoint_pending"
        reviewer_headers = {"Authorization": f"Bearer {reviewer_login['access_token']}"}
        agents_response = client.get("/api/v1/agents", headers=reviewer_headers)
        assert agents_response.status_code == 200
        assert {item["id"] for item in agents_response.json()} == {
            str(organization_leader.id),
            str(team.leader.id),
            str(team.workers[0].id),
        }
        inbox = client.get("/api/v1/review-requests?status=pending", headers=reviewer_headers)
        assert inbox.status_code == 200
        assert inbox.json()[0]["evidence_version"] == "task:example:v1"
        decision_payload = {
            "review_request_id": inbox.json()[0]["id"],
            "decision": "approved",
            "reason": "task is safe to execute",
        }
        approved = client.post(
            f"/api/v1/projects/{project_id}/checkpoint-decisions",
            headers=reviewer_headers,
            json=decision_payload,
        )
        assert approved.status_code == 200
        assert approved.json()["human_principal_id"] == reviewer["id"]
        assert (
            client.get("/api/v1/review-requests?status=pending", headers=reviewer_headers).json()
            == []
        )
        duplicate_decision = client.post(
            f"/api/v1/projects/{project_id}/checkpoint-decisions",
            headers=reviewer_headers,
            json=decision_payload,
        )
        assert duplicate_decision.status_code == 409

        for action, expected_status in (
            ("pause_project", "paused"),
            ("resume_project", "active"),
            ("cancel_project", "cancelled"),
        ):
            controlled = client.post(
                f"/api/v1/projects/{project_id}/control",
                headers=reviewer_headers,
                json={"action": action},
            )
            assert controlled.status_code == 200
            assert controlled.json()["operational_status"] == expected_status
        cannot_resume = client.post(
            f"/api/v1/projects/{project_id}/control",
            headers=reviewer_headers,
            json={"action": "resume_project"},
        )
        assert cannot_resume.status_code == 403


def test_register_and_discover_repository(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    # These writes used to take no credential; they now share the action token
    # with the rest of this router, so the happy path has to present it.
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    get_settings.cache_clear()
    headers = {"Authorization": "Bearer internal-secret"}
    try:
        _register_and_discover(application_container, headers)
    finally:
        get_settings.cache_clear()


def _register_and_discover(
    application_container: ApplicationContainer, headers: dict[str, str]
) -> None:
    with TestClient(create_app(application_container)) as client:
        created = client.post(
            "/api/v1/repositories",
            headers=headers,
            json={
                "name": "billing-api",
                "url": "https://github.com/example/billing",
                "description": "Invoice and payment service",
                "topics": ["billing", "payment"],
                "languages": ["python"],
            },
        )
        assert created.status_code == 201

        repository_id = created.json()["id"]
        updated = client.patch(
            f"/api/v1/repositories/{repository_id}/verification",
            headers=headers,
            json={
                "test_commands": ["  python scripts/run_tests.py  ", ""],
                "test_paths": [" tests/** "],
            },
        )
        assert updated.status_code == 200
        assert updated.json()["test_commands"] == ["python scripts/run_tests.py"]
        assert updated.json()["test_paths"] == ["tests/**"]

        replayed = client.patch(
            f"/api/v1/repositories/{repository_id}/verification",
            headers=headers,
            json={
                "test_commands": ["python scripts/run_tests.py"],
                "test_paths": ["tests/**"],
            },
        )
        assert replayed.status_code == 200
        assert replayed.json() == updated.json()

        missing = client.patch(
            f"/api/v1/repositories/{uuid4()}/verification",
            headers=headers,
            json={"test_commands": [], "test_paths": []},
        )
        assert missing.status_code == 404

        unauthorized = client.patch(
            f"/api/v1/repositories/{repository_id}/verification",
            json={"test_commands": [], "test_paths": []},
        )
        assert unauthorized.status_code == 401

        profiled = client.patch(
            f"/api/v1/repositories/{repository_id}/capability-profile",
            headers=headers,
            json={"capability_profile": "cross-repo-test-team"},
        )
        assert profiled.status_code == 200
        assert profiled.json()["capability_profile"] == "cross-repo-test-team"

        replayed_profile = client.patch(
            f"/api/v1/repositories/{repository_id}/capability-profile",
            headers=headers,
            json={"capability_profile": "cross-repo-test-team"},
        )
        assert replayed_profile.status_code == 200
        assert replayed_profile.json() == profiled.json()

        unknown_profile = client.patch(
            f"/api/v1/repositories/{repository_id}/capability-profile",
            headers=headers,
            json={"capability_profile": "team-x"},
        )
        assert unknown_profile.status_code == 422

        cleared = client.patch(
            f"/api/v1/repositories/{repository_id}/capability-profile",
            headers=headers,
            json={"capability_profile": None},
        )
        assert cleared.status_code == 200
        assert cleared.json()["capability_profile"] is None

        missing_profile = client.patch(
            f"/api/v1/repositories/{uuid4()}/capability-profile",
            headers=headers,
            json={"capability_profile": "cross-repo-test-team"},
        )
        assert missing_profile.status_code == 404

        unauthenticated_profile = client.patch(
            f"/api/v1/repositories/{repository_id}/capability-profile",
            json={"capability_profile": None},
        )
        assert unauthenticated_profile.status_code == 401

        discovered = client.post(
            "/api/v1/discovery",
            headers=headers,
            json={"requirement": "Add payment invoice support"},
        )
        assert discovered.status_code == 200
        assert discovered.json()[0]["repository_name"] == "billing-api"
        assert discovered.json()[0]["matched_terms"] == ["invoice", "payment"]


def test_runner_control_requires_configured_token(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_RUNNER_CONTROL_TOKEN", "")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(application_container)) as client:
            response = client.get("/api/v1/runtime/runner-tasks/next")
        assert response.status_code == 503
        assert response.json()["detail"] == "runner control token is not configured"

        monkeypatch.setenv("REPOMESH_RUNNER_CONTROL_TOKEN", "runner-secret")
        get_settings.cache_clear()
        with TestClient(create_app(application_container)) as client:
            unauthorized = client.get(
                "/api/v1/runtime/runner-tasks/next",
                headers={"Authorization": "Bearer wrong"},
            )
            authorized = client.get(
                "/api/v1/runtime/runner-tasks/next",
                headers={"Authorization": "Bearer runner-secret"},
            )
        assert unauthorized.status_code == 401
        assert authorized.status_code == 204
    finally:
        get_settings.cache_clear()


def test_worker_start_action_requires_internal_token(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(application_container)) as client:
            response = client.post(
                "/api/v1/agent-actions/start-worker-task",
                json={
                    "task_id": str(uuid4()),
                    "worker_agent_id": str(uuid4()),
                    "adapter_id": "claude-code",
                },
            )
        assert response.status_code == 401
    finally:
        get_settings.cache_clear()


def test_delivery_reconciliation_requires_token_and_configured_scm(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(application_container)) as client:
            unauthorized = client.post(f"/api/v1/delivery/change-sets/{uuid4()}/reconcile")
            unavailable = client.post(
                f"/api/v1/delivery/change-sets/{uuid4()}/reconcile",
                headers={"Authorization": "Bearer internal-secret"},
            )
        assert unauthorized.status_code == 401
        assert unavailable.status_code == 503
        assert unavailable.json()["detail"] == "SCM adapter is not configured"
    finally:
        get_settings.cache_clear()


def test_worker_mcp_initializes_with_gateway_token(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_MCP_GATEWAY_TOKEN", "gateway-secret")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(application_container)) as client:
            response = client.post(
                "/api/v1/mcp/worker",
                headers={"X-RepoMesh-Gateway-Token": "gateway-secret"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            )
        assert response.status_code == 200
        assert response.json()["result"]["serverInfo"]["name"] == ("repomesh-task-control")
    finally:
        get_settings.cache_clear()


def test_worker_mcp_accepts_agentteams_bearer_gateway_token(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_MCP_GATEWAY_TOKEN", "agentteams-worker-key")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(application_container)) as client:
            response = client.post(
                "/api/v1/mcp/worker",
                headers={"Authorization": "Bearer agentteams-worker-key"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            )
        assert response.status_code == 200
    finally:
        get_settings.cache_clear()


def test_worker_mcp_accepts_any_configured_agentteams_gateway_token(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.delenv("REPOMESH_MCP_GATEWAY_TOKEN", raising=False)
    monkeypatch.setenv(
        "REPOMESH_MCP_GATEWAY_TOKENS",
        '["api-worker-key", "client-worker-key"]',
    )
    get_settings.cache_clear()
    try:
        with TestClient(create_app(application_container)) as client:
            response = client.post(
                "/api/v1/mcp/worker",
                headers={"Authorization": "Bearer client-worker-key"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            )
        assert response.status_code == 200
    finally:
        get_settings.cache_clear()


def test_worker_mcp_can_be_explicitly_enabled_for_local_direct_mode(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_ENVIRONMENT", "test")
    monkeypatch.setenv("REPOMESH_DIRECT_WORKER_MCP_ENABLED", "true")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(application_container)) as client:
            response = client.post(
                "/api/v1/mcp/worker",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
        assert response.status_code == 200
        assert response.json()["result"]["tools"][0]["name"] == "start_assigned_task"
    finally:
        get_settings.cache_clear()


def test_worker_mcp_tools_call_success_returns_started_payload(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Regression: the endpoint must unwrap McpCallResult.value.

    The guard wraps every invoke (policy enforcement cannot be bypassed), so
    ``call_gated`` returns ``McpCallResult`` — not the service's
    ``WorkerExecutionStarted``. The endpoint used to treat the wrapper as the
    result object and crashed with AttributeError after the execution side
    effects had already committed. It must read ``guard_result.value`` and map
    non-success outcomes to an isError tool result instead.
    """

    from types import SimpleNamespace

    from repomesh.modules.capability_management.mcp_guard import McpCallGuard
    from repomesh.modules.task_orchestration.contracts import TaskStatus

    monkeypatch.setenv("REPOMESH_ENVIRONMENT", "test")
    monkeypatch.setenv("REPOMESH_DIRECT_WORKER_MCP_ENABLED", "true")

    task_id = uuid4()
    run_id = uuid4()

    class FakeExecutionService:
        def __init__(self) -> None:
            self.commands = []

        async def execute(self, command):
            self.commands.append(command)
            return SimpleNamespace(
                task=SimpleNamespace(
                    task_id=command.task_id,
                    run_id=run_id,
                    workspace=SimpleNamespace(
                        workspace_id="ws-1", base_sha="0" * 40
                    ),
                ),
                status=TaskStatus.IN_PROGRESS,
            )

    class ContainerOverride:
        """Delegate everything to the real container except the two services.

        ApplicationContainer is a frozen slotted dataclass, so services cannot
        be patched onto the instance; a proxy keeps the test hermetic without
        rebuilding the whole dependency graph.
        """

        def __init__(self, base: ApplicationContainer, guard, service) -> None:
            self._base = base
            self._guard = guard
            self._service = service

        def __getattr__(self, name):
            if name == "mcp_call_guard":
                return lambda: self._guard
            if name == "worker_execution_service":
                return lambda: self._service
            return getattr(self._base, name)

    fake_service = FakeExecutionService()
    # A bare guard without a policy provider: defaults (no retry, 30s) keep
    # the test hermetic — no registry/DB round trip for the policy.
    overridden = ContainerOverride(application_container, McpCallGuard(), fake_service)
    get_settings.cache_clear()
    try:
        with TestClient(create_app(overridden)) as client:
            response = client.post(
                "/api/v1/mcp/worker",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "start_assigned_task",
                        "arguments": {
                            "task_id": str(task_id),
                            "worker_agent_id": str(uuid4()),
                            "adapter_id": "claude-code",
                        },
                    },
                },
            )
        assert response.status_code == 200
        body = response.json()["result"]
        assert body.get("isError") is None
        payload = body["structuredContent"]
        assert payload["task_id"] == str(task_id)
        assert payload["run_id"] == str(run_id)
        assert payload["status"] == "in_progress"
        assert payload["workspace_id"] == "ws-1"
        assert payload["base_sha"] == "0" * 40
        assert len(fake_service.commands) == 1
    finally:
        get_settings.cache_clear()


def test_worker_mcp_direct_mode_is_forbidden_in_production(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_ENVIRONMENT", "production")
    # The deployment guard rejects the public default token before the app
    # starts, so the fixture must provide a real one to reach the 503 path.
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "test-non-default-token")
    monkeypatch.setenv("REPOMESH_DIRECT_WORKER_MCP_ENABLED", "true")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(application_container)) as client:
            response = client.post(
                "/api/v1/mcp/worker",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
        assert response.status_code == 503
    finally:
        get_settings.cache_clear()


def test_repository_intelligence_writes_all_require_the_action_token(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Every write on this router, not just POST /issues.

    The check used to live inside the intake handler, so the other eight — the
    manual approval gate and the org scanner among them — took no credential.
    Sampling one endpoint would not have caught that, so this asserts the whole
    set and the reads it must not have locked.
    """

    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    get_settings.cache_clear()
    doc_id = uuid4()
    writes = [
        ("/api/v1/issues", {}),
        ("/api/v1/repositories", {}),
        ("/api/v1/repositories/scan-org", {}),
        ("/api/v1/repositories/scan-repo", {}),
        ("/api/v1/discovery", {}),
        ("/api/v1/requirement-analysis", {}),
        ("/api/v1/confirmation", {}),
        ("/api/v1/integration", {}),
        ("/api/v1/bridge/materialize", {}),
        ("/api/v1/bridge/replan", {}),
        (f"/api/v1/handoff-docs/{doc_id}/decision", {}),
    ]
    try:
        with TestClient(create_app(application_container)) as client:
            unauthorized = {path: client.post(path, json=body).status_code for path, body in writes}
            wrong_token = client.post(
                "/api/v1/discovery",
                headers={"Authorization": "Bearer wrong"},
                json={"requirement": "x"},
            )
            reads_still_open = client.get("/api/v1/repositories")
    finally:
        get_settings.cache_clear()

    assert set(unauthorized.values()) == {401}, unauthorized
    assert wrong_token.status_code == 401
    # The reads share this router and stay open: this change stops the bleeding
    # on the writes without altering behaviour anyone reads today.
    assert reads_still_open.status_code == 200


def test_org_scan_refuses_hosts_outside_the_allowlist(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """A host in the request body used to become an outbound request.

    The fetcher derives its API base from the submitted URL, so the body chose
    who this server talked to. Hosts that are neither a known platform nor
    declared are refused before any egress; 400 means the refusal happened
    before any request left this process.
    """

    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    get_settings.cache_clear()
    headers = {"Authorization": "Bearer internal-secret"}
    try:
        with TestClient(create_app(application_container)) as client:
            metadata = client.post(
                "/api/v1/repositories/scan-org",
                headers=headers,
                json={"org_url": "http://169.254.169.254/latest/meta-data/"},
            )
            internal = client.post(
                "/api/v1/repositories/scan-org",
                headers=headers,
                json={"org_url": "https://code.internal.example/acme"},
            )
    finally:
        get_settings.cache_clear()

    assert metadata.status_code == 400
    assert internal.status_code == 400
    assert "REPOMESH_REPOSITORY_PLATFORMS" in internal.json()["detail"]


def test_known_platform_hosts_scan_without_an_allowlist_entry(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """GitHub and self-hosted GitLab hosts are reachable by default.

    The allowlist exists to bound SSRF, not to re-gate platform support:
    github.com, *.github.com, gitlab.com and any host whose name contains
    "gitlab" reach the scan stage with an empty allowlist, while an unnamed
    custom domain is still refused before any egress.
    """

    from repomesh.modules.repository_intelligence.application import scan_remote

    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    monkeypatch.setenv("REPOMESH_REPOSITORY_SCAN_ALLOWED_HOSTS", "")
    get_settings.cache_clear()

    async def _explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(scan_remote, "scan_org", _explode)
    headers = {"Authorization": "Bearer internal-secret"}
    try:
        with TestClient(create_app(application_container)) as client:
            gitlab = client.post(
                "/api/v1/repositories/scan-org",
                headers=headers,
                json={"org_url": "https://gitlab.example.com/orders"},
            )
            github = client.post(
                "/api/v1/repositories/scan-org",
                headers=headers,
                json={"org_url": "https://github.com/acme"},
            )
            custom = client.post(
                "/api/v1/repositories/scan-org",
                headers=headers,
                json={"org_url": "https://code.internal.example/acme"},
            )
    finally:
        get_settings.cache_clear()

    # The known platforms passed the gate and reached the (mocked) scan,
    # which failed → 502. The unnamed custom domain never left the process.
    assert gitlab.status_code == 502
    assert github.status_code == 502
    assert custom.status_code == 400
    assert "REPOMESH_REPOSITORY_PLATFORMS" in custom.json()["detail"]


def test_register_repository_refuses_undeclared_hosts(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """The manual registration endpoint keeps the strict allowlist.

    It takes a URL the caller typed by hand and persists the row; that row is
    later cloned out-of-process (git_worktree), so a host off the operator's
    allowlist here would become an unvetted outbound request on the first
    dispatch. The scan endpoints gate the same way earlier, via platform
    recognition; this endpoint has no platform detection to lean on.
    """

    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    monkeypatch.setenv("REPOMESH_REPOSITORY_SCAN_ALLOWED_HOSTS", "github.com")
    get_settings.cache_clear()
    headers = {"Authorization": "Bearer internal-secret"}
    try:
        with TestClient(create_app(application_container)) as client:
            response = client.post(
                "/api/v1/repositories",
                headers=headers,
                json={"name": "x", "url": "https://code.internal.example/acme"},
            )
    finally:
        get_settings.cache_clear()

    assert response.status_code == 400
    assert "allowlist" in response.json()["detail"]


def test_scan_refuses_unsupported_and_undeclared_platforms(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Known-but-unimplemented hosts are refused by name; mystery hosts are refused outright.

    Before this change every non-github.com git URL was treated as GitLab, so
    ``https://gitee.com/...`` would have become an outbound request to Gitee's
    API. Both refusals happen before any egress and before the allowlist, so
    an operator can extend ``REPOMESH_REPOSITORY_PLATFORMS`` without touching
    the allowlist.
    """

    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    get_settings.cache_clear()
    headers = {"Authorization": "Bearer internal-secret"}
    try:
        with TestClient(create_app(application_container)) as client:
            unsupported = client.post(
                "/api/v1/repositories/scan-org",
                headers=headers,
                json={"org_url": "https://gitee.com/acme"},
            )
            unknown = client.post(
                "/api/v1/repositories/scan-org",
                headers=headers,
                json={"org_url": "https://git.example.internal/acme"},
            )
    finally:
        get_settings.cache_clear()

    assert unsupported.status_code == 400
    assert "Gitee" in unsupported.json()["detail"]
    assert unknown.status_code == 400
    assert "REPOMESH_REPOSITORY_PLATFORMS" in unknown.json()["detail"]


def test_org_scan_failures_do_not_echo_the_underlying_error(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """502 used to carry the outbound failure's text back to the caller."""

    from repomesh.modules.repository_intelligence.application import scan_remote

    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    monkeypatch.setenv("REPOMESH_REPOSITORY_SCAN_ALLOWED_HOSTS", "github.com")
    get_settings.cache_clear()

    async def _explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("connect to 10.0.0.7:5432 refused")

    monkeypatch.setattr(scan_remote, "scan_org", _explode)
    try:
        with TestClient(create_app(application_container)) as client:
            response = client.post(
                "/api/v1/repositories/scan-org",
                headers={"Authorization": "Bearer internal-secret"},
                json={"org_url": "https://github.com/acme"},
            )
    finally:
        get_settings.cache_clear()

    assert response.status_code == 502
    assert response.json()["detail"] == "organization scan failed"
    assert "10.0.0.7" not in response.text


def test_url_type_endpoint_is_the_console_badge_source_of_truth(
    application_container: ApplicationContainer,
) -> None:
    """Organization / single repo / neither, decided in Python.

    The console debounces against this instead of reimplementing
    ``detect_platform`` in TypeScript, so the badge cannot drift from what the
    scan endpoints will actually do. It is a read: no token, no egress.
    """

    with TestClient(create_app(application_container)) as client:
        org = client.get("/api/v1/repositories/url-type", params={"url": "https://github.com/acme"})
        repo = client.get(
            "/api/v1/repositories/url-type",
            params={"url": "https://github.com/acme/order-service"},
        )
        junk = client.get("/api/v1/repositories/url-type", params={"url": "order-service"})
        gitlab = client.get(
            "/api/v1/repositories/url-type",
            params={"url": "https://gitlab.internal.example/acme"},
        )

    assert org.status_code == 200
    assert org.json() == {
        "url": "https://github.com/acme",
        "url_type": "group",
        "platform": "github",
        "repository_name": None,
    }
    assert repo.json()["url_type"] == "single_repo"
    assert repo.json()["repository_name"] == "order-service"
    assert junk.json()["url_type"] == "unknown"
    assert junk.json()["platform"] == "local"
    # A host this server would refuse to scan still classifies honestly: the
    # badge must not double as an oracle for the operator's allowlist.
    assert gitlab.json() == {
        "url": "https://gitlab.internal.example/acme",
        "url_type": "group",
        "platform": "gitlab",
        "repository_name": None,
    }


async def _stub_require_single_repo_url(url: str, fetcher: object) -> None:
    """Offline stand-in for the online single-repo verdict in API tests.

    The real ``require_single_repo_url`` asks the platform's ``identify``
    (``GET /repos/...``), which is a real network call a unit test must not
    make. This keeps the endpoint's refusal path intact — the 400 a scan-repo
    call gets for a group URL — without egress. The online verdict itself is
    covered at the fetcher level.
    """

    from urllib.parse import urlparse  # noqa: PLC0415

    from fastapi import HTTPException  # noqa: PLC0415

    segments = [s for s in urlparse(url).path.split("/") if s]
    if len(segments) < 2:
        raise HTTPException(400, "URL must point at a single repository, not a group")


def test_single_repo_scan_registers_the_repository_from_its_url(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """A pasted repo URL becomes a catalog entry with a real AutoCard.

    POST /repositories already existed but makes the caller type every field;
    this one fetches the tree/deps/commits. The scan itself is stubbed — the
    test asserts the endpoint's contract, not GitHub's.
    """

    from repomesh.modules.repository_intelligence.api import router as repo_router
    from repomesh.modules.repository_intelligence.application import scan_remote
    from repomesh.modules.repository_intelligence.domain import AutoCard, RepositoryProfile

    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    monkeypatch.setenv("REPOMESH_REPOSITORY_SCAN_ALLOWED_HOSTS", "github.com")
    monkeypatch.setattr(
        repo_router, "require_single_repo_url", _stub_require_single_repo_url
    )
    get_settings.cache_clear()

    async def _fake_scan(url: str, fetcher: object) -> RepositoryProfile:
        return RepositoryProfile(
            name="order-service",
            url=url,
            auto_card=AutoCard(top_dirs=("src",), recent_commits=("add wechat pay",)),
        )

    monkeypatch.setattr(scan_remote, "scan_single_repo", _fake_scan)
    headers = {"Authorization": "Bearer internal-secret"}
    try:
        with TestClient(create_app(application_container)) as client:
            first = client.post(
                "/api/v1/repositories/scan-repo",
                headers=headers,
                json={"repo_url": "https://github.com/acme/order-service"},
            )
            # Re-scanning is the only retry the console offers, so it has to be
            # safe to repeat: the second run skips instead of duplicating.
            again = client.post(
                "/api/v1/repositories/scan-repo",
                headers=headers,
                json={"repo_url": "https://github.com/acme/order-service"},
            )
            listed = client.get("/api/v1/repositories")
    finally:
        get_settings.cache_clear()

    assert first.status_code == 200
    body = first.json()
    assert body["repo_url"] == "https://github.com/acme/order-service"
    assert (body["total_scanned"], body["registered"], body["skipped"], body["failed"]) == (
        1,
        1,
        0,
        0,
    )
    assert body["repositories"][0]["name"] == "order-service"
    assert body["repositories"][0]["auto_card"]["recent_commits"] == ["add wechat pay"]

    assert again.json()["registered"] == 0
    assert again.json()["skipped"] == 1
    assert [r["name"] for r in listed.json()] == ["order-service"]


def test_single_repo_scan_refuses_a_group_url_and_hosts_off_the_allowlist(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Both refusals happen before anything leaves this process.

    The group URL is caught by the same identification the console badges
    with; the internal host by the SSRF allowlist that scan-org already had.
    """

    from repomesh.modules.repository_intelligence.api import router as repo_router

    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    monkeypatch.setenv("REPOMESH_REPOSITORY_SCAN_ALLOWED_HOSTS", "github.com")
    monkeypatch.setattr(
        repo_router, "require_single_repo_url", _stub_require_single_repo_url
    )
    get_settings.cache_clear()
    headers = {"Authorization": "Bearer internal-secret"}
    try:
        with TestClient(create_app(application_container)) as client:
            group = client.post(
                "/api/v1/repositories/scan-repo",
                headers=headers,
                json={"repo_url": "https://github.com/acme"},
            )
            internal = client.post(
                "/api/v1/repositories/scan-repo",
                headers=headers,
                json={"repo_url": "https://code.internal.example/acme/orders"},
            )
            metadata = client.post(
                "/api/v1/repositories/scan-repo",
                headers=headers,
                json={"repo_url": "http://169.254.169.254/latest/meta-data/"},
            )
    finally:
        get_settings.cache_clear()

    assert group.status_code == 400
    assert "single repository" in group.json()["detail"]
    assert internal.status_code == 400
    assert "REPOMESH_REPOSITORY_PLATFORMS" in internal.json()["detail"]
    assert metadata.status_code == 400


def test_single_repo_scan_failures_do_not_echo_the_underlying_error(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Same silence as scan-org: the caller does not learn what we reached."""

    from repomesh.modules.repository_intelligence.api import router as repo_router
    from repomesh.modules.repository_intelligence.application import scan_remote

    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    monkeypatch.setenv("REPOMESH_REPOSITORY_SCAN_ALLOWED_HOSTS", "github.com")
    monkeypatch.setattr(
        repo_router, "require_single_repo_url", _stub_require_single_repo_url
    )
    get_settings.cache_clear()

    async def _explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("connect to 10.0.0.7:5432 refused")

    monkeypatch.setattr(scan_remote, "scan_single_repo", _explode)
    try:
        with TestClient(create_app(application_container)) as client:
            response = client.post(
                "/api/v1/repositories/scan-repo",
                headers={"Authorization": "Bearer internal-secret"},
                json={"repo_url": "https://github.com/acme/order-service"},
            )
    finally:
        get_settings.cache_clear()

    assert response.status_code == 502
    assert response.json()["detail"] == "repository scan failed"
    assert "10.0.0.7" not in response.text


# ---------------------------------------------------------------------------
# The supervision-policy draft: PUT / GET / DELETE
# ---------------------------------------------------------------------------


def _admin_headers(client: TestClient) -> dict[str, str]:
    client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": "admin",
            "password": "strong-password-123",
            "display_name": "Administrator",
        },
    )
    return {
        "Authorization": "Bearer "
        + client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "strong-password-123"},
        ).json()["access_token"]
    }


def _member(
    client: TestClient, admin_headers: dict[str, str], username: str
) -> tuple[dict, dict[str, str]]:
    password = f"{username}-password-123"
    account = client.post(
        "/api/v1/auth/accounts",
        headers=admin_headers,
        json={
            "username": username,
            "password": password,
            "display_name": username.title(),
        },
    ).json()
    headers = {
        "Authorization": "Bearer "
        + client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        ).json()["access_token"]
    }
    return account, headers


def _grant(human_principal_id: str, **overrides: object) -> dict:
    grant = {
        "human_principal_id": human_principal_id,
        "role": "project_supervisor",
        "code_access": "read",
        "control_actions": ["view_decisions", "approve_checkpoint", "request_changes"],
        "repository_id": None,
        "path_patterns": [],
    }
    grant.update(overrides)
    return grant


def _draft_body(human_principal_id: str, **overrides: object) -> dict:
    body = {
        "execution_mode": "supervised",
        "required_checkpoints": ["repository_scope", "delivery"],
        "human_grants": [_grant(human_principal_id)],
    }
    body.update(overrides)
    return body


def _instant(value: str) -> datetime:
    """Compare two timestamps as instants, not as strings.

    The endpoint answers out of the record it just wrote, so the first PUT
    returns the aware ``datetime`` it stored (``...Z``) while the second returns
    the one SQLite read back, which carries no offset — the same instant spelled
    two ways, and only under the test database. Postgres holds these columns as
    ``timestamptz`` and hands both back aware, so normalising here hides a
    fixture artefact rather than a behaviour.
    """

    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def test_policy_draft_is_overwritten_read_and_withdrawn(
    application_container: ApplicationContainer,
) -> None:
    """The lifecycle of the one supervision intent a requirement holds.

    ``PUT`` twice is 200 twice — whole-document overwrite, no idempotency key —
    and the pair of timestamps carries the distinction the store exists to keep:
    who first decided this project needed watching stays put, when it was last
    touched moves.
    """

    project_id = uuid4()
    with TestClient(create_app(application_container)) as client:
        admin_headers = _admin_headers(client)
        reviewer, reviewer_headers = _member(client, admin_headers, "reviewer")
        _, outsider_headers = _member(client, admin_headers, "outsider")

        assert (
            client.get(
                f"/api/v1/projects/{project_id}/policy-draft", headers=admin_headers
            ).status_code
            == 404
        )

        first = client.put(
            f"/api/v1/projects/{project_id}/policy-draft",
            headers=admin_headers,
            json=_draft_body(reviewer["id"]),
        )
        assert first.status_code == 200
        assert first.json()["execution_mode"] == "supervised"
        assert sorted(first.json()["required_checkpoints"]) == [
            "delivery",
            "repository_scope",
        ]

        second = client.put(
            f"/api/v1/projects/{project_id}/policy-draft",
            headers=admin_headers,
            json=_draft_body(reviewer["id"], required_checkpoints=["delivery"]),
        )
        assert second.status_code == 200
        assert second.json()["required_checkpoints"] == ["delivery"]
        assert _instant(second.json()["created_at"]) == _instant(first.json()["created_at"])
        assert _instant(second.json()["updated_at"]) > _instant(first.json()["updated_at"])
        assert second.json()["created_by"] == first.json()["created_by"]

        # Readable by the same rule as GET .../topology: administrators, or the
        # people the policy itself names.
        granted = client.get(
            f"/api/v1/projects/{project_id}/policy-draft", headers=reviewer_headers
        )
        assert granted.status_code == 200
        assert granted.json()["human_grants"][0]["human_principal_id"] == reviewer["id"]
        refused = client.get(
            f"/api/v1/projects/{project_id}/policy-draft", headers=outsider_headers
        )
        assert refused.status_code == 403
        assert refused.json()["detail"] == "human project membership is required"

        withdrawn = client.delete(
            f"/api/v1/projects/{project_id}/policy-draft", headers=admin_headers
        )
        assert withdrawn.status_code == 204
        again = client.delete(
            f"/api/v1/projects/{project_id}/policy-draft", headers=admin_headers
        )
        assert again.status_code == 404
        assert again.json()["detail"] == "project policy draft does not exist"


def test_policy_draft_writes_are_admin_only(
    application_container: ApplicationContainer,
) -> None:
    """Setting a policy is the one thing the console's shared token must not reach.

    The whole design rests on it: materialization keeps its own guard and merely
    *reads* what an admin left, so a write that leaked to any authenticated
    session — or to none — would put the policy back inside the blast radius of
    the shared action token.
    """

    project_id = uuid4()
    with TestClient(create_app(application_container)) as client:
        admin_headers = _admin_headers(client)
        reviewer, reviewer_headers = _member(client, admin_headers, "reviewer")

        forbidden = client.put(
            f"/api/v1/projects/{project_id}/policy-draft",
            headers=reviewer_headers,
            json=_draft_body(reviewer["id"]),
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"] == "local administrator permission is required"
        assert (
            client.delete(
                f"/api/v1/projects/{project_id}/policy-draft", headers=reviewer_headers
            ).status_code
            == 403
        )

    with TestClient(create_app(application_container)) as anonymous:
        unauthenticated = anonymous.put(
            f"/api/v1/projects/{project_id}/policy-draft",
            json=_draft_body(str(uuid4())),
        )
        assert unauthenticated.status_code == 401
        assert unauthenticated.json()["detail"] == "local authentication is required"


def test_policy_draft_runs_the_single_grant_rules_by_building_a_real_grant(
    application_container: ApplicationContainer,
) -> None:
    """The two refusals below only happen if the endpoint builds a HumanProjectGrant.

    ``assert_supervision_policy`` takes a Protocol and reads two fields off each
    grant, so forwarding the request rows straight to it would type-check, pass,
    and store a draft that materialization later refuses — the exact
    two-verdicts-on-one-policy failure the shared module was cut out to prevent.
    The four single-grant rules live in ``HumanProjectGrant.__post_init__`` and
    run only when the object is really constructed; these are the regression
    pins for that, and the sentences are the domain's own rather than Pydantic's.
    """

    with TestClient(create_app(application_container)) as client:
        admin_headers = _admin_headers(client)
        reviewer, _ = _member(client, admin_headers, "reviewer")

        unscoped_supervisor = client.put(
            f"/api/v1/projects/{uuid4()}/policy-draft",
            headers=admin_headers,
            json=_draft_body(
                reviewer["id"],
                human_grants=[_grant(reviewer["id"], role="repository_supervisor")],
            ),
        )
        assert unscoped_supervisor.status_code == 422
        assert (
            unscoped_supervisor.json()["detail"]
            == "repository supervisor requires repository scope"
        )

        no_actions = client.put(
            f"/api/v1/projects/{uuid4()}/policy-draft",
            headers=admin_headers,
            json=_draft_body(
                reviewer["id"],
                human_grants=[_grant(reviewer["id"], control_actions=[])],
            ),
        )
        assert no_actions.status_code == 422
        assert no_actions.json()["detail"] == "human grant requires control actions"

        unknown_account = client.put(
            f"/api/v1/projects/{uuid4()}/policy-draft",
            headers=admin_headers,
            json=_draft_body(str(uuid4())),
        )
        assert unknown_account.status_code == 422
        assert unknown_account.json()["detail"] == "human grant account does not exist"


def test_policy_draft_reports_each_broken_policy_rule_verbatim(
    application_container: ApplicationContainer,
) -> None:
    """A draft is judged now by the same function that will judge it at materialization."""

    with TestClient(create_app(application_container)) as client:
        admin_headers = _admin_headers(client)
        reviewer, _ = _member(client, admin_headers, "reviewer")

        cases = (
            (
                {
                    "execution_mode": "auto",
                    "required_checkpoints": ["delivery"],
                    "human_grants": [],
                },
                "automatic projects cannot require human checkpoints",
            ),
            (
                {
                    "execution_mode": "supervised",
                    "required_checkpoints": ["delivery"],
                    "human_grants": [],
                },
                "human-controlled projects require a human grant",
            ),
            (
                {
                    "execution_mode": "supervised",
                    "required_checkpoints": [],
                    "human_grants": [_grant(reviewer["id"])],
                },
                "human-controlled projects require checkpoints",
            ),
            (
                {
                    "execution_mode": "manual_controlled",
                    "required_checkpoints": [
                        "repository_scope",
                        "specification",
                        "execution",
                        "validation",
                        "delivery",
                    ],
                    "human_grants": [_grant(reviewer["id"])],
                },
                "manual-controlled projects require every human checkpoint",
            ),
            (
                {
                    "execution_mode": "supervised",
                    "required_checkpoints": ["delivery"],
                    "human_grants": [_grant(reviewer["id"]), _grant(reviewer["id"])],
                },
                "duplicate human grant scope",
            ),
        )
        for body, detail in cases:
            refused = client.put(
                f"/api/v1/projects/{uuid4()}/policy-draft",
                headers=admin_headers,
                json=body,
            )
            assert refused.status_code == 422, detail
            assert refused.json()["detail"] == detail


# ---------------------------------------------------------------------------
# "already has a topology" is a conflict, on both endpoints
# ---------------------------------------------------------------------------


def test_both_topology_endpoints_answer_409_for_a_project_that_already_has_one(
    application_container: ApplicationContainer,
) -> None:
    """One defect in two copies, plus the control cases that keep the fix honest.

    ``ProjectTopologyConflict`` subclasses ``ProjectTopologyError``, so a lone
    ``except ProjectTopologyError`` gave "this project already has a topology"
    and "your request is wrong" the same 422 — telling a caller who cannot fix
    anything to go fix their request. The subclass clause has to come first, and
    the automatic endpoint carried its own copy of the defect because it
    delegates to the same creator. The two 422 assertions are what fails if the
    pair is ever collapsed or reversed.
    """

    organization_id = uuid4()
    repository_id = uuid4()

    async def agents():
        leader = await CreateAgent(application_container.agent_directory).execute(
            CreateAgentRequest(
                organization_id=organization_id,
                role=AgentRole.ORGANIZATION_LEADER,
                agentteams_resource_name="conflict-org-leader",
            ),
            idempotency_key="conflict-org-leader",
        )
        team = await CreateRepositoryAgentTeam(application_container.agent_directory).execute(
            CreateRepositoryAgentTeamRequest(
                organization_id=organization_id,
                organization_leader_id=leader.principal.id,
                repository_id=repository_id,
                leader_agentteams_resource_name="conflict-repo-leader",
                worker_agentteams_resource_names=("conflict-worker",),
            ),
            idempotency_key="conflict-repo-team",
        )
        return leader.principal, team

    organization_leader, team = asyncio.run(agents())

    def explicit(project_id, key: str, **overrides: object) -> dict:
        body = {
            "organization_id": str(organization_id),
            "project_id": str(project_id),
            "organization_leader_id": str(organization_leader.id),
            "repository_teams": [
                {
                    "repository_id": str(repository_id),
                    "leader_agent_id": str(team.leader.id),
                    "worker_agent_ids": [str(team.workers[0].id)],
                }
            ],
            "idempotency_key": key,
        }
        body.update(overrides)
        return body

    def automatic(project_id, key: str, **overrides: object) -> dict:
        body = {
            "organization_id": str(organization_id),
            "project_id": str(project_id),
            "repository_ids": [str(repository_id)],
            "idempotency_key": key,
        }
        body.update(overrides)
        return body

    with TestClient(create_app(application_container)) as client:
        admin_headers = _admin_headers(client)

        explicit_project = uuid4()
        created = client.post(
            "/api/v1/projects/topologies",
            headers=admin_headers,
            json=explicit(explicit_project, "explicit-first"),
        )
        assert created.status_code == 201
        # A fresh idempotency key, so this is the store refusing a second
        # topology for the project rather than the replay path answering.
        repeated = client.post(
            "/api/v1/projects/topologies",
            headers=admin_headers,
            json=explicit(explicit_project, "explicit-second"),
        )
        assert repeated.status_code == 409
        assert repeated.json()["detail"] == "project topology already exists"

        malformed = client.post(
            "/api/v1/projects/topologies",
            headers=admin_headers,
            json=explicit(
                uuid4(),
                "explicit-violation",
                execution_mode="auto",
                required_checkpoints=["delivery"],
            ),
        )
        assert malformed.status_code == 422
        assert malformed.json()["detail"] == "automatic projects cannot require human checkpoints"

        automatic_project = uuid4()
        auto_created = client.post(
            "/api/v1/projects/automatic-topologies",
            headers=admin_headers,
            json=automatic(automatic_project, "automatic-first"),
        )
        assert auto_created.status_code == 201
        auto_repeated = client.post(
            "/api/v1/projects/automatic-topologies",
            headers=admin_headers,
            json=automatic(automatic_project, "automatic-second"),
        )
        assert auto_repeated.status_code == 409
        assert auto_repeated.json()["detail"] == "project topology already exists"

        auto_malformed = client.post(
            "/api/v1/projects/automatic-topologies",
            headers=admin_headers,
            json=automatic(
                uuid4(),
                "automatic-violation",
                execution_mode="auto",
                required_checkpoints=["delivery"],
            ),
        )
        assert auto_malformed.status_code == 422
        assert (
            auto_malformed.json()["detail"]
            == "automatic projects cannot require human checkpoints"
        )
