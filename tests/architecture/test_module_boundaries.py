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
