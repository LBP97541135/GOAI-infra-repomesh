"""API-level tests for repository_intelligence graph projections (PR-5).

PR-5 frontends render from the unified graph returned by ``/integration``:
the top-level ``execution_batches`` / ``contracts`` / ``task_dag`` fields must
be exactly the graph's materialised projections (read graph ≡ projection
columns at the API boundary). The version-diff endpoint (PR-4) projects the
graph change between two immutable snapshots.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from repomesh.bootstrap import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.repository_intelligence.infrastructure import (
    PlanSnapshotStore,
)
from repomesh.settings import get_settings


class StubLLM:
    """Returns a canned integrated-plan JSON (LLM-only path)."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[list[dict[str, str]]] = []

    def chat(
        self, messages: list[dict[str, str]], *, temperature: float = 0.0
    ) -> str:
        self.calls.append(messages)
        return self._response


_LLM_PLAN = json.dumps(
    {
        "engineering_spec": "Connect A and B through contract API1",
        "contracts": [
            {
                "producer": "A",
                "consumer": "B",
                "interface": "API1",
                "agreement": "ok",
            }
        ],
        "task_dag": [
            {
                "repository": "A",
                "instruction": "change A",
                "depends_on": [],
                "parallelizable_with": [],
            },
            {
                "repository": "B",
                "instruction": "change B",
                "depends_on": ["A"],
                "parallelizable_with": [],
            },
        ],
    }
)


def _integration_payload() -> dict:
    result = {
        "repository": "",
        "status": "REQUIRED",
        "confidence": 0.9,
        "reason": "confirmed repo",
        "plan_summary": "change repo",
        "plan": {
            "changed_apis": ["/api"],
            "changed_modules": ["src"],
            "depends_on": [],
            "impacts": [],
            "risk": "low",
        },
        "missing_dependencies": [],
    }

    def with_repo(name: str, depends_on: list[str]) -> dict:
        item = {**result, "repository": name, "plan_summary": f"change {name}"}
        item["plan"] = {**item["plan"], "depends_on": depends_on}
        return item

    return {
        "requirement": "实现用户登录功能",
        "confirmation": {
            "required": [with_repo("A", []), with_repo("B", ["A"])],
            "maybe": [],
            "excluded": [],
            "final_repos": ["A", "B"],
        },
    }


def test_integration_returns_graph_with_matching_projections(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """PR-5: /integration carries the unified graph, and its materialised
    projections equal the top-level fields the frontend previously spliced."""
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    get_settings.cache_clear()
    application_container = replace(
        application_container, llm_client=StubLLM(_LLM_PLAN)
    )

    try:
        with TestClient(create_app(application_container)) as client:
            response = client.post(
                "/api/v1/integration",
                json=_integration_payload(),
                headers={"Authorization": "Bearer internal-secret"},
            )
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200, response.text
    body = response.json()
    graph = body["graph"]
    assert graph is not None
    assert graph["plan_version"] == 1  # placeholder; snapshot rows own versions

    # Read graph ≡ projection columns at the API boundary.
    assert graph["execution_batches"] == body["execution_batches"] == [
        ["A"],
        ["B"],
    ]
    assert graph["contracts"] == body["contracts"] == [
        {
            "producer": "A",
            "consumer": "B",
            "interface": "API1",
            "agreement": "ok",
        }
    ]
    graph_deps = {t["repository"]: t["depends_on"] for t in graph["task_dag"]}
    top_deps = {t["repository"]: list(t["depends_on"]) for t in body["task_dag"]}
    assert graph_deps == top_deps == {"A": [], "B": ["A"]}

    # Edges serialise by alias ("from" not "from_") and carry metadata.
    assert graph["edges"] == [
        {
            "from": "A",
            "to": "B",
            "status": "confirmed",
            "source": "llm",
            "interface": "API1",
            "agreement": "ok",
        }
    ]
    # Nodes carry instructions for the timeline cards.
    instructions = {n["repository"]: n["instruction"] for n in graph["nodes"]}
    assert instructions == {"A": "change A", "B": "change B"}


async def _save_snapshot(
    store: PlanSnapshotStore,
    *,
    project_id: UUID,
    version: int,
    repos: list[str],
    edges: list[dict],
) -> None:
    await store.save(
        project_id=project_id,
        plan_version=version,
        engineering_spec=f"plan v{version}",
        contracts=[],
        task_dag=[
            {"repository": repo, "instruction": f"change {repo}", "tests": []}
            for repo in repos
        ],
        execution_batches=[repos],
        graph_edges=edges,
    )


def test_diff_endpoint_projects_graph_between_snapshot_versions(
    application_container: ApplicationContainer,
) -> None:
    """GET /plans/{project_id}/diff projects the graph change between two
    immutable snapshots (PR-4 endpoint consumed by the PR-5 timeline)."""
    store = PlanSnapshotStore(application_container.database)
    project_id = uuid4()
    asyncio.run(
        _save_snapshot(
            store, project_id=project_id, version=1, repos=["A", "B"], edges=[]
        )
    )
    asyncio.run(
        _save_snapshot(
            store,
            project_id=project_id,
            version=2,
            repos=["A", "B", "C"],
            edges=[
                {
                    "from": "A",
                    "to": "C",
                    "status": "confirmed",
                    "source": "llm",
                    "interface": "API2",
                    "agreement": "ok",
                }
            ],
        )
    )

    with TestClient(create_app(application_container)) as client:
        response = client.get(
            f"/api/v1/plans/{project_id}/diff", params={"from": 1, "to": 2}
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["from_version"] == 1
    assert body["to_version"] == 2
    assert body["added_edges"] == [
        {
            "from": "A",
            "to": "C",
            "status": "confirmed",
            "source": "llm",
            "interface": "API2",
            "agreement": "ok",
        }
    ]
    assert body["removed_edges"] == []
    assert body["added_repos"] == ["C"]
    assert body["affected_repos"] == ["C"]


def test_diff_endpoint_404_for_missing_version(
    application_container: ApplicationContainer,
) -> None:
    """Missing versions 404; the endpoint is a pure read projection."""
    store = PlanSnapshotStore(application_container.database)
    project_id = uuid4()
    asyncio.run(
        _save_snapshot(
            store, project_id=project_id, version=1, repos=["A"], edges=[]
        )
    )

    with TestClient(create_app(application_container)) as client:
        response = client.get(
            f"/api/v1/plans/{project_id}/diff", params={"to": 2}
        )

    assert response.status_code == 404
