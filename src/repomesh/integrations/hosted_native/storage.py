"""Shared-directory readers: the one external seam the hosted-native spec opens (§4.2 M2).

Every implementation answers the same two questions about one file of one
task directory — ``read`` (the bytes, or ``None`` when the object is not
there) and ``stat`` (size, etag, last-modified, or ``None``) — under the key
``teams/<team_name>/shared/tasks/<task_dir>/<name>``. ``task_dir`` is the
directory *name*: for the observer that is the attempt id it owns (D-6), never
a path it discovered by listing. None of the readers lists directories, which
is what keeps the observer from ever touching a directory it has no row for.

Three readers: MinIO (the same client and bucket as the object publisher), a
directory on disk (``agentteams_storage_root``) and an in-memory dict for the
tests, which also records every key it was asked for.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from .contracts import ObjectStat, SharedTaskDirectoryReader

DEFAULT_BUCKET = "agentteams-storage"

_MISSING_OBJECT_CODES = frozenset({"NoSuchKey", "NoSuchObject"})


def shared_task_key(team_name: str, task_dir: str, name: str) -> str:
    """The object key of ``name`` inside one task directory.

    Refuses empty or path-walking segments: the callers build these from their
    own rows and constants, so a ``..`` here is a bug, not a request.
    """

    for label, segment in (("team_name", team_name), ("task_dir", task_dir)):
        if not segment or "/" in segment or "\\" in segment or segment in (".", ".."):
            raise ValueError(f"invalid shared task {label}: {segment!r}")
    parts = name.split("/")
    if not name or name.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"invalid shared task file name: {name!r}")
    return f"teams/{team_name}/shared/tasks/{task_dir}/{name}"


def _content_etag(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class DiskSharedTaskDirectoryReader(SharedTaskDirectoryReader):
    """Read the directory the disk publisher writes (``agentteams_storage_root``)."""

    def __init__(self, root: Path) -> None:
        self._root = root

    async def read(self, team_name: str, task_dir: str, name: str) -> bytes | None:
        return await asyncio.to_thread(self._read, shared_task_key(team_name, task_dir, name))

    async def stat(self, team_name: str, task_dir: str, name: str) -> ObjectStat | None:
        return await asyncio.to_thread(self._stat, shared_task_key(team_name, task_dir, name))

    def _path(self, key: str) -> Path:
        return self._root / Path(key)

    def _read(self, key: str) -> bytes | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def _stat(self, key: str) -> ObjectStat | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            content = path.read_bytes()
            stat = path.stat()
        except FileNotFoundError:
            return None
        return ObjectStat(
            size=stat.st_size,
            etag=_content_etag(content),
            last_modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
        )


class MinioSharedTaskDirectoryReader(SharedTaskDirectoryReader):
    """Read through the S3 API of AgentTeams' storage, with the publisher's client.

    ``minio`` is imported inside the methods, like the object publisher does,
    so deployments on the disk channel never load it. The client is blocking;
    every call is pushed to a worker thread.
    """

    def __init__(self, client, bucket: str = DEFAULT_BUCKET) -> None:
        self._client = client
        self._bucket = bucket

    @classmethod
    def with_client(cls, client, bucket: str = DEFAULT_BUCKET) -> MinioSharedTaskDirectoryReader:
        """Build the reader around an already-configured S3 client (or a stand-in
        with ``get_object`` / ``stat_object``)."""

        return cls(client, bucket)

    @classmethod
    def from_endpoint(
        cls,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str = DEFAULT_BUCKET,
    ) -> MinioSharedTaskDirectoryReader:
        """Dial the endpoint the way ``AgentTeamsObjectTaskPublisher`` does."""

        from minio import Minio

        secure = endpoint.startswith("https://")
        host = endpoint.removeprefix("https://").removeprefix("http://").rstrip("/")
        return cls(Minio(host, access_key=access_key, secret_key=secret_key, secure=secure), bucket)

    async def read(self, team_name: str, task_dir: str, name: str) -> bytes | None:
        return await asyncio.to_thread(self._read, shared_task_key(team_name, task_dir, name))

    async def stat(self, team_name: str, task_dir: str, name: str) -> ObjectStat | None:
        return await asyncio.to_thread(self._stat, shared_task_key(team_name, task_dir, name))

    def _read(self, key: str) -> bytes | None:
        from minio.error import S3Error

        try:
            response = self._client.get_object(self._bucket, key)
        except S3Error as error:
            if error.code in _MISSING_OBJECT_CODES:
                return None
            raise
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def _stat(self, key: str) -> ObjectStat | None:
        from minio.error import S3Error

        try:
            result = self._client.stat_object(self._bucket, key)
        except S3Error as error:
            if error.code in _MISSING_OBJECT_CODES:
                return None
            raise
        size = getattr(result, "size", None)
        etag = getattr(result, "etag", None)
        last_modified = getattr(result, "last_modified", None)
        return ObjectStat(
            size=int(size) if size is not None else 0,
            etag=str(etag).strip('"') if etag else None,
            last_modified=last_modified if isinstance(last_modified, datetime) else None,
        )


class InMemorySharedTaskDirectoryReader(SharedTaskDirectoryReader):
    """A dict of full keys to bytes, for tests.

    ``reads`` records every key ``read`` or ``stat`` was asked for, in order —
    the observer tests use it to prove that a directory without a store row is
    never touched. ``failures`` maps a key to the exception its next access
    raises, for the "one bad directory does not stop the scan" tests.
    """

    def __init__(self, files: Mapping[str, bytes] | None = None) -> None:
        self.files: dict[str, bytes] = dict(files or {})
        self.reads: list[str] = []
        self.failures: dict[str, Exception] = {}

    def put(self, team_name: str, task_dir: str, name: str, content: bytes | str) -> str:
        key = shared_task_key(team_name, task_dir, name)
        self.files[key] = content.encode("utf-8") if isinstance(content, str) else content
        return key

    def remove(self, team_name: str, task_dir: str, name: str) -> None:
        self.files.pop(shared_task_key(team_name, task_dir, name), None)

    async def read(self, team_name: str, task_dir: str, name: str) -> bytes | None:
        key = shared_task_key(team_name, task_dir, name)
        self._touch(key)
        return self.files.get(key)

    async def stat(self, team_name: str, task_dir: str, name: str) -> ObjectStat | None:
        key = shared_task_key(team_name, task_dir, name)
        self._touch(key)
        content = self.files.get(key)
        if content is None:
            return None
        return ObjectStat(size=len(content), etag=_content_etag(content), last_modified=None)

    def _touch(self, key: str) -> None:
        self.reads.append(key)
        failure = self.failures.get(key)
        if failure is not None:
            raise failure
