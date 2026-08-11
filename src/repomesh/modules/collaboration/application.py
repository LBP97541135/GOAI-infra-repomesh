import asyncio
import hashlib
import json
import logging
from contextlib import suppress
from dataclasses import asdict
from uuid import UUID

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalReader,
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.collaboration.contracts import (
    CollaborationDeliveryStatus,
    CollaborationMessageKind,
    CollaborationMessageView,
    InboundMatrixMessage,
    MatrixInboundResult,
    SendCollaborationMessageCommand,
)
from repomesh.modules.collaboration.domain import (
    CollaborationConflict,
    CollaborationDenied,
    CollaborationMessage,
    CollaborationRouteUnavailable,
    parse_matrix_task_report,
)
from repomesh.modules.collaboration.ports import (
    CollaborationMessageStore,
    CollaborationMessenger,
    MatrixIdentityVerifier,
    ProcessedMatrixEventStore,
)
from repomesh.modules.identity_access.contracts import (
    AgentAuthorizationGateway,
    AuthorizationAction,
    AuthorizationRequest,
)
from repomesh.modules.project.contracts import (
    ProjectAgentTopologyView,
    ProjectTopologyReader,
    RepositoryTeamView,
)
from repomesh.modules.task_orchestration.contracts import (
    ReportTaskCommand,
    TaskReportGateway,
    TaskStatus,
)


