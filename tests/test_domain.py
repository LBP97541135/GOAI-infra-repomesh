from uuid import uuid4

import pytest

from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.repository_intelligence.in_memory import InMemoryRepositoryCatalog
from repomesh.modules.repository_intelligence.service import RepositoryDiscoveryService
from repomesh.modules.runtime.mock import MockCodingAgent
from repomesh.modules.runtime.ports import CodingRunRequest, RunStatus


@pytest.mark.asyncio
async def test_discovery_returns_ranked_evidence() -> None:
    catalog = InMemoryRepositoryCatalog()
    await catalog.add(
        RepositoryProfile(
            name="checkout-service",
            url="https://github.com/example/checkout",
            description="Payment and checkout API",
            topics=("payment", "orders"),
            languages=("python",),
        )
    )
    await catalog.add(
        RepositoryProfile(
            name="marketing-site",
            url="https://github.com/example/site",
            description="Public website",
            languages=("typescript",),
        )
    )

    results = await RepositoryDiscoveryService(catalog).discover("Add payment checkout flow")

    assert len(results) == 1
    assert results[0].matched_terms == ("checkout", "payment")
    assert results[0].rationale


@pytest.mark.asyncio
async def test_mock_coding_agent_is_deterministic() -> None:
    result = await MockCodingAgent().execute(
        CodingRunRequest(
            task_id=uuid4(),
            repository_url="https://github.com/example/repo",
            instruction="Add a health endpoint",
        )
    )

    assert result.status is RunStatus.SUCCEEDED
    assert result.changed_files == ("README.md",)

