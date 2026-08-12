"""Issue intake endpoint (contract v0.3 §1): POST /api/v1/issues.

Behavioral coverage over HTTP: auth, actor validation (404/403), creation
(201 with the v0.2 §2 issue projection), idempotent replay (200, same issue,
no duplicate audit), and the honest-draft shape (empty DAG, phase=plan).
"""

import asyncio
from uuid import uuid4, uuid5

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from repomesh.bootstrap.app import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.repository_intelligence.application.issue_intake import (
    ISSUE_INTAKE_NAMESPACE,
)
from repomesh.persistence.models.platform import AuditEventRecord
from repomesh.settings import get_settings

HEADERS = {"Authorization": "Bearer internal-secret"}


def _seed_agents(container: ApplicationContainer):
    from repomesh.modules.agent_directory.application import (
        CreateAgent,
        CreateAgentRequest,
    )

    organization_id = uuid4()
    second_organization_id = uuid4()

    async def seed():
        creator = CreateAgent(container.agent_directory)
        leader = await creator.execute(
            CreateAgentRequest(
                organization_id=organization_id,
                role=AgentRole.ORGANIZATION_LEADER,
                agentteams_resource_name="intake-org-leader",
            ),
            idempotency_key="intake-org-leader",
        )
        # A non-leader principal for the 403 case: repository leaders sit under
        # the organization leader and require a repository + paths (role policy).
        repo_leader = await creator.execute(
            CreateAgentRequest(
                organization_id=organization_id,
                role=AgentRole.REPOSITORY_LEADER,
                agentteams_resource_name="intake-repo-leader",
                leader_agent_id=leader.principal.id,
                repository_id=uuid4(),
                responsibility_paths=("src/**",),
            ),
            idempotency_key="intake-repo-leader",
        )
        # A second workspace's leader for the keyspace-scoping cases (S-5).
        second_leader = await creator.execute(
            CreateAgentRequest(
                organization_id=second_organization_id,
                role=AgentRole.ORGANIZATION_LEADER,
                agentteams_resource_name="intake-second-org-leader",
            ),
            idempotency_key="intake-second-org-leader",
        )
        return leader.principal.id, repo_leader.principal.id, second_leader.principal.id

    leader_id, repo_leader_id, second_leader_id = asyncio.run(seed())
    return (
        organization_id,
        leader_id,
        repo_leader_id,
        second_organization_id,
        second_leader_id,
    )


def _audit_count(container: ApplicationContainer) -> int:
    async def count():
        async with container.database.transaction() as session:
            result = await session.execute(
                select(func.count())
                .select_from(AuditEventRecord)
                .where(AuditEventRecord.event_type == "IssueIntakeCreated")
            )
            return result.scalar_one()

    return asyncio.run(count())


