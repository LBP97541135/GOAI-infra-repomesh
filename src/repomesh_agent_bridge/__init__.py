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

This tier answers, and it works. It syncs the confirmed rooms, accepts
invitations into them, turns an explicit mention into exactly one turn behind a
real coding CLI, and says one honest thing back without losing or duplicating it
across a crash. Given ``--workspace-root`` it also consumes its own worker's
RepoMesh queue in this process: a mention that says ``start task <id>`` wakes a
governed run, the run executes through the Runner's own driver chain and its own
governance gates, and its lifecycle is narrated back into the thread that asked.
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
    SessionNotReady,
    WorkerBinding,
)
from .instance_lock import InstanceAlreadyRunning
from .ports import (
    CodingSessionPort,
    GovernedStartReceipt,
    GovernedTaskError,
    GovernedTaskPort,
    GovernedTaskRefused,
    GovernedTaskUnavailable,
    RoomBatch,
    RoomBody,
    RoomEvent,
    RoomInvite,
    RoomPort,
    RoomRefused,
    RoomTransportError,
    RoomUnavailable,
    TurnOutcome,
    TurnRequest,
    WorkerBindingPort,
)
from .runner_consumer import GovernedRuntime, RunnerConsumer

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
    "GovernedRuntime",
    "GovernedStartReceipt",
    "GovernedTaskError",
    "GovernedTaskPort",
    "GovernedTaskRefused",
    "GovernedTaskUnavailable",
    "InstanceAlreadyRunning",
    "RoomBatch",
    "RoomBody",
    "RoomEvent",
    "RoomInvite",
    "RoomNativeAgent",
    "RoomObservation",
    "RoomPort",
    "RoomRefused",
    "RoomTransportError",
    "RoomUnavailable",
    "RunnerConsumer",
    "SessionNotReady",
    "StartupOutcome",
    "TurnOutcome",
    "TurnRequest",
    "WorkerBinding",
    "WorkerBindingPort",
    "resolve_env_credential",
]
