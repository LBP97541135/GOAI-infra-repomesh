import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from repomesh.shared.domain import new_id


def tokenize(text: str) -> frozenset[str]:
    return frozenset(token.lower() for token in re.findall(r"[\w-]+", text, re.UNICODE))


#: The six dependency-evidence mechanisms
#: (docs/chenwenhui/仓库扫描链路问题清单-2026-08-25.md §6-机制通用表).
#: Every graph edge names the single mechanism that proved it.
Mechanism = Literal[
    "BUILD",  # ① 构建期依赖：pom/build.gradle/package.json/go.mod…
    "RUNTIME_CALL",  # ② 运行时调用声明：Feign/Dubbo/gRPC target…
    "SHARED_RESOURCE",  # ③ 数据/资源共享：datasource/MQ/Redis/bucket 配置
    "DEPLOY",  # ④ 部署拓扑：compose/k8s/Helm/CI
    "SOURCE",  # ⑤ 源码引用：workspace/submodule/跨仓 import
    "OBSERVED",  # ⑥ 运行时观测（保留位：外部平台适配器）
]


@dataclass(frozen=True, slots=True)
class DepEvidence:
    """One structured dependency fact extracted from a repository.

    Replaces the free-text ``deps`` strings: instead of guessing from a
    string, the scan records *which identifier* was seen, *which mechanism*
    proved it, and *how much to trust* the resulting edge.

    ``name`` — the identifier as written in the evidence file (a Maven
    artifactId, a ``spring.application.name``, a Feign target, a go module
    path, a workspace project name…). The graph resolves it to a catalog
    repository through the service registry; identifiers that resolve to
    nothing (public libraries, external services) simply produce no edge.

    ``confidence`` — ``confirmed`` for hard evidence (mechanisms ① ② ⑤),
    the only class that participates in topological ordering; ``declared``
    for self-declared configuration (mechanisms ③ ④), a discovery hint
    only. The legacy ``possible`` string-guess class never originates from
    an evidence event.
    """

    name: str
    mechanism: Mechanism
    confidence: Literal["confirmed", "declared"]


@dataclass(frozen=True, slots=True)
class AutoCard:
    """Compact repository snapshot used during repository discovery.

    ``dep_evidence`` is the structured successor of ``deps``: each entry
    records one dependency fact with its mechanism and confidence (Phase
    1.2). ``deps`` stays as the legacy free-text list so pre-evidence scans
    and the keyword discovery path keep working unchanged.
    """

    top_dirs: tuple[str, ...] = ()
    deps: tuple[str, ...] = ()
    dep_evidence: tuple[DepEvidence, ...] = ()
    identities: tuple[str, ...] = ()
    """Identifiers this repository declares for itself in its build files.

    Maven ``groupId:artifactId`` (plus bare artifactId), npm package name,
    go module path, PEP 508 distribution name (Phase 2). They feed the
    service registry as aliases, so another repository's BUILD evidence
    that names this identifier resolves back to this repository. The
    authoritative platform name always wins over these self-declared
    aliases on collision.
    """
    deploy_identities: tuple[str, ...] = ()
    """Identifiers this repository declares in its deployment manifests.

    Compose service names, k8s workload ``app`` labels and Service
    ``metadata.name`` values (Phase 5). They feed the service registry as
    aliases exactly like ``identities``, so another repository's DEPLOY
    reference (``depends_on``, a Service selector) that names them
    resolves back to this repository. The authoritative platform name
    always wins over these self-declared aliases on collision.
    """
    recent_commits: tuple[str, ...] = ()
    exposed_apis: tuple[str, ...] = ()
    low_signal: bool = False


@dataclass(frozen=True, slots=True)
class RepositoryProfile:
    name: str
    url: str
    description: str = ""
    topics: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    auto_card: AutoCard | None = None
    scan_status: Literal["ok", "failed", "skipped"] = "ok"
    """How the scan that produced this profile ended.

    ``ok`` — the card is trustworthy (the default; the vast majority).
    ``failed`` — the scan could not read the repository, so ``auto_card``
    is ``None`` and the profile must never be registered as if it were a
    real repository.
    ``skipped`` — deliberately not scanned (reserved; nothing sets it yet).

    The field is a transient scan result, not a persisted repository fact:
    failed profiles are filtered out before registration, so the catalog
    never stores anything but ``ok`` rows.
    """
    test_commands: tuple[str, ...] = ()
    """How this repository verifies itself, in its own words (defect A-19).

    The integration LLM does not emit verification commands, so a plan's
    ``TaskNode.tests`` arrives empty and the console materialize path had
    nothing to put there — every console round dispatched ``testCommands: []``
    and the Runner dutifully ran nothing. The commands are a property of the
    repository, not of a requirement, so this is where they belong: one
    catalog row states once how its repository is checked, and every round
    over that repository inherits it.

    Empty is honest and stays legal: a repository nobody has told us how to
    test yields tasks with no verification, and delivery refuses the
    unverified candidate downstream rather than this field inventing a
    plausible ``pytest``.
    """
    test_paths: tuple[str, ...] = ()
    """Where this repository keeps the files its verification commands read.

    Defect A-21, and the other half of ``test_commands``: supplying the command
    without the path is a trap. A Worker's allowed paths come from its
    responsibility paths — ``src/checkout/**`` — while ``run_tests.py``
    discovers from the repository root's ``tests/``. So the compliant agent
    wrote the test where the command looks, and the path guard voided the
    entire run (``changed_path_denied: tests/test_discount.py``, commitSha
    null). The evading agent hid the test under ``src/`` where the command
    never finds it. Two agents, both dead ends, because the permission and the
    verification described different repositories.

    Added to a task's allowed paths, never substituted for them: a repository
    saying where its tests live cannot be a way to widen what a Worker may
    touch elsewhere.
    """
    id: UUID = field(default_factory=new_id)
    profiled_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def searchable_text(self) -> str:
        values = [self.name, self.description, *self.topics, *self.languages]
        if self.auto_card is not None:
            values.extend(self.auto_card.top_dirs)
            values.extend(self.auto_card.deps)
            values.extend(
                evidence.name for evidence in self.auto_card.dep_evidence
            )
            values.extend(self.auto_card.identities)
            values.extend(self.auto_card.recent_commits)
            values.extend(self.auto_card.exposed_apis)
        return " ".join(values)


@dataclass(frozen=True, slots=True)
class DiscoveryEvidence:
    repository_id: UUID
    matched_terms: tuple[str, ...]
    score: float
    rationale: str
    is_entry_point: bool = False
