"""Tests for URL parsing, cache, and entry-point discovery scenarios."""

import time
from pathlib import Path

import pytest

from repomesh.modules.repository_intelligence.application import (
    RepositoryDiscoveryService,
    extract_entry_repo_name,
    load_requirement,
    parse_user_input,
)
from repomesh.modules.repository_intelligence.application.scan_remote import (
    _extract_top_dirs,
    _find_dep_files,
    _parse_dep_file,
)
from repomesh.modules.repository_intelligence.domain import (
    AutoCard,
    RepositoryProfile,
)
from repomesh.modules.repository_intelligence.infrastructure import (
    InMemoryRepositoryCatalog,
)
from repomesh.modules.repository_intelligence.infrastructure.cache import OrgCache
from repomesh.modules.repository_intelligence.infrastructure.platform import (
    FileEntry,
    Platform,
    detect_platform,
)

# ---------------------------------------------------------------------------
# parse_user_input tests
# ---------------------------------------------------------------------------


def test_parse_user_input_single_repo() -> None:
    result = parse_user_input(
        "在 https://gitlab.metaglobal.cn/orders/order-service 里加微信支付"
    )
    assert result.url == "https://gitlab.metaglobal.cn/orders/order-service"
    assert "微信支付" in result.requirement
    assert result.entry_repo_name == "order-service"


def test_parse_user_input_group_url() -> None:
    result = parse_user_input(
        "在 https://gitlab.metaglobal.cn/orders/ 下加微信支付"
    )
    assert result.url == "https://gitlab.metaglobal.cn/orders"
    assert "微信支付" in result.requirement
    assert result.entry_repo_name is None  # only 1 path segment → group


def test_parse_user_input_github_url() -> None:
    result = parse_user_input(
        "Add payment to https://github.com/my-org/payment-service"
    )
    assert result.url == "https://github.com/my-org/payment-service"
    assert "payment" in result.requirement.lower()


def test_parse_user_input_no_url_raises() -> None:
    with pytest.raises(ValueError, match="must contain a repository URL"):
        parse_user_input("加微信支付")


def test_parse_user_input_strips_connector_words() -> None:
    result = parse_user_input(
        "在 https://gitlab.example.com/team/repo 里 fix checkout bug"
    )
    assert "fix checkout bug" in result.requirement
    assert not result.requirement.startswith("在")


# ---------------------------------------------------------------------------
# detect_platform tests
# ---------------------------------------------------------------------------


def test_detect_platform_gitlab() -> None:
    assert detect_platform("https://gitlab.metaglobal.cn/team/repo") is Platform.GITLAB


def test_detect_platform_github() -> None:
    assert detect_platform("https://github.com/org/repo") is Platform.GITHUB


def test_detect_platform_local() -> None:
    assert detect_platform("D:\\repos\\order-service") is Platform.LOCAL
    assert detect_platform("./components/hermes") is Platform.LOCAL


# ---------------------------------------------------------------------------
# File tree helper tests
# ---------------------------------------------------------------------------


def test_extract_top_dirs() -> None:
    entries = [
        FileEntry(path="src", is_dir=True),
        FileEntry(path="src/api", is_dir=True),
        FileEntry(path="src/api/orders.py", is_dir=False),
        FileEntry(path="tests", is_dir=True),
        FileEntry(path="README.md", is_dir=False),
    ]
    result = _extract_top_dirs(entries)
    assert "src" in result
    assert "src/api" in result
    assert "tests" in result
    assert "README.md" not in result


def test_find_dep_files() -> None:
    entries = [
        FileEntry(path="requirements.txt", is_dir=False),
        FileEntry(path="src/main.py", is_dir=False),
        FileEntry(path="package.json", is_dir=False),
        FileEntry(path="docs/config.json", is_dir=False),  # not root level
    ]
    result = _find_dep_files(entries)
    assert "requirements.txt" in result
    assert "package.json" in result
    # docs/config.json is not a known dep file and not at root.
    assert "docs/config.json" not in result


def test_parse_dep_file_requirements() -> None:
    content = "fastapi>=0.100\nsqlalchemy\nstripe==5.0.0\n# comment\n"
    deps = _parse_dep_file("requirements.txt", content)
    assert "fastapi" in deps
    assert "sqlalchemy" in deps
    assert "stripe" in deps


def test_parse_dep_file_package_json() -> None:
    import json

    content = json.dumps({"dependencies": {"express": "^4.18", "stripe": "^12.0"}})
    deps = _parse_dep_file("package.json", content)
    assert "express" in deps
    assert "stripe" in deps


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------


def test_cache_save_and_load(tmp_path: Path) -> None:
    cache = OrgCache(cache_dir=tmp_path / "cache")
    org_url = "https://gitlab.example.com/team"

    profiles = [
        RepositoryProfile(
            name="order-service",
            url="https://gitlab.example.com/team/order-service",
            description="Orders",
            auto_card=AutoCard(
                top_dirs=("src/api",),
                deps=("fastapi", "stripe"),
                recent_commits=("feat: checkout",),
                low_signal=False,
            ),
        ),
        RepositoryProfile(
            name="payment-service",
            url="https://gitlab.example.com/team/payment-service",
            auto_card=AutoCard(low_signal=True),
        ),
    ]

    cache.save(org_url, profiles)
    loaded = cache.load(org_url)

    assert loaded is not None
    assert len(loaded) == 2
    names = {p.name for p in loaded}
    assert "order-service" in names
    assert "payment-service" in names

    # Verify AutoCard round-trips.
    order = next(p for p in loaded if p.name == "order-service")
    assert order.auto_card is not None
    assert order.auto_card.deps == ("fastapi", "stripe")


def test_cache_returns_none_when_empty(tmp_path: Path) -> None:
    cache = OrgCache(cache_dir=tmp_path / "cache")
    assert cache.load("https://gitlab.example.com/nonexistent") is None


def test_cache_expires(tmp_path: Path) -> None:
    cache = OrgCache(cache_dir=tmp_path / "cache")
    org_url = "https://gitlab.example.com/team"
    profiles = [RepositoryProfile(name="svc", url="...")]
    cache.save(org_url, profiles)

    # Manually age the cache by rewriting _meta.json with old timestamp.
    import hashlib
    import json

    org_hash = hashlib.sha256(org_url.encode()).hexdigest()[:16]
    meta_path = tmp_path / "cache" / org_hash / "_meta.json"
    meta = json.loads(meta_path.read_text())
    meta["cached_at"] = time.time() - (48 * 3600)  # 48 hours ago
    meta_path.write_text(json.dumps(meta))

    # Should be expired with default 24h.
    result = cache.load(org_url, max_age_hours=24)
    assert result is None

    # Should still work with a higher max_age.
    result = cache.load(org_url, max_age_hours=72)
    assert result is not None


def test_cache_repo_count(tmp_path: Path) -> None:
    cache = OrgCache(cache_dir=tmp_path / "cache")
    org_url = "https://gitlab.example.com/team"
    assert cache.get_repo_count(org_url) == 0

    profiles = [
        RepositoryProfile(name="a", url="..."),
        RepositoryProfile(name="b", url="..."),
    ]
    cache.save(org_url, profiles)
    assert cache.get_repo_count(org_url) == 2


def test_cache_clear(tmp_path: Path) -> None:
    cache = OrgCache(cache_dir=tmp_path / "cache")
    cache.save("https://example.com/org", [RepositoryProfile(name="x", url="...")])
    cache.clear()
    assert cache.load("https://example.com/org") is None


# ---------------------------------------------------------------------------
# Discovery — entry-point scenario tests
# ---------------------------------------------------------------------------


class _MockLLMClient:
    def __init__(self, response: str) -> None:
        self._response = response

    def chat(self, messages, *, temperature=0.0) -> str:
        return self._response


@pytest.mark.asyncio
async def test_discovery_scenario_one_with_entry_point() -> None:
    """Scenario one: user specified entry repo, LLM finds affected repos."""

    catalog = InMemoryRepositoryCatalog()
    await catalog.add(
        RepositoryProfile(
            name="order-service",
            url="https://gitlab.example.com/team/order-service",
            description="Order management",
            auto_card=AutoCard(
                deps=("payment-sdk", "notification-client"),
                recent_commits=("feat: checkout flow",),
            ),
        )
    )
    await catalog.add(
        RepositoryProfile(
            name="payment-service",
            url="https://gitlab.example.com/team/payment-service",
            description="Payment processing",
        )
    )
    await catalog.add(
        RepositoryProfile(
            name="marketing-site",
            url="https://gitlab.example.com/team/marketing-site",
            description="Public website",
        )
    )

    import json

    llm_response = json.dumps([
        {"repository": "payment-service", "confidence": 0.85,
         "rationale": "order-service deps include payment-sdk"},
        {"repository": "notification-service", "confidence": 0.6,
         "rationale": "order-service deps include notification-client"},
    ])
    mock = _MockLLMClient(llm_response)
    service = RepositoryDiscoveryService(catalog, llm_client=mock)

    results = await service.discover(
        "改订单结算逻辑",
        entry_point="order-service",
    )

    # Entry point must be present with confidence=1.0.
    entry = [r for r in results if r.is_entry_point]
    assert len(entry) == 1
    assert entry[0].score == 1.0

    # Other repos ranked below entry.
    non_entry = [r for r in results if not r.is_entry_point]
    assert len(non_entry) >= 1
    assert non_entry[0].score < 1.0


@pytest.mark.asyncio
async def test_discovery_scenario_two_without_entry_point() -> None:
    """Scenario two: no entry point, LLM finds all relevant repos."""

    catalog = InMemoryRepositoryCatalog()
    await catalog.add(
        RepositoryProfile(name="payment-service", url="...", description="Payment")
    )
    await catalog.add(
        RepositoryProfile(name="order-service", url="...", description="Orders")
    )
    await catalog.add(
        RepositoryProfile(name="marketing-site", url="...", description="Website")
    )

    llm_response = """[
        {"repository": "payment-service", "confidence": 0.92, "rationale": "Handles payments"},
        {"repository": "order-service", "confidence": 0.80, "rationale": "Order flow"}
    ]"""
    mock = _MockLLMClient(llm_response)
    service = RepositoryDiscoveryService(catalog, llm_client=mock)

    results = await service.discover("加微信支付")

    # No entry point.
    assert not any(r.is_entry_point for r in results)
    assert len(results) == 2
    assert results[0].score == 0.92


@pytest.mark.asyncio
async def test_discovery_entry_point_in_llm_results() -> None:
    """LLM already returns the entry repo — system overrides its score to 1.0."""

    catalog = InMemoryRepositoryCatalog()
    await catalog.add(RepositoryProfile(name="order-service", url="..."))
    await catalog.add(RepositoryProfile(name="payment-service", url="..."))

    llm_response = """[
        {"repository": "order-service", "confidence": 0.7, "rationale": "Some match"},
        {"repository": "payment-service", "confidence": 0.85, "rationale": "Payment"}
    ]"""
    mock = _MockLLMClient(llm_response)
    service = RepositoryDiscoveryService(catalog, llm_client=mock)

    results = await service.discover("test", entry_point="order-service")

    # order-service should be forced to 1.0 and marked as entry point.
    all_profiles = await catalog.list()
    order_profile = next(p for p in all_profiles if p.name == "order-service")
    order = next(r for r in results if r.repository_id == order_profile.id)
    assert order.score == 1.0
    assert order.is_entry_point is True
    assert order.rationale == "User-specified entry point"


# ---------------------------------------------------------------------------
# load_requirement tests
# ---------------------------------------------------------------------------


class TestLoadRequirement:
    def test_load_from_text(self) -> None:
        result = load_requirement(requirement="修复支付回调超时")
        assert result == "修复支付回调超时"

    def test_load_from_file(self, tmp_path: Path) -> None:
        req_file = tmp_path / "req.md"
        req_file.write_text("# 需求\n\n修复支付回调超时的问题", encoding="utf-8")
        result = load_requirement(requirement_file=str(req_file))
        assert "修复支付回调超时" in result

    def test_file_takes_priority_over_text(self, tmp_path: Path) -> None:
        """When both are given, file takes priority (CLI uses mutual exclusion)."""
        req_file = tmp_path / "req.md"
        req_file.write_text("file content", encoding="utf-8")
        result = load_requirement(requirement="text content", requirement_file=str(req_file))
        assert result == "file content"

    def test_neither_given_raises(self) -> None:
        with pytest.raises(ValueError, match="必须提供需求"):
            load_requirement()

    def test_file_not_found_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_requirement(requirement_file="/nonexistent/path.md")


# ---------------------------------------------------------------------------
# extract_entry_repo_name tests
# ---------------------------------------------------------------------------


class TestExtractEntryRepoName:
    def test_single_repo_url(self) -> None:
        result = extract_entry_repo_name(
            "https://gitlab.example.com/orders/order-service"
        )
        assert result == "order-service"

    def test_group_url_returns_none(self) -> None:
        result = extract_entry_repo_name("https://gitlab.example.com/orders/")
        assert result is None

    def test_github_url(self) -> None:
        result = extract_entry_repo_name(
            "https://github.com/FudanSELab/train-ticket"
        )
        assert result == "train-ticket"


# ---------------------------------------------------------------------------
# discover with keywords parameter
# ---------------------------------------------------------------------------


class TestDiscoverWithKeywords:
    @pytest.mark.asyncio
    async def test_keywords_passed_through(self) -> None:
        """Verify discover accepts keywords without error."""
        catalog = InMemoryRepositoryCatalog()
        catalog._profiles["r1"] = RepositoryProfile(
            id="r1",
            name="payment-service",
            url="https://gitlab.example.com/payments/payment-service",
            auto_card=AutoCard(
                top_dirs=("src",),
                deps=("stripe",),
                recent_commits=("add stripe payment",),
                exposed_apis=(),
                low_signal=False,
            ),
        )
        service = RepositoryDiscoveryService(catalog)
        # Use a requirement that matches the repo name and deps for fallback.
        results = await service.discover(
            "payment stripe integration",
            keywords=["payment", "stripe"],
        )
        assert len(results) > 0
