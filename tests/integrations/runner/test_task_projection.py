from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from repomesh.integrations.runner import (
    RunnerContextMaterializer,
    RunnerTaskProjectionDenied,
    RunnerTaskProjectionRequest,
    RunnerTaskProjector,
)
from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.capability_management import PresetCapabilityAssembler
from repomesh.modules.capability_management.contracts import AgentCapabilityBundle
from repomesh.modules.context.contracts import ExecutionContextGrant
from repomesh.modules.specification.contracts import (
    CodingAgentPackage,
    RenderedSpecification,
)
from repomesh_runner.context_verifier import WorkspaceContextVerifier
from repomesh_runner.executor import _parse_porcelain

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64


def scenario(tmp_path: Path):
    organization_id = uuid4()
    project_id = uuid4()
    repository_id = uuid4()
    task_id = uuid4()
    worker_id = uuid4()
    run_id = uuid4()
    worker = AgentPrincipalView(
        id=worker_id,
        organization_id=organization_id,
        role=AgentRole.WORKER,
        leader_agent_id=uuid4(),
        repository_id=repository_id,
        responsibility_paths=("src/**", "tests/**"),
        agentteams_resource_name="runner-worker",
        status=AgentPrincipalStatus.ACTIVE,
    )
    package = CodingAgentPackage(
        project_id=project_id,
        repository_id=repository_id,
        task_id=task_id,
        worker_agent_id=worker_id,
        instruction="Fix pricing rounding",
        acceptance=("pricing tests pass",),
        constraints=("keep old API",),
        dependencies=(),
        interface_changes=(),
        allowed_paths=("src/**", "tests/**"),
        forbidden_paths=("src/legacy/**",),
        test_commands=("pytest tests/pricing",),
        context_files=(
            RenderedSpecification(
                specification_id=uuid4(),
                version_id=uuid4(),
                version=1,
                source_content_hash=HASH_A,
                rendered_content_hash=HASH_B,
                mount_path=".repomesh/context/current-task.md",
                mime_type="text/markdown",
                content="# Current task\n\nFix pricing rounding.\n",
            ),
        ),
        content_hash=HASH_C,
    )
    grant = ExecutionContextGrant(
        bundle_id=uuid4(),
        project_id=project_id,
        run_id=run_id,
        agent_id=worker_id,
        repository_id=repository_id,
        allowed_tools=("read", "edit", "test", "context7.query-docs"),
        allowed_paths=("src/**", "tests/**"),
        denied_paths=(".github/**",),
        network_policy=(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        content_hash=HASH_A,
    )
    capabilities = PresetCapabilityAssembler().assemble(worker)
    request = RunnerTaskProjectionRequest(
        package=package,
        context_grant=grant,
        capabilities=capabilities,
        organization_id=organization_id,
        run_id=run_id,
        correlation_id=uuid4(),
        adapter_id="claude-code",
        repository_url="https://github.com/acme/pricing.git",
        base_revision="main",
        workspace_id="ws-pricing",
        workspace_path=tmp_path,
        base_sha="abc123",
        context_manifest_uri="repomesh://bundles/current/manifest.json",
    )
    return request, package, capabilities


def test_project_materialize_and_verify_context(tmp_path: Path) -> None:
    request, package, capabilities = scenario(tmp_path)
    task = RunnerTaskProjector().project(request)

    mounted = RunnerContextMaterializer(Path.cwd()).materialize(task, package, capabilities)

    assert task.permissions.allowed_tools == ("read", "edit", "test", "context7.query-docs")
    assert task.permissions.denied_paths == (".github/**", "src/legacy/**")
    assert "github.pull_requests.merge" in task.permissions.disallowed_tools
    assert task.test_commands == ("pytest tests/pricing",)
    assert mounted.manifest_path.is_file()
    assert WorkspaceContextVerifier()(task) is None
    assert (tmp_path / ".repomesh/skills/self-test/SKILL.md").is_file()


def test_the_instruction_names_the_tdd_skill_when_the_bundle_carries_it(tmp_path: Path) -> None:
    """Mounting a skill changes nothing unless the instruction sends the agent to it."""

    request, _, _ = scenario(tmp_path)
    task = RunnerTaskProjector().project(request)
    assert ".repomesh/skills/tdd/SKILL.md" in task.instruction

    bare = replace(
        request,
        capabilities=AgentCapabilityBundle(AgentRole.WORKER, (), ()),
    )
    bare_task = RunnerTaskProjector().project(bare)
    assert ".repomesh/skills/tdd/SKILL.md" not in bare_task.instruction
    assert bare_task.instruction.startswith(
        "Read .repomesh/context/current-task.md and the mounted RepoMesh skills."
    )


def test_tampered_skill_is_rejected_before_execution(tmp_path: Path) -> None:
    request, package, capabilities = scenario(tmp_path)
    task = RunnerTaskProjector().project(request)
    RunnerContextMaterializer(Path.cwd()).materialize(task, package, capabilities)
    skill = tmp_path / ".repomesh/skills/self-test/SKILL.md"
    skill.chmod(0o644)
    skill.write_text("tampered", encoding="utf-8")

    assert WorkspaceContextVerifier()(task) == (
        "context_file_hash_mismatch:.repomesh/skills/self-test/SKILL.md"
    )


def test_projection_rejects_paths_outside_context_grant(tmp_path: Path) -> None:
    request, package, capabilities = scenario(tmp_path)
    request = replace(request, package=replace(package, allowed_paths=("infra/**",)))

    with pytest.raises(RunnerTaskProjectionDenied, match="exceed"):
        RunnerTaskProjector().project(request)


def test_injected_context_is_not_reported_as_changed_code() -> None:
    output = "?? .repomesh/context/current-task.md\n M src/pricing.py\n"

    assert _parse_porcelain(output) == ("src/pricing.py",)


def test_projection_rejects_a_different_immutable_base(tmp_path: Path) -> None:
    request, package, capabilities = scenario(tmp_path)
    request = replace(
        request,
        context_grant=replace(request.context_grant, base_sha="expected-sha"),
        base_sha="different-sha",
    )

    with pytest.raises(RunnerTaskProjectionDenied, match="base SHA"):
        RunnerTaskProjector().project(request)


# ---------------------------------------------------------------------------
# Verification commands are resolved at dispatch time, not baked in (A-19)
# ---------------------------------------------------------------------------


def test_a_spec_that_states_its_tests_outranks_the_catalog(tmp_path: Path) -> None:
    """Precedence, in the direction that protects deliberate scoping.

    A task somebody narrowed to one suite must keep it. If the catalog could
    override, correcting one repository's command would silently rewrite every
    in-flight task that had been scoped on purpose — and it would rewrite them
    at dispatch, where nobody is looking.
    """

    request, _package, _capabilities = scenario(tmp_path)
    request = replace(request, catalog_test_commands=("python scripts/run_tests.py",))

    task = RunnerTaskProjector().project(request)

    assert task.test_commands == ("pytest tests/pricing",)


def test_a_silent_spec_falls_back_to_the_catalogs_current_answer(tmp_path: Path) -> None:
    """The rescue: a task row with no tests baked in still dispatches verified.

    Every round materialized before A-19's first half carries an empty ``tests``
    in its Specification, and re-dispatch replays that row verbatim. Resolving
    here is what reaches them — and what lets an operator fix a wrong command
    without re-materialising anything.
    """

    request, package, _capabilities = scenario(tmp_path)
    request = replace(
        request,
        package=replace(package, test_commands=()),
        catalog_test_commands=("python scripts/run_tests.py",),
    )

    task = RunnerTaskProjector().project(request)

    assert task.test_commands == ("python scripts/run_tests.py",)


def test_neither_spec_nor_catalog_dispatches_honestly_empty(tmp_path: Path) -> None:
    """No default is invented at the last moment either.

    A command nobody has stated cannot be guessed here any more than it could
    be guessed in the migration. The dispatch goes out with none, the Runner
    runs none, and delivery refuses the unverified candidate — which is the
    loop closing honestly rather than a green tick over nothing.
    """

    request, package, _capabilities = scenario(tmp_path)
    request = replace(request, package=replace(package, test_commands=()))

    assert RunnerTaskProjector().project(request).test_commands == ()


# ---------------------------------------------------------------------------
# Test paths are added to the permit at dispatch time (A-21)
# ---------------------------------------------------------------------------


def test_catalog_test_paths_are_added_to_the_tasks_own_paths(tmp_path: Path) -> None:
    """The contradiction that voided a whole run, resolved by union.

    The Worker is permitted the product code it owns — ``src/**``, ``tests/**``
    in this fixture's terms — and its verification command discovers from a
    directory nobody granted it. Live, the compliant agent wrote
    ``tests/test_discount.py`` where ``run_tests.py`` looks and the guard
    voided the run with ``changed_path_denied`` and a null commitSha.

    The grant is widened alongside, exactly as ``StartAssignedWorkerTask`` does
    in production: the projector validates every payload path against the
    grant, so a union the grant does not cover would be refused rather than
    dispatched.
    """

    request, package, _capabilities = scenario(tmp_path)
    request = replace(
        request,
        package=replace(package, allowed_paths=("src/checkout/**",)),
        context_grant=replace(
            request.context_grant, allowed_paths=("src/checkout/**", "integration-tests/**")
        ),
        catalog_test_paths=("integration-tests/**",),
    )

    task = RunnerTaskProjector().project(request)

    assert task.permissions.allowed_paths == ("src/checkout/**", "integration-tests/**")


def test_catalog_test_paths_never_replace_the_tasks_own(tmp_path: Path) -> None:
    """Added, never substituted.

    A repository saying where its tests live must not become a way to narrow —
    or widen — what a Worker may touch anywhere else. The task's own paths
    survive unchanged and keep their order, so the permit still reads as the
    scoping decision somebody made plus one addition.
    """

    request, package, _capabilities = scenario(tmp_path)
    request = replace(
        request,
        context_grant=replace(
            request.context_grant, allowed_paths=("src/**", "tests/**", "e2e/**")
        ),
        package=replace(package, allowed_paths=("src/**", "tests/**")),
        catalog_test_paths=("e2e/**",),
    )

    task = RunnerTaskProjector().project(request)

    assert task.permissions.allowed_paths[:2] == ("src/**", "tests/**")
    assert "e2e/**" in task.permissions.allowed_paths


def test_a_repository_that_declares_no_test_paths_changes_nothing(tmp_path: Path) -> None:
    """The honest half: no path is invented for a repository that named none."""

    request, _package, _capabilities = scenario(tmp_path)

    task = RunnerTaskProjector().project(request)

    assert task.permissions.allowed_paths == ("src/**", "tests/**")


def test_test_paths_alone_are_not_a_permit(tmp_path: Path) -> None:
    """A task nobody scoped is still refused, catalog or no catalog.

    The union happens after the emptiness check on purpose. "This repository
    keeps its tests in tests/" is not an authorisation to do anything; a task
    that arrived with no paths of its own and no grant is a task nobody
    scoped, and turning that into a one-directory permit would be inventing
    the scope rather than refusing to guess at it.
    """

    request, package, _capabilities = scenario(tmp_path)
    request = replace(
        request,
        package=replace(package, allowed_paths=()),
        context_grant=replace(request.context_grant, allowed_paths=()),
        catalog_test_paths=("tests/**",),
    )

    with pytest.raises(RunnerTaskProjectionDenied, match="at least one allowed path"):
        RunnerTaskProjector().project(request)


def test_context_materialization_is_idempotent(tmp_path: Path) -> None:
    request, package, capabilities = scenario(tmp_path)
    task = RunnerTaskProjector().project(request)
    materializer = RunnerContextMaterializer(Path.cwd())

    first = materializer.materialize(task, package, capabilities)
    second = materializer.materialize(task, package, capabilities)

    assert first.file_hashes == second.file_hashes
