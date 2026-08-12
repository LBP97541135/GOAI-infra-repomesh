"""Defect A-10: a store that will not take the task package is a retry, not a 500.

A Worker is handed its work as files. The package goes to AgentTeams' shared
storage — a directory on the file channel, an S3 bucket on the object channel —
and the room message only points at it. When that upload was refused the store's
own exception escaped every translation on the materialize path and reached the
console as ``text/plain`` "Internal Server Error": found live 2026-08-12 with an
``InvalidAccessKeyId``, on a round that only needed the button again once the
credentials were right.

These tests drive the real publishers in
``repomesh.integrations.agentteams.task_publishing`` through the composition
root's wrapper. Nothing touches a network or a real MinIO: the object publisher
builds its ``Minio`` client without connecting, and the failures are injected at
the client call it would have made.
"""

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from minio.error import S3Error

from repomesh.bootstrap.container import storage_backed_task_publisher
from repomesh.integrations.agentteams.task_publishing import (
    AgentTeamsObjectTaskPublisher,
    AgentTeamsTaskPublisher,
)
from repomesh.modules.project.contracts import CheckpointGateDecision
from repomesh.modules.task_orchestration.application import TaskOrchestrator
from repomesh.modules.task_orchestration.contracts import (
    AssignTaskCommand,
    PublishedTaskPackage,
    TaskPublicationUnavailable,
    TaskStatus,
    TaskView,
)

from .test_plan_execution import Environment

#: The sentence the live acceptance walk actually got back, verbatim.
LIVE_S3_MESSAGE = (
    "S3 operation failed; code: InvalidAccessKeyId, message: The Access Key Id "
    "you provided does not exist in our records., resource: /agentteams-storage, "
    "bucket_name: agentteams-storage"
)


def _task() -> TaskView:
    return TaskView(
        id=uuid4(),
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_id=uuid4(),
        parent_task_id=None,
        assigned_by_agent_id=uuid4(),
        assignee_agent_id=uuid4(),
        title="Implement pricing",
        instruction="Own the repository-level pricing change.",
        acceptance=("Tests pass",),
        status=TaskStatus.ASSIGNED,
        result_summary=None,
        version=1,
    )


async def _publish(publisher, task: TaskView):
    return await publisher.publish(
        task,
        team_name="rm-team-c51f652f",
        room_id="!team:matrix.local",
        assignee_resource_name="rm-worker-1",
        idempotency_key="materialize-1:publication",
    )


def _object_publisher() -> AgentTeamsObjectTaskPublisher:
    """The real object publisher, pointed at a host it will never call."""

    return AgentTeamsObjectTaskPublisher(
        "http://storage.invalid:9000",
        access_key="wrong-key",
        secret_key="wrong-secret",
    )


def _s3_error(code: str = "InvalidAccessKeyId") -> S3Error:
    return S3Error(
        code=code,
        message="The Access Key Id you provided does not exist in our records.",
        resource="/agentteams-storage",
        request_id="17E0…",
        host_id="…",
        response=None,
        bucket_name="agentteams-storage",
    )


# ---------------------------------------------------------------------------
# The object channel — the variant the defect was found on
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_refused_upload_is_a_publication_refusal(monkeypatch) -> None:
    """The store's own sentence survives; only the exception family changes."""

    publisher = _object_publisher()

    def refuse(*_args, **_kwargs):
        raise _s3_error()

    monkeypatch.setattr(publisher._client, "put_object", refuse)

    with pytest.raises(TaskPublicationUnavailable) as raised:
        await _publish(storage_backed_task_publisher(publisher), _task())

    # The whole actionable content is the server's words — which code, which
    # bucket. A wrapper that reworded them would throw that away.
    assert "InvalidAccessKeyId" in str(raised.value)
    assert "agentteams-storage" in str(raised.value)
    # And it is still an S3 refusal underneath, for anyone reading logs.
    assert isinstance(raised.value.__cause__, S3Error)


@pytest.mark.asyncio
async def test_an_unreachable_store_reads_the_same_as_a_misconfigured_one(
    monkeypatch,
) -> None:
    """Connection failures are the other half of "cannot take this yet".

    ``minio`` lets ``urllib3``'s connection errors through, and a DNS or
    refused-connection failure surfaces as ``OSError``. Both mean the plane is
    not there, which is the same reading as credentials it will not accept.
    """

    from urllib3.exceptions import MaxRetryError, NewConnectionError

    publisher = _object_publisher()
    failures = (
        OSError("[Errno 111] Connection refused"),
        MaxRetryError(None, "http://storage.invalid:9000", NewConnectionError(None, "no route")),
    )
    for failure in failures:

        def refuse(*_args, _failure=failure, **_kwargs):
            raise _failure

        monkeypatch.setattr(publisher._client, "put_object", refuse)

        with pytest.raises(TaskPublicationUnavailable):
            await _publish(storage_backed_task_publisher(publisher), _task())


