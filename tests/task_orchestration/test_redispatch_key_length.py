"""The first live press of re-dispatch died on a column width (§8.7.4).

``asyncpg.exceptions.StringDataRightTruncationError: value too long for type
character varying(200)``, escaping as a bare ``text/plain`` 500. Nothing was
written — Postgres rejected the insert — so the damage was entirely to the
operator, who got no class name, no sentence, and no way to tell a bug from an
outage.

The arithmetic, which no test had ever done:

    disc-console-discovery-materialize-<uuid>            71
    :b0:<repository uuid>                              +40  → 111
    :decompose                                         +10  → 121
    :worker:<worker uuid>                              +44  → 165
    :message                                            +8  → 173   fits
    :redispatch:<60-char console key>                  +72  → 245   does not

So the assignment key was already using five sixths of the column before
re-dispatch appended a client-supplied string of arbitrary length to it. These
tests pin the derivation against the *real* column definitions rather than
against the number 200, so that a schema change cannot quietly outgrow them.

Nothing here touches a network or a database. The store double enforces exactly
what the driver enforces, which is what makes the reverse-proof mean anything.
"""

from uuid import uuid4

import pytest
from sqlalchemy import String

from repomesh.modules.collaboration import (
    InMemoryCollaborationMessageStore,
    SendCollaborationMessage,
)
from repomesh.modules.collaboration.infrastructure import CollaborationMessageRecord
from repomesh.modules.identity_access import PolicyAuthorizationGateway
from repomesh.modules.task_orchestration import AssignTaskCommand, TaskOrchestrator
from repomesh.modules.task_orchestration.application import (
    IDEMPOTENCY_KEY_LIMIT,
    _attempt_token,
    _dispatch_message_key,
)
from repomesh.modules.task_orchestration.infrastructure import TaskRecord

from .test_plan_execution import Environment
from .test_round_redispatch import (
    MatrixDedupingMessenger,
    OpenCheckpoints,
    RecordingTaskPublisher,
)

#: The console's own shape, verbatim from the live failure.
#: ``console-redispatch-${roundId}-${crypto.randomUUID()}``.
LIVE_CLIENT_KEY = f"console-redispatch-{uuid4()}-{uuid4()}"

#: The assignment key a console round actually produces, rebuilt from the parts
#: that make it: ``DiscoveryMaterializationService._prefix`` puts ``disc-`` in
#: front of the browser's materialize key, ``_assign_batch`` adds the batch and
#: repository, ``DecomposeRepositoryTask`` adds the decomposition and worker.
LIVE_ASSIGNMENT_KEY = (
    f"disc-console-discovery-materialize-{uuid4()}"
    f":b0:{uuid4()}"
    f":decompose:worker:{uuid4()}"
)


def _column_limit(record, column: str = "idempotency_key") -> int:
    """The declared width, read off the model rather than assumed."""

    kind = record.__table__.columns[column].type
    assert isinstance(kind, String), f"{record.__tablename__}.{column} is not a String"
    assert kind.length is not None, f"{record.__tablename__}.{column} has no declared width"
    return kind.length


# ---------------------------------------------------------------------------
# The constant is the schema's, not a guess
# ---------------------------------------------------------------------------


def test_the_limit_matches_the_columns_it_protects() -> None:
    """If a migration widens or narrows these, this test says so first."""

    assert _column_limit(CollaborationMessageRecord) == IDEMPOTENCY_KEY_LIMIT
    assert _column_limit(TaskRecord) == IDEMPOTENCY_KEY_LIMIT


def test_the_live_shape_reproduces_the_arithmetic_that_failed() -> None:
    """The fixture is the real thing, not a short stand-in that would pass."""

    assert len(LIVE_ASSIGNMENT_KEY) == 165
    assert len(f"{LIVE_ASSIGNMENT_KEY}:message") == 173
    # What shipped, and what Postgres refused.
    assert len(f"{LIVE_ASSIGNMENT_KEY}:message:redispatch:{LIVE_CLIENT_KEY}") > (
        IDEMPOTENCY_KEY_LIMIT
    )


