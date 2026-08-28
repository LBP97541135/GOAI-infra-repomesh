"""Tests for URL parsing, cache, and entry-point discovery scenarios."""

import time
from pathlib import Path

import pytest

from repomesh.modules.repository_intelligence.application import (
    RepositoryDiscoveryService,
    extract_entry_repo_name,
    identify_url_type,
    load_requirement,
    scan_org,
    scan_single_repo,
)
from repomesh.modules.repository_intelligence.application.scan import (
    _scan_exposed_apis,
)
from repomesh.modules.repository_intelligence.application.scan_remote import (
    _MAX_API_SOURCE_FILES_PER_REPO,
    _extract_top_dirs,
    _find_api_source_files,
    _find_dep_files,
    _find_deploy_files,
    _find_resource_files,
    _find_source_ref_files,
    _parse_dep_file,
    parse_user_input,
    scan_remote,
)
from repomesh.modules.repository_intelligence.domain import (
    AutoCard,
    DepEvidence,
    RepositoryProfile,
)
from repomesh.modules.repository_intelligence.infrastructure import (
    InMemoryRepositoryCatalog,
)
from repomesh.modules.repository_intelligence.infrastructure.cache import OrgCache
from repomesh.modules.repository_intelligence.infrastructure.platform import (
    FileEntry,
    Platform,
    RepoInfo,
    UrlType,
    detect_platform,
)

# ---------------------------------------------------------------------------
# parse_user_input tests
# ---------------------------------------------------------------------------


def test_parse_user_input_single_repo() -> None:
    result = parse_user_input(
        "在 https://gitlab.example.com/orders/order-service 里加微信支付"
    )
    assert result.url == "https://gitlab.example.com/orders/order-service"
    assert "微信支付" in result.requirement
    assert result.entry_repo_name == "order-service"


def test_parse_user_input_group_url() -> None:
    result = parse_user_input(
        "在 https://gitlab.example.com/orders/ 下加微信支付"
    )
    assert result.url == "https://gitlab.example.com/orders"
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
    assert detect_platform("https://gitlab.example.com/team/repo") is Platform.GITLAB


def test_detect_platform_github() -> None:
    assert detect_platform("https://github.com/org/repo") is Platform.GITHUB


def test_detect_platform_local() -> None:
    assert detect_platform("D:\\repos\\order-service") is Platform.LOCAL
    assert detect_platform("./components/hermes") is Platform.LOCAL


def test_detect_platform_names_unsupported_hosts_instead_of_guessing_gitlab() -> None:
    # The pre-fix behaviour treated every non-github.com git URL as GitLab and
    # would have pointed the fetcher at these hosts' APIs.
    assert detect_platform("https://gitee.com/org/repo") is Platform.UNSUPPORTED
    assert detect_platform("https://bitbucket.org/org/repo") is Platform.UNSUPPORTED
    assert detect_platform("https://dev.azure.com/org/repo") is Platform.UNSUPPORTED
    # A host that is neither known nor declared is UNKNOWN, not GitLab.
    assert detect_platform("https://git.example.internal/team/repo") is Platform.UNKNOWN
    assert detect_platform("git@example.org:team/repo.git") is Platform.UNKNOWN


def test_detect_platform_honours_an_explicit_platform_mapping() -> None:
    # REPOMESH_REPOSITORY_PLATFORMS lets an operator declare self-hosted hosts.
    mapping = {"git.example.internal": "gitlab"}
    assert (
        detect_platform("https://git.example.internal/team/repo", platform_map=mapping)
        is Platform.GITLAB
    )
    # A declared-but-unknown platform name stays UNKNOWN rather than guessing.
    assert (
        detect_platform(
            "https://git.example.internal/team/repo",
            platform_map={"git.example.internal": "gitea"},
        )
        is Platform.UNKNOWN
    )


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
        FileEntry(path="docs/config.json", is_dir=False),  # not a known dep file
    ]
    result = _find_dep_files(entries)
    assert "requirements.txt" in result
    assert "package.json" in result
    # config.json is not a known dependency file name.
    assert "docs/config.json" not in result


def test_find_dep_files_scans_the_whole_tree() -> None:
    """Nested build files (monorepo modules, subdirectory services) are found."""
    entries = [
        FileEntry(path="pom.xml", is_dir=False),
        FileEntry(path="services/order/pom.xml", is_dir=False),
        FileEntry(path="services/payment/pom.xml", is_dir=False),
        FileEntry(path="go.mod", is_dir=False),
        FileEntry(path="cmd/worker/go.mod", is_dir=False),
    ]
    result = _find_dep_files(entries)
    assert "services/order/pom.xml" in result
    assert "services/payment/pom.xml" in result
    assert "cmd/worker/go.mod" in result


def test_find_dep_files_applies_per_type_and_total_caps() -> None:
    """Caps keep the fetch list rate-limit friendly in big monorepos."""
    entries = [
        FileEntry(path=f"module-{i}/pom.xml", is_dir=False) for i in range(15)
    ]
    entries.extend(
        FileEntry(path=f"go-svc-{i}/go.mod", is_dir=False) for i in range(30)
    )
    entries.extend(
        FileEntry(path=f"py-{i}/requirements.txt", is_dir=False) for i in range(20)
    )
    entries.extend(
        FileEntry(path=f"js-{i}/package.json", is_dir=False) for i in range(20)
    )
    result = _find_dep_files(entries)

    # At most 10 of any one kind…
    pom_count = sum(1 for p in result if p.endswith("pom.xml"))
    go_count = sum(1 for p in result if p.endswith("go.mod"))
    assert pom_count == 10
    assert go_count == 10
    # …and at most 30 total (4 kinds × 10 would otherwise be 40).
    assert len(result) == 30


def test_parse_dep_file_requirements() -> None:
    content = "fastapi>=0.100\nsqlalchemy\nstripe==5.0.0\n# comment\n"
    result = _parse_dep_file("requirements.txt", content)
    # Legacy names keep feeding the free-text deps field…
    assert "fastapi" in result.legacy_names
    assert "sqlalchemy" in result.legacy_names
    assert "stripe" in result.legacy_names
    # …while structured BUILD evidence feeds the graph.
    assert {ev.name for ev in result.evidence} == {"fastapi", "sqlalchemy", "stripe"}
    assert all(ev.mechanism == "BUILD" for ev in result.evidence)
    assert all(ev.confidence == "confirmed" for ev in result.evidence)
    # requirements.txt declares no identity for this repo.
    assert result.identities == ()


def test_parse_dep_file_package_json() -> None:
    import json

    content = json.dumps(
        {
            "name": "order-service",
            "dependencies": {"express": "^4.18", "stripe": "^12.0"},
        }
    )
    result = _parse_dep_file("package.json", content)
    assert "express" in result.legacy_names
    assert "stripe" in result.legacy_names
    assert result.identities == ("order-service",)


def test_parse_dep_file_pom_registers_composite_and_bare_identities() -> None:
    """A Maven project answers to groupId:artifactId and the bare artifactId."""
    content = (
        "<project>"
        "<groupId>com.example</groupId>"
        "<artifactId>auth-service</artifactId>"
        "<dependencies>"
        "<dependency><groupId>org.spring</groupId>"
        "<artifactId>spring-core</artifactId><version>6.1</version></dependency>"
        "</dependencies>"
        "</project>"
    )
    result = _parse_dep_file("pom.xml", content)
    assert result.identities == ("com.example:auth-service", "auth-service")
    assert "org.spring:spring-core" in result.legacy_names
    assert {ev.name for ev in result.evidence} == {"org.spring:spring-core"}


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
                dep_evidence=(
                    DepEvidence(
                        name="fastapi",
                        mechanism="BUILD",
                        confidence="confirmed",
                    ),
                    DepEvidence(
                        name="ts-payment-service",
                        mechanism="RUNTIME_CALL",
                        confidence="confirmed",
                    ),
                ),
                identities=("com.example:order-service", "order-service"),
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

    # Verify AutoCard round-trips, including the Phase 2/3 structured fields.
    order = next(p for p in loaded if p.name == "order-service")
    assert order.auto_card is not None
    assert order.auto_card.deps == ("fastapi", "stripe")
    assert order.auto_card.dep_evidence == (
        DepEvidence(name="fastapi", mechanism="BUILD", confidence="confirmed"),
        DepEvidence(
            name="ts-payment-service",
            mechanism="RUNTIME_CALL",
            confidence="confirmed",
        ),
    )
    assert order.auto_card.identities == ("com.example:order-service", "order-service")


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
        limit=5,
        entry_point="order-service",
    )

    # The entry point lands even though no scorer named it — presence is
    # guaranteed by instruction. The score is 0.0 on purpose: nothing was
    # measured, so nothing is claimed (no forced 1.0).
    entry = [r for r in results if r.is_entry_point]
    assert len(entry) == 1
    assert entry[0].score == 0.0

    # Other repos keep their natural model scores and rank above the floor.
    non_entry = [r for r in results if not r.is_entry_point]
    assert len(non_entry) >= 1
    assert non_entry[0].score > entry[0].score


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

    results = await service.discover("加微信支付", limit=5)

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

    results = await service.discover("test", limit=5, entry_point="order-service")

    # order-service keeps its model score — no forced 1.0 — and is marked as
    # the entry point with its rationale intact.
    all_profiles = await catalog.list()
    order_profile = next(p for p in all_profiles if p.name == "order-service")
    order = next(r for r in results if r.repository_id == order_profile.id)
    assert order.score == 0.7
    assert order.is_entry_point is True
    assert order.rationale == "Some match"


@pytest.mark.asyncio
async def test_discovery_entry_point_survives_the_cut() -> None:
    """The entry point is a floor: it lands even when the N cut drops it.

    It displaces the weakest scored candidate only when the list is at
    capacity — the user's explicit pick overriding a weaker signal, which is
    the point of naming an entry point at all.
    """

    import json

    catalog = InMemoryRepositoryCatalog()
    for i in range(6):
        await catalog.add(
            RepositoryProfile(name=f"repo-{i}", url="...", description=f"topic {i}")
        )
    llm_response = json.dumps(
        [
            {"repository": f"repo-{i}", "confidence": 0.9 - i * 0.1, "rationale": f"match {i}"}
            for i in range(5)  # top five; the user's repo-5 is absent from the model
        ]
    )
    service = RepositoryDiscoveryService(catalog, llm_client=_MockLLMClient(llm_response))
    results = await service.discover("anything", entry_point="repo-5", limit=3)

    by_id = {p.id: p.name for p in await catalog.list()}
    names = [by_id[r.repository_id] for r in results]
    assert "repo-5" in names  # user pick is present despite the cut
    assert len(results) == 3  # and the cap still holds
    assert names[0] == "repo-0"  # strongest natural score still ranks first


@pytest.mark.asyncio
async def test_discovery_low_signal_empty_facade() -> None:
    """A profile with nothing beyond its name is flagged low-signal."""

    catalog = InMemoryRepositoryCatalog()
    await catalog.add(RepositoryProfile(name="mystery", url="..."))
    await catalog.add(
        RepositoryProfile(name="described", url="...", description="clear purpose")
    )
    results = await RepositoryDiscoveryService(catalog).discover(
        "mystery described", limit=5
    )
    by_id = {p.id: p.name for p in await catalog.list()}
    flags = {
        by_id[r.repository_id]: r.low_signal for r in results
    }
    assert flags["mystery"] is True
    assert flags["described"] is False


@pytest.mark.asyncio
async def test_discovery_low_signal_reuses_scan_verdict() -> None:
    """A scanned profile's verdict (AutoCard.low_signal), not the facade, rules."""

    catalog = InMemoryRepositoryCatalog()
    await catalog.add(
        RepositoryProfile(
            name="card-flagged", url="...", auto_card=AutoCard(low_signal=True)
        )
    )
    await catalog.add(
        RepositoryProfile(
            name="card-rich",
            url="...",
            auto_card=AutoCard(
                deps=("payment-sdk",), recent_commits=("feat: add gateway",)
            ),
        )
    )
    results = await RepositoryDiscoveryService(catalog).discover(
        "card-flagged card-rich", limit=5
    )
    by_id = {p.id: p.name for p in await catalog.list()}
    flags = {by_id[r.repository_id]: r.low_signal for r in results}
    assert flags["card-flagged"] is True
    assert flags["card-rich"] is False


@pytest.mark.asyncio
async def test_discovery_keyword_idf_prefers_rare_terms() -> None:
    """IDF weighting: a match on a rare term outranks one on generic
    vocabulary, even at the same match count.

    Naive counting would tie "order" and "payment" (one requirement term
    each); IDF sees "order" in three repositories and "payment" in one, so
    the discriminating term carries more of the requirement's weight.
    """

    catalog = InMemoryRepositoryCatalog()
    for name, description in [
        ("order-service", "order service"),
        ("order-management", "order management"),
        ("order-tracking", "order tracking"),
        ("payment-gateway", "payment gateway"),
        ("content-site", "content website"),
    ]:
        await catalog.add(
            RepositoryProfile(name=name, url="...", description=description)
        )

    results = await RepositoryDiscoveryService(catalog).discover(
        "order payment", limit=5
    )
    by_id = {p.id: p.name for p in await catalog.list()}
    scores = {by_id[r.repository_id]: r.score for r in results}
    assert scores["payment-gateway"] > scores["order-service"]
    assert len(results) == 4  # the three order-* repos plus payment-gateway


@pytest.mark.asyncio
async def test_discovery_keyword_score_cap() -> None:
    """The keyword-fallback ceiling is injectable, not a hardcoded 0.99."""

    catalog = InMemoryRepositoryCatalog()
    await catalog.add(
        RepositoryProfile(name="checkout", url="...", description="payment checkout")
    )
    service = RepositoryDiscoveryService(catalog, keyword_score_cap=0.8)
    results = await service.discover("payment checkout", limit=5)
    assert results[0].score == 0.8


@pytest.mark.asyncio
async def test_discovery_llm_drops_unknown_repo_with_log(caplog) -> None:
    """A model-named repo outside the catalog is dropped — with a trace.

    Silent dropping makes the panel show "fewer candidates than the model
    produced" with no way to know why; the warn log is the observability
    contract for hallucinated or mistyped names.
    """

    import json
    import logging

    catalog = InMemoryRepositoryCatalog()
    await catalog.add(
        RepositoryProfile(name="real-service", url="...", description="real")
    )
    llm_response = json.dumps(
        [
            {"repository": "real-service", "confidence": 0.8, "rationale": "real"},
            {"repository": "ghost-service", "confidence": 0.9, "rationale": "hallucinated"},
        ]
    )
    service = RepositoryDiscoveryService(catalog, llm_client=_MockLLMClient(llm_response))
    with caplog.at_level(
        logging.WARNING,
        logger="repomesh.modules.repository_intelligence.application.discovery",
    ):
        results = await service.discover("anything", limit=5)

    assert len(results) == 1  # only the real repo survives
    assert "ghost-service" in caplog.text  # and the drop is on record


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
            limit=5,
            keywords=["payment", "stripe"],
        )
        assert len(results) > 0


# ---------------------------------------------------------------------------
# identify_url_type — the console badge's single source of truth
# ---------------------------------------------------------------------------


class TestIdentifyUrlType:
    """The verdict the console renders next to the URL box.

    Every case here is a pure string judgement: if any of these ever needed a
    network call the endpoint's promise (debounce on every keystroke, no
    egress) would already be broken.
    """

    def test_group_url_is_a_group(self) -> None:
        assert identify_url_type("https://github.com/FudanSELab") is UrlType.GROUP

    def test_single_repo_url_is_a_single_repo(self) -> None:
        assert (
            identify_url_type("https://github.com/FudanSELab/train-ticket")
            is UrlType.SINGLE_REPO
        )

    def test_host_only_url_is_unknown(self) -> None:
        """No path at all names neither an org nor a repo."""
        assert identify_url_type("https://github.com") is UrlType.UNKNOWN
        assert identify_url_type("https://gitlab.example.com/") is UrlType.UNKNOWN

    def test_local_path_is_unknown(self) -> None:
        """The remote scanners cannot reach a filesystem path."""
        assert identify_url_type(r"D:\repos\order-service") is UrlType.UNKNOWN
        assert identify_url_type("not a url at all") is UrlType.UNKNOWN

    def test_verdict_agrees_with_the_name_the_scan_would_use(self) -> None:
        """The badge and the scan must not be able to disagree.

        SINGLE_REPO is defined as "extract_entry_repo_name found a name", so a
        URL badged single-repo always yields the name the registration uses.
        """

        for url in (
            "https://github.com/acme/orders",
            "https://gitlab.example.com/group/subgroup/orders",
            "https://github.com/acme/orders.git",
        ):
            assert identify_url_type(url) is UrlType.SINGLE_REPO
            assert extract_entry_repo_name(url) is not None

        for url in ("https://github.com/acme", "https://github.com"):
            assert identify_url_type(url) is not UrlType.SINGLE_REPO
            assert extract_entry_repo_name(url) is None


# ---------------------------------------------------------------------------
# scan_single_repo — the single-repo peer of scan_org
# ---------------------------------------------------------------------------


class _StubFetcher:
    """Offline stand-in for a platform fetcher; records what was asked for."""

    def __init__(
        self,
        *,
        explode: bool = False,
        resolved_name: str | None = None,
    ) -> None:
        self._explode = explode
        self._resolved_name = resolved_name
        self.tree_calls: list[str] = []
        self.resolve_calls: list[str] = []

    async def resolve_repo_name(self, url: str) -> str | None:
        self.resolve_calls.append(url)
        return self._resolved_name

    async def fetch_file_tree(self, repo_url: str) -> list[FileEntry]:
        self.tree_calls.append(repo_url)
        if self._explode:
            raise RuntimeError("connect to 10.0.0.7:443 refused")
        return [FileEntry(path="src", is_dir=True)]

    async def fetch_commits(self, repo_url: str, limit: int = 5) -> list[str]:
        return ["add wechat payment"]

    async def fetch_file_content(self, repo_url: str, file_path: str) -> str | None:
        return None


class TestScanSingleRepo:
    @pytest.mark.asyncio
    async def test_uses_the_platform_name_when_available(self) -> None:
        fetcher = _StubFetcher(resolved_name="Order Service")
        profile = await scan_single_repo("https://gitlab.example.com/orders/order-service", fetcher)

        # The registered name is the platform's own metadata, not the URL path.
        assert profile.name == "Order Service"
        assert profile.url == "https://gitlab.example.com/orders/order-service"
        assert profile.auto_card is not None
        assert profile.auto_card.recent_commits == ("add wechat payment",)
        assert fetcher.resolve_calls == ["https://gitlab.example.com/orders/order-service"]
        assert fetcher.tree_calls == ["https://gitlab.example.com/orders/order-service"]

    @pytest.mark.asyncio
    async def test_falls_back_to_the_url_when_the_platform_is_mute(self) -> None:
        fetcher = _StubFetcher()  # resolve_repo_name -> None, as for a 404
        profile = await scan_single_repo("https://github.com/acme/order-service", fetcher)

        assert profile.name == "order-service"
        assert profile.url == "https://github.com/acme/order-service"
        assert profile.auto_card is not None
        # The metadata call was still attempted before falling back.
        assert fetcher.resolve_calls == ["https://github.com/acme/order-service"]
        assert fetcher.tree_calls == ["https://github.com/acme/order-service"]

    @pytest.mark.asyncio
    async def test_group_url_is_rejected_rather_than_guessed(self) -> None:
        fetcher = _StubFetcher()
        with pytest.raises(ValueError, match="not a single-repository URL"):
            await scan_single_repo("https://github.com/acme", fetcher)
        assert fetcher.resolve_calls == ["https://github.com/acme"]
        assert fetcher.tree_calls == []

    @pytest.mark.asyncio
    async def test_failure_propagates_instead_of_registering_an_empty_card(self) -> None:
        """scan_org swallows a per-repo failure; a scan of one must not.

        An empty AutoCard registered under the user's repository name would be
        worse than an error: discovery would score it and find nothing.
        """

        fetcher = _StubFetcher(explode=True, resolved_name="order-service")
        with pytest.raises(RuntimeError):
            await scan_single_repo("https://github.com/acme/order-service", fetcher)


