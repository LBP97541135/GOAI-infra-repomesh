import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from minio.error import S3Error

from repomesh.integrations.agentteams.task_package import HELPER_COMMANDS, load_helper_script
from repomesh.integrations.agentteams.task_publishing import (
    AgentTeamsObjectTaskPublisher,
    AgentTeamsTaskPublisher,
)
from repomesh.modules.task_orchestration.contracts import (
    PackageInputs,
    PathPolicy,
    ReviewInputs,
    TaskStatus,
    TaskView,
)


def task_view() -> TaskView:
    return TaskView(
        id=uuid4(),
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_id=uuid4(),
        parent_task_id=uuid4(),
        assigned_by_agent_id=uuid4(),
        assignee_agent_id=uuid4(),
        title="Fix pricing resolver",
        instruction="Apply the approved pricing change.",
        acceptance=("Pricing tests pass", "Old API remains compatible"),
        status=TaskStatus.ASSIGNED,
        result_summary=None,
        version=0,
    )


@pytest.mark.asyncio
async def test_publishes_agentteams_compatible_task_and_verifies_replay(tmp_path) -> None:
    publisher = AgentTeamsTaskPublisher(tmp_path)
    task = task_view()

    first = await publisher.publish(
        task,
        team_name="pricing-team",
        room_id="!pricing:matrix.local",
        assignee_resource_name="pricing-worker",
        idempotency_key="publish-pricing",
    )
    replay = await publisher.publish(
        task,
        team_name="pricing-team",
        room_id="!pricing:matrix.local",
        assignee_resource_name="pricing-worker",
        idempotency_key="publish-pricing",
    )

    task_dir = tmp_path / first.task_path
    meta = json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))
    manifest = json.loads((task_dir / "manifest.json").read_text(encoding="utf-8"))
    assert meta["assigned_to"] == "pricing-worker"
    assert meta["room_id"] == "!pricing:matrix.local"
    assert "Pricing tests pass" in (task_dir / "spec.md").read_text(encoding="utf-8")
    assert manifest["content_hash"] == first.content_hash
    assert replay == first


# ---------------------------------------------------------------------------
# v1 stays what it was
# ---------------------------------------------------------------------------

V1_FIXED_TASK = TaskView(
    id=UUID("00000000-0000-0000-0000-000000000101"),
    organization_id=UUID("00000000-0000-0000-0000-000000000001"),
    project_id=UUID("00000000-0000-0000-0000-000000000002"),
    repository_id=UUID("00000000-0000-0000-0000-000000000003"),
    parent_task_id=UUID("00000000-0000-0000-0000-000000000100"),
    assigned_by_agent_id=UUID("00000000-0000-0000-0000-000000000010"),
    assignee_agent_id=UUID("00000000-0000-0000-0000-000000000011"),
    title="Fix pricing resolver",
    instruction="Apply the approved pricing change.",
    acceptance=("Pricing tests pass", "Old API remains compatible"),
    status=TaskStatus.ASSIGNED,
    result_summary=None,
    version=0,
)
# Computed with the pre-v2 publisher (sha256 of ``spec + NUL + meta``); a change
# here means the local-CLI package changed, which is a contract change.
V1_FIXED_HASH = "sha256:e15a3fa174046524ebe24dc8ec1502a11c27b3aad8c640e76d1d730b19ad5112"


