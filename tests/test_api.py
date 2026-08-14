import asyncio
from dataclasses import replace
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

        discovered = client.post(
            "/api/v1/discovery",
            headers=headers,
            json={"requirement": "Add payment invoice support"},
        )
        assert discovered.status_code == 200
        assert discovered.json()[0]["repository_name"] == "billing-api"
        assert discovered.json()[0]["matched_terms"] == ["invoice", "payment"]


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


def test_worker_mcp_direct_mode_is_forbidden_in_production(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_ENVIRONMENT", "production")
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

    Anything that is not github.com is treated as a self-hosted GitLab and the
    fetcher derives its API base from the submitted URL, so the body chose who
    this server talked to. 400 means the refusal happened before any egress.
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
                json={"org_url": "https://gitlab.internal.example/acme"},
            )
    finally:
        get_settings.cache_clear()

    assert metadata.status_code == 400
    assert internal.status_code == 400
    assert "allowlist" in internal.json()["detail"]


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


def test_single_repo_scan_registers_the_repository_from_its_url(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """A pasted repo URL becomes a catalog entry with a real AutoCard.

    POST /repositories already existed but makes the caller type every field;
    this one fetches the tree/deps/commits. The scan itself is stubbed — the
    test asserts the endpoint's contract, not GitHub's.
    """

    from repomesh.modules.repository_intelligence.application import scan_remote
    from repomesh.modules.repository_intelligence.domain import AutoCard, RepositoryProfile

    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    monkeypatch.setenv("REPOMESH_REPOSITORY_SCAN_ALLOWED_HOSTS", "github.com")
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

    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    monkeypatch.setenv("REPOMESH_REPOSITORY_SCAN_ALLOWED_HOSTS", "github.com")
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
                json={"repo_url": "https://gitlab.internal.example/acme/orders"},
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
    assert "allowlist" in internal.json()["detail"]
    assert metadata.status_code == 400


def test_single_repo_scan_failures_do_not_echo_the_underlying_error(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Same silence as scan-org: the caller does not learn what we reached."""

    from repomesh.modules.repository_intelligence.application import scan_remote

    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    monkeypatch.setenv("REPOMESH_REPOSITORY_SCAN_ALLOWED_HOSTS", "github.com")
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
