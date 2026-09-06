"""Room text of the hosted-native round (spec §4.2 M1 "藏在后面：房间文案").

Pure functions so ``round.py`` carries no prose and the tests can assert on the
exact words. Two rules from the spike shape every line here: the worker is told
to report to ``@admin`` (the platform's sender identity) or to nobody and
never to @mention the Team Leader (D-3, S-4), and neither notice mentions the
MCP projection — the worker must not call ``start_assigned_task`` (D-18).
The Matrix adapter prefixes the recipient's own id to the body, so the notices
do not repeat it.
"""

from __future__ import annotations

from uuid import UUID

from repomesh.integrations.agentteams.task_package import HELPER_COMMANDS

REVISION_NOTE_HEADING = "## Note from the previous attempt (Leader review)"


def _minutes(seconds: int) -> int:
    return max(1, seconds // 60)


def construction_notice(
    *, attempt_id: UUID, package_dir: str, title: str, budget_seconds: int
) -> str:
    """The team-room notice that opens one construction attempt for the worker."""

    task_dir = f"shared/tasks/{attempt_id}"
    commands = "\n".join(f"   {command}" for command in HELPER_COMMANDS[:3])
    return (
        f"Task package ready: {title}\n"
        f"Task directory: {task_dir}/ (object prefix {package_dir})\n"
        "Do everything below yourself, inside your container; no other tool server is "
        "needed or allowed for this task.\n"
        f'1. taskflow(action="ack_task", payload={{"taskId": "{attempt_id}"}})\n'
        f"2. Read {task_dir}/spec.md and follow it exactly.\n"
        "3. From the task directory run these command lines, in this order and exactly as "
        "written:\n"
        f"{commands}\n"
        '4. taskflow(action="submit_task", ...) with the four candidate/ deliverables spec.md '
        "lists, or status BLOCKED with the reason in summary and no deliverables.\n"
        f"Budget: {_minutes(budget_seconds)} minutes from this notice.\n"
        "Completion notice: after submit_task, post exactly one TASK_COMPLETED line in this "
        "room addressed to @admin or to nobody. Never @mention the Team Leader or any other "
        "member: the platform reads your result from submit_task, not from the room."
    )


def review_notice(
    *,
    review_id: UUID,
    attempt_id: UUID,
    package_dir: str,
    head_sha: str,
    title: str,
    budget_seconds: int,
) -> str:
    """The Leader-room notice that puts one candidate in front of the Team Leader."""

    task_dir = f"shared/tasks/{review_id}"
    return (
        f"Review requested: candidate {head_sha[:8]} for \"{title}\"\n"
        f"Review task directory: {task_dir}/ (object prefix {package_dir})\n"
        f"It reviews construction attempt {attempt_id}. Review only; do not build, do not "
        "run the tests and do not edit any file.\n"
        f'1. taskflow(action="ack_task", payload={{"taskId": "{review_id}"}})\n'
        f"2. Read {task_dir}/spec.md, then review/candidate.diff, review/changes.json and "
        "review/evidence.json.\n"
        '3. Answer with exactly one taskflow(action="submit_task", ...): deliverables [] and a '
        "summary whose first line is VERDICT: ACCEPT, VERDICT: REVISION or VERDICT: BLOCKED, "
        "followed by 2-6 lines of reasons. The status must agree: SUCCESS or "
        "SUCCESS_WITH_NOTES = ACCEPT, REVISION_NEEDED = REVISION, BLOCKED = BLOCKED.\n"
        "Do not @mention the Worker; the platform relays your verdict. An independent "
        "verifier re-runs the frozen tests after ACCEPT.\n"
        f"Budget: {_minutes(budget_seconds)} minutes from this notice."
    )


def revision_instruction(instruction: str, note: str) -> str:
    """The original instruction with the Leader's revision reasons appended."""

    return f"{instruction.rstrip()}\n\n{REVISION_NOTE_HEADING}\n\n{note.strip()}"