@pytest.mark.asyncio
async def test_a_store_that_does_not_keep_what_it_was_given_is_still_a_retry(
    monkeypatch,
) -> None:
    """The object channel verifies its own write and refuses on a mismatch."""

    publisher = _object_publisher()
    monkeypatch.setattr(publisher._client, "put_object", lambda *a, **k: None)

    class _Stored:
        def read(self):
            return json.dumps({"content_hash": "sha256:something-else"}).encode()

        def close(self):
            return None

        def release_conn(self):
            return None

    monkeypatch.setattr(publisher._client, "get_object", lambda *a, **k: _Stored())

    with pytest.raises(TaskPublicationUnavailable) as raised:
        await _publish(storage_backed_task_publisher(publisher), _task())

    assert "verification failed" in str(raised.value)


# ---------------------------------------------------------------------------
# The file channel — the same reading, a different vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_file_channel_that_cannot_write_is_a_publication_refusal(
    monkeypatch, tmp_path: Path
) -> None:
    """Both variants are covered because the *port* is what is wrapped.

    A full disk or a read-only mount stops the dispatch exactly as an
    unauthenticated bucket does, and the deployment that has no object storage
    configured is the one running this channel.
    """

    publisher = AgentTeamsTaskPublisher(tmp_path)

    def refuse(_path, _content):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(AgentTeamsTaskPublisher, "_atomic_write", staticmethod(refuse))

    with pytest.raises(TaskPublicationUnavailable) as raised:
        await _publish(storage_backed_task_publisher(publisher), _task())

    assert "No space left on device" in str(raised.value)
    assert isinstance(raised.value.__cause__, OSError)


@pytest.mark.asyncio
async def test_a_writable_file_channel_is_passed_straight_through(tmp_path: Path) -> None:
    """The wrapper is a translation and is otherwise invisible."""

    publisher = storage_backed_task_publisher(AgentTeamsTaskPublisher(tmp_path))
    task = _task()

    package = await _publish(publisher, task)

    assert package.team_name == "rm-team-c51f652f"
    assert package.task_path == f"teams/rm-team-c51f652f/shared/tasks/{task.id}"
    assert package.content_hash.startswith("sha256:")
    written = tmp_path / package.task_path
    assert (written / "spec.md").exists()
    assert (written / "meta.json").exists()
    # A second publication of the same package is the replay's no-op, and it
    # answers the same hash rather than refusing.
    assert (await _publish(publisher, task)).content_hash == package.content_hash


# ---------------------------------------------------------------------------
# What is *not* translated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_wrapper_does_not_dress_a_fault_as_a_wait(tmp_path: Path) -> None:
    """A task path already holding different content is a fault, not a wait.

    The store answered and the answer was no. Pressing materialize again cannot
    change it, and a 503 there would tell the operator to keep pressing a
    button that cannot work — the same line ``collaboration_routed_messenger``
    draws between ``AgentTeamsUnavailable`` and its siblings (A-6).
    """

    publisher = storage_backed_task_publisher(AgentTeamsTaskPublisher(tmp_path))
    task = _task()
    package = await _publish(publisher, task)

    # Rewrite the manifest so the next publication disagrees with it.
    manifest = tmp_path / package.task_path / "manifest.json"
    manifest.write_text(
        json.dumps({"content_hash": "sha256:a-different-package"}), encoding="utf-8"
    )

    with pytest.raises(ValueError) as raised:
        await _publish(publisher, task)

    assert not isinstance(raised.value, TaskPublicationUnavailable)


# ---------------------------------------------------------------------------
# The replay: this refusal lands *after* the task row is written
# ---------------------------------------------------------------------------


class RefusingOnceTaskPublisher:
    """A store that refuses the first upload and keeps every one after it.

    The live shape: the bucket was there, the credentials were not, and the
    task row had already been written when the upload was tried.
    """

    def __init__(self, refusals: int = 1) -> None:
        self._refusals = refusals
        self.published: list[tuple[UUID, str]] = []

    async def publish(self, task, **kwargs) -> PublishedTaskPackage:
        if self._refusals > 0:
            self._refusals -= 1
            raise _s3_error()
        self.published.append((task.id, kwargs["idempotency_key"]))
        return PublishedTaskPackage(
            kwargs["team_name"],
            f"teams/{kwargs['team_name']}/shared/tasks/{task.id}",
            "sha256:verified",
        )