def test_issue_intake_over_http(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    get_settings.cache_clear()
    try:
        (
            organization_id,
            leader_id,
            non_leader_id,
            second_organization_id,
            second_leader_id,
        ) = _seed_agents(application_container)
        with TestClient(create_app(application_container)) as client:
            payload = {
                "requirement_text": "结算页支持满额免运费门槛",
                "created_by_agent_id": str(leader_id),
                "idempotency_key": "intake-test-key-1",
                # Optional cross-check field (S-4): matching org passes.
                "organization_id": str(organization_id),
            }

            # Auth is required on the write path.
            assert client.post("/api/v1/issues", json=payload).status_code == 401

            # Unknown actor → 404; non-leader actor → 403.
            unknown = {**payload, "created_by_agent_id": str(uuid4())}
            assert (
                client.post("/api/v1/issues", json=unknown, headers=HEADERS).status_code
                == 404
            )
            non_leader = {**payload, "created_by_agent_id": str(non_leader_id)}
            assert (
                client.post("/api/v1/issues", json=non_leader, headers=HEADERS).status_code
                == 403
            )

            # Whitespace-only text passes pydantic min_length but not the service.
            blank = {**payload, "requirement_text": "   "}
            assert (
                client.post("/api/v1/issues", json=blank, headers=HEADERS).status_code
                == 422
            )

            # S-5: single-character keys are rejected at the model layer.
            short_key = {**payload, "idempotency_key": "1"}
            assert (
                client.post("/api/v1/issues", json=short_key, headers=HEADERS).status_code
                == 422
            )

            # S-4: a body organization_id that disagrees with the actor's
            # workspace is a cross-organization subject — rejected, not
            # silently attributed.
            cross_org = {**payload, "organization_id": str(second_organization_id)}
            assert (
                client.post("/api/v1/issues", json=cross_org, headers=HEADERS).status_code
                == 403
            )

            # First creation: 201 with the §2 issue projection (honest draft).
            created = client.post("/api/v1/issues", json=payload, headers=HEADERS)
            assert created.status_code == 201
            issue = created.json()
            assert issue["state"] == "open"  # §2.1 rule 4: virtual draft
            assert issue["phase"] == "plan"
            assert issue["phase_note"] == "计划 v1 待物化"
            assert issue["requirement_text"] == payload["requirement_text"]
            assert issue["organization_id"] == str(organization_id)
            assert issue["opened_by_agent_id"] == str(leader_id)
            assert issue["opened_by_name"] == "intake-org-leader"
            assert issue["round_count"] == 0
            assert issue["issue_key"] is None  # no project registry (v0.2 §0)
            assert _audit_count(application_container) == 1

            # Idempotent replay: 200, byte-identical issue, no second audit row.
            replay = client.post("/api/v1/issues", json=payload, headers=HEADERS)
            assert replay.status_code == 200
            assert replay.json()["issue_id"] == issue["issue_id"]
            assert _audit_count(application_container) == 1

            # A different key is a different issue even with the same text.
            second = client.post(
                "/api/v1/issues",
                json={**payload, "idempotency_key": "intake-test-key-2"},
                headers=HEADERS,
            )
            assert second.status_code == 201
            assert second.json()["issue_id"] != issue["issue_id"]
            assert _audit_count(application_container) == 2

            # The new issues are visible to the list read model.
            listed = client.get("/api/v1/issues", headers=HEADERS).json()
            assert {item["issue_id"] for item in listed["issues"]} >= {
                issue["issue_id"],
                second.json()["issue_id"],
            }

            # S-5: the same key in another workspace is a different issue —
            # the keyspace is organization-scoped, so a guessed or reused key
            # can never replay (and read back) another workspace's issue.
            other_ws = client.post(
                "/api/v1/issues",
                json={
                    "requirement_text": "另一工作区的同键需求",
                    "created_by_agent_id": str(second_leader_id),
                    "idempotency_key": payload["idempotency_key"],
                },
                headers=HEADERS,
            )
            assert other_ws.status_code == 201
            assert other_ws.json()["issue_id"] != issue["issue_id"]
            assert other_ws.json()["organization_id"] == str(second_organization_id)

            # S-5 replay-ownership guard: a snapshot sitting where this
            # workspace's derivation lands but created by another workspace
            # (legacy global-namespace row) must not be replayable — 403 and
            # no projection in the body.
            legacy_key = "legacy-shared-key"
            legacy_project = uuid5(
                ISSUE_INTAKE_NAMESPACE, f"{second_organization_id}:{legacy_key}"
            )
            asyncio.run(
                application_container.plan_snapshot_store().save(
                    project_id=legacy_project,
                    plan_version=1,
                    engineering_spec="",
                    contracts=[],
                    task_dag=[],
                    execution_batches=[],
                    graph_edges=[],
                    created_by_agent_id=leader_id,  # owned by the first workspace
                    execution_plan_id=None,
                    requirement_text="遗留全局键行",
                )
            )
            foreign_replay = client.post(
                "/api/v1/issues",
                json={
                    "requirement_text": "尝试重放他人 issue",
                    "created_by_agent_id": str(second_leader_id),
                    "idempotency_key": legacy_key,
                },
                headers=HEADERS,
            )
            assert foreign_replay.status_code == 403
            assert "issue_id" not in foreign_replay.json()
    finally:
        get_settings.cache_clear()
