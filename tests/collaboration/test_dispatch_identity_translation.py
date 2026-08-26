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

from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_orchestration_flow import build_flow

from repomesh.bootstrap.container import collaboration_routed_messenger
from repomesh.integrations.agentteams.control_plane import (
    AgentTeamsConflict,
    AgentTeamsResponseError,
    AgentTeamsUnavailable,
)
from repomesh.integrations.agentteams.matrix import AgentTeamsMatrixClient
from repomesh.modules.collaboration import (
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


def _client(control_plane) -> AgentTeamsMatrixClient:
    return AgentTeamsMatrixClient(
        "http://matrix.invalid",
        "test-token",
        control_plane=control_plane,
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
        )

    # The server's own sentence, unreworded — it is the whole actionable
    # content, and a wrapper that replaces it throws that away.
    assert str(raised.value) == "AgentTeams recipient Matrix identity is unavailable"
    # And it is still an AgentTeams refusal underneath, for anyone reading logs.
    assert isinstance(raised.value.__cause__, AgentTeamsUnavailable)
    assert control_plane.asked == ["repomesh-worker-b-checkout"]


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
            self, room_id, body, *, transaction_id, recipient_resource_name=None
        ) -> str:
            self.calls.append((room_id, body, transaction_id, recipient_resource_name))
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
    )

    assert event_id == "$event-1"
    assert inner.calls == [("!room:matrix.local", "body", "txn-1", "repomesh-worker-1")]
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

    with pytest.raises(CollaborationRouteUnavailable) as raised:
        await collaboration.send(command, idempotency_key="a-6-dispatch")

    assert "Matrix identity is unavailable" in str(raised.value)
    # Persisted, and persisted as failed — the round is repairable, and the
    # console can say which message never landed.
    failed = await collaboration._store.list_failed()
    assert len(failed) == 1
    assert failed[0][0].task_id == command.task_id