class RecordingCollaboration:
    def __init__(self) -> None:
        self.sent: list[tuple[UUID, str]] = []

    async def send(self, command, *, idempotency_key: str):
        self.sent.append((command.task_id, idempotency_key))
        return None


class AlwaysOpenCheckpoints:
    async def operational_gate(self, project_id) -> CheckpointGateDecision:
        return CheckpointGateDecision(True, "project_active")

    async def evaluate(self, *_args, **_kwargs) -> CheckpointGateDecision:
        return CheckpointGateDecision(True, "open")


def _orchestrator(environment: Environment, publisher, collaboration):
    return TaskOrchestrator(
        environment.directory,
        environment.topologies,
        environment.tasks,
        collaboration,
        storage_backed_task_publisher(publisher),
        AlwaysOpenCheckpoints(),
    )


def _worker_command(environment: Environment) -> AssignTaskCommand:
    return AssignTaskCommand(
        organization_id=environment.organization_id,
        project_id=environment.project_id,
        repository_id=environment.repository_ids[0],
        assigned_by_agent_id=environment.leader_ids[0],
        assignee_agent_id=environment.worker_ids[0],
        title="Implement pricing",
        instruction="Own the repository-level pricing change.",
        acceptance=("Tests pass",),
    )


@pytest.mark.asyncio
async def test_the_task_row_survives_the_refused_upload() -> None:
    """The state A-10 leaves behind: a task written, a package unpublished.

    ``assign`` persists the row and *then* delivers, so the refusal arrives
    with the task already on file. This is what made the defect worse than its
    siblings — a round that had gone further than any of them, and a receipt
    that could not be read as "nothing happened".
    """

    environment = Environment()
    publisher = RefusingOnceTaskPublisher()
    orchestrator = _orchestrator(environment, publisher, RecordingCollaboration())

    with pytest.raises(TaskPublicationUnavailable):
        await orchestrator.assign(_worker_command(environment), idempotency_key="round-1")

    persisted = await environment.tasks.get_by_idempotency_key("round-1")
    assert persisted is not None
    # Written down, and nothing published for it.
    assert publisher.published == []


@pytest.mark.asyncio
async def test_the_same_key_publishes_the_task_the_first_press_could_not() -> None:
    """The acceptance criterion: pressing materialize again finishes the round.

    The addendum's live evidence is exactly this state — task rows claimed, and
    ``teams/rm-team-…/shared/tasks/`` holding nothing but ``.keep``. A replay
    that recognised the key and returned the row would leave the bucket empty
    forever, which is what it used to do.
    """

    environment = Environment()
    publisher = RefusingOnceTaskPublisher()
    collaboration = RecordingCollaboration()
    orchestrator = _orchestrator(environment, publisher, collaboration)

    with pytest.raises(TaskPublicationUnavailable):
        await orchestrator.assign(_worker_command(environment), idempotency_key="round-1")
    stranded = await environment.tasks.get_by_idempotency_key("round-1")
    assert stranded is not None

    view = await orchestrator.assign(_worker_command(environment), idempotency_key="round-1")

    # Same task, now published and now dispatched.
    assert view.id == stranded[0].id
    assert publisher.published == [(stranded[0].id, "round-1:publication")]
    assert collaboration.sent == [(stranded[0].id, "round-1:message")]
    # And exactly one task row: the replay found it rather than writing another.
    assert len(await environment.tasks.list_by_project(environment.project_id)) == 1


@pytest.mark.asyncio
async def test_the_dispatch_is_only_sent_once_the_package_is_stored() -> None:
    """Order matters: a Worker must not be pointed at a package that is absent.

    The room message carries the task path. Sending it before the upload lands
    would tell the Worker to go read a file that is not there — so the refusal
    has to stop the dispatch too, and the replay has to do both in order.
    """

    environment = Environment()
    collaboration = RecordingCollaboration()
    orchestrator = _orchestrator(environment, RefusingOnceTaskPublisher(), collaboration)

    with pytest.raises(TaskPublicationUnavailable):
        await orchestrator.assign(_worker_command(environment), idempotency_key="round-1")

    assert collaboration.sent == []
