"""Project a RepoMesh Worker task into AgentTeams Team-scoped storage.

Two layouts leave here. The v1 package (``spec.md`` + ``meta.json`` +
``manifest.json``) is what the local-CLI path has always published and is
untouched when ``publish`` is called without a ``package``. The v2 package
(spec §4.2 M3, ``contracts/agentteams-task/v2/``) is one hosted-native
attempt: a directory named after the attempt id holding the worker's spec,
the copaw-native ``meta.json``, the platform's control data under ``base/``
(which copaw never pushes back, spike S-9) and, for a review, the candidate
under ``review/``.

Both layouts are assembled exactly once, as an ordered list of
``(relative path, bytes, content type)`` plus the manifest, and the two
adapters only store and read bytes. That is what makes the disk channel and
the S3 channel produce the same bytes and the same ``content_hash`` — the
guarantee the observer, the verifier and the conflict check all lean on.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from repomesh.integrations.agentteams.task_package import (
    HELPER_COMMANDS,
    HELPER_PATH,
    render_construction_spec,
    render_review_spec,
)
from repomesh.modules.task_orchestration.contracts import (
    PackageInputs,
    PublishedTaskPackage,
    TaskAssignmentPublisher,
    TaskView,
)

V1_SCHEMA = "repomesh.agentteams-task.v1"
V2_SCHEMA = "repomesh.agentteams-task.v2"
V2_PACKAGE_SCHEMA = "repomesh.agentteams-task.v2/package"

_MARKDOWN = "text/markdown"
_JSON = "application/json"
_SHELL = "text/x-shellscript"
_DIFF = "text/x-diff"
_BINARY = "application/octet-stream"

FileBytes = Sequence[tuple[str, bytes]]


@dataclass(frozen=True, slots=True)
class PackageFile:
    path: str
    content: bytes
    content_type: str


@dataclass(frozen=True, slots=True)
class AssembledPackage:
    """A task package ready to store: every file but the manifest, then the manifest.

    ``content_hash`` is recomputable from the stored bytes with ``hash_files``,
    which is how a publish verifies itself after writing and how a replay
    proves the directory already holds this very package.
    """

    task_path: str
    files: tuple[PackageFile, ...]
    manifest: bytes
    content_hash: str
    hash_files: Callable[[FileBytes], str]

    @property
    def manifest_path(self) -> str:
        return f"{self.task_path}/manifest.json"


def _json_text(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


# ---------------------------------------------------------------------------
# v1 — unchanged layout
# ---------------------------------------------------------------------------


def _v1_content_hash(files: FileBytes) -> str:
    """``sha256(spec + NUL + meta)`` — the digest the v1 manifest has always carried."""

    by_path = dict(files)
    return _sha256(by_path["spec.md"] + b"\0" + by_path["meta.json"])


def _render_v1_spec(task: TaskView) -> str:
    acceptance = "\n".join(f"- {item}" for item in task.acceptance)
    database = task.database_change
    database_section = (
        "\n## Database change requirements\n\n"
        f"- Declared: {str(database.declared).lower()}\n"
        f"- Required: {str(database.required).lower()}\n"
        f"- Change kinds: {', '.join(item.value for item in database.change_kinds) or 'none'}\n"
        f"- Affected tables: {', '.join(database.affected_tables) or 'none'}\n"
        f"- Migration required: {str(database.migration_required).lower()}\n"
        f"- Backfill required: {str(database.backfill_required).lower()}\n"
        f"- Required checks: {', '.join(database.required_checks) or 'none'}\n"
    )
    if database.required:
        database_section += (
            "\nWrite structured evidence to `.repomesh/database-change-report.json` "
            "with migrationFiles, backfillFiles, affectedTables, and checks "
            "[{name, exitCode}]. RepoMesh removes this control report before committing.\n"
        )
    return (
        f"# {task.title}\n\n"
        f"## Current task\n\n{task.instruction}\n\n"
        f"## Acceptance criteria\n\n{acceptance}\n{database_section}"
    )


def _database_change(task: TaskView) -> dict[str, object]:
    requirement = task.database_change
    return {
        "declared": requirement.declared,
        "required": requirement.required,
        "change_kinds": [item.value for item in requirement.change_kinds],
        "affected_tables": list(requirement.affected_tables),
        "migration_required": requirement.migration_required,
        "backfill_required": requirement.backfill_required,
        "required_checks": list(requirement.required_checks),
    }


def assemble_v1_package(
    task: TaskView,
    *,
    team_name: str,
    room_id: str,
    assignee_resource_name: str,
    idempotency_key: str,
) -> AssembledPackage:
    task_path = f"teams/{team_name}/shared/tasks/{task.id}"
    spec = _render_v1_spec(task).encode()
    meta = _json_text(
        {
            "task_id": str(task.id),
            "project_id": str(task.project_id),
            "task_title": task.title,
            "assigned_to": assignee_resource_name,
            "room_id": room_id,
            "status": "assigned",
            "depends_on": [],
            "database_change": _database_change(task),
            "repomesh": {
                "organization_id": str(task.organization_id),
                "repository_id": str(task.repository_id),
                "parent_task_id": str(task.parent_task_id) if task.parent_task_id else None,
                "idempotency_key": idempotency_key,
            },
        }
    ).encode()
    files = (
        PackageFile("spec.md", spec, _MARKDOWN),
        PackageFile("meta.json", meta, _JSON),
    )
    content_hash = _v1_content_hash([(item.path, item.content) for item in files])
    manifest = (
        json.dumps(
            {"schema": V1_SCHEMA, "content_hash": content_hash, "files": ["meta.json", "spec.md"]},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()
    return AssembledPackage(task_path, files, manifest, content_hash, _v1_content_hash)


# ---------------------------------------------------------------------------
# v2 — one hosted-native attempt
# ---------------------------------------------------------------------------


def _v2_content_hash(files: FileBytes) -> str:
    """sha256 over ``"<path>\\0<digest>\\n"`` for every file, sorted by path.

    The reference implementation is the wave-0 spike's ``write_manifest``;
    ``manifest.json`` itself is never part of the input.
    """

    ordered = sorted((path, content) for path, content in files if path != "manifest.json")
    joined = "".join(f"{path}\0{_sha256(content)}\n" for path, content in ordered)
    return _sha256(joined.encode())


def assemble_v2_package(
    task: TaskView,
    package: PackageInputs,
    *,
    team_name: str,
    room_id: str,
    assignee_resource_name: str,
) -> AssembledPackage:
    attempt_id = str(package.attempt_id)
    task_path = f"teams/{team_name}/shared/tasks/{attempt_id}"
    policy = package.policy
    repomesh_block: dict[str, object] = {
        "kind": package.kind,
        "task_id": str(task.id),
        "attempt_id": attempt_id,
        "generation": package.generation,
        "budget_seconds": package.budget_seconds,
        "base_sha": package.base_sha,
        "repository_id": str(task.repository_id),
        "organization_id": str(task.organization_id),
        "package": "base/package.json",
    }
    files: list[PackageFile] = []

    if package.kind == "construction":
        task_title = task.title
        spec = render_construction_spec(
            title=task.title,
            attempt_id=attempt_id,
            generation=package.generation,
            task_id=str(task.id),
            base_sha=package.base_sha,
            budget_seconds=package.budget_seconds,
            instruction=task.instruction,
            acceptance=task.acceptance,
            test_commands=package.test_commands,
            allowed_paths=policy.allowed_paths,
            denied_paths=policy.denied_paths,
        )
        assert package.base_bundle is not None  # guaranteed by PackageInputs
        files.append(PackageFile("base/base.bundle", package.base_bundle, _BINARY))
    else:
        review = package.review
        assert review is not None  # guaranteed by PackageInputs
        task_title = f"Review candidate {review.head_sha[:8]}: {task.title}"
        repomesh_block["review_of"] = str(review.review_of)
        spec = render_review_spec(
            title=task.title,
            attempt_id=attempt_id,
            review_of=str(review.review_of),
            generation=package.generation,
            task_id=str(task.id),
            base_sha=package.base_sha,
            head_sha=review.head_sha,
            budget_seconds=package.budget_seconds,
            instruction=task.instruction,
            acceptance=task.acceptance,
            test_commands=package.test_commands,
            allowed_paths=policy.allowed_paths,
            denied_paths=policy.denied_paths,
            candidate_diff=review.candidate_diff,
            changes_json=review.changes_json,
            evidence_json=review.evidence_json,
        )
        files.extend(
            (
                PackageFile("review/candidate.diff", review.candidate_diff.encode(), _DIFF),
                PackageFile("review/changes.json", review.changes_json.encode(), _JSON),
                PackageFile("review/evidence.json", review.evidence_json.encode(), _JSON),
            )
        )

    meta = {
        "task_id": attempt_id,
        "project_id": str(task.project_id),
        "task_title": task_title,
        "assigned_to": assignee_resource_name,
        "room_id": room_id,
        "status": "assigned",
        "depends_on": [],
        # Publish-time snapshot only: copaw rewrites meta.json from its own
        # TaskMeta on ack/submit and this block is gone (spike S-3, D-6). The
        # observer claims attempts by directory name and reads base/package.json.
        "repomesh": repomesh_block,
    }
    control = {
        "schema": V2_PACKAGE_SCHEMA,
        "kind": package.kind,
        "task_id": str(task.id),
        "attempt_id": attempt_id,
        "generation": package.generation,
        "budget_seconds": package.budget_seconds,
        "base_sha": package.base_sha,
        "repository_id": str(task.repository_id),
        "organization_id": str(task.organization_id),
        "test_commands": list(package.test_commands),
        "test_timeout_seconds": package.test_timeout_seconds,
        "allowed_paths": list(policy.allowed_paths),
        "denied_paths": list(policy.denied_paths),
        "workspace_root": package.workspace_root,
        "helper": HELPER_PATH,
        "helper_commands": list(HELPER_COMMANDS),
    }
    files.extend(
        (
            PackageFile("spec.md", spec.encode(), _MARKDOWN),
            PackageFile("meta.json", _json_text(meta).encode(), _JSON),
            PackageFile("base/package.json", _json_text(control).encode(), _JSON),
            PackageFile(HELPER_PATH, package.helper_script, _SHELL),
        )
    )
    files.sort(key=lambda item: item.path)

    digests = {item.path: _sha256(item.content) for item in files}
    content_hash = _v2_content_hash([(item.path, item.content) for item in files])
    manifest = _json_text(
        {
            "schema": V2_SCHEMA,
            "kind": package.kind,
            "attempt_id": attempt_id,
            "files": [item.path for item in files],
            "file_digests": digests,
            "file_sizes": {item.path: len(item.content) for item in files},
            "content_hash": content_hash,
        }
    ).encode()
    return AssembledPackage(task_path, tuple(files), manifest, content_hash, _v2_content_hash)


def assemble_package(
    task: TaskView,
    *,
    team_name: str,
    room_id: str,
    assignee_resource_name: str,
    idempotency_key: str,
    package: PackageInputs | None,
) -> AssembledPackage:
    if package is None:
        return assemble_v1_package(
            task,
            team_name=team_name,
            room_id=room_id,
            assignee_resource_name=assignee_resource_name,
            idempotency_key=idempotency_key,
        )
    return assemble_v2_package(
        task,
        package,
        team_name=team_name,
        room_id=room_id,
        assignee_resource_name=assignee_resource_name,
    )


# ---------------------------------------------------------------------------
# Storing — the only part the two adapters do differently
# ---------------------------------------------------------------------------


class PackageStore(Protocol):
    def read(self, key: str) -> bytes | None: ...

    def write(self, key: str, content: bytes, content_type: str) -> None: ...


def store_package(
    store: PackageStore,
    assembled: AssembledPackage,
    *,
    check_existing: bool,
    verification_error: str,
) -> str:
    """Write ``assembled`` through ``store`` and return its ``content_hash``.

    With ``check_existing`` an existing manifest decides first: the same
    ``content_hash`` is a replay and nothing is rewritten; a different one is
    a conflict (``ValueError``) — the attempt directory is never reused
    (D-8). After writing, every file is read back and re-hashed so the
    returned digest describes what the store actually holds.
    """

    if check_existing:
        existing = store.read(assembled.manifest_path)
        if existing is not None:
            if json.loads(existing.decode()).get("content_hash") != assembled.content_hash:
                raise ValueError("published AgentTeams task conflicts with existing content")
            return assembled.content_hash
    for item in assembled.files:
        store.write(f"{assembled.task_path}/{item.path}", item.content, item.content_type)
    store.write(assembled.manifest_path, assembled.manifest, _JSON)
    stored: list[tuple[str, bytes]] = []
    for item in assembled.files:
        content = store.read(f"{assembled.task_path}/{item.path}")
        if content is None:
            raise OSError(verification_error)
        stored.append((item.path, content))
    if assembled.hash_files(stored) != assembled.content_hash:
        raise OSError(verification_error)
    return assembled.content_hash


class _DirectoryStore:
    def __init__(self, root: Path, write_file: Callable[[Path, bytes], None]) -> None:
        self._root = root
        self._write_file = write_file

    def read(self, key: str) -> bytes | None:
        path = self._root / Path(key)
        return path.read_bytes() if path.exists() else None

    def write(self, key: str, content: bytes, content_type: str) -> None:
        path = self._root / Path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_file(path, content)


class _ObjectStore:
    def __init__(self, client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def read(self, key: str) -> bytes | None:
        from minio.error import S3Error

        try:
            response = self._client.get_object(self._bucket, key)
        except S3Error as error:
            if error.code in ("NoSuchKey", "NoSuchObject"):
                return None
            raise
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def write(self, key: str, content: bytes, content_type: str) -> None:
        self._client.put_object(
            self._bucket,
            key,
            io.BytesIO(content),
            len(content),
            content_type=content_type,
        )


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


class AgentTeamsTaskPublisher(TaskAssignmentPublisher):
    """Publish task packages into a directory that AgentTeams' storage sync exposes."""

    def __init__(self, storage_root: Path) -> None:
        self._storage_root = storage_root

    async def publish(
        self,
        task: TaskView,
        *,
        team_name: str,
        room_id: str,
        assignee_resource_name: str,
        idempotency_key: str,
        package: PackageInputs | None = None,
    ) -> PublishedTaskPackage:
        return await asyncio.to_thread(
            self._publish,
            task,
            team_name,
            room_id,
            assignee_resource_name,
            idempotency_key,
            package,
        )

    def _publish(
        self,
        task: TaskView,
        team_name: str,
        room_id: str,
        assignee_resource_name: str,
        idempotency_key: str,
        package: PackageInputs | None,
    ) -> PublishedTaskPackage:
        assembled = assemble_package(
            task,
            team_name=team_name,
            room_id=room_id,
            assignee_resource_name=assignee_resource_name,
            idempotency_key=idempotency_key,
            package=package,
        )
        digest = store_package(
            _DirectoryStore(self._storage_root, self._atomic_write),
            assembled,
            check_existing=True,
            verification_error="AgentTeams task publication verification failed",
        )
        return PublishedTaskPackage(team_name, assembled.task_path, digest)

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        """The one place the file channel touches the disk; tests fail it on purpose."""

        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(path)


class AgentTeamsObjectTaskPublisher(AgentTeamsTaskPublisher):
    """Publish the same verified task package through AgentTeams' S3 API."""

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str = "agentteams-storage",
    ) -> None:
        from minio import Minio

        secure = endpoint.startswith("https://")
        host = endpoint.removeprefix("https://").removeprefix("http://").rstrip("/")
        self._client = Minio(host, access_key=access_key, secret_key=secret_key, secure=secure)
        self._bucket = bucket

    @classmethod
    def with_client(
        cls, client, bucket: str = "agentteams-storage"
    ) -> AgentTeamsObjectTaskPublisher:
        """Build the publisher around an already-configured S3 client.

        The constructor dials the endpoint; tests and callers that already
        hold a client (or a stand-in with ``put_object`` / ``get_object``)
        come in here.
        """

        publisher = cls.__new__(cls)
        publisher._client = client
        publisher._bucket = bucket
        return publisher

    async def publish(
        self,
        task: TaskView,
        *,
        team_name: str,
        room_id: str,
        assignee_resource_name: str,
        idempotency_key: str,
        package: PackageInputs | None = None,
    ) -> PublishedTaskPackage:
        return await asyncio.to_thread(
            self._publish_object,
            task,
            team_name,
            room_id,
            assignee_resource_name,
            idempotency_key,
            package,
        )

    def _publish_object(
        self,
        task: TaskView,
        team_name: str,
        room_id: str,
        assignee_resource_name: str,
        idempotency_key: str,
        package: PackageInputs | None,
    ) -> PublishedTaskPackage:
        assembled = assemble_package(
            task,
            team_name=team_name,
            room_id=room_id,
            assignee_resource_name=assignee_resource_name,
            idempotency_key=idempotency_key,
            package=package,
        )
        # The v1 object channel has always overwritten in place (a re-dispatch
        # of the same task id is expected there); only an attempt directory
        # is fenced, because it is never reused (D-8).
        digest = store_package(
            _ObjectStore(self._client, self._bucket),
            assembled,
            check_existing=package is not None,
            verification_error="AgentTeams object task publication verification failed",
        )
        return PublishedTaskPackage(team_name, assembled.task_path, digest)
