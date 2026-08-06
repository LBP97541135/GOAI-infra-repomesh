import json
import os
from uuid import uuid4

import pytest

from repomesh.integrations.agentteams.project_topology import ReconcileProjectAgentTopology
from repomesh.modules.agent_directory.application import (
    CreateAgent,
    CreateAgentRequest,
    CreateRepositoryAgentTeam,
    CreateRepositoryAgentTeamRequest,
)
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.agent_directory.infrastructure import PostgresAgentDirectory
from repomesh.modules.agent_runtime.ports import TeamRuntimeRef
from repomesh.modules.collaboration import (
    PostgresCollaborationMessageStore,
    SendCollaborationMessage,
)
from repomesh.modules.context.application import ContextPublicationGateway
from repomesh.modules.context.infrastructure import PostgresContextStore
from repomesh.modules.identity_access import PolicyAuthorizationGateway
from repomesh.modules.project import (
    CreateProjectAgentTopology,
    CreateProjectAgentTopologyRequest,
    RepositoryTeamAssignment,
)
from repomesh.modules.project.infrastructure import PostgresProjectTopologyStore
from repomesh.modules.repository_intelligence.application import (
    RegisterRepository,
    RepositoryDiscoveryService,
    RequirementAnalyzer,
)
from repomesh.modules.repository_intelligence.application.confirmation import (
    ConfirmationService,
)
from repomesh.modules.repository_intelligence.domain import AutoCard, RepositoryProfile
from repomesh.modules.repository_intelligence.infrastructure import PostgresRepositoryCatalog
from repomesh.modules.specification import (
    ApproveSpecificationCommand,
    BuildCodingAgentPackage,
    BuildCodingAgentPackageCommand,
    CreateSpecificationCommand,
    PostgresSpecificationStore,
    PublishSpecificationContextCommand,
    SpecificationKind,
    SpecificationService,
    SubmitSpecificationCommand,
)
from repomesh.modules.task_orchestration import (
    AssignTaskCommand,
    PostgresTaskStore,
    TaskOrchestrator,
)
from repomesh.modules.task_orchestration.contracts import PublishedTaskPackage
from repomesh.persistence import Database

POSTGRES_URL = os.getenv("REPOMESH_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="REPOMESH_TEST_POSTGRES_URL is not configured"),
]


class StaticLLM:
    def __init__(self, response: object) -> None:
        self._response = json.dumps(response, ensure_ascii=False)

    def chat(
        self, messages: list[dict[str, str]], *, temperature: float = 0.0
    ) -> str:
        return self._response


class ReadyControlPlane:
    async def ensure_team(self, projection, *, idempotency_key: str) -> TeamRuntimeRef:
        return TeamRuntimeRef(
            name=projection.name,
            phase="Ready",
            team_room_id=f"!team-{projection.name}:matrix.local",
            leader_room_id=f"!leader-{projection.name}:matrix.local",
            leader_name=projection.members[0].name,
            ready_workers=len(projection.members),
            total_workers=len(projection.members),
        )


class RecordingMessenger:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send_task(
        self, room_id: str, body: str, *, transaction_id: str, **kwargs
    ) -> str:
        self.messages.append(
            {"room_id": room_id, "body": json.loads(body), "transaction_id": transaction_id}
        )
        return f"$event-{len(self.messages)}"


class RecordingTaskPublisher:
    async def publish(self, task, **kwargs):
        return PublishedTaskPackage(
            kwargs["team_name"],
            f"teams/{kwargs['team_name']}/shared/tasks/{task.id}",
            "sha256:verified",
        )


