"""Room-Native Coding Agent Bridge (ADR 0004).

An operator-hosted process that binds one AgentTeams ``containerManaged: false``
Worker identity to one local coding CLI session: Matrix for conversation,
RepoMesh for governed execution. It is a separate process from the control
plane, it holds no AgentTeams management credential, and it never talks to the
AgentTeams Go controller — its cross-process contracts are
``contracts/agent-bridge/v1``.

The public surface is :class:`RoomNativeAgent` and the wire models. Everything
else — the two-stage startup function, the instance lock, the adapters' internals
— is package-private in effect: importing them from outside is importing an
implementation detail.

This tier is conversation-free scaffolding: startup validation, preflight, the
instance lock and the process lifecycle. Matrix (PR 3), a real coding CLI behind
a restricted process factory (PR 4) and governed execution (PR 5) follow.
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
from .ports import CodingSessionPort, RoomPort, WorkerBindingPort

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
    "RoomNativeAgent",
    "RoomObservation",
    "RoomPort",
    "StartupOutcome",
    "WorkerBinding",
    "WorkerBindingPort",
    "resolve_env_credential",
]