# ---------------------------------------------------------------------------
# scan_remote — runtime call declarations become evidence (Phase 3)
# ---------------------------------------------------------------------------


class _SourceStubFetcher:
    """Offline fetcher that serves a fixed tree and file contents."""

    def __init__(self, tree: list[FileEntry], contents: dict[str, str]) -> None:
        self._tree = tree
        self._contents = contents

    async def fetch_file_tree(self, repo_url: str) -> list[FileEntry]:
        return self._tree

    async def fetch_commits(self, repo_url: str, limit: int = 5) -> list[str]:
        return ["feat: add checkout"]

    async def fetch_file_content(self, repo_url: str, file_path: str) -> str | None:
        return self._contents.get(file_path)


class TestScanRemoteCallDeclarations:
    @pytest.mark.asyncio
    async def test_feign_declaration_becomes_runtime_call_evidence(self) -> None:
        """A declared @FeignClient name lands in deps and dep_evidence."""
        fetcher = _SourceStubFetcher(
            tree=[
                FileEntry(path="src", is_dir=True),
                FileEntry(path="src/OrderClient.java", is_dir=False),
                FileEntry(path="README.md", is_dir=False),
            ],
            contents={
                "src/OrderClient.java": (
                    '@FeignClient(name = "ts-order-service")\n'
                    "public interface OrderClient {}\n"
                ),
            },
        )
        card = await scan_remote(
            RepoInfo(
                name="ts-checkout-service",
                url="https://gitlab.example.com/team/ts-checkout-service",
            ),
            fetcher,
        )

        assert "ts-order-service" in card.deps
        assert (
            DepEvidence(
                name="ts-order-service",
                mechanism="RUNTIME_CALL",
                confidence="confirmed",
            )
            in card.dep_evidence
        )

    @pytest.mark.asyncio
    async def test_build_and_call_evidence_coexist_on_one_card(self) -> None:
        """pom deps (BUILD) and Feign targets (RUNTIME_CALL) stay separate."""
        fetcher = _SourceStubFetcher(
            tree=[
                FileEntry(path="pom.xml", is_dir=False),
                FileEntry(path="src/OrderClient.java", is_dir=False),
            ],
            contents={
                "pom.xml": (
                    "<project><groupId>com.example</groupId>"
                    "<artifactId>checkout-service</artifactId>"
                    "<dependencies><dependency><groupId>org.spring</groupId>"
                    "<artifactId>spring-core</artifactId></dependency>"
                    "</dependencies></project>"
                ),
                "src/OrderClient.java": (
                    '@FeignClient(name = "ts-order-service")\n'
                    "public interface OrderClient {}\n"
                ),
            },
        )
        card = await scan_remote(
            RepoInfo(
                name="ts-checkout-service",
                url="https://gitlab.example.com/team/ts-checkout-service",
            ),
            fetcher,
        )

        mechanisms = {ev.mechanism for ev in card.dep_evidence}
        assert mechanisms == {"BUILD", "RUNTIME_CALL"}
        assert {ev.name for ev in card.dep_evidence} == {
            "org.spring:spring-core",
            "ts-order-service",
        }


# ---------------------------------------------------------------------------
# scan_remote — per-path content cache (one fetch per file per repo)
# ---------------------------------------------------------------------------


class _CountingFetcher:
    """Offline fetcher that counts fetch_file_content attempts per path."""

    def __init__(
        self,
        tree: list[FileEntry],
        contents: dict[str, str],
        raise_on: set[str] | None = None,
    ) -> None:
        self._tree = tree
        self._contents = contents
        self._raise_on = raise_on or set()
        self.fetch_attempts: dict[str, int] = {}

    async def fetch_file_tree(self, repo_url: str) -> list[FileEntry]:
        return self._tree

    async def fetch_commits(self, repo_url: str, limit: int = 5) -> list[str]:
        return ["feat: add checkout"]

    async def fetch_file_content(self, repo_url: str, file_path: str) -> str | None:
        self.fetch_attempts[file_path] = self.fetch_attempts.get(file_path, 0) + 1
        if file_path in self._raise_on:
            raise RuntimeError(f"read {file_path} failed")
        return self._contents.get(file_path)


class TestScanRemoteContentCache:
    @pytest.mark.asyncio
    async def test_overlapping_channels_fetch_a_path_once(self) -> None:
        """package.json feeds both the BUILD and the SOURCE channel.

        Before the cache, the same file cost two API round trips per repo;
        it must now be fetched exactly once and parsed by both channels.
        """

        fetcher = _CountingFetcher(
            tree=[FileEntry(path="package.json", is_dir=False)],
            contents={
                "package.json": (
                    '{"name": "checkout-service", '
                    '"dependencies": {"express": "^4.18.0"}}'
                ),
            },
        )
        card = await scan_remote(
            RepoInfo(
                name="ts-checkout-service",
                url="https://gitlab.example.com/team/ts-checkout-service",
            ),
            fetcher,
        )

        assert fetcher.fetch_attempts == {"package.json": 1}
        # The BUILD channel still parsed the shared content into evidence.
        assert any(ev.name == "express" for ev in card.dep_evidence)

    @pytest.mark.asyncio
    async def test_failed_fetch_is_not_retried_by_a_later_channel(self) -> None:
        """A fetch that raises is cached as None, not re-attempted.

        The same package.json is selected by the BUILD and SOURCE channels;
        when the first fetch fails, the second channel must reuse the
        cached negative result instead of paying another round trip.
        """

        fetcher = _CountingFetcher(
            tree=[FileEntry(path="package.json", is_dir=False)],
            contents={},
            raise_on={"package.json"},
        )
        card = await scan_remote(
            RepoInfo(
                name="ts-checkout-service",
                url="https://gitlab.example.com/team/ts-checkout-service",
            ),
            fetcher,
        )

        assert fetcher.fetch_attempts == {"package.json": 1}
        assert card.dep_evidence == ()


