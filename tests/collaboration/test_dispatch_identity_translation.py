"""Defect A-6: a recipient with no Matrix identity is a retry, not a 500.

The gateway asks the control plane for the recipient's Matrix user id before it
puts a task in a room, and raises ``AgentTeamsUnavailable`` when the worker has
none. On the materialize path that escaped every translation and reached the
client as a bare ``text/plain`` 500 — the one answer that tells an operator to
file a bug about a round that only needed the button pressed again.

These tests drive the real ``matrix.py`` raise (it happens before any HTTP, so
nothing here touches a network) through the composition root's wrapper and out
the far side as the collaboration port's own retryable refusal.
"""

import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from test_orchestration_flow import build_flow

from repomesh.bootstrap.container import collaboration_routed_messenger
from repomesh.integrations.agentteams.control_plane import (
    AgentTeamsConflict,
    AgentTeamsResponseError,
    AgentTeamsUnavailable,
)
from repomesh.integrations.agentteams.identity import (
    AgentTeamsRecipientMatrixIdentityResolver,
)
from repomesh.integrations.agentteams.matrix import AgentTeamsMatrixClient
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.collaboration import (
    CollaborationDeliveryDeferred,
    CollaborationMessageKind,
    SendCollaborationMessageCommand,
)
from repomesh.modules.collaboration.contracts import CollaborationRouteUnavailable


class _ControlPlaneWithoutIdentity:
    """A worker the controller knows about but has given no Matrix user id.

    This is the live shape: the runtime projection registered the worker, the
    container died on boot, and the controller kept a resource with a null
    ``matrix_user_id``.
    """

    def __init__(self, matrix_user_id: str | None = None) -> None:
        self.matrix_user_id = matrix_user_id
        self.asked: list[str] = []

    async def get_worker(self, name: str):
        self.asked.append(name)
        return SimpleNamespace(name=name, matrix_user_id=self.matrix_user_id)


class _ControlPlaneWithManagerIdentity:
    """The live AgentTeams shape for an Organization Leader recipient."""

    def __init__(self) -> None:
        self.worker_lookups: list[str] = []
        self.manager_lookups: list[str] = []

    async def get_worker(self, name: str):
        self.worker_lookups.append(name)
        return None

    async def get_manager(self, name: str):
        self.manager_lookups.append(name)
        return SimpleNamespace(
            name=name,
            matrix_user_id=f"@{name}:matrix.local",
        )


def _client(control_plane) -> AgentTeamsMatrixClient:
    return AgentTeamsMatrixClient(
        "http://matrix.invalid",
        "test-token",
        recipient_identity_resolver=AgentTeamsRecipientMatrixIdentityResolver(control_plane),
    )


