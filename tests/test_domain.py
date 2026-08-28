import json
from pathlib import Path
from uuid import uuid4

import pytest

from repomesh.integrations.coding_agents.mock import MockCodingAgent
from repomesh.modules.agent_runtime.ports import CodingRunRequest, RunStatus
from repomesh.modules.repository_intelligence.application import (
    RepositoryDiscoveryService,
    infer_languages,
    infer_name,
    scan_repo,
)
from repomesh.modules.repository_intelligence.domain import (
    AutoCard,
    RepositoryProfile,
)
from repomesh.modules.repository_intelligence.infrastructure import (
    InMemoryRepositoryCatalog,
)

# ---------------------------------------------------------------------------
# Domain model tests
# ---------------------------------------------------------------------------


def test_repository_profile_description_defaults_to_empty() -> None:
    profile = RepositoryProfile(name="svc", url="https://github.com/example/svc")
    assert profile.description == ""
    assert profile.auto_card is None


def test_repository_profile_with_auto_card() -> None:
    card = AutoCard(
        top_dirs=("src/api", "src/models"),
        deps=("fastapi", "stripe"),
        recent_commits=("feat: add payment endpoint",),
        exposed_apis=("fastapi:/api/v1/charge",),
        low_signal=False,
    )
    profile = RepositoryProfile(
        name="payment-service",
        url="https://github.com/example/payment",
        auto_card=card,
    )
    assert profile.auto_card is card
    # searchable_text should incorporate auto_card fields.
    assert "payment-service" in profile.searchable_text
    assert "src/api" in profile.searchable_text
    assert "stripe" in profile.searchable_text
    assert "feat: add payment endpoint" in profile.searchable_text


def test_auto_card_defaults() -> None:
    card = AutoCard()
    assert card.top_dirs == ()
    assert card.deps == ()
    assert card.recent_commits == ()
    assert card.exposed_apis == ()
    assert card.low_signal is False


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

    results = await RepositoryDiscoveryService(catalog).discover(
        "Add payment checkout flow", limit=5
    )

    assert len(results) == 1
    assert results[0].matched_terms == ("checkout", "payment")
    assert results[0].rationale


def test_scan_repo_builds_auto_card(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sample"\ndependencies = ["fastapi"]\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")

    card = scan_repo(tmp_path)

    assert "src" in card.top_dirs
    assert "fastapi" in card.deps


def test_infer_repository_metadata(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"name": "web-app"}), encoding="utf-8")
    (tmp_path / "main.ts").write_text("export {};\n", encoding="utf-8")

    assert infer_name(str(tmp_path)) == tmp_path.name
    assert "javascript" in infer_languages(tmp_path)


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