# ---------------------------------------------------------------------------
# scan_remote — shared-resource config (mechanism ③)
# ---------------------------------------------------------------------------


class TestScanRemoteSharedResources:
    @pytest.mark.asyncio
    async def test_application_yml_datasource_becomes_shared_resource_evidence(
        self,
    ) -> None:
        """A datasource in application.yml → SHARED_RESOURCE evidence only.

        The resource identifier must *not* leak into the legacy ``deps``
        list: the graph's name-matching fallback would otherwise mint a
        call edge out of a database name.
        """
        fetcher = _SourceStubFetcher(
            tree=[
                FileEntry(
                    path="src/main/resources/application.yml",
                    is_dir=False,
                ),
                FileEntry(path="README.md", is_dir=False),
            ],
            contents={
                "src/main/resources/application.yml": (
                    "spring:\n"
                    "  datasource:\n"
                    "    url: jdbc:mysql://db-01:3306/orders-db\n"
                ),
            },
        )
        card = await scan_remote(
            RepoInfo(
                name="ts-order-service",
                url="https://gitlab.example.com/team/ts-order-service",
            ),
            fetcher,
        )

        assert DepEvidence(
            name="DATABASE:orders-db",
            mechanism="SHARED_RESOURCE",
            confidence="declared",
        ) in card.dep_evidence
        assert "DATABASE:orders-db" not in card.deps

    @pytest.mark.asyncio
    async def test_three_evidence_mechanisms_coexist_on_one_card(self) -> None:
        """BUILD (pom) + RUNTIME_CALL (Feign) + SHARED_RESOURCE (config)."""
        fetcher = _SourceStubFetcher(
            tree=[
                FileEntry(path="pom.xml", is_dir=False),
                FileEntry(path="src/OrderClient.java", is_dir=False),
                FileEntry(path="application.yml", is_dir=False),
            ],
            contents={
                "pom.xml": (
                    "<project><groupId>com.example</groupId>"
                    "<artifactId>checkout-service</artifactId>"
                    "<dependencies><dependency><groupId>org.spring</groupId>"
                    "<artifactId>spring-core</artifactId></dependency>"
                    "</dependencies></project>"
                ),
                "src/OrderClient.java": (
                    '@FeignClient(name = "ts-order-service")\n'
                    "public interface OrderClient {}\n"
                ),
                "application.yml": (
                    "spring:\n"
                    "  redis:\n"
                    "    host: cache-01\n"
                    "    port: 6379\n"
                ),
            },
        )
        card = await scan_remote(
            RepoInfo(
                name="ts-checkout-service",
                url="https://gitlab.example.com/team/ts-checkout-service",
            ),
            fetcher,
        )

        mechanisms = {ev.mechanism for ev in card.dep_evidence}
        assert mechanisms == {"BUILD", "RUNTIME_CALL", "SHARED_RESOURCE"}
        assert DepEvidence(
            name="REDIS:cache-01:6379",
            mechanism="SHARED_RESOURCE",
            confidence="declared",
        ) in card.dep_evidence

    def test_find_resource_files_matches_application_config_only(self) -> None:
        """k8s/Helm manifests and .env are not shared-resource evidence.

        ``.env`` files are local environment overrides (often carrying
        secrets), not a repository's declaration that it shares a resource
        — they stay out of the evidence surface.
        """
        tree = [
            FileEntry(path="src/main/resources/application.yml", is_dir=False),
            FileEntry(path="src/main/resources/application-prod.yaml", is_dir=False),
            FileEntry(path="src/main/resources/application.properties", is_dir=False),
            FileEntry(path="config/bootstrap.yml", is_dir=False),
            FileEntry(path=".env", is_dir=False),
            FileEntry(path="k8s/deployment.yaml", is_dir=False),
            FileEntry(path="helm/values.yaml", is_dir=False),
            FileEntry(path="src/main/resources/application.json", is_dir=False),
            FileEntry(path="src", is_dir=True),
        ]
        found = _find_resource_files(tree)

        assert found == [
            "config/bootstrap.yml",
            "src/main/resources/application-prod.yaml",
            "src/main/resources/application.properties",
            "src/main/resources/application.yml",
        ]


# ---------------------------------------------------------------------------
# scan_remote — deployment manifests (mechanism ④)
# ---------------------------------------------------------------------------