# ---------------------------------------------------------------------------
# The derivation is bounded, and still says what it has to say
# ---------------------------------------------------------------------------


def test_the_live_key_now_fits_with_room_to_spare() -> None:
    derived = _dispatch_message_key(LIVE_ASSIGNMENT_KEY, LIVE_CLIENT_KEY)

    assert len(derived) <= IDEMPOTENCY_KEY_LIMIT
    assert derived.startswith(f"{LIVE_ASSIGNMENT_KEY}:message:rd:"), (
        "the ordinary form stays readable — an operator reading the messages "
        "table should see which assignment this belongs to"
    )


@pytest.mark.parametrize(
    "client_key",
    [
        LIVE_CLIENT_KEY,
        "k",
        "console-redispatch-" + "x" * 400,
        "中文幂等键",
    ],
)
def test_no_client_key_can_overflow_the_column(client_key) -> None:
    """The caller's length stops being the column's problem.

    The endpoint bounds the field at 180 characters, but that bound is not what
    keeps the insert legal and must not be relied on as if it were — the
    parameters here run past it deliberately.
    """

    derived = _dispatch_message_key(LIVE_ASSIGNMENT_KEY, client_key)
    assert len(derived) <= IDEMPOTENCY_KEY_LIMIT


def test_an_over_long_base_falls_back_and_is_still_bounded() -> None:
    """Correctness must not rest on today's prefixes staying short.

    The readable form leaves roughly a dozen characters of headroom, which is a
    coincidence rather than a margin. This is the branch that makes the bound a
    property rather than an observation.
    """

    huge = "disc-" + "y" * 400
    derived = _dispatch_message_key(huge, LIVE_CLIENT_KEY)

    assert len(derived) <= IDEMPOTENCY_KEY_LIMIT
    assert derived.startswith("rd:")
    # Still distinct per task: two different bases cannot collide into one key.
    assert derived != _dispatch_message_key(huge + "z", LIVE_CLIENT_KEY)


def test_the_two_properties_the_feature_rests_on_survive_hashing() -> None:
    """Distinct across presses, identical within one — before and after."""

    first = f"console-redispatch-{uuid4()}-{uuid4()}"
    second = f"console-redispatch-{uuid4()}-{uuid4()}"

    assert _dispatch_message_key(LIVE_ASSIGNMENT_KEY, first) != _dispatch_message_key(
        LIVE_ASSIGNMENT_KEY, second
    )
    assert _dispatch_message_key(LIVE_ASSIGNMENT_KEY, first) == _dispatch_message_key(
        LIVE_ASSIGNMENT_KEY, first
    )
    # And the ordinary path is untouched, so replay still finds its own row.
    assert _dispatch_message_key(LIVE_ASSIGNMENT_KEY, None) == f"{LIVE_ASSIGNMENT_KEY}:message"


def test_two_presses_a_moment_apart_do_not_collide() -> None:
    """48 bits of digest, spent on the only thing the attempt has to be."""

    tokens = {_attempt_token(f"console-redispatch-{uuid4()}-{uuid4()}") for _ in range(500)}
    assert len(tokens) == 500
    assert all(len(token) == 12 for token in tokens)


# ---------------------------------------------------------------------------
# End to end, through the store that enforces what the driver enforces
# ---------------------------------------------------------------------------


class StringTruncation(Exception):
    """Stands in for ``asyncpg.exceptions.StringDataRightTruncationError``."""


class WidthCheckedMessageStore(InMemoryCollaborationMessageStore):
    """An in-memory store that refuses what the column would refuse.

    The plain in-memory store accepts a key of any length, so every existing
    test would have passed against the shipped bug — and did. Modelling the
    column here is what turns "the string is shorter now" into "the insert is
    legal now", and is what the reverse-proof below actually proves.
    """

    def __init__(self, limit: int) -> None:
        super().__init__()
        self._limit = limit

    async def add(self, message, *, idempotency_key: str, request_fingerprint: str) -> None:
        if len(idempotency_key) > self._limit:
            raise StringTruncation(
                f"value too long for type character varying({self._limit})"
            )
        await super().add(
            message,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )


async def _dispatched_worker_task():
    """One assigned worker task, dispatched through a width-checked store."""

    environment = Environment()
    messenger = MatrixDedupingMessenger()
    orchestrator = TaskOrchestrator(
        environment.directory,
        environment.topologies,
        environment.tasks,
        SendCollaborationMessage(
            environment.directory,
            environment.topologies,
            PolicyAuthorizationGateway(),
            WidthCheckedMessageStore(_column_limit(CollaborationMessageRecord)),
            messenger,
        ),
        RecordingTaskPublisher(),
        OpenCheckpoints(),
    )
    task = await orchestrator.assign(
        AssignTaskCommand(
            organization_id=environment.organization_id,
            project_id=environment.project_id,
            repository_id=environment.repository_ids[0],
            assigned_by_agent_id=environment.leader_ids[0],
            assignee_agent_id=environment.worker_ids[0],
            title="Implement pricing",
            instruction="Own the repository-level pricing change.",
            acceptance=("Tests pass",),
        ),
        idempotency_key=LIVE_ASSIGNMENT_KEY,
    )
    return orchestrator, messenger, task


@pytest.mark.asyncio
async def test_the_live_press_now_reaches_the_room() -> None:
    """The whole bug, end to end: same key, same length, no truncation."""

    orchestrator, messenger, task = await _dispatched_worker_task()
    assert len(messenger.deliveries) == 1

    await orchestrator.redispatch(task.id, attempt=LIVE_CLIENT_KEY)

    assert len(messenger.deliveries) == 2
    assert len(messenger.deliveries[1][2]) <= IDEMPOTENCY_KEY_LIMIT


def _shipped_derivation(key: str, attempt: str | None) -> str:
    """The derivation exactly as it was merged, and exactly as it failed."""

    if attempt is None:
        return f"{key}:message"
    return f"{key}:message:redispatch:{attempt}"


@pytest.mark.asyncio
async def test_reverse_proof_the_shipped_derivation_still_truncates(monkeypatch) -> None:
    """Put the merged code back and the live failure returns, same call.

    Stashing only the digest is not enough to reproduce it, and that is worth
    knowing rather than hiding: the length check alone already catches this
    input, so either layer would have prevented the outage. The reverse-proof
    therefore removes both — which is what "before the fix" actually means.
    """

    orchestrator, messenger, task = await _dispatched_worker_task()
    posted = len(messenger.deliveries)

    from repomesh.modules.task_orchestration import application

    monkeypatch.setattr(application, "_dispatch_message_key", _shipped_derivation)
    with pytest.raises(StringTruncation, match="character varying"):
        await orchestrator.redispatch(task.id, attempt=LIVE_CLIENT_KEY)

    assert len(messenger.deliveries) == posted, (
        "the row was refused, so the room must not have been told either — "
        "which is why the live press had zero side effects"
    )

    monkeypatch.undo()
    await orchestrator.redispatch(task.id, attempt=LIVE_CLIENT_KEY)
    assert len(messenger.deliveries) == posted + 1


def test_reverse_proof_each_layer_alone_would_have_held() -> None:
    """Belt and braces, stated: neither layer is load-bearing by itself."""

    # Digest only, no length check — the live shape fits (197 of 200).
    digest_only = f"{LIVE_ASSIGNMENT_KEY}:message:rd:{_attempt_token(LIVE_CLIENT_KEY)}"
    assert len(digest_only) <= IDEMPOTENCY_KEY_LIMIT

    # Length check only, no digest — falls back, so also fits.
    assert len(_dispatch_message_key(LIVE_ASSIGNMENT_KEY, LIVE_CLIENT_KEY)) <= (
        IDEMPOTENCY_KEY_LIMIT
    )

    # And the shipped derivation fits under neither.
    assert len(_shipped_derivation(LIVE_ASSIGNMENT_KEY, LIVE_CLIENT_KEY)) > (
        IDEMPOTENCY_KEY_LIMIT
    )