@pytest.mark.asyncio
async def test_v1_package_without_package_inputs_is_byte_stable(tmp_path) -> None:
    published = await AgentTeamsTaskPublisher(tmp_path).publish(
        V1_FIXED_TASK,
        team_name="pricing-team",
        room_id="!pricing:matrix.local",
        assignee_resource_name="pricing-worker",
        idempotency_key="publish-pricing",
    )

    task_dir = tmp_path / published.task_path
    assert published.task_path == f"teams/pricing-team/shared/tasks/{V1_FIXED_TASK.id}"
    assert published.content_hash == V1_FIXED_HASH
    manifest = json.loads((task_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == {
        "schema": "repomesh.agentteams-task.v1",
        "content_hash": V1_FIXED_HASH,
        "files": ["meta.json", "spec.md"],
    }
    assert sorted(path.name for path in task_dir.iterdir()) == [
        "manifest.json",
        "meta.json",
        "spec.md",
    ]
    assert b"\r" not in (task_dir / "spec.md").read_bytes()


# ---------------------------------------------------------------------------
# v2: one attempt, one directory
# ---------------------------------------------------------------------------

ATTEMPT = UUID("00000000-0000-0000-0000-00000000aaaa")
REVIEW_ATTEMPT = UUID("00000000-0000-0000-0000-00000000bbbb")
BASE_SHA = "882231dd887688a986b0faec656a90d29141406c"
HEAD_SHA = "5d9f0c2a" + "1" * 32
POLICY = PathPolicy(allowed_paths=("src/**", "tests/**"), denied_paths=(".github/**",))


def construction(**overrides) -> PackageInputs:
    values = {
        "kind": "construction",
        "attempt_id": ATTEMPT,
        "generation": 1,
        "budget_seconds": 2700,
        "base_sha": BASE_SHA,
        "helper_script": load_helper_script(),
        "policy": POLICY,
        "test_commands": ("python scripts/run_tests.py",),
        "base_bundle": b"# git bundle v2\nbase\n",
    }
    values.update(overrides)
    return PackageInputs(**values)


def review() -> PackageInputs:
    changes = {
        "attempt_id": str(ATTEMPT),
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "changed_files": [{"status": "M", "path": "src/pricing/resolver.py"}],
    }
    evidence = {
        "attempt_id": str(ATTEMPT),
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "tree": "c" * 40,
        "tests_ran_at": "2026-09-03T20:26:40+00:00",
        "tests": [{"command": "python scripts/run_tests.py", "exit_code": 0, "excerpt": "OK"}],
        "produced_at": "2026-09-03T20:27:01+00:00",
    }
    return PackageInputs(
        kind="review",
        attempt_id=REVIEW_ATTEMPT,
        generation=1,
        budget_seconds=900,
        base_sha=BASE_SHA,
        helper_script=load_helper_script(),
        policy=POLICY,
        test_commands=("python scripts/run_tests.py",),
        review=ReviewInputs(
            review_of=ATTEMPT,
            head_sha=HEAD_SHA,
            candidate_diff="--- a/src/pricing/resolver.py\n+++ b/src/pricing/resolver.py\n",
            changes_json=json.dumps(changes, indent=2) + "\n",
            evidence_json=json.dumps(evidence, indent=2) + "\n",
        ),
    )


async def publish(publisher, task: TaskView, package: PackageInputs, *, assignee="pricing-worker"):
    return await publisher.publish(
        task,
        team_name="pricing-team",
        room_id="!pricing:matrix.local",
        assignee_resource_name=assignee,
        idempotency_key=f"publish-{package.attempt_id}",
        package=package,
    )


def read_tree(task_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(task_dir).as_posix(): path.read_bytes()
        for path in sorted(task_dir.rglob("*"))
        if path.is_file()
    }


@pytest.mark.asyncio
async def test_construction_package_lands_in_the_attempt_directory(tmp_path) -> None:
    task = task_view()

    published = await publish(AgentTeamsTaskPublisher(tmp_path), task, construction())

    assert published.task_path == f"teams/pricing-team/shared/tasks/{ATTEMPT}"
    task_dir = tmp_path / published.task_path
    tree = read_tree(task_dir)
    assert sorted(tree) == [
        "base/base.bundle",
        "base/package.json",
        "base/tools/repomesh-work.sh",
        "manifest.json",
        "meta.json",
        "spec.md",
    ]
    meta = json.loads(tree["meta.json"])
    assert meta["task_id"] == str(ATTEMPT)
    assert meta["task_title"] == task.title
    assert meta["assigned_to"] == "pricing-worker"
    assert meta["status"] == "assigned" and meta["depends_on"] == []
    assert meta["repomesh"] == {
        "kind": "construction",
        "task_id": str(task.id),
        "attempt_id": str(ATTEMPT),
        "generation": 1,
        "budget_seconds": 2700,
        "base_sha": BASE_SHA,
        "repository_id": str(task.repository_id),
        "organization_id": str(task.organization_id),
        "package": "base/package.json",
    }
    control = json.loads(tree["base/package.json"])
    assert control["schema"] == "repomesh.agentteams-task.v2/package"
    assert control["helper"] == "base/tools/repomesh-work.sh"
    assert control["helper_commands"] == list(HELPER_COMMANDS)
    assert control["test_timeout_seconds"] == 600
    manifest = json.loads(tree["manifest.json"])
    assert manifest["schema"] == "repomesh.agentteams-task.v2"
    assert manifest["kind"] == "construction"
    assert manifest["attempt_id"] == str(ATTEMPT)
    assert manifest["files"] == [name for name in sorted(tree) if name != "manifest.json"]
    assert manifest["content_hash"] == published.content_hash
    assert tree["base/base.bundle"] == b"# git bundle v2\nbase\n"
    assert tree["base/tools/repomesh-work.sh"] == load_helper_script()
    for name, data in tree.items():
        if name != "base/base.bundle":
            assert b"\r" not in data and data.endswith(b"\n"), name


@pytest.mark.asyncio
async def test_review_package_carries_the_candidate_and_no_bundle(tmp_path) -> None:
    task = task_view()

    published = await publish(
        AgentTeamsTaskPublisher(tmp_path), task, review(), assignee="pricing-leader"
    )

    task_dir = tmp_path / published.task_path
    assert task_dir.name == str(REVIEW_ATTEMPT)
    tree = read_tree(task_dir)
    assert sorted(tree) == [
        "base/package.json",
        "base/tools/repomesh-work.sh",
        "manifest.json",
        "meta.json",
        "review/candidate.diff",
        "review/changes.json",
        "review/evidence.json",
        "spec.md",
    ]
    meta = json.loads(tree["meta.json"])
    assert meta["assigned_to"] == "pricing-leader"
    assert meta["task_title"] == f"Review candidate {HEAD_SHA[:8]}: {task.title}"
    assert meta["repomesh"]["review_of"] == str(ATTEMPT)
    assert json.loads(tree["base/package.json"])["kind"] == "review"
    spec = tree["spec.md"].decode()
    assert "VERDICT: <ACCEPT | REVISION | BLOCKED>" in spec
    assert "- `M` `src/pricing/resolver.py`" in spec
    assert "+++ b/src/pricing/resolver.py" in spec


@pytest.mark.asyncio
async def test_replaying_the_same_attempt_rewrites_nothing(tmp_path) -> None:
    publisher = AgentTeamsTaskPublisher(tmp_path)
    task = task_view()
    first = await publish(publisher, task, construction())
    task_dir = tmp_path / first.task_path
    sentinel = 1_000_000_000
    for path in task_dir.rglob("*"):
        if path.is_file():
            os.utime(path, (sentinel, sentinel))

    replay = await publish(publisher, task, construction())

    assert replay == first
    for path in task_dir.rglob("*"):
        if path.is_file():
            assert int(path.stat().st_mtime) == sentinel, path


@pytest.mark.asyncio
async def test_a_different_package_for_the_same_attempt_is_refused(tmp_path) -> None:
    publisher = AgentTeamsTaskPublisher(tmp_path)
    task = task_view()
    first = await publish(publisher, task, construction())

    with pytest.raises(ValueError, match="conflicts with existing content"):
        await publish(publisher, task, construction(base_bundle=b"# git bundle v2\nother\n"))

    # The refusal left the first package exactly as it was.
    manifest = json.loads((tmp_path / first.task_path / "manifest.json").read_text())
    assert manifest["content_hash"] == first.content_hash


class FakeObjectStore:
    """Enough of ``minio.Minio`` for the object channel: put, get, not-found."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.puts = 0

    def put_object(self, bucket, name, data, length, content_type=None):
        payload = data.read()
        assert len(payload) == length
        self.objects[f"{bucket}/{name}"] = (payload, content_type)
        self.puts += 1

    def get_object(self, bucket, name):
        key = f"{bucket}/{name}"
        if key not in self.objects:
            raise S3Error(None, "NoSuchKey", "The specified key does not exist.", name, None, None)
        return _Response(self.objects[key][0])


class _Response:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def close(self) -> None:
        pass

    def release_conn(self) -> None:
        pass


@pytest.mark.asyncio
async def test_disk_and_object_channels_publish_identical_bytes(tmp_path) -> None:
    task = task_view()
    store = FakeObjectStore()
    object_publisher = AgentTeamsObjectTaskPublisher.with_client(store, "agentteams-storage")

    for package in (construction(), review()):
        on_disk = await publish(AgentTeamsTaskPublisher(tmp_path), task, package)
        in_bucket = await publish(object_publisher, task, package)

        assert in_bucket == on_disk
        tree = read_tree(tmp_path / on_disk.task_path)
        for name, data in tree.items():
            payload, content_type = store.objects[f"agentteams-storage/{on_disk.task_path}/{name}"]
            assert payload == data, name
            assert content_type, name
        assert {
            key.removeprefix(f"agentteams-storage/{on_disk.task_path}/")
            for key in store.objects
            if key.startswith(f"agentteams-storage/{on_disk.task_path}/")
        } == set(tree)


@pytest.mark.asyncio
async def test_object_channel_replays_and_fences_attempts_like_the_disk(tmp_path) -> None:
    task = task_view()
    store = FakeObjectStore()
    publisher = AgentTeamsObjectTaskPublisher.with_client(store)

    first = await publish(publisher, task, construction())
    puts_after_first = store.puts
    replay = await publish(publisher, task, construction())

    assert replay == first
    assert store.puts == puts_after_first
    with pytest.raises(ValueError, match="conflicts with existing content"):
        await publish(publisher, task, construction(generation=2))


def test_package_inputs_refuse_shapes_the_publisher_cannot_write() -> None:
    with pytest.raises(ValueError, match="base_bundle"):
        construction(base_bundle=None)
    with pytest.raises(ValueError, match="review inputs"):
        construction(review=review().review)
    with pytest.raises(ValueError, match="review inputs"):
        PackageInputs(
            kind="review",
            attempt_id=REVIEW_ATTEMPT,
            generation=1,
            budget_seconds=900,
            base_sha=BASE_SHA,
            helper_script=b"#!/usr/bin/env bash\n",
            policy=POLICY,
            test_commands=(),
        )
    with pytest.raises(ValueError, match="generation"):
        construction(generation=0)
    with pytest.raises(ValueError, match="budget_seconds"):
        construction(budget_seconds=0)
    with pytest.raises(ValueError, match="kind"):
        construction(kind="verification")
