"""The auto-approval branch (D-23, spec §8.17 ①–⑤) against a real construction package.

The package under test is written by the real disk publisher, so
``base/package.json.helper_commands[]`` is whatever M3 ships; the Tool Guard
prompt body is the one copaw really sent in wave 0
(``output/hosted-native-e2e/2026-09-03/spike/rooms.jsonl`` 20:01:05), with the
helper renamed to ``repomesh-work.sh`` and the directory renamed to this
test's attempt. Every ``execute`` case asserts three things: the inbound
result, how many ``/approve`` went out, and what the event inbox holds.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from repomesh.integrations.agentteams.task_package import HELPER_COMMANDS, load_helper_script
from repomesh.integrations.agentteams.task_publishing import AgentTeamsTaskPublisher
from repomesh.integrations.hosted_native.approval import (
    APPROVAL_TRANSACTION_PREFIX,
    ToolGuardAutoApprover,
    normalize_helper_command,
    parse_tool_guard_request,
)
from repomesh.integrations.hosted_native.contracts import (
    AttemptPhase,
    EventKind,
    HostedNativeAttempt,
    SharedTaskDirectoryReader,
)
from repomesh.integrations.hosted_native.storage import (
    DiskSharedTaskDirectoryReader,
    InMemorySharedTaskDirectoryReader,
)
from repomesh.integrations.hosted_native.store import InMemoryHostedNativeAttemptStore
from repomesh.modules.collaboration.contracts import InboundMatrixMessage, MatrixInboundResult
from repomesh.modules.task_orchestration.contracts import (
    PackageInputs,
    PathPolicy,
    TaskStatus,
    TaskView,
)

T0 = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC)
TEAM = "repomesh-team-x"
ROOM = "!team:hs"
WORKER_MATRIX = "@agt-worker-x:hs"
OTHER_MATRIX = "@agt-other:hs"
WORKER_AGENT_ID = UUID("00000000-0000-0000-0000-0000000000a1")
OTHER_AGENT_ID = UUID("00000000-0000-0000-0000-0000000000a2")
LEADER_AGENT_ID = UUID("00000000-0000-0000-0000-0000000000b1")
ATTEMPT_ID = UUID("6f0c9d1e-2b3a-4c5d-8e7f-90a1b2c3d4e5")
SECOND_ATTEMPT_ID = UUID("7a1d0e2f-3c4b-4d5e-9f80-a1b2c3d4e5f6")

#: The absolute form copaw's worker really typed (sync root + this attempt's directory).
OWN = f"/root/.copaw-worker/agt-worker-x/.copaw/workspaces/default/shared/tasks/{ATTEMPT_ID}"
INIT = "bash base/tools/repomesh-work.sh init"
TEST = "bash base/tools/repomesh-work.sh test"
BUNDLE = "bash base/tools/repomesh-work.sh bundle"
CLEAN = "bash base/tools/repomesh-work.sh clean"

#: rooms.jsonl 20:01:05, verbatim apart from the helper name and the attempt id.
TOOL_GUARD_BODY_TEMPLATE = (
    "⏳ Waiting for approval / 等待审批\n"
    "\n"
    "- Tool / 工具: `{tool}`\n"
    "- Triggered by / 触发来源: `Tool Guard / 工具护栏`\n"
    "- Parameters / 参数:\n"
    "```json\n"
    "{parameters}\n"
    "```\n"
    "\n"
    "💡 Triggered by tool guardrails (configurable in Security → Tool Guard settings)\n"
    "💡 触发工具护栏（在安全-工具护栏页面可以更改设置）\n"
    "\n"
    "Type `/approve` to approve, or send any message to deny.\n"
    "输入 `/approve` 批准执行，或发送任意消息拒绝。\n"
    "\n"
    "💡 提示：请确认删除的文件位置和内容。\n"
    "💡 Reminder: Please verify file location and content.\n"
    "❌ 如不确定，请拒绝本次删除。\n"
    "❌ If unsure, please reject this operation."
)


def tool_guard_body(
    command: str | None, *, tool: str = "execute_shell_command", parameters: str | None = None
) -> str:
    """A copaw Tool Guard prompt for *command* (or a raw *parameters* block)."""

    if parameters is None:
        parameters = json.dumps({"command": command}, indent=2, ensure_ascii=False)
    return TOOL_GUARD_BODY_TEMPLATE.replace("{tool}", tool).replace("{parameters}", parameters)


REAL_BODY = tool_guard_body(f"cd {OWN} && {INIT}")

#: rooms.jsonl 20:39:00 (spike S-5): the worker *said* it was waiting; no tool ran.
FABRICATED_BODY = (
    "I need to run `bundle`. Waiting for Tool Guard approval for the bundle command.\n"
    "\n"
    "⏳ Waiting for approval / 等待审批 — Tool Guard for `bash base/tools/repomesh-work.sh "
    f"bundle` in the {ATTEMPT_ID} task directory. Type `/approve` to proceed."
)


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class FakeSenders:
    """Matrix user id -> agent id, ``None`` for anyone else."""

    def __init__(self, mapping: dict[str, UUID]) -> None:
        self._mapping = mapping
        self.lookups: list[str] = []

    async def resolve(self, matrix_user_id: str) -> UUID | None:
        self.lookups.append(matrix_user_id)
        return self._mapping.get(matrix_user_id)


@dataclass
class FakeApprovals:
    """Records every ``send_approval`` call; raises ``fail_once`` on the next one."""

    calls: list[tuple[str, str, str]] = field(default_factory=list)
    fail_once: Exception | None = None

    async def send_approval(
        self, room_id: str, worker_matrix_user_id: str, *, transaction_id: str
    ) -> str:
        self.calls.append((room_id, worker_matrix_user_id, transaction_id))
        if self.fail_once is not None:
            error, self.fail_once = self.fail_once, None
            raise error
        return f"$approve-{len(self.calls)}"


def make_message(
    body: str,
    *,
    sender: str = WORKER_MATRIX,
    room_id: str = ROOM,
    event_id: str = "$evt1",
    occurred_at: datetime = T0 + timedelta(minutes=1),
) -> InboundMatrixMessage:
    return InboundMatrixMessage(
        event_id=event_id, room_id=room_id, sender=sender, body=body, occurred_at=occurred_at
    )


def make_attempt(attempt_id: UUID = ATTEMPT_ID, **overrides: object) -> HostedNativeAttempt:
    values: dict[str, object] = {
        "id": attempt_id,
        "task_id": uuid4(),
        "worker_agent_id": WORKER_AGENT_ID,
        "leader_agent_id": LEADER_AGENT_ID,
        "team_name": TEAM,
        "room_id": ROOM,
        "assignment_attempt_id": uuid4(),
        "generation": 1,
        "execution_id": uuid4(),
        "phase": AttemptPhase.NOTIFIED,
        "package_dir": f"teams/{TEAM}/shared/tasks/{attempt_id}",
        "base_sha": "a" * 40,
        "budget_until": T0 + timedelta(minutes=45),
        "notified_at": T0,
        "created_at": T0,
        "updated_at": T0,
    }
    values.update(overrides)
    return HostedNativeAttempt(**values)  # type: ignore[arg-type]


async def publish_package(root: Path, attempt_id: UUID) -> str:
    """Write the real v2 construction package for *attempt_id*; returns its task path."""

    task = TaskView(
        id=uuid4(),
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_id=uuid4(),
        parent_task_id=uuid4(),
        assigned_by_agent_id=LEADER_AGENT_ID,
        assignee_agent_id=WORKER_AGENT_ID,
        title="Implement multi-currency quote()",
        instruction="Add a currency parameter to quote().",
        acceptance=("Frozen tests pass",),
        status=TaskStatus.ASSIGNED,
        result_summary=None,
        version=0,
    )
    published = await AgentTeamsTaskPublisher(root).publish(
        task,
        team_name=TEAM,
        room_id=ROOM,
        assignee_resource_name="agt-worker-x",
        idempotency_key=f"publish-{attempt_id}",
        package=PackageInputs(
            kind="construction",
            attempt_id=attempt_id,
            generation=1,
            budget_seconds=2700,
            base_sha="a" * 40,
            helper_script=load_helper_script(),
            policy=PathPolicy(("src/**",), ()),
            test_commands=("python scripts/run_tests.py",),
            base_bundle=b"BUNDLE",
        ),
    )
    return published.task_path


@dataclass
class Harness:
    attempts: InMemoryHostedNativeAttemptStore
    reader: SharedTaskDirectoryReader
    senders: FakeSenders
    approvals: FakeApprovals
    approver: ToolGuardAutoApprover
    attempt: HostedNativeAttempt
    root: Path

    async def deliver(self, body: str, **message: object) -> MatrixInboundResult:
        return await self.approver.execute(make_message(body, **message))  # type: ignore[arg-type]

    async def events(self, attempt_id: UUID | None = None):
        return await self.attempts.list_events(attempt_id or self.attempt.id)


@pytest.fixture
async def harness(tmp_path: Path) -> Harness:
    attempts = InMemoryHostedNativeAttemptStore()
    task_path = await publish_package(tmp_path, ATTEMPT_ID)
    attempt = make_attempt(package_dir=task_path)
    assert attempt.package_dir == f"teams/{TEAM}/shared/tasks/{ATTEMPT_ID}"
    await attempts.add(attempt)
    reader = DiskSharedTaskDirectoryReader(tmp_path)
    senders = FakeSenders({WORKER_MATRIX: WORKER_AGENT_ID, OTHER_MATRIX: OTHER_AGENT_ID})
    approvals = FakeApprovals()
    approver = ToolGuardAutoApprover(attempts, reader, senders, approvals)
    return Harness(attempts, reader, senders, approvals, approver, attempt, tmp_path)


async def assert_left_for_a_human(harness: Harness, result: MatrixInboundResult) -> None:
    assert result is MatrixInboundResult.IGNORED
    assert harness.approvals.calls == []
    assert await harness.events() == ()


# ---------------------------------------------------------------------------
# The approver: what it approves
# ---------------------------------------------------------------------------


async def test_the_real_prompt_is_approved_once_and_audited(harness: Harness) -> None:
    result = await harness.deliver(REAL_BODY)

    assert result is MatrixInboundResult.PROCESSED
    assert len(harness.approvals.calls) == 1
    room, mentioned, transaction = harness.approvals.calls[0]
    assert room == ROOM
    assert mentioned == WORKER_MATRIX
    assert transaction.startswith(APPROVAL_TRANSACTION_PREFIX)
    rows = await harness.events()
    assert len(rows) == 1
    event = rows[0]
    assert event.kind is EventKind.AUTO_APPROVED
    assert event.marker == "$evt1"
    assert event.applied_at is not None
    assert event.observed_at == T0 + timedelta(minutes=1)
    assert transaction == f"{APPROVAL_TRANSACTION_PREFIX}{event.id}"
    assert event.payload["normalized"] == HELPER_COMMANDS[0] == INIT
    assert event.payload["command"] == f"cd {OWN} && {INIT}"
    assert event.payload["sender"] == WORKER_MATRIX
    assert event.payload["room_id"] == ROOM


@pytest.mark.parametrize(
    ("command", "normalized"),
    [
        pytest.param(f"cd {OWN} && {INIT}", INIT, id="absolute-init"),
        pytest.param(f"cd shared/tasks/{ATTEMPT_ID} && {TEST}", TEST, id="relative-test"),
        pytest.param(BUNDLE, BUNDLE, id="bare-bundle"),
        pytest.param(f"cd {OWN} && {CLEAN}", CLEAN, id="absolute-clean"),
        pytest.param(f'cd "{OWN}" && {INIT}', INIT, id="double-quoted"),
        pytest.param(f"cd '{OWN}' && {INIT}", INIT, id="single-quoted"),
        pytest.param(f"cd {OWN}/ && {INIT}", INIT, id="trailing-slash"),
    ],
)
async def test_own_directory_helper_lines_are_approved(
    harness: Harness, command: str, normalized: str
) -> None:
    result = await harness.deliver(tool_guard_body(command))

    assert result is MatrixInboundResult.PROCESSED
    assert len(harness.approvals.calls) == 1
    rows = await harness.events()
    assert len(rows) == 1
    assert rows[0].payload["normalized"] == normalized
    assert normalized in HELPER_COMMANDS


@pytest.mark.parametrize(
    "command",
    [
        pytest.param(
            f"cd /root/.copaw-worker/agt-worker-x/.copaw/workspaces/default/shared/tasks/{uuid4()}"
            f" && {INIT}",
            id="another-attempt",
        ),
        pytest.param(f"ls -la && cd {OWN} && {INIT}", id="leading-command"),
        pytest.param(f"cd {OWN} && {INIT} && echo ok", id="trailing-command"),
        pytest.param(f"cd {OWN}; {INIT}", id="semicolon"),
        pytest.param(f"cd {OWN} && {INIT} --force", id="extra-argument"),
        pytest.param(f"cd /work/{ATTEMPT_ID} && {INIT}", id="workspace-directory"),
        pytest.param(f"cd {OWN}x && {INIT}", id="longer-name"),
        pytest.param(f"cd {OWN.upper()} && {INIT}", id="uppercase-uuid"),
        pytest.param(f"cd {OWN} && {INIT} | tee log", id="pipe"),
        pytest.param(f"cd {OWN} && {INIT} > out.txt", id="redirection"),
        pytest.param(f"cd {OWN} && {INIT.upper()}", id="uppercase-command"),
        pytest.param(f'cd {OWN} && bash -c "{INIT}"', id="bash-c"),
        pytest.param(f"cd {OWN} && cd . && {INIT}", id="second-cd"),
        pytest.param(f"cd {OWN} && FOO=1 {INIT}", id="environment-assignment"),
    ],
)
async def test_anything_else_is_left_for_a_human(harness: Harness, command: str) -> None:
    await assert_left_for_a_human(harness, await harness.deliver(tool_guard_body(command)))


# ---------------------------------------------------------------------------
# The gates around the comparison (§8.17 ⑤)
# ---------------------------------------------------------------------------


async def test_another_workers_prompt_is_ignored(harness: Harness) -> None:
    await assert_left_for_a_human(harness, await harness.deliver(REAL_BODY, sender=OTHER_MATRIX))


async def test_an_unknown_senders_prompt_is_ignored(harness: Harness) -> None:
    result = await harness.deliver(REAL_BODY, sender="@stranger:hs")

    await assert_left_for_a_human(harness, result)
    assert harness.senders.lookups == ["@stranger:hs"]


async def test_a_prompt_in_another_room_is_ignored(harness: Harness) -> None:
    result = await harness.deliver(REAL_BODY, room_id="!elsewhere:hs")

    await assert_left_for_a_human(harness, result)
    assert harness.senders.lookups == []


async def test_an_attempt_past_the_worker_side_is_ignored(harness: Harness) -> None:
    reviewing = harness.attempt.with_phase(
        AttemptPhase.REVIEW_PENDING,
        at=T0 + timedelta(minutes=20),
        submitted_at=T0 + timedelta(minutes=20),
    )
    await harness.attempts.save(reviewing)

    await assert_left_for_a_human(harness, await harness.deliver(REAL_BODY))


async def test_a_terminal_attempt_is_ignored(harness: Harness) -> None:
    fenced = harness.attempt.with_phase(
        AttemptPhase.FENCED, at=T0 + timedelta(minutes=2), fence_reason="worker_restarted"
    )
    await harness.attempts.save(fenced)

    await assert_left_for_a_human(harness, await harness.deliver(REAL_BODY))


async def test_a_prompt_older_than_the_notice_is_ignored(harness: Harness) -> None:
    result = await harness.deliver(REAL_BODY, occurred_at=T0 - timedelta(minutes=1))

    await assert_left_for_a_human(harness, result)


async def test_the_fabricated_waiting_text_never_enters_the_comparison(harness: Harness) -> None:
    result = await harness.deliver(FABRICATED_BODY)

    await assert_left_for_a_human(harness, result)
    assert harness.senders.lookups == []


async def test_a_prompt_for_another_tool_is_ignored(harness: Harness) -> None:
    body = tool_guard_body(None, tool="read_file", parameters='{\n  "path": "spec.md"\n}')

    await assert_left_for_a_human(harness, await harness.deliver(body))


async def test_a_parameters_block_without_a_command_is_ignored(harness: Harness) -> None:
    body = tool_guard_body(None, parameters='{\n  "cwd": "' + OWN + '"\n}')

    await assert_left_for_a_human(harness, await harness.deliver(body))


# ---------------------------------------------------------------------------
# Idempotency: duplicates and a failed send
# ---------------------------------------------------------------------------


async def test_the_same_prompt_twice_approves_once(harness: Harness) -> None:
    first = await harness.deliver(REAL_BODY)
    second = await harness.deliver(REAL_BODY)

    assert (first, second) == (MatrixInboundResult.PROCESSED, MatrixInboundResult.DUPLICATE)
    assert len(harness.approvals.calls) == 1
    assert len(await harness.events()) == 1


async def test_a_failed_send_is_retried_under_the_same_transaction(harness: Harness) -> None:
    harness.approvals.fail_once = RuntimeError("homeserver unreachable")

    with pytest.raises(RuntimeError, match="homeserver unreachable"):
        await harness.deliver(REAL_BODY)

    rows = await harness.events()
    assert len(rows) == 1
    assert rows[0].applied_at is None
    assert len(harness.approvals.calls) == 1

    retried = await harness.deliver(REAL_BODY)

    assert retried is MatrixInboundResult.PROCESSED
    assert len(harness.approvals.calls) == 2
    assert harness.approvals.calls[0][2] == harness.approvals.calls[1][2]
    assert harness.approvals.calls[1][2] == f"{APPROVAL_TRANSACTION_PREFIX}{rows[0].id}"
    rows_after = await harness.events()
    assert [row.id for row in rows_after] == [rows[0].id]
    assert rows_after[0].applied_at is not None


# ---------------------------------------------------------------------------
# The package file is the authority (§8.17 ③)
# ---------------------------------------------------------------------------


async def test_a_missing_package_file_approves_nothing(harness: Harness) -> None:
    approver = ToolGuardAutoApprover(
        harness.attempts, InMemorySharedTaskDirectoryReader(), harness.senders, harness.approvals
    )

    result = await approver.execute(make_message(REAL_BODY))

    await assert_left_for_a_human(harness, result)


async def test_helper_commands_come_from_the_attempts_own_package(harness: Harness) -> None:
    memory = InMemorySharedTaskDirectoryReader()
    memory.put(
        TEAM,
        str(ATTEMPT_ID),
        "base/package.json",
        json.dumps({"helper_commands": ["bash base/tools/other.sh go"]}),
    )
    approver = ToolGuardAutoApprover(harness.attempts, memory, harness.senders, harness.approvals)

    ignored = await approver.execute(make_message(REAL_BODY))
    processed = await approver.execute(
        make_message(tool_guard_body(f"cd {OWN} && bash base/tools/other.sh go"), event_id="$evt2")
    )

    assert ignored is MatrixInboundResult.IGNORED
    assert processed is MatrixInboundResult.PROCESSED
    assert len(harness.approvals.calls) == 1
    assert [row.marker for row in await harness.events()] == ["$evt2"]


async def test_the_cd_directory_picks_the_attempt_among_two(harness: Harness) -> None:
    second_path = await publish_package(harness.root, SECOND_ATTEMPT_ID)
    second = make_attempt(
        SECOND_ATTEMPT_ID, package_dir=second_path, notified_at=T0 + timedelta(seconds=1)
    )
    await harness.attempts.add(second)
    second_own = OWN.replace(str(ATTEMPT_ID), str(SECOND_ATTEMPT_ID))

    for_second = await harness.deliver(tool_guard_body(f"cd {second_own} && {TEST}"))
    for_first = await harness.deliver(tool_guard_body(f"cd {OWN} && {INIT}"), event_id="$evt2")

    assert (for_second, for_first) == (MatrixInboundResult.PROCESSED,) * 2
    assert len(harness.approvals.calls) == 2
    second_rows = await harness.events(SECOND_ATTEMPT_ID)
    first_rows = await harness.events(ATTEMPT_ID)
    assert [(row.marker, row.payload["normalized"]) for row in second_rows] == [("$evt1", TEST)]
    assert [(row.marker, row.payload["normalized"]) for row in first_rows] == [("$evt2", INIT)]


# ---------------------------------------------------------------------------
# normalize_helper_command (§8.17 ②)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        pytest.param(INIT, INIT, id="bare"),
        pytest.param(f"cd shared/tasks/{ATTEMPT_ID} && {INIT}", INIT, id="own-relative"),
        pytest.param(f"cd {OWN} && {INIT}", INIT, id="own-absolute"),
        pytest.param(f"cd {OWN}/ && {INIT}", INIT, id="trailing-slash"),
        pytest.param(f"cd shared/tasks/{ATTEMPT_ID}/ && {INIT}", INIT, id="relative-slash"),
        pytest.param(f"cd '{OWN}' && {INIT}", INIT, id="single-quotes"),
        pytest.param(f'cd "{OWN}" && {INIT}', INIT, id="double-quotes"),
        pytest.param(f"cd \"{OWN}' && {INIT}", None, id="mismatched-quotes"),
        pytest.param(f"cd  {OWN} && {INIT}", None, id="two-spaces-after-cd"),
        pytest.param(f"cd {OWN} &&{INIT}", None, id="no-space-after-and"),
        pytest.param(f"cd {OWN}&& {INIT}", None, id="no-space-before-and"),
        pytest.param(f"cd {OWN}; {INIT}", None, id="semicolon"),
        pytest.param(f"cd {OWN} || {INIT}", None, id="or"),
        pytest.param(
            f"cd $HOME/.copaw/workspaces/default/shared/tasks/{ATTEMPT_ID} && {INIT}",
            None,
            id="metachar",
        ),
        pytest.param(f"cd {OWN} && cd x && {INIT}", f"cd x && {INIT}", id="one-prefix-only"),
        pytest.param(f'bash -c "{INIT}"', f'bash -c "{INIT}"', id="bash-c-unchanged"),
        pytest.param(f"  cd {OWN} && {INIT}  \n", INIT, id="outer-whitespace"),
        pytest.param(f"  {INIT}\t", INIT, id="bare-outer-whitespace"),
        pytest.param(f"cd shared/tasks && {INIT}", None, id="shared-tasks-root"),
        pytest.param(f"cd shared/tasks/{uuid4()} && {INIT}", None, id="other-attempt"),
        pytest.param(f"cd /work/{ATTEMPT_ID} && {INIT}", None, id="workspace"),
        pytest.param(f"cd {OWN}x && {INIT}", None, id="longer-name"),
        pytest.param(f"cd {OWN.upper()} && {INIT}", None, id="uppercase"),
        pytest.param(f"cd\t{OWN} && {INIT}", None, id="tab-after-cd"),
        pytest.param("cd", None, id="bare-cd"),
        pytest.param(f"cd {OWN}", None, id="cd-alone"),
        pytest.param(f"cdx {OWN} && {INIT}", f"cdx {OWN} && {INIT}", id="not-a-cd"),
    ],
)
def test_normalize_helper_command(command: str, expected: str | None) -> None:
    assert normalize_helper_command(command, attempt_id=ATTEMPT_ID) == expected


def test_normalize_helper_command_accepts_the_id_as_text() -> None:
    assert normalize_helper_command(f"cd {OWN} && {INIT}", attempt_id=str(ATTEMPT_ID)) == INIT
    assert normalize_helper_command(f"cd {OWN} && {INIT}", attempt_id=str(uuid4())) is None


def test_only_one_prefix_is_removed_and_the_rest_is_not_a_helper_line() -> None:
    remaining = normalize_helper_command(f"cd {OWN} && cd x && {INIT}", attempt_id=ATTEMPT_ID)

    assert remaining == f"cd x && {INIT}"
    assert remaining not in HELPER_COMMANDS
    assert f'bash -c "{INIT}"' not in HELPER_COMMANDS


# ---------------------------------------------------------------------------
# parse_tool_guard_request (§8.17 ①)
# ---------------------------------------------------------------------------


def test_parse_tool_guard_request_reads_the_real_prompt() -> None:
    request = parse_tool_guard_request(make_message(REAL_BODY))

    assert request is not None
    assert request.command == f"cd {OWN} && {INIT}"
    assert request.event_id == "$evt1"
    assert request.room_id == ROOM
    assert request.sender == WORKER_MATRIX
    assert request.occurred_at == T0 + timedelta(minutes=1)


def test_parse_tool_guard_request_survives_crlf() -> None:
    request = parse_tool_guard_request(make_message(REAL_BODY.replace("\n", "\r\n")))

    assert request is not None
    assert request.command == f"cd {OWN} && {INIT}"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(FABRICATED_BODY, id="fabricated"),
        pytest.param(tool_guard_body(INIT, tool="read_file"), id="wrong-tool"),
        pytest.param(tool_guard_body(None, parameters='{"path": "x"}'), id="no-command"),
        pytest.param(tool_guard_body(None, parameters='"just a string"'), id="not-an-object"),
        pytest.param(tool_guard_body(None, parameters="{not json"), id="broken-json"),
        pytest.param(tool_guard_body(None, parameters='{"command": ""}'), id="blank-command"),
        pytest.param(tool_guard_body(None, parameters='{"command": 42}'), id="non-text-command"),
        pytest.param(REAL_BODY.replace("Waiting for approval", "Approved"), id="no-header"),
        pytest.param(REAL_BODY.replace("```json", "```"), id="unlabelled-fence"),
        pytest.param("/approve", id="an-approval-itself"),
    ],
)
def test_parse_tool_guard_request_rejects_everything_else(body: str) -> None:
    assert parse_tool_guard_request(make_message(body)) is None


def test_parse_tool_guard_request_keeps_the_command_verbatim() -> None:
    body = tool_guard_body(f"  cd {OWN}  &&  {INIT}  ")

    request = parse_tool_guard_request(make_message(body))

    assert request is not None
    assert request.command == f"  cd {OWN}  &&  {INIT}  "
