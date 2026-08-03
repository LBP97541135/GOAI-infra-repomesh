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


def test_auto_card_defaults() -> Non