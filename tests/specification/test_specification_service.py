from dataclasses import replace
from uuid import uuid4

import pytest

from repomesh.modules.agent_directory.application import (
    CreateAgent,
    CreateAgentRequest,
    CreateRepositoryAgentTeam,
    CreateRepositoryAgentTeamRequest,
)
from repomesh.modules.agent_directory.contracts import AgentPrincipalStatus, AgentRole
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
    ApproveSpecificationCommand,
    CreateSpecificationCommand,
    InMemorySpecificationStore,
    PublishSpecificationContextCommand,
    ReviseSpecificationCommand,
    SpecificationConflict,
    SpecificationDenied,
    SpecificationKind,
    SpecificationService,
    SpecificationStatus,
    SubmitSpecificationCommand,
)


async def build_service():
    organization_id = uuid4()
    project_id = uuid4()
    repository_id = uuid4()
    directory = InMemoryAgentDirectory()
    creator = CreateAgent(directory)
    organization_leader = await creator.execute(
        CreateAgentRequest(
            organization_id=organization_id,
            role=AgentRole.ORGANIZATION_LEADER,
            agentteams_resource_name="spec-org-leader",
        ),
        idempotency_key="spec-org-leader",
    )
    team = await CreateRepositoryAgentTeam(directory).execute(
        CreateRepositoryAgentTeamRequest(
            organization_id=organization_id,
            organization_leader_id=organization_leader.principal.id,
            repository_id=repository_id,
            leader_agentteams_resource_name="spec-repo-leader",
            worker_agentteams_resource_names=("spec-worker",),
            worker_responsibility_paths=("src/**", "tests/**"),
        ),
        idempotency_key="spec-repository-team",
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
        idempotency_key="spec-project-topology",
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
    return (
        service,
        specifications,
        contexts,
        organization_id,
        project_id,
        repository_id,
        organization_leader.principal,
        team.leader,
        team.workers[0],
    )


def create_command(*, organization_id, project_id, actor_id, kind, repository_id=None):
    return CreateSpecificationCommand(
        organization_id=organization_id,
        project_id=project_id,
        repository_id=repository_id,
        kind=kind,
        title="Pricing API specification",
        created_by_agent_id=actor_id,
        goal="Update pricing without breaking existing clients",
        acceptance=("Old clients remain compatible", "Pricing tests pass"),
        scope=("Pricing API",),
        tests=("pytest tests/pricing",),
        allowed_paths=("src/pricing/**", "tests/pricing/**"),
        task_id=uuid4() if kind is SpecificationKind.TASK else None,
    )


@pytest.mark.asyncio
async def test_engineering_spec_is_frozen_and_published_as_project_context() -> None:
    (
        service,
        _,
        contexts,
        organization_id,
        project_id,
        _,
        organization_leader,
        _,
        _,
    ) = await build_service()
    created = await service.create(
        create_command(
            organization_id=organization_id,
            project_id=project_id,
            actor_id=organization_leader.id,
            kind=SpecificationKind.ENGINEERING,
        ),
        idempotency_key="engineering-v1",
    )
    submitted = await service.submit(
        SubmitSpecificationCommand(created.id, organization_leader.id, created.revision)
    )
    approved = await service.approve(
        ApproveSpecificationCommand(
            submitted.id, organization_leader.id, submitted.revision, freeze=True
        )
    )
    context_ref = await service.publish_to_context(
        PublishSpecificationContextCommand(approved.id, organization_leader.id)
    )

    assert approved.status is SpecificationStatus.FROZEN
    assert context_ref.object_type is ContextObjectType.ENGINEERING_SPEC
    assert context_ref.scope is ContextScope.PROJECT_SHARED
    assert context_ref.content_hash == approved.current_version.content_hash
    assert await contexts.get_object(context_ref.context_object_id) is not None


@pytest.mark.asyncio
async def test_repository_leader_owns_repository_and_task_specs() -> None:
    (
        service,
        _,
        _,
        organization_id,
        project_id,
        repository_id,
        _,
        repository_leader,
        worker,
    ) = await build_service()

    for kind, expected_scope in (
        (SpecificationKind.REPOSITORY, ContextScope.TEAM_PRIVATE),
        (SpecificationKind.TASK, ContextScope.TASK_PRIVATE),
    ):
        created = await service.create(
            create_command(
                organization_id=organization_id,
                project_id=project_id,
                repository_id=repository_id,
                actor_id=repository_leader.id,
                kind=kind,
            ),
            idempotency_key=f"{kind.value}-v1",
        )
        submitted = await service.submit(
            SubmitSpecificationCommand(created.id, repository_leader.id, created.revision)
        )
        approved = await service.approve(
            ApproveSpecificationCommand(
                submitted.id, repository_leader.id, submitted.revision
            )
        )
        context_ref = await service.publish_to_context(
            PublishSpecificationContextCommand(approved.id, repository_leader.id)
        )
        assert context_ref.scope is expected_scope

    with pytest.raises(SpecificationDenied):
        await service.create(
            create_command(
                organization_id=organization_id,
                project_id=project_id,
                repository_id=repository_id,
                actor_id=worker.id,
                kind=SpecificationKind.TASK,
            ),
            idempotency_key="worker-cannot-create",
        )


@pytest.mark.asyncio
async def test_revision_preserves_history_and_reports_changed_fields() -> None:
    (
        service,
        specifications,
        _,
        organization_id,
        project_id,
        repository_id,
        _,
        repository_leader,
        _,
    ) = await build_service()
    created = await service.create(
        create_command(
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository_id,
            actor_id=repository_leader.id,
            kind=SpecificationKind.REPOSITORY,
        ),
        idempotency_key="repository-revision",
    )
    revised = await service.revise(
        ReviseSpecificationCommand(
            specification_id=created.id,
            actor_agent_id=repository_leader.id,
            expected_revision=created.revision,
            goal="Update pricing and tax calculation",
            acceptance=("Old clients remain compatible", "Tax tests pass"),
            allowed_paths=("src/pricing/**", "src/tax/**"),
        )
    )
    difference = await service.diff(created.id, 1, 2)

    assert revised.current_version.version == 2
    assert set(difference.changed_fields) == {
        "acceptance",
        "allowed_paths",
        "goal",
        "scope",
        "tests",
    }
    assert await specifications.get_version(created.id, 1) is not None


@pytest.mark.asyncio
async def test_idempotency_replay_requires_the_same_request() -> None:
    (
        service,
        _,
        _,
        organization_id,
        project_id,
        _,
        organization_leader,
        _,
        _,
    ) = await build_service()
    command = create_command(
        organization_id=organization_id,
        project_id=project_id,
        actor_id=organization_leader.id,
        kind=SpecificationKind.ENGINEERING,
    )
    first = await service.create(command, idempotency_key="same-key")
    replay = await service.create(command, idempotency_key="same-key")
    assert replay.id == first.id

    conflicting = replace(command, title="Different specification")
    with pytest.raises(SpecificationConflict):
        await service.create(conflicting, idempotency_key="same-key")


@pytest.mark.asyncio
async def test_disabled_owner_cannot_revise_submit_or_publish() -> None:
    (
        service,
        _,
        _,
        organization_id,
        project_id,
        repository_id,
        _,
        repository_leader,
        _,
    ) = await build_service()
    created = await service.create(
        create_command(
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository_id,
            actor_id=repository_leader.id,
            kind=SpecificationKind.REPOSITORY,
        ),
        idempotency_key="disabled-owner",
    )
    approved_candidate = await service.create(
        create_command(
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository_id,
            actor_id=repository_leader.id,
            kind=SpecificationKind.REPOSITORY,
        ),
        idempotency_key="disabled-owner-publish",
    )
    submitted = await service.submit(
        SubmitSpecificationCommand(
            approved_candidate.id,
            repository_leader.id,
            approved_candidate.revision,
        )
    )
    approved = await service.approve(
        ApproveSpecificationCommand(
            submitted.id, repository_leader.id, submitted.revision
        )
    )
    directory = service._directory  # noqa: SLF001
    directory._principals[repository_leader.id] = replace(  # noqa: SLF001
        directory._principals[repository_leader.id],  # noqa: SLF001
        status=AgentPrincipalStatus.DISABLED,
    )

    with pytest.raises(SpecificationDenied, match="agent_disabled"):
        await service.revise(
            ReviseSpecificationCommand(
                specification_id=created.id,
                actor_agent_id=repository_leader.id,
                expected_revision=created.revision,
                goal="change after disable",
                acceptance=("must be denied",),
            )
        )
    with pytest.raises(SpecificationDenied, match="agent_disabled"):
        await service.submit(
            SubmitSpecificationCommand(
                created.id, repository_leader.id, created.revision
            )
        )
    with pytest.raises(SpecificationDenied, match="agent_disabled"):
        await service.publish_to_context(
            PublishSpecificationContextCommand(approved.id, repository_leader.id)
        )
