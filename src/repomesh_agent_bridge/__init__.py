"""Room-Native Coding Agent Bridge (ADR 0004).

An operator-hosted process that binds one AgentTeams ``containerManaged: false``
Worker identity to one local coding CLI session: Matrix for conversation,
RepoMesh for governed execution. It is a separate process from the control
plane, it holds no AgentTeams management credential, and it never talks to the
AgentTeams Go controller — its cross-process contracts are
``contracts/agent-bridge/v1``.

The public surface is :class:`RoomNativeAgent`, the wire models, and the ports
with the types their signatures are written in — a caller that implements a port
cannot do so without the vocabulary that port speaks, so the two are exported
together. Everything else — the two-stage startup function, the supervisor, the
local SQLite state, the inbox and outbox, the adapters' internals — is
package-private in effect: importing them from outside is importing an
implementation detail.

This tier answers. It syncs the confirmed rooms, accepts invitations into them,
turns an explicit mention into exactly one turn, and says one honest thing back
without losing or duplicating it across a crash. What it does not yet have is a
coding CLI behind that conversation (PR 4) or governed execution behind that
(PR 5), so the session it assembles today answers from memory and spawns
nothing.
"""

from .application import RoomNativeAgent, StartupOutcome, resolve_env_credential
from .contracts import (
    BINDING_SCHEMA_VERSION,
    ENROLLMENT_SCHEMA_VERSION,
    ROOM_OBSERVATION_SCHEMA_VERSION,
    BindingRefused,
    BindingUnavailable,
    BridgeStartupError,
    CredentialRefs,
    EnrollmentInvalid,
    ExternalWorkerEnrollment,
    RoomObservation,
    WorkerBinding,
)
from .instance_lock import InstanceAlreadyRunning
from .ports import (
    CodingSessionPort,
    RoomBatch,
    RoomBody,
    RoomEvent,
    RoomInvite,
    RoomPort,
    TurnOutcome,
    TurnRequest,
    WorkerBindingPort,
)

__all__ = [
    "BINDING_SCHEMA_VERSION",
    "ENROLLMENT_SCHEMA_VERSION",
    "ROOM_OBSERVATION_SCHEMA_VERSION",
    "BindingRefused",
    "BindingUnavailable",
    "BridgeStartupError",
    "CodingSessionPort",
    "CredentialRefs",
    "EnrollmentInvalid",
    "ExternalWorkerEnrollment",
    "InstanceAlreadyRunning",
    "RoomBatch",
    "RoomBody",
    "RoomEvent",
    "RoomInvite",
    "RoomNativeAgent",
    "RoomObservation",
    "RoomPort",
    "StartupOutcome",
    "TurnOutcome",
    "TurnRequest",
    "WorkerBinding",
    "WorkerBindingPort",
    "resolve_env_credential",
]
