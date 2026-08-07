"""The composition root's Task Specification author, over the real service.

``DecomposeRepositoryTask`` only knows the ``TaskSpecificationAuthor`` port; the
adapter that actually walks a Task Spec from draft to frozen and published lives
in the container.  These tests run it against a real ``SpecificationService`` so
the four-step ritual is proven, not mocked.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from repomesh.bootstrap.container import ApprovedTaskSpecificationAuthor
from repomesh.modules.agent_directory.application import (
    CreateAgent,
    CreateAgentRequest,
    CreateRepositoryAgentTeam,
    CreateRepositoryAgentTeamRequest,
)
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.agent_directory.infrastructure import InMemoryAgentDirectory
from repomesh.modules.context.application import ContextPublicationGateway
from repomesh.modules.context.contracts import ContextObjectType, ContextScope
from repomesh.modules.context.infrastructure import InMemoryContextStore
from repomesh.modules.identity_access import PolicyAuthorizationGateway
from repomesh.modules.project import (
    CreateProjectAgentTopology,
    CreateProjectAgentTopologyRequest,
    RepositoryTeamAssignment,
)
from repomesh.modules.project.infrastructure import InMemoryProjectTopologyStore
from repomesh.modules.specification import (
    InMemorySpecificationStore,
    SpecificationKind,
    SpecificationService,
    SpecificationStatus,
)
from repomesh.modules.task_orchestration.contracts import TaskStatus, TaskView

TESTS = ("python scripts/run_tests.py",)
ALLOWED_PATHS = ("src/checkout_fixture/**",)


@dataclass(frozen=True, slots=True)
class Environment:
    author: ApprovedTaskSpecificationAuthor
    specifications: InMemorySpecificationStore
    contexts: InMemoryContextStore
    project_id: UUID
    repository_leader_id: UUID
    worker_task: TaskView


async def build_environment() -> Environment:
    organization_id = uuid4()
    project_id = uuid4()
    repository_id = uuid4()
    directory = InMemoryAgentDirectory()
    organization_leader = await CreateAgent(directory).execute(
        CreateAgentRequest(
            organization_id=organization_id,
            role=AgentRole.ORGANIZATION_LEADER,
            agentteams_resource_name="author-org-leader",
        ),
        idempotency_key="author-org-leader",
    )
    team = await CreateRepositoryAgentTeam(directory).execute(
        CreateRepositoryAgentTeamRequest(
            organization_id=organization_id,
            organization_leader_id=organization_leader.principal.id,
            repository_id=repository_id,
            leader_agentteams_resource_name="author-repo-leader",
            worker_agentteams_resource_names=("author-worker",),
            worker_responsibility_paths=ALLOWED_PATHS,
        ),
        idempotency_key="author-repository-team",
    )
    topologies = InMemoryProjectTopologyStore()
    await CreateProjectAgentTopology(directory, topologies).execute(
        CreateProjectAgentTopologyRequest(
            organization_id=organization_id,
            project_id=project_id,
            organization_leader_id=organization_leader.principal.id,
            repository_teams=(
                RepositoryTeamAssignment(
                    repository_id=repository_id,
                    leader_agent_id=team.leader.id,
                    worker_agent_ids=(team.workers[0].id,),
                ),
            ),
        ),
        idempotency_key="author-project-topology",
    )
    specifications = InMemorySpecificationStore()
    contexts = InMemoryContextStore()
    service = SpecificationService(
        directory,
        topologies,
        specifications,
        ContextPublicationGateway(contexts),
        PolicyAuthorizationGateway(),
    )
    worker_task = TaskView(
        id=uuid4(),
        organization_id=organization_id,
        project_id=project_id,
        repository_id=repository_id,
        parent_task_id=uuid4(),
        assigned_by_agent_id=team.leader.id,
        assignee_agent_id=team.workers[0].id,
        title="Implement changes for checkout-live",
        instruction="Apply the discount before shipping is added.",
        acceptance=("Code compiles without errors.", "Existing tests pass."),
        status=TaskStatus.ASSIGNED,
        result_summary=None,
        version=1,
    )
    return Environment(
        author=ApprovedTaskSpecificationAuthor(service, specifications),
        specifications=specifications,
        contexts=contexts,
        project_id=project_id,
        repository_leader_id=team.leader.id,
        worker_task=worker_task,
    )


async def test_ensure_approved_publishes_a_frozen_task_spec_for_the_worker_task() -> None:
    environment = await build_environment()

    await environment.author.ensure_approved(
        environment.worker_task,
        allowed_paths=(*ALLOWED_PATHS, "   "),
        tests=(*TESTS, ""),
        idempotency_key="plan-1:spec:worker",
    )

    stored = await environment.specifications.list_by_project(environment.project_id)
    assert len(stored) == 1
    specification = stored[0]
    assert specification.kind is SpecificationKind.TASK
    assert specification.status is SpecificationStatus.FROZEN
    assert specification.task_id == environment.worker_task.id
    assert specification.repository_id == environment.worker_task.repository_id
    assert specification.owner_agent_id == environment.repository_leader_id
    content = specification.current_version.content
    assert content.goal == environment.worker_task.instruction
    assert content.acceptance == environment.worker_task.acceptance
    # Blank entries would make the specification content invalid.
    assert content.tests == TESTS
    assert content.allowed_paths == ALLOWED_PATHS

    published = list(environment.contexts.objects.values())
    assert len(published) == 1
    assert published[0].object_type is ContextObjectType.TASK_SPEC
    assert published[0].scope is ContextScope.TASK_PRIVATE


async def test_ensure_approved_replayed_with_the_same_key_is_a_no_op() -> None:
    environment = await build_environment()

    for _ in range(2):
        await environment.author.ensure_approved(
            environment.worker_task,
            allowed_paths=ALLOWED_PATHS,
            tests=TESTS,
            idempotency_key="plan-1:spec:worker",
        )

    stored = await environment.specifications.list_by_project(environment.project_id)
    assert len(stored) == 1
    assert stored[0].status is SpecificationStatus.FROZEN
    assert len(environment.contexts.objects) == 1


async def test_a_task_that_already_holds_a_permit_is_not_given_a_second_one() -> None:
    """Two approved specs would break ``start_assigned_task`` with a conflict."""

    environment = await build_environment()

    for key in ("plan-1:spec:worker", "manual-decompose:spec:worker"):
        await environment.author.ensure_approved(
            environment.worker_task,
            allowed_paths=ALLOWED_PATHS,
            tests=TESTS,
            idempotency_key=key,
        )

    stored = await environment.specifications.list_by_project(environment.project_id)
    assert len(stored) == 1
    assert len(environment.contexts.objects) == 1