class TestScanRemoteDeployment:
    @pytest.mark.asyncio
    async def test_compose_depends_on_becomes_deploy_evidence(self) -> None:
        """A compose depends_on → DEPLOY evidence on the card, not in deps."""
        fetcher = _SourceStubFetcher(
            tree=[
                FileEntry(path="docker-compose.yml", is_dir=False),
                FileEntry(path="README.md", is_dir=False),
            ],
            contents={
                "docker-compose.yml": (
                    "services:\n"
                    "  checkout-service:\n"
                    "    build: .\n"
                    "    depends_on:\n"
                    "      - ts-payment-service\n"
                ),
            },
        )
        card = await scan_remote(
            RepoInfo(
                name="ts-checkout-service",
                url="https://gitlab.example.com/team/ts-checkout-service",
            ),
            fetcher,
        )

        assert DepEvidence(
            name="ts-payment-service",
            mechanism="DEPLOY",
            confidence="declared",
        ) in card.dep_evidence
        # Deployment refs are service refs, not call deps — stay out of deps.
        assert "ts-payment-service" not in card.deps

    @pytest.mark.asyncio
    async def test_k8s_labels_and_selector_land_on_the_card(self) -> None:
        """Deployment labels → deploy identities; Service selector → evidence."""
        fetcher = _SourceStubFetcher(
            tree=[
                FileEntry(path="k8s/deployment.yaml", is_dir=False),
                FileEntry(path="k8s/service.yaml", is_dir=False),
            ],
            contents={
                "k8s/deployment.yaml": (
                    "apiVersion: apps/v1\n"
                    "kind: Deployment\n"
                    "metadata:\n"
                    "  name: checkout\n"
                    "spec:\n"
                    "  template:\n"
                    "    metadata:\n"
                    "      labels:\n"
                    "        app: ts-checkout-service\n"
                ),
                "k8s/service.yaml": (
                    "apiVersion: v1\n"
                    "kind: Service\n"
                    "metadata:\n"
                    "  name: checkout-svc\n"
                    "spec:\n"
                    "  selector:\n"
                    "    app: ts-payment-service\n"
                ),
            },
        )
        card = await scan_remote(
            RepoInfo(
                name="ts-checkout-service",
                url="https://gitlab.example.com/team/ts-checkout-service",
            ),
            fetcher,
        )

        assert DepEvidence(
            name="ts-payment-service",
            mechanism="DEPLOY",
            confidence="declared",
        ) in card.dep_evidence
        assert "checkout-svc" in card.deploy_identities
        assert "ts-checkout-service" in card.deploy_identities

    @pytest.mark.asyncio
    async def test_four_evidence_mechanisms_coexist_on_one_card(self) -> None:
        """BUILD + RUNTIME_CALL + SHARED_RESOURCE + DEPLOY all on one card."""
        fetcher = _SourceStubFetcher(
            tree=[
                FileEntry(path="pom.xml", is_dir=False),
                FileEntry(path="src/OrderClient.java", is_dir=False),
                FileEntry(path="application.yml", is_dir=False),
                FileEntry(path="docker-compose.yml", is_dir=False),
            ],
            contents={
                "pom.xml": (
                    "<project><groupId>com.example</groupId>"
                    "<artifactId>checkout-service</artifactId>"
                    "<dependencies><dependency><groupId>org.spring</groupId>"
                    "<artifactId>spring-core</artifactId></dependency>"
                    "</dependencies></project>"
                ),
                "src/OrderClient.java": (
                    '@FeignClient(name = "ts-order-service")\n'
                    "public interface OrderClient {}\n"
                ),
                "application.yml": (
                    "spring:\n"
                    "  redis:\n"
                    "    host: cache-01\n"
                    "    port: 6379\n"
                ),
                "docker-compose.yml": (
                    "services:\n"
                    "  checkout-service:\n"
                    "    build: .\n"
                    "    depends_on:\n"
                    "      - ts-payment-service\n"
                ),
            },
        )
        card = await scan_remote(
            RepoInfo(
                name="ts-checkout-service",
                url="https://gitlab.example.com/team/ts-checkout-service",
            ),
            fetcher,
        )

        mechanisms = {ev.mechanism for ev in card.dep_evidence}
        assert mechanisms == {
            "BUILD",
            "RUNTIME_CALL",
            "SHARED_RESOURCE",
            "DEPLOY",
        }
        assert DepEvidence(
            name="ts-payment-service",
            mechanism="DEPLOY",
            confidence="declared",
        ) in card.dep_evidence
        assert "checkout-service" in card.deploy_identities

    def test_find_deploy_files_matches_manifests_only(self) -> None:
        """Compose + k8s/Helm manifests match; values and app config don't."""
        tree = [
            FileEntry(path="docker-compose.yml", is_dir=False),
            FileEntry(path="compose.yaml", is_dir=False),
            FileEntry(path="k8s/deployment.yaml", is_dir=False),
            FileEntry(path="helm/templates/service.yaml", is_dir=False),
            FileEntry(path="deploy/statefulset.yaml", is_dir=False),
            FileEntry(path="helm/values.yaml", is_dir=False),
            FileEntry(path="src/main/resources/application.yml", is_dir=False),
            FileEntry(path=".env", is_dir=False),
            FileEntry(path="pipeline.yaml", is_dir=False),
            FileEntry(path="scripts/deploy.sh", is_dir=False),
            FileEntry(path="src", is_dir=True),
        ]
        found = _find_deploy_files(tree)

        assert found == [
            "compose.yaml",
            "deploy/statefulset.yaml",
            "docker-compose.yml",
            "helm/templates/service.yaml",
            "k8s/deployment.yaml",
        ]


class TestScanRemoteSourceRef:
    @pytest.mark.asyncio
    async def test_gitmodules_becomes_source_evidence(self) -> None:
        """A submodule URL → SOURCE evidence on the card *and* in deps."""
        fetcher = _SourceStubFetcher(
            tree=[
                FileEntry(path=".gitmodules", is_dir=False),
                FileEntry(path="ts-common", is_dir=True),
                FileEntry(path="README.md", is_dir=False),
            ],
            contents={
                ".gitmodules": (
                    '[submodule "ts-common"]\n'
                    "\tpath = ts-common\n"
                    "\turl = https://github.com/acme/ts-common.git\n"
                ),
            },
        )
        card = await scan_remote(
            RepoInfo(
                name="ts-checkout-service",
                url="https://gitlab.example.com/team/ts-checkout-service",
            ),
            fetcher,
        )

        assert DepEvidence(
            name="ts-common",
            mechanism="SOURCE",
            confidence="confirmed",
        ) in card.dep_evidence
        # Source refs are confirmed like BUILD/RUNTIME_CALL → also in deps.
        assert "ts-common" in card.deps

    @pytest.mark.asyncio
    async def test_go_work_outside_use_lands_on_card(self) -> None:
        """``use ../ts-common`` in go.work is a confirmed source ref."""
        fetcher = _SourceStubFetcher(
            tree=[
                FileEntry(path="go.work", is_dir=False),
                FileEntry(path="go.mod", is_dir=False),
                FileEntry(path="cmd", is_dir=True),
            ],
            contents={
                "go.work": "go 1.22.0\nuse (\n\t./cmd\n\t../ts-common\n)\n",
            },
        )
        card = await scan_remote(
            RepoInfo(
                name="ts-tool",
                url="https://gitlab.example.com/team/ts-tool",
            ),
            fetcher,
        )

        assert DepEvidence(
            name="ts-common",
            mechanism="SOURCE",
            confidence="confirmed",
        ) in card.dep_evidence

    @pytest.mark.asyncio
    async def test_five_evidence_mechanisms_coexist_on_one_card(self) -> None:
        """BUILD + RUNTIME_CALL + SHARED_RESOURCE + DEPLOY + SOURCE all on one card."""
        fetcher = _SourceStubFetcher(
            tree=[
                FileEntry(path="pom.xml", is_dir=False),
                FileEntry(path="src/OrderClient.java", is_dir=False),
                FileEntry(path="application.yml", is_dir=False),
                FileEntry(path="docker-compose.yml", is_dir=False),
                FileEntry(path=".gitmodules", is_dir=False),
            ],
            contents={
                "pom.xml": (
                    "<project><groupId>com.example</groupId>"
                    "<artifactId>checkout-service</artifactId>"
                    "<dependencies><dependency><groupId>org.spring</groupId>"
                    "<artifactId>spring-core</artifactId></dependency>"
                    "</dependencies></project>"
                ),
                "src/OrderClient.java": (
                    '@FeignClient(name = "ts-order-service")\n'
                    "public interface OrderClient {}\n"
                ),
                "application.yml": (
                    "spring:\n"
                    "  redis:\n"
                    "    host: cache-01\n"
                    "    port: 6379\n"
                ),
                "docker-compose.yml": (
                    "services:\n"
                    "  checkout-service:\n"
                    "    build: .\n"
                    "    depends_on:\n"
                    "      - ts-payment-service\n"
                ),
                ".gitmodules": (
                    '[submodule "ts-common"]\n'
                    "\tpath = ts-common\n"
                    "\turl = https://github.com/acme/ts-common.git\n"
                ),
            },
        )
        card = await scan_remote(
            RepoInfo(
                name="ts-checkout-service",
                url="https://gitlab.example.com/team/ts-checkout-service",
            ),
            fetcher,
        )

        mechanisms = {ev.mechanism for ev in card.dep_evidence}
        assert mechanisms == {
            "BUILD",
            "RUNTIME_CALL",
            "SHARED_RESOURCE",
            "DEPLOY",
            "SOURCE",
        }
        assert DepEvidence(
            name="ts-common",
            mechanism="SOURCE",
            confidence="confirmed",
        ) in card.dep_evidence

    def test_find_source_ref_files_matches_expected(self) -> None:
        """Source-ref files match; README / app config / env files don't."""
        tree = [
            FileEntry(path=".gitmodules", is_dir=False),
            FileEntry(path="go.work", is_dir=False),
            FileEntry(path="package.json", is_dir=False),
            FileEntry(path="Cargo.toml", is_dir=False),
            FileEntry(path="vendor/.gitmodules", is_dir=False),
            FileEntry(path="README.md", is_dir=False),
            FileEntry(path="application.yml", is_dir=False),
            FileEntry(path=".env", is_dir=False),
            FileEntry(path="pipeline.yaml", is_dir=False),
            FileEntry(path="scripts", is_dir=True),
        ]
        found = _find_source_ref_files(tree)

        assert found == [
            ".gitmodules",
            "Cargo.toml",
            "go.work",
            "package.json",
            "vendor/.gitmodules",
        ]


