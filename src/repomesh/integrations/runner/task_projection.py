from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from repomesh.modules.capability_management.contracts import AgentCapabilityBundle
from repomesh.modules.context.contracts import ExecutionContextGrant
from repomesh.modules.specification.contracts import CodingAgentPackage
from repomesh_runner.contracts import (
    ContextBundleRef,
    RepositoryCheckout,
    RunnerPermissionMode,
    RunnerPermissions,
    RunnerTask,
    WorkspaceAssignment,
)


class RunnerTaskProjectionDenied(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RunnerTaskProjectionRequest:
    package: CodingAgentPackage
    context_grant: ExecutionContextGrant
    capabilities: AgentCapabilityBundle
    organization_id: UUID
    run_id: UUID
    correlation_id: UUID
    adapter_id: str
    repository_url: str
    base_revision: str
    workspace_id: str
    workspace_path: Path
    base_sha: str
    context_manifest_uri: str
    catalog_test_paths: tuple[str, ...] = ()
    """Where the catalog says this repository keeps the files its tests read.

    Unioned into the payload's allowed paths, never substituted for them — see
    :meth:`RunnerTaskProjector.project`. Resolved by the caller for the same
    reason as ``catalog_test_commands``: this projector does no I/O.
    """
    catalog_test_commands: tuple[str, ...] = ()
    """How the repository catalog says this repository is verified, right now.

    A fallback, never an override — see :meth:`RunnerTaskProjector.project`.
    Resolved by the caller because this projector is a pure translation with no
    I/O in it, and reading the catalog is the dispatcher's business; it already
    holds the profile it fetched to get the clone URL.
    """
    context_version: int = 1
    attempt: int = 1
    permission_mode: RunnerPermissionMode = RunnerPermissionMode.ACCEPT_EDITS
    resume_session_id: str | None = None
    credential_refs: tuple[str, ...] = ()
    issued_at: datetime | None = None


class RunnerTaskProjector:
    """Translate governed product objects into the frozen runtime.v1 task."""

    def project(self, request: RunnerTaskProjectionRequest) -> RunnerTask:
        """Build the frozen runtime.v1 task this dispatch will send.

        **Verification commands are resolved here, not baked in** (defect A-19,
        second half). The Task Specification states them and wins whenever it
        has any; when it is silent the repository catalog's *current* answer is
        used. That precedence — spec-stated over catalog-current — matters in
        both directions: a task somebody deliberately scoped to one test suite
        keeps it, and a task nobody scoped at all still gets verified.

        Resolving at dispatch time rather than only at materialization is what
        makes the commands convergent. A08 fixed materialization, so fresh
        rounds carry their commands in the spec; every round materialized
        *before* that has an empty ``tests`` baked into its task rows, and
        re-dispatch replays those rows verbatim — so ``testCommands`` stayed
        ``[]`` forever no matter what the catalog said (live: re-dispatched run
        8aa3b0a5 completed with ``testResults: []`` while its catalog row read
        ``["python scripts/run_tests.py"]``). Reading the catalog on the way
        out rescues every one of those rounds on its next dispatch, and means
        an operator correcting a wrong command reaches the next run without
        re-materialising anything.

        Empty on both sides stays empty. The dispatch then carries no
        verification, the Runner honestly runs none, and delivery refuses the
        unverified candidate downstream — visibly, since the refusal is
        recorded on the round.
        """

        package = request.package
        grant = request.context_grant
        self._validate_bindings(request)

        allowed_paths = package.allowed_paths or grant.allowed_paths
        if allowed_paths:
            # Defect A-21: added, never substituted. The Specification's paths
            # say what this task may change; the catalog's test paths say where
            # its own verification command reads from, and a task permitted
            # only `src/checkout/**` cannot write the test `run_tests.py` will
            # look for in `tests/`. Live, the compliant agent wrote it there
            # anyway and the guard voided the whole run (commitSha null); the
            # evading one hid it under `src/` where the command never finds it.
            #
            # Unioned rather than replaced because a repository saying where
            # its tests live must not widen what a Worker may touch elsewhere,
            # and unioned *after* the emptiness check because test paths alone
            # are not a permit to do anything — a task with no paths of its own
            # is still a task nobody scoped, and that is still a refusal.
            allowed_paths = tuple(
                dict.fromkeys((*allowed_paths, *request.catalog_test_paths))
            )
        if not allowed_paths:
            raise RunnerTaskProjectionDenied("runner task requires at least one allowed path")
        uncovered = tuple(path for path in allowed_paths if not self._path_granted(path, grant))
        if uncovered:
            raise RunnerTaskProjectionDenied(
                "package paths exceed the execution grant: " + ", ".join(uncovered)
            )

        allowed_tools = tuple(dict.fromkeys(grant.allowed_tools))
        denied_tools = tuple(
            dict.fromkeys(
                (
                    "git-push",
                    "github.contents.write",
                    "github.pull_requests.merge",
                    *(
                        operation
                        for server in request.capabilities.mcp_servers
                        for operation in server.denied_operations
                    ),
                )
            )
        )

        issued_at = request.issued_at or datetime.now(UTC)
        return RunnerTask(
            organization_id=request.organization_id,
            project_id=package.project_id,
            task_id=package.task_id,
            run_id=request.run_id,
            correlation_id=request.correlation_id,
            attempt=request.attempt,
            adapter_id=request.adapter_id,
            instruction=(
                "Read .repomesh/context/current-task.md and the mounted RepoMesh skills. "
                f"Complete only the assigned task: {package.instruction}"
            ),
            repository=RepositoryCheckout(
                repository_id=package.repository_id,
                url=request.repository_url,
                base_revision=request.base_revision,
            ),
            workspace=WorkspaceAssignment(
                workspace_id=request.workspace_id,
                path=str(request.workspace_path.resolve()),
                base_sha=request.base_sha,
            ),
            context_bundle=ContextBundleRef(
                bundle_id=grant.bundle_id,
                version=request.context_version,
                manifest_uri=request.context_manifest_uri,
                content_hash=grant.content_hash,
                coding_package_hash=package.content_hash,
            ),
            permissions=RunnerPermissions(
                mode=request.permission_mode,
                allowed_tools=allowed_tools,
                disallowed_tools=denied_tools,
                network_targets=grant.network_policy,
                allowed_paths=allowed_paths,
                denied_paths=tuple(
                    dict.fromkeys((*grant.denied_paths, *package.forbidden_paths))
                ),
            ),
            test_commands=package.test_commands or request.catalog_test_commands,
            resume_session_id=request.resume_session_id,
            credential_refs=request.credential_refs,
            worker_agent_id=package.worker_agent_id,
            idempotency_key=(f"{package.task_id}:run:{request.run_id}:attempt:{request.attempt}"),
            issued_at=issued_at,
        )

    @staticmethod
    def _validate_bindings(request: RunnerTaskProjectionRequest) -> None:
        package = request.package
        grant = request.context_grant
        if grant.project_id != package.project_id:
            raise RunnerTaskProjectionDenied("project binding mismatch")
        if grant.repository_id != package.repository_id:
            raise RunnerTaskProjectionDenied("repository binding mismatch")
        if grant.agent_id != package.worker_agent_id:
            raise RunnerTaskProjectionDenied("worker binding mismatch")
        if grant.run_id != request.run_id:
            raise RunnerTaskProjectionDenied("run binding mismatch")
        if grant.base_sha is not None and grant.base_sha != request.base_sha:
            raise RunnerTaskProjectionDenied("base SHA binding mismatch")
        if grant.workspace_id is not None and str(grant.workspace_id) != request.workspace_id:
            raise RunnerTaskProjectionDenied("workspace binding mismatch")
        if request.capabilities.role.value != "worker":
            raise RunnerTaskProjectionDenied("coding execution requires a worker capability bundle")
        if datetime.now(UTC) >= grant.expires_at:
            raise RunnerTaskProjectionDenied("execution context grant expired")

    @staticmethod
    def _path_granted(path: str, grant: ExecutionContextGrant) -> bool:
        return "**" in grant.allowed_paths or path in grant.allowed_paths