class SendCollaborationMessage:
    def __init__(
        self,
        directory: AgentPrincipalReader,
        topologies: ProjectTopologyReader,
        authorizer: AgentAuthorizationGateway,
        store: CollaborationMessageStore,
        messenger: CollaborationMessenger,
    ) -> None:
        self._directory = directory
        self._topologies = topologies
        self._authorizer = authorizer
        self._store = store
        self._messenger = messenger

    async def send(
        self,
        command: SendCollaborationMessageCommand,
        *,
        idempotency_key: str,
    ) -> CollaborationMessageView:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")
        fingerprint = self._fingerprint(command)
        if existing := await self._store.get_by_idempotency_key(key):
            message, previous_fingerprint = existing
            if fingerprint != previous_fingerprint:
                raise CollaborationConflict(
                    "idempotency key was used for a different collaboration message"
                )
            if message.status is CollaborationDeliveryStatus.DELIVERED:
                return message.to_view()
            return await self._deliver(message, key)

        sender = await self._required_agent(command.sender_agent_id)
        recipient = await self._required_agent(command.recipient_agent_id)
        topology = await self._topologies.get_view(command.project_id)
        if topology is None:
            raise CollaborationDenied("project topology does not exist")
        decision = self._authorizer.authorize(
            sender,
            AuthorizationRequest(
                action=AuthorizationAction.COLLABORATION_MESSAGE,
                organization_id=command.organization_id,
                project_id=command.project_id,
                repository_id=command.repository_id,
                target_agent_id=recipient.id,
            ),
            topology=topology,
        )
        if not decision.allowed:
            raise CollaborationDenied(decision.reason)
        self._validate_message_direction(command.kind, sender, recipient)
        room_id, repository_id = self._route(sender, recipient, topology)
        if command.repository_id is not None and command.repository_id != repository_id:
            raise CollaborationDenied("message repository does not match routed team")

        message = CollaborationMessage(
            organization_id=command.organization_id,
            project_id=command.project_id,
            repository_id=repository_id,
            task_id=command.task_id,
            sender_agent_id=sender.id,
            recipient_agent_id=recipient.id,
            kind=command.kind,
            subject=command.subject.strip(),
            body=command.body.strip(),
            room_id=room_id,
            correlation_id=command.correlation_id or command.task_id or sender.id,
        )
        await self._store.add(
            message,
            idempotency_key=key,
            request_fingerprint=fingerprint,
        )
        return await self._deliver(message, key)

    async def _deliver(
        self, message: CollaborationMessage, transaction_id: str
    ) -> CollaborationMessageView:
        recipient = await self._required_agent(message.recipient_agent_id)
        try:
            event_id = await self._messenger.send_task(
                message.room_id,
                self._wire_payload(message),
                transaction_id=transaction_id,
                recipient_resource_name=recipient.agentteams_resource_name,
            )
        except Exception:
            await self._store.update(message.failed())
            raise
        delivered = message.delivered(event_id)
        await self._store.update(delivered)
        return delivered.to_view()

    async def _required_agent(self, agent_id: UUID) -> AgentPrincipalView:
        profile = await self._directory.get_view(agent_id)
        if profile is None:
            raise CollaborationDenied(f"agent does not exist: {agent_id}")
        if profile.status is not AgentPrincipalStatus.ACTIVE:
            raise CollaborationDenied("agent_disabled")
        return profile

    @staticmethod
    def _validate_message_direction(
        kind: CollaborationMessageKind,
        sender: AgentPrincipalView,
        recipient: AgentPrincipalView,
    ) -> None:
        downward = recipient.leader_agent_id == sender.id
        upward = sender.leader_agent_id == recipient.id
        if kind is CollaborationMessageKind.TASK_ASSIGNMENT and not downward:
            raise CollaborationDenied("tasks can only be assigned to a direct subordinate")
        if kind in {
            CollaborationMessageKind.TASK_REPORT,
            CollaborationMessageKind.PROGRESS,
            CollaborationMessageKind.QUESTION,
        } and not upward:
            raise CollaborationDenied(f"{kind.value} must be sent to the direct leader")
        if kind in {
            CollaborationMessageKind.ANSWER,
            CollaborationMessageKind.DECISION,
        } and not downward:
            raise CollaborationDenied(f"{kind.value} must be sent to a direct subordinate")
        if sender.role is AgentRole.WORKER and recipient.role is AgentRole.WORKER:
            raise CollaborationDenied("workers cannot communicate directly")

    @staticmethod
    def _route(
        sender: AgentPrincipalView,
        recipient: AgentPrincipalView,
        topology: ProjectAgentTopologyView,
    ) -> tuple[str, UUID]:
        team = SendCollaborationMessage._shared_team(sender.id, recipient.id, topology)
        if team is None:
            raise CollaborationRouteUnavailable("agents do not share a project route")
        if AgentRole.ORGANIZATION_LEADER in {sender.role, recipient.role}:
            room_id = team.leader_room_id
        else:
            room_id = team.room_id
        if not room_id:
            raise CollaborationRouteUnavailable("AgentTeams room is not ready")
        return room_id, team.repository_id

    @staticmethod
    def _shared_team(
        sender_id, recipient_id, topology: ProjectAgentTopologyView
    ) -> RepositoryTeamView | None:
        for team in topology.repository_teams:
            members = {team.leader_agent_id, *team.worker_agent_ids}
            participants = members | {topology.organization_leader_id}
            if sender_id in participants and recipient_id in participants:
                if topology.organization_leader_id in {sender_id, recipient_id}:
                    other = (
                        recipient_id
                        if sender_id == topology.organization_leader_id
                        else sender_id
                    )
                    if other != team.leader_agent_id:
                        continue
                return team
        return None

    @staticmethod
    def _wire_payload(message: CollaborationMessage) -> str:
        return json.dumps(
            {
                "schema": "repomesh.collaboration.v1",
                "message_id": str(message.id),
                "correlation_id": str(message.correlation_id),
                "project_id": str(message.project_id),
                "repository_id": str(message.repository_id),
                "task_id": str(message.task_id) if message.task_id else None,
                "sender_agent_id": str(message.sender_agent_id),
                "recipient_agent_id": str(message.recipient_agent_id),
                "kind": message.kind.value,
                "subject": message.subject,
                "body": message.body,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _fingerprint(command: SendCollaborationMessageCommand) -> str:
        encoded = json.dumps(
            asdict(command), sort_keys=True, default=str, separators=(",", ":")
        ).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class CollaborationDeliveryRetryWorker:
    """Retry persisted failed collaboration messages without duplicating delivery."""

    def __init__(
        self,
        store: CollaborationMessageStore,
        sender: SendCollaborationMessage,
        *,
        interval_seconds: float = 5.0,
    ) -> None:
        self._store = store
        self._sender = sender
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            for message, key in await self._store.list_failed():
                try:
                    await self._sender.send(
                        SendCollaborationMessageCommand(
                            organization_id=message.organization_id,
                            project_id=message.project_id,
                            repository_id=message.repository_id,
                            task_id=message.task_id,
                            sender_agent_id=message.sender_agent_id,
                            recipient_agent_id=message.recipient_agent_id,
                            kind=message.kind,
                            subject=message.subject,
                            body=message.body,
                            correlation_id=message.correlation_id,
                        ),
                        idempotency_key=key,
                    )
                except Exception:
                    logging.getLogger(__name__).exception(
                        "failed collaboration delivery will be retried",
                        extra={"message_id": str(message.id)},
                    )
            await asyncio.sleep(self._interval)


class ProcessMatrixTaskReport:
    def __init__(
        self,
        directory: AgentPrincipalReader,
        topologies: ProjectTopologyReader,
        identity_verifier: MatrixIdentityVerifier,
        processed_events: ProcessedMatrixEventStore,
        tasks: TaskReportGateway,
    ) -> None:
        self._directory = directory
        self._topologies = topologies
        self._identity_verifier = identity_verifier
        self._processed_events = processed_events
        self._tasks = tasks

    async def execute(self, message: InboundMatrixMessage) -> MatrixInboundResult:
        if await self._processed_events.contains(message.event_id):
            return MatrixInboundResult.DUPLICATE
        report = parse_matrix_task_report(message.body)
        if report is None:
            return MatrixInboundResult.IGNORED
        profile = await self._directory.get_view(report.sender_agent_id)
        if profile is None or profile.status is not AgentPrincipalStatus.ACTIVE:
            raise CollaborationDenied("inbound agent is missing or disabled")
        if not await self._identity_verifier.verify(profile, message.sender):
            raise CollaborationDenied("matrix sender does not match AgentTeams identity")
        topology = await self._topologies.get_view(report.project_id)
        if topology is None:
            raise CollaborationDenied("project topology does not exist")
        expected_room = self._report_room(profile, topology)
        if message.room_id != expected_room:
            raise CollaborationDenied("task report arrived through an unauthorized room")
        await self._tasks.report(
            ReportTaskCommand(
                task_id=report.task_id,
                reporter_agent_id=report.sender_agent_id,
                status=TaskStatus(report.status),
                summary=report.summary,
            ),
            idempotency_key=f"matrix:{message.event_id}",
        )
        await self._processed_events.add(
            message.event_id,
            project_id=report.project_id,
            task_id=report.task_id,
            sender_agent_id=report.sender_agent_id,
        )
        return MatrixInboundResult.PROCESSED

    @staticmethod
    def _report_room(
        profile: AgentPrincipalView, topology: ProjectAgentTopologyView
    ) -> str:
        for team in topology.repository_teams:
            if profile.id == team.leader_agent_id:
                if not team.leader_room_id:
                    break
                return team.leader_room_id
            if profile.id in team.worker_agent_ids:
                if not team.room_id:
                    break
                return team.room_id
        raise CollaborationRouteUnavailable("agent has no ready report room")