# ---------------------------------------------------------------------------
# scan_remote — exposed API routes (Phase 7.1)
# ---------------------------------------------------------------------------


class TestScanRemoteExposedApis:
    @pytest.mark.asyncio
    async def test_fastapi_routes_become_exposed_apis(self) -> None:
        """HTTP method + path collected as ``framework:route`` metadata."""
        fetcher = _SourceStubFetcher(
            tree=[
                FileEntry(path="app.py", is_dir=False),
                FileEntry(path="src/routes.py", is_dir=False),
                FileEntry(path="README.md", is_dir=False),
            ],
            contents={
                "app.py": (
                    "from fastapi import FastAPI\n"
                    "app = FastAPI()\n"
                    '@app.get("/ping")\n'
                    "def ping():\n"
                    '    return {"ok": True}\n'
                ),
                "src/routes.py": (
                    "from fastapi import APIRouter\n"
                    "router = APIRouter()\n"
                    '@router.post("/orders")\n'
                    "def create():\n"
                    "    ...\n"
                ),
            },
        )
        card = await scan_remote(
            RepoInfo(
                name="ts-order-service",
                url="https://gitlab.example.com/team/ts-order-service",
            ),
            fetcher,
        )

        # Each framework regex matches independently (local behaviour): a
        # FastAPI ``@app.get(...)`` line is also caught by the Express
        # ``app.get`` pattern, and ``@router.post(...)`` by the Gin one.
        assert card.exposed_apis == (
            "fastapi:/ping",
            "express:/ping",
            "fastapi:/orders",
            "express:/orders",
            "gin:/orders",
        )
        # Routes are card metadata, never dependency evidence.
        assert "fastapi:/ping" not in card.deps
        assert card.dep_evidence == ()

    @pytest.mark.asyncio
    async def test_express_and_gin_routes_collected(self) -> None:
        """Express ``app.get`` and Gin ``r.GET`` are matched like local."""
        fetcher = _SourceStubFetcher(
            tree=[
                FileEntry(path="server.js", is_dir=False),
                FileEntry(path="main.go", is_dir=False),
            ],
            contents={
                "server.js": (
                    "const app = require('express')();\n"
                    "app.get('/health', (req, res) => res.send('ok'));\n"
                    "app.post('/orders', handler);\n"
                ),
                "main.go": (
                    "package main\n"
                    "func main() {\n"
                    "    r := gin.Default()\n"
                    '    r.GET("/api/orders", listOrders)\n'
                    "}\n"
                ),
            },
        )
        card = await scan_remote(
            RepoInfo(
                name="ts-order-service",
                url="https://gitlab.example.com/team/ts-order-service",
            ),
            fetcher,
        )

        # Sorted file order puts main.go (gin) before server.js (express).
        assert card.exposed_apis == (
            "gin:/api/orders",
            "express:/health",
            "express:/orders",
        )

    @pytest.mark.asyncio
    async def test_routes_deduplicated_across_files(self) -> None:
        """The same route declared in two files appears once."""
        fetcher = _SourceStubFetcher(
            tree=[
                FileEntry(path="app.py", is_dir=False),
                FileEntry(path="admin.py", is_dir=False),
            ],
            contents={
                "app.py": '@app.get("/ping")\n',
                "admin.py": '@app.get("/ping")\n@app.get("/health")\n',
            },
        )
        card = await scan_remote(
            RepoInfo(
                name="ts-order-service",
                url="https://gitlab.example.com/team/ts-order-service",
            ),
            fetcher,
        )

        # Within one file the regexes run per framework, so both /ping and
        # /health appear under fastapi before the express duplicates.
        assert card.exposed_apis == (
            "fastapi:/ping",
            "fastapi:/health",
            "express:/ping",
            "express:/health",
        )

    @pytest.mark.asyncio
    async def test_no_api_routes_yields_empty(self) -> None:
        """A source file with no route declarations contributes nothing."""
        fetcher = _SourceStubFetcher(
            tree=[
                FileEntry(path="app.py", is_dir=False),
                FileEntry(path="README.md", is_dir=False),
            ],
            contents={"app.py": "print('hello')\n"},
        )
        card = await scan_remote(
            RepoInfo(
                name="ts-order-service",
                url="https://gitlab.example.com/team/ts-order-service",
            ),
            fetcher,
        )

        assert card.exposed_apis == ()

    @pytest.mark.asyncio
    async def test_remote_matches_local_scan_output(self, tmp_path: Path) -> None:
        """Same source content → same exposed_apis, local and remote.

        The local scanner reads from disk; the remote scanner reads through
        the fetcher.  Both must end at the same ``framework:route`` tuple —
        this is the "local/remote口径一致" guarantee, verified end to end.
        """
        files = {
            "app.py": (
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n"
                '@app.get("/ping")\n'
                "def ping():\n"
                '    return {"ok": True}\n'
            ),
            "web/main.ts": (
                "import express from 'express';\n"
                "const app = express();\n"
                "app.get('/health', (_req, res) => res.send('ok'));\n"
            ),
        }
        # Local scan from a real directory tree.
        root = tmp_path / "repo"
        for rel, content in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        local_apis = _scan_exposed_apis(root)

        # Remote scan with the same tree + contents.
        fetcher = _SourceStubFetcher(
            tree=[FileEntry(path=rel, is_dir=False) for rel in files],
            contents=files,
        )
        card = await scan_remote(
            RepoInfo(
                name="ts-order-service",
                url="https://gitlab.example.com/team/ts-order-service",
            ),
            fetcher,
        )

        assert local_apis == ("fastapi:/ping", "express:/ping", "express:/health")
        assert card.exposed_apis == local_apis

    def test_find_api_source_files_matches_suffixes_and_ignores_dirs(self) -> None:
        """Source suffixes match; ignored dirs and non-source files don't."""
        tree = [
            FileEntry(path="app.py", is_dir=False),
            FileEntry(path="src/main.ts", is_dir=False),
            FileEntry(path="cmd/server/main.go", is_dir=False),
            FileEntry(path="web/static/app.js", is_dir=False),
            FileEntry(path="node_modules/pkg/index.js", is_dir=False),
            FileEntry(path="dist/bundle.js", is_dir=False),
            FileEntry(path=".venv/lib/site-packages/pkg/main.py", is_dir=False),
            FileEntry(path="README.md", is_dir=False),
            FileEntry(path="data/config.json", is_dir=False),
            FileEntry(path="src", is_dir=True),
            FileEntry(path="node_modules", is_dir=True),
        ]
        found = _find_api_source_files(tree)

        assert found == [
            "app.py",
            "cmd/server/main.go",
            "src/main.ts",
            "web/static/app.js",
        ]

    def test_find_api_source_files_caps_fetch_list(self) -> None:
        """The fetch list stays rate-limit friendly in big monorepos."""
        tree = [
            FileEntry(path=f"svc-{i}/main.py", is_dir=False) for i in range(40)
        ]
        found = _find_api_source_files(tree)

        assert len(found) == _MAX_API_SOURCE_FILES_PER_REPO
        assert found == sorted(found)


