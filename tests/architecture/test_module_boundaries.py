"""Executable rules for RepoMesh module ownership and dependency direction."""

import ast
from pathlib import Path

import pytest

from repomesh.modules.project import (
    CodeAccessLevel,
    HumanControlAction,
    HumanProjectGrantView,
    HumanProjectRole,
    ProjectAgentTopologyView,
    ProjectCheckpoint,
    ProjectExecutionMode,
    TopologyAwareCheckpointFallback,
)

SOURCE_ROOT = Path(__file__).parents[2] / "src" / "repomesh"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_domain_modules_do_not_depend_on_adapters_or_persistence() -> None:
    forbidden = (
        "repomesh.api",
        "repomesh.bootstrap",
        "repomesh.integrations",
        "repomesh.persistence",
    )
    violations = []
    module_root = SOURCE_ROOT / "modules"
    domain_files = (
        path
        for path in module_root.rglob("*.py")
        if path.name == "domain.py" or "domain" in path.relative_to(module_root).parts
    )
    for path in domain_files:
        for dependency in imported_modules(path):
            if dependency.startswith(forbidden):
                violations.append(f"{path.relative_to(SOURCE_ROOT)} -> {dependency}")
    assert violations == []


def test_change_workflow_is_not_owned_by_repository_intelligence() -> None:
    implementation = SOURCE_ROOT / "modules" / "change_orchestration" / "application.py"
    assert "class PlanExecutionBridge" in implementation.read_text(encoding="utf-8")
    assert not (
        SOURCE_ROOT
        / "modules"
        / "repository_intelligence"
        / "application"
        / "plan_execution_bridge.py"
    ).exists()


def test_business_modules_publish_ownership_metadata() -> None:
    module_root = SOURCE_ROOT / "modules"
    missing = []
    for module in module_root.iterdir():
        if not module.is_dir() or module.name.startswith("__"):
            continue
        for required in ("README.md", "module.toml"):
            if not (module / required).is_file():
                missing.append(f"{module.name}/{required}")
    assert missing == []


def test_cross_module_imports_target_contracts_only() -> None:
    module_root = SOURCE_ROOT / "modules"
    violations = []
    for path in module_root.rglob("*.py"):
        relative = path.relative_to(module_root)
        if not relative.parts or relative.parts[0].startswith("__"):
            continue
        owner = relative.parts[0]
        for dependency in imported_modules(path):
            if not dependency.startswith("repomesh.modules."):
                continue
            parts = dependency.split(".")
            if len(parts) < 3:
                continue
            producer = parts[2]
            suffix = ".".join(parts[3:])
            if producer == owner:
                continue
            if suffix == "contracts" or suffix.startswith("contracts."):
                continue
            violations.append(
                f"{path.relative_to(SOURCE_ROOT)} -> {dependency}"
            )
    assert violations == []


def test_the_api_layer_reads_collaboration_only_through_its_contracts() -> None:
    """Gate #9: the room stream is a read model, not a second query planner.

    The API layer holds a Database handle and could join
    ``collaboration.messages`` or ``collaboration.room_timeline_messages``
    directly. Doing so would make the ordering rule, the dedupe rule and the
    ingest whitelist two independent opinions the moment either changed — and
    the console's would be the one nobody updated.

    Scoped to ``collaboration`` rather than declared over every module: some
    API modules legitimately mount another module's own API or application
    surface, and a blanket rule here would either fail on those or be widened
    until it asserted nothing.
    """

    api_root = SOURCE_ROOT / "api"
    violations = [
        f"{path.relative_to(SOURCE_ROOT)} -> {dependency}"
        for path in api_root.rglob("*.py")
        for dependency in imported_modules(path)
        if dependency.startswith("repomesh.modules.collaboration")
        and dependency != "repomesh.modules.collaboration.contracts"
        and not dependency.startswith("repomesh.modules.collaboration.contracts.")
    ]
    assert violations == []


def test_the_read_model_names_no_collaboration_table() -> None:
    """The stronger half of the same rule, because an import is not the only
    way in: a raw ``text("select ... from collaboration.…")`` would pass the
    import check above while doing exactly what it forbids."""

    # The two table names, plus the SQL shape that would reach any of the
    # schema's tables. Prose may name ``collaboration.messages`` — the room
    # stream's docstring does, explaining what it merges — so the marker is the
    # ``FROM`` that would make it a query rather than a sentence.
    forbidden = (
        "room_timeline_messages",
        "processed_matrix_events",
        "from collaboration.",
        "FROM collaboration.",
    )
    offenders = [
        f"{path.relative_to(SOURCE_ROOT)}: {name}"
        for path in (SOURCE_ROOT / "api").rglob("*.py")
        for name in forbidden
        if name in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


class TopologyReader:
    def __init__(self, topology) -> None:
        self.topology = topology

    async def get_view(self, project_id):
        return self.topology if self.topology.project_id == project_id else None


@pytest.mark.asyncio
async def test_missing_checkpoint_service_fails_closed_for_supervised_project() -> None:
    from uuid import uuid4

    human_id = uuid4()
    topology = ProjectAgentTopologyView(
        id=uuid4(),
        organization_id=uuid4(),
        project_id=uuid4(),
        organization_leader_id=uuid4(),
        repository_teams=(),
        execution_mode=ProjectExecutionMode.SUPERVISED,
        required_checkpoints=frozenset({ProjectCheckpoint.EXECUTION}),
        human_grants=(
            HumanProjectGrantView(
                human_principal_id=human_id,
                role=HumanProjectRole.PROJECT_SUPERVISOR,
                code_access=CodeAccessLevel.READ,
                control_actions=frozenset({HumanControlAction.APPROVE_CHECKPOINT}),
            ),
        ),
    )
    decision = await TopologyAwareCheckpointFallback(
        TopologyReader(topology)
    ).operational_gate(topology.project_id)
    assert not decision.allowed
    assert decision.reason == "checkpoint_gateway_not_configured"


def test_container_reuses_process_level_services(application_container) -> None:
    assert (
        application_container.project_checkpoint_service()
        is application_container.project_checkpoint_service()
    )
    assert application_container.topology_reader() is application_container.topology_reader()
    assert (
        application_container.specification_service()
        is application_container.specification_service()
    )
    assert (
        application_container.execution_plan_advancer()
        is application_container.execution_plan_advancer()
    )


def test_decision_history_adapter_wired_into_discovery_chain(application_container) -> None:
    from repomesh.modules.repository_intelligence.infrastructure import (
        DecisionHistoryFromChainStore,
    )
    from repomesh.modules.repository_intelligence.infrastructure.decision_history_vector import (
        DecisionHistoryVectorStore,
    )

    structural = application_container.decision_history_from_chain()
    assert isinstance(structural, DecisionHistoryFromChainStore)
    assert structural._similar is application_container.decision_chain_similarity_service()

    service = application_container.discovery_chain_service()
    wired = service._decision_history
    # L3: the hybrid vector adapter wraps the structural one as its fallback
    # when semantic retrieval is configured; otherwise the Phase-4b structural
    # adapter is wired directly (both are valid compositions — the port's
    # enhancement-not-gate rule holds either way).
    if isinstance(wired, DecisionHistoryFromChainStore):
        assert wired._similar is structural._similar
    else:
        assert isinstance(wired, DecisionHistoryVectorStore)
        assert isinstance(wired._structural, DecisionHistoryFromChainStore)
        assert wired._structural._similar is structural._similar