@pytest.mark.asyncio
async def test_a_recipient_without_a_matrix_identity_is_a_route_refusal() -> None:
    """The integration's words survive; only the exception family changes."""

    control_plane = _ControlPlaneWithoutIdentity()
    messenger = collaboration_routed_messenger(_client(control_plane))

    with pytest.raises(CollaborationRouteUnavailable) as raised:
        await messenger.send_task(
            "!room:matrix.local",
            "do the thing",
            transaction_id="txn-1",
            recipient_resource_name="repomesh-worker-b-checkout",
            recipient_role=AgentRole.WORKER,
        )

    # The server's own sentence, unreworded — it is the whole actionable
    # content, and a wrapper that replaces it throws that away.
    assert str(raised.value) == "AgentTeams recipient Matrix identity is unavailable"
    # And it is still an AgentTeams refusal underneath, for anyone reading logs.
    assert isinstance(raised.value.__cause__, AgentTeamsUnavailable)
    assert control_plane.asked == ["repomesh-worker-b-checkout"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    [AgentRole.REPOSITORY_LEADER, AgentRole.WORKER],
)
async def test_non_manager_recipient_roles_use_the_worker_collection(role: AgentRole) -> None:
    control_plane = _ControlPlaneWithManagerIdentity()
    resolver = AgentTeamsRecipientMatrixIdentityResolver(control_plane)

    resolved = await resolver.resolve(role, "native-repository-member")

    assert resolved is None
    assert control_plane.worker_lookups == ["native-repository-member"]
    assert control_plane.manager_lookups == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("recipient_resource_name", "recipient_role", "error", "message"),
    [
        ("native-worker", None, ValueError, "recipient_role is required"),
        (None, AgentRole.WORKER, ValueError, "recipient_resource_name is required"),
        ("native-worker", AgentRole.WORKER, RuntimeError, "resolver is required"),
    ],
)
async def test_recipient_identity_configuration_fails_closed(
    recipient_resource_name: str | None,
    recipient_role: AgentRole | None,
    error: type[Exception],
    message: str,
) -> None:
    client = AgentTeamsMatrixClient("http://matrix.invalid", "test-token")
    try:
        with pytest.raises(error, match=message):
            await client.send_task(
                "!room:matrix.local",
                "do the thing",
                transaction_id="txn-invalid-recipient",
                recipient_resource_name=recipient_resource_name,
                recipient_role=recipient_role,
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_wrapper_does_not_dress_a_fault_as_a_wait() -> None:
    """Only ``AgentTeamsUnavailable`` is retryable; its siblings are faults.

    A 409 conflict or a bad response means the plane answered and the answer
    was wrong. Translating those to 503 would tell the operator to keep
    pressing a button that cannot start working.
    """

    for error in (
        AgentTeamsResponseError(500, "Matrix task delivery failed"),
        AgentTeamsConflict("existing AgentTeams worker differs in: runtime"),
    ):

        class Raising:
            def __init__(self, failure: Exception) -> None:
                self.failure = failure

            async def send_task(self, room_id, body, *, transaction_id, **kwargs):
                raise self.failure

        messenger = collaboration_routed_messenger(Raising(error))
        with pytest.raises(type(error)):
            await messenger.send_task("!room:matrix.local", "body", transaction_id="t")


@pytest.mark.asyncio
async def test_a_reachable_recipient_is_passed_straight_through() -> None:
    """The wrapper is delivery-only and otherwise invisible."""

    class Recording:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        async def send_task(
            self,
            room_id,
            body,
            *,
            transaction_id,
            recipient_resource_name=None,
            recipient_role=None,
        ) -> str:
            self.calls.append(
                (room_id, body, transaction_id, recipient_resource_name, recipient_role)
            )
            return "$event-1"

        @property
        def whoami(self):
            return "@repomesh:matrix.local"

    inner = Recording()
    messenger = collaboration_routed_messenger(inner)

    event_id = await messenger.send_task(
        "!room:matrix.local",
        "body",
        transaction_id="txn-1",
        recipient_resource_name="repomesh-worker-1",
        recipient_role=AgentRole.WORKER,
    )

    assert event_id == "$event-1"
    assert inner.calls == [
        (
            "!room:matrix.local",
            "body",
            "txn-1",
            "repomesh-worker-1",
            AgentRole.WORKER,
        )
    ]
    # Everything that is not delivery is still the gateway's own.
    assert messenger.whoami == "@repomesh:matrix.local"


@pytest.mark.asyncio
async def test_the_module_sees_the_refusal_and_leaves_a_failed_message() -> None:
    """The seam composes: the sender records the failure and re-raises it.

    This is the state a mid-dispatch A-6 leaves behind, and it differs from
    B-11's in one way worth naming: B-11 raised in ``_route``, *before* the
    message was stored, so nothing was persisted. Here the message is stored
    first and marked ``failed``, which is what the delivery retry worker
    replays.
    """

    (
        organization_id,
        repository_id,
        project_id,
        organization_leader,
        repository_team,
        _messenger,
        collaboration,
        _orchestrator,
        _directory,
        _topologies,
    ) = await build_flow(
        messenger=collaboration_routed_messenger(_client(_ControlPlaneWithoutIdentity()))
    )

    command = SendCollaborationMessageCommand(
        organization_id=organization_id,
        project_id=project_id,
        task_id=uuid4(),
        sender_agent_id=organization_leader.id,
        recipient_agent_id=repository_team.leader.id,
        kind=CollaborationMessageKind.TASK_ASSIGNMENT,
        subject="Implement pricing API",
        body="Own the repository-level pricing change.",
    )

    with pytest.raises(CollaborationDeliveryDeferred) as raised:
        await collaboration.send(command, idempotency_key="a-6-dispatch")

    assert "Matrix identity is unavailable" in str(raised.value)
    # Persisted, and persisted as failed — the round is repairable, and the
    # console can say which message never landed.
    failed = await collaboration._store.list_failed()
    assert len(failed) == 1
    assert failed[0][0].task_id == command.task_id
    assert raised.value.message_id == failed[0][0].id


@pytest.mark.asyncio
async def test_a_repository_leader_report_resolves_its_manager_as_a_manager() -> None:
    """D-M7-1: the final Leader -> Manager hop uses the Manager collection.

    Organization Leaders are AgentTeams Manager resources, not Workers.  This
    drives the same Collaboration -> Matrix path as the live review roll-up;
    asking ``get_worker`` for the recipient is therefore the exact defect, not
    a nearby adapter detail.
    """

    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"event_id": "$manager-report"})

    control_plane = _ControlPlaneWithManagerIdentity()
    client = AgentTeamsMatrixClient(
        "http://matrix.invalid",
        "test-token",
        transport=httpx.MockTransport(handler),
        recipient_identity_resolver=AgentTeamsRecipientMatrixIdentityResolver(
            control_plane
        ),
    )
    try:
        (
            organization_id,
            repository_id,
            project_id,
            organization_leader,
            repository_team,
            _messenger,
            collaboration,
            _orchestrator,
            _directory,
            _topologies,
        ) = await build_flow(messenger=collaboration_routed_messenger(client))

        delivered = await collaboration.send(
            SendCollaborationMessageCommand(
                organization_id=organization_id,
                project_id=project_id,
                repository_id=repository_id,
                task_id=uuid4(),
                sender_agent_id=repository_team.leader.id,
                recipient_agent_id=organization_leader.id,
                kind=CollaborationMessageKind.TASK_REPORT,
                subject="Repository review approved",
                body="All worker evidence passed review.",
            ),
            idempotency_key="d-m7-1-manager-report",
        )
    finally:
        await client.close()

    assert delivered.event_id == "$manager-report"
    assert control_plane.manager_lookups == ["native-org-leader"]
    assert control_plane.worker_lookups == []
    assert sent[0]["m.mentions"]["user_ids"] == ["@native-org-leader:matrix.local"]