@pytest.mark.asyncio
async def test_requirement_discovery_to_worker_spec_package_on_postgres() -> None:
    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    suffix = uuid4().hex
    organization_id = uuid4()
    project_id = uuid4()

    directory = PostgresAgentDirectory(database)
    topologies = PostgresProjectTopologyStore(database)
    repositories = PostgresRepositoryCatalog(database)
    tasks = PostgresTaskStore(database)
    collaborations = PostgresCollaborationMessageStore(database)
    contexts = PostgresContextStore(database)
    specifications = PostgresSpecificationStore(database)
    authorizer = PolicyAuthorizationGateway()

    try:
        requirement = "为 Saleor pricing API 增加可选价格字段，并兼容旧客户端"
        analysis = RequirementAnalyzer(
            StaticLLM(
                {
                    "sufficient": True,
                    "confidence": 0.96,
                    "missing_dimensions": [],
                    "questions": [],
                    "extracted_keywords": ["pricing", "API", "兼容"],
                }
            )
        ).analyze(requirement)
        assert analysis.sufficient

        target = RepositoryProfile(
            name=f"saleor-{suffix}",
            url=f"https://github.com/example/saleor-{suffix}",
            description="Saleor GraphQL backend and pricing domain",
            auto_card=AutoCard(
                top_dirs=("saleor/graphql", "saleor/product"),
                deps=("django", "graphql-core"),
                exposed_apis=("graphql:ProductVariant.pricing",),
            ),
        )
        distractor = RepositoryProfile(
            name=f"docs-{suffix}",
            url=f"https://github.com/example/docs-{suffix}",
            description="Public documentation website",
        )
        await RegisterRepository(repositories).execute(target)
        await RegisterRepository(repositories).execute(distractor)

        discovery = RepositoryDiscoveryService(
            repositories,
            llm_client=StaticLLM(
                [
                    {
                        "repository": target.name,
                        "confidence": 0.94,
                        "rationale": "Pricing GraphQL API is owned by this repository",
                    }
                ]
            ),
        )
        candidates = await discovery.discover(
            requirement,
            keywords=analysis.extracted_keywords,
            limit=5,
        )
        assert [candidate.repository_id for candidate in candidates] == [target.id]

        confirmation = ConfirmationService(
            StaticLLM(
                {
                    "status": "REQUIRED",
                    "confidence": 0.98,
                    "reason": "Repository owns ProductVariant pricing",
                    "plan_summary": "Extend pricing schema and resolver compatibly",
                    "missing_dependencies": [],
                }
            ),
            {target.name: target},
        ).confirm([target.name], requirement)
        assert confirmation.final_repos == [target.name]

        organization_leader = await CreateAgent(directory).execute(
            CreateAgentRequest(
                organization_id=organization_id,
                role=AgentRole.ORGANIZATION_LEADER,
                agentteams_resource_name=f"org-leader-{suffix}",
            ),
            idempotency_key=f"e2e:{suffix}:organization-leader",
        )
        team = await CreateRepositoryAgentTeam(directory).execute(
            CreateRepositoryAgentTeamRequest(
                organization_id=organization_id,
                organization_leader_id=organization_leader.principal.id,
                repository_id=target.id,
                leader_agentteams_resource_name=f"repository-leader-{suffix}",
                worker_agentteams_resource_names=(f"worker-{suffix}",),
                worker_responsibility_paths=("saleor/graphql/**", "saleor/product/**"),
            ),
            idempotency_key=f"e2e:{suffix}:repository-team",
        )
        await CreateProjectAgentTopology(directory, topologies).execute(
            CreateProjectAgentTopologyRequest(
                organization_id=organization_id,
                project_id=project_id,
                organization_leader_id=organization_leader.principal.id,
                repository_teams=(
                    RepositoryTeamAssignment(
                        repository_id=target.id,
                        leader_agent_id=team.leader.id,
                        worker_agent_ids=(team.workers[0].id,),
                    ),
                ),
            ),
            idempotency_key=f"e2e:{suffix}:project-topology",
        )
        await ReconcileProjectAgentTopology(
            directory, topologies, ReadyControlPlane()
        ).execute(project_id)

        messenger = RecordingMessenger()
        collaboration = SendCollaborationMessage(
            directory,
            topologies,
            authorizer,
            collaborations,
            messenger,
        )
        orchestrator = TaskOrchestrator(
            directory, topologies, tasks, collaboration, RecordingTaskPublisher()
        )
        repository_task = await orchestrator.assign(
            AssignTaskCommand(
                organization_id=organization_id,
                project_id=project_id,
                repository_id=target.id,
                assigned_by_agent_id=organization_leader.principal.id,
                assignee_agent_id=team.leader.id,
                title="Coordinate compatible pricing API change",
                instruction=confirmation.required[0].plan_summary,
                acceptance=("Old clients remain compatible", "Repository integration passes"),
            ),
            idempotency_key=f"e2e:{suffix}:repository-task",
        )
        worker_task = await orchestrator.assign(
            AssignTaskCommand(
                organization_id=organization_id,
                project_id=project_id,
                repository_id=target.id,
                parent_task_id=repository_task.id,
                assigned_by_agent_id=team.leader.id,
                assignee_agent_id=team.workers[0].id,
                title="Implement pricing schema and resolver",
                instruction="Implement the repository leader's approved pricing design",
                acceptance=("Old clients remain compatible", "Pricing tests pass"),
            ),
            idempotency_key=f"e2e:{suffix}:worker-task",
        )

        specification_service = SpecificationService(
            directory,
            topologies,
            specifications,
            ContextPublicationGateway(contexts),
            authorizer,
        )
        created = await specification_service.create(
            CreateSpecificationCommand(
                organization_id=organization_id,
                project_id=project_id,
                repository_id=target.id,
                task_id=worker_task.id,
                kind=SpecificationKind.TASK,
                title="Pricing API task specification",
                created_by_agent_id=team.leader.id,
                goal="Add an optional pricing field without breaking existing clients",
                acceptance=("Old clients remain compatible", "Pricing tests pass"),
                constraints=("Keep the current GraphQL field behavior",),
                tests=("pytest saleor/graphql/product/tests",),
                dependencies=("pricing-contract v1",),
                allowed_paths=("saleor/graphql/**", "saleor/product/**"),
                interface_changes=("New pricing field is nullable",),
            ),
            idempotency_key=f"e2e:{suffix}:task-spec",
        )
        submitted = await specification_service.submit(
            SubmitSpecificationCommand(created.id, team.leader.id, created.revision)
        )
        approved = await specification_service.approve(
            ApproveSpecificationCommand(
                submitted.id,
                team.leader.id,
                submitted.revision,
                freeze=True,
            )
        )
        context_ref = await specification_service.publish_to_context(
            PublishSpecificationContextCommand(approved.id, team.leader.id)
        )

        package = await BuildCodingAgentPackage(
            directory,
            topologies,
            tasks,
            specifications,
            authorizer,
        ).execute(
            BuildCodingAgentPackageCommand(
                organization_id=organization_id,
                project_id=project_id,
                repository_id=target.id,
                task_id=worker_task.id,
                worker_agent_id=team.workers[0].id,
            )
        )

        assert context_ref.content_hash == approved.current_version.content_hash
        assert await contexts.get_object(context_ref.context_object_id) is not None
        assert package.instruction == (
            "Add an optional pricing field without breaking existing clients"
        )
        assert package.allowed_paths == ("saleor/graphql/**", "saleor/product/**")
        assert package.test_commands == ("pytest saleor/graphql/product/tests",)
        assert len(package.context_files) == 1
        assert package.context_files[0].mount_path == ".repomesh/context/current-task.md"
        assert "docs-" not in package.context_files[0].content
        assert len(messenger.messages) == 2
    finally:
        await database.dispose()