# ---------------------------------------------------------------------------
# scan_org — fork filtering and per-repo scan failure status
# ---------------------------------------------------------------------------


class _OrgStubFetcher:
    """Offline stand-in for scan_org: lists repos, scans them on demand."""

    def __init__(self, repos: list[RepoInfo]) -> None:
        self._repos = repos
        self._explode_on: set[str] = set()

    def make_repo_unreadable(self, name: str) -> None:
        self._explode_on.add(name)

    async def list_repos(self, group_url: str) -> list[RepoInfo]:
        return self._repos

    async def fetch_file_tree(self, repo_url: str) -> list[FileEntry]:
        if any(name in repo_url for name in self._explode_on):
            raise RuntimeError("rate limited")
        return [FileEntry(path="src", is_dir=True)]

    async def fetch_commits(self, repo_url: str, limit: int = 5) -> list[str]:
        return ["fix checkout"]

    async def fetch_file_content(self, repo_url: str, file_path: str) -> str | None:
        return None


def _repo(
    name: str, *, fork: bool = False, archived: bool = False, empty: bool = False
) -> RepoInfo:
    return RepoInfo(
        name=name,
        url=f"https://gitlab.example.com/team/{name}",
        description=f"Service {name}",
        fork=fork,
        archived=archived,
        empty=empty,
    )


class TestScanOrg:
    @pytest.mark.asyncio
    async def test_fork_repos_excluded_by_default(self) -> None:
        """A fork duplicates its upstream's service; default scan excludes it."""
        fetcher = _OrgStubFetcher([
            _repo("order-service"),
            _repo("order-service-fork", fork=True),
        ])
        profiles = await scan_org("https://gitlab.example.com/team", fetcher)

        assert [p.name for p in profiles] == ["order-service"]

    @pytest.mark.asyncio
    async def test_include_forks_opt_in(self) -> None:
        """REPOMESH_REPOSITORY_SCAN_INCLUDE_FORKS=true brings forks in."""
        fetcher = _OrgStubFetcher([
            _repo("order-service"),
            _repo("order-service-fork", fork=True),
        ])
        profiles = await scan_org(
            "https://gitlab.example.com/team", fetcher, include_forks=True
        )

        assert {p.name for p in profiles} == {"order-service", "order-service-fork"}

    @pytest.mark.asyncio
    async def test_archived_and_empty_always_skipped(self) -> None:
        """Archived/empty are platform facts — no switch brings them back."""
        fetcher = _OrgStubFetcher([
            _repo("order-service"),
            _repo("retired", archived=True),
            _repo("blank", empty=True),
        ])
        profiles = await scan_org(
            "https://gitlab.example.com/team", fetcher, include_forks=True
        )

        assert [p.name for p in profiles] == ["order-service"]

    @pytest.mark.asyncio
    async def test_unreadable_repo_marked_failed_not_empty_card(self) -> None:
        """A per-repo failure must not become a plausible low-signal card.

        scan_org still returns the profile (so the caller can count it) but
        marks ``scan_status="failed"`` with no card — registration and
        discovery both skip it downstream.
        """
        fetcher = _OrgStubFetcher([
            _repo("order-service"),
            _repo("broken-service"),
        ])
        fetcher.make_repo_unreadable("broken-service")
        profiles = await scan_org("https://gitlab.example.com/team", fetcher)

        by_name = {p.name: p for p in profiles}
        assert by_name["order-service"].scan_status == "ok"
        assert by_name["order-service"].auto_card is not None
        assert by_name["broken-service"].scan_status == "failed"
        assert by_name["broken-service"].auto_card is None
