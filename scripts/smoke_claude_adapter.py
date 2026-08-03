"""Run a real Claude Code adapter smoke test against an isolated Git worktree."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from repomesh.integrations.coding_agents.registry import build_default_registry
from repomesh.modules.agent_runtime.ports.agent_adapter import (
    AgentFeedback,
    AgentLaunchRequest,
    AgentSessionRef,
    AuthStatus,
    FeedbackKind,
    LaunchPlan,
    PermissionMode,
)
from repomesh_runner.contracts import (
    ArtifactRef,
    ContextBundleRef,
    RepositoryCheckout,
    RunnerExecutionResult,
    RunnerPermissionMode,
    RunnerPermissions,
    RunnerResultStatus,
    RunnerTask,
)
from repomesh_runner.engine import ExecuteRunnerTask


@dataclass(frozen=True, slots=True)
class ProcessResult:
    exit_code: int
    output: str
    result_event: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class WorkerAnswer:
    question: str
    answer: str
    evidence: tuple[dict[str, object], ...]


class ContextWorkerAgent:
    """Answer narrow execution questions from repository-owned task context."""

    _STOP_WORDS = frozenset(
        {
            "a",
            "an",
            "and",
            "are",
            "be",
            "does",
            "in",
            "is",
            "of",
            "or",
            "the",
            "to",
        }
    )

    def __init__(self, context_files: tuple[str, ...] = ("TASK.md", "README.md")) -> None:
        self._context_files = context_files

    def answer(self, question: str, workspace: Path) -> WorkerAnswer | None:
        question_tokens = self._tokens(question)
        candidates: list[tuple[int, str, int, str]] = []
        for relative_path in self._context_files:
            path = workspace / relative_path
            if not path.is_file():
                continue
            for line_number, raw_line in enumerate(
                path.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                line = raw_line.strip().lstrip("#*-0123456789. ").strip()
                if not line:
                    continue
                overlap = question_tokens & self._tokens(line)
                if overlap:
                    authority_bonus = 3 if re.search(r"\b(only|must)\b", line.casefold()) else 0
                    candidates.append(
                        (len(overlap) + authority_bonus, relative_path, line_number, line)
                    )

        candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
        selected = candidates[:4]
        if not selected:
            return None

        evidence = tuple(
            {
                "path": relative_path,
                "line": line_number,
                "text": line,
            }
            for _, relative_path, line_number, line in selected
        )
        answer = "Approved repository requirements state: " + " ".join(
            item["text"] for item in evidence
        )
        return WorkerAnswer(question=question, answer=answer, evidence=evidence)

    @classmethod
    def _tokens(cls, value: str) -> set[str]:
        tokens: set[str] = set()
        for raw_token in re.findall(r"[a-z0-9_]+", value.casefold()):
            for token in raw_token.split("_"):
                if token.endswith("s") and len(token) > 3:
                    token = token[:-1]
                if len(token) > 1 and token not in cls._STOP_WORDS:
                    tokens.add(token)
        return tokens


class JsonlEventSink:
    def __init__(self, path: Path) -> None:
        self._path = path

    async def publish(self, event: Any, *, idempotency_key: str) -> None:
        record = event.to_wire()
        record["idempotencyKey"] = idempotency_key
        with self._path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=True) + "\n")


class ClaudeFixtureExecutor:
    def __init__(
        self,
        *,
        repository: Path,
        worktree: Path,
        artifact_dir: Path,
        allowed_paths: frozenset[str],
        test_command: tuple[str, ...],
        timeout_seconds: float,
        max_test_attempts: int,
        worker_agent: ContextWorkerAgent | None = None,
        max_question_rounds: int = 2,
        completion_timeout_seconds: float = 240.0,
    ) -> None:
        self._repository = repository
        self._worktree = worktree
        self._artifact_dir = artifact_dir
        self._allowed_paths = allowed_paths
        self._test_command = test_command
        self._timeout_seconds = timeout_seconds
        self._max_test_attempts = max_test_attempts
        self._worker_agent = worker_agent
        self._max_question_rounds = max_question_rounds
        self._completion_timeout_seconds = completion_timeout_seconds
        self.candidate_sha: str | None = None
        self.changed_files: tuple[str, ...] = ()
        self.agent_attempts = 0
        self.test_attempts = 0
        self.worker_answers: list[WorkerAnswer] = []

    async def execute(self, task: RunnerTask) -> RunnerExecutionResult:
        adapter = build_default_registry().resolve(task.adapter_id)
        probe = await adapter.probe()
        self._write_json(
            "adapter-probe.json",
            {
                "adapterId": probe.adapter_id,
                "installed": probe.installed,
                "executable": probe.executable,
                "authStatus": probe.auth_status.value,
                "detail": probe.detail,
            },
        )
        if not probe.installed:
            raise RuntimeError("Claude Code is not installed")
        if probe.auth_status is AuthStatus.UNAUTHORIZED:
            raise RuntimeError("Claude Code is not authenticated")

        self._create_worktree(task.repository.base_revision)
        session_id = str(uuid4())
        deadline = asyncio.get_running_loop().time() + self._timeout_seconds
        next_prompt = task.instruction
        question_rounds = 0

        while self.test_attempts < self._max_test_attempts:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return self._failed("Coding run exceeded its total timeout", session_id)

            self.agent_attempts += 1
            if self.agent_attempts > self._max_test_attempts + self._max_question_rounds:
                return self._failed(
                    "Coding run exceeded its maximum interaction rounds",
                    session_id,
                )

            request = AgentLaunchRequest(
                workspace_path=self._worktree,
                prompt=next_prompt,
                session_id=session_id,
                permission_mode=PermissionMode.ACCEPT_EDITS,
                system_prompt=(
                    "You are a coding executor controlled by RepoMesh. Work only on the current "
                    "task. Do not commit, push, create pull requests, or change task contracts. "
                    "Inspect the repository, make the smallest correct change, and run the "
                    "specified test command before finishing."
                ),
                allowed_tools=(
                    "Read",
                    "Edit",
                    "Write",
                    "Glob",
                    "Grep",
                    "Bash",
                ),
                disallowed_tools=(
                    "Bash(git commit:*)",
                    "Bash(git push:*)",
                ),
            )
            if self.agent_attempts == 1:
                plan = adapter.build_launch(request)
            else:
                plan = adapter.build_restore(
                    request,
                    AgentSessionRef(session_id, self._worktree),
                )
                if plan is None:
                    return self._failed("Adapter cannot restore the Claude session", session_id)

            agent_result = await self._run_plan(plan, min(remaining, self._timeout_seconds))
            self._artifact_dir.joinpath(
                f"agent-attempt-{self.agent_attempts}.log"
            ).write_text(
                agent_result.output,
                encoding="utf-8",
            )

            question = self._input_question(agent_result)
            if question is not None:
                question_rounds += 1
                if question_rounds > self._max_question_rounds:
                    return self._input_required(
                        "Claude exceeded the automatic-answer question limit",
                        session_id,
                    )
                if self._worker_agent is None:
                    return self._input_required(question, session_id)
                worker_answer = self._worker_agent.answer(question, self._worktree)
                if worker_answer is None:
                    return self._input_required(
                        f"Worker Agent could not answer from approved context: {question}",
                        session_id,
                    )
                self.worker_answers.append(worker_answer)
                self._write_json(
                    f"worker-answer-{question_rounds}.json",
                    {
                        "agent": "context-worker",
                        "question": worker_answer.question,
                        "answer": worker_answer.answer,
                        "evidence": list(worker_answer.evidence),
                        "nativeSessionId": session_id,
                        "action": "resume_same_session",
                    },
                )
                next_prompt = (
                    "[WORKER AGENT ANSWER]\n"
                    f"{worker_answer.answer}\n\n"
                    "Use this answer as project context and continue the staged task."
                )
                continue

            self.test_attempts += 1

            test_result = self._run_test()
            self._artifact_dir.joinpath(
                f"test-attempt-{self.test_attempts}.log"
            ).write_text(
                test_result.output,
                encoding="utf-8",
            )
            self.changed_files = self._collect_changed_files()
            violations = sorted(set(self.changed_files) - self._allowed_paths)

            if self._head_revision() != task.repository.base_revision:
                return self._failed("AGENT_COMMIT_DETECTED", session_id)

            if violations:
                return self._failed(
                    f"PATH_POLICY_VIOLATION: {', '.join(violations)}",
                    session_id,
                )

            if test_result.exit_code == 0:
                if agent_result.exit_code == 0:
                    return self._create_candidate(task, session_id)
                completion_result = await self._confirm_session_completion(
                    adapter=adapter,
                    request=request,
                    session_id=session_id,
                )
                if completion_result.exit_code == 0:
                    return self._create_candidate(task, session_id)

            if self.test_attempts == self._max_test_attempts:
                return self._failed(
                    f"Authoritative tests failed after {self.test_attempts} attempts",
                    session_id,
                )

            feedback = adapter.receive_feedback(
                AgentFeedback(
                    kind=FeedbackKind.CI,
                    summary="Authoritative fixture tests failed",
                    details=test_result.output[-4_000:],
                )
            )
            next_prompt = feedback.prompt

        return self._failed("Coding run ended without a result", session_id)

    async def _confirm_session_completion(
        self,
        *,
        adapter: Any,
        request: AgentLaunchRequest,
        session_id: str,
    ) -> ProcessResult:
        completion_request = AgentLaunchRequest(
            workspace_path=request.workspace_path,
            prompt=(
                "The Runner independently verified that all authoritative tests now pass. "
                "Do not use tools or inspect files. Return exactly: RECOVERY_COMPLETE"
            ),
            session_id=session_id,
            permission_mode=PermissionMode.DEFAULT,
            system_prompt="Return only the requested terminal acknowledgement.",
            allowed_tools=(),
            disallowed_tools=(),
        )
        plan = adapter.build_restore(
            completion_request,
            AgentSessionRef(session_id, self._worktree),
        )
        if plan is None:
            return ProcessResult(1, "Adapter cannot confirm session completion")
        result = await self._run_plan(plan, self._completion_timeout_seconds)
        self._artifact_dir.joinpath(
            f"agent-completion-{self.agent_attempts}.log"
        ).write_text(result.output, encoding="utf-8")
        if result.result_event is None:
            return ProcessResult(1, result.output, result.result_event)
        if result.result_event.get("result") != "RECOVERY_COMPLETE":
            return ProcessResult(1, result.output, result.result_event)
        return result

    def _create_worktree(self, base_revision: str) -> None:
        self._worktree.parent.mkdir(parents=True, exist_ok=True)
        result = self._run(
            ("git", "worktree", "add", "--detach", str(self._worktree), base_revision),
            cwd=self._repository,
        )
        if result.exit_code != 0:
            raise RuntimeError(f"failed to create worktree: {result.output}")

    async def _run_plan(self, plan: LaunchPlan, timeout_seconds: float) -> ProcessResult:
        environment = dict(os.environ)
        environment.update(plan.environment)
        process = await asyncio.create_subprocess_exec(
            *plan.argv,
            cwd=plan.working_directory,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output_lines: list[str] = []
        result_event: dict[str, Any] | None = None
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        try:
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError
                line = await asyncio.wait_for(process.stdout.readline(), remaining)
                if not line:
                    break
                text = line.decode(errors="replace")
                output_lines.append(text)
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "result":
                    result_event = event
                    break
        except TimeoutError:
            process.kill()
            await process.wait()
            output_lines.append("\n[REPOMESH] Claude process timed out before result event.\n")
            return ProcessResult(124, "".join(output_lines))

        if result_event is not None and process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), 5.0)
            except TimeoutError:
                process.kill()
                await process.wait()
        elif process.returncode is None:
            await process.wait()

        output = "".join(output_lines)
        if result_event is not None:
            failed = bool(result_event.get("is_error")) or result_event.get("subtype") == "error"
            return ProcessResult(1 if failed else 0, output, result_event)
        return ProcessResult(process.returncode or 0, output)

    @staticmethod
    def _input_question(result: ProcessResult) -> str | None:
        if result.result_event is None:
            return None
        marker = "REPOMESH_INPUT_REQUIRED:"
        message = str(result.result_event.get("result", ""))
        if marker not in message:
            return None
        question = message.split(marker, 1)[1].strip().splitlines()[0].strip()
        return question or "Claude requested additional project context"

    def _run_test(self) -> ProcessResult:
        command = list(self._test_command)
        if command and command[0].casefold() in {"python", "python3"}:
            command[0] = sys.executable
        return self._run(tuple(command), cwd=self._worktree)

    def _collect_changed_files(self) -> tuple[str, ...]:
        result = self._run(
            ("git", "status", "--porcelain", "--untracked-files=all"),
            cwd=self._worktree,
        )
        if result.exit_code != 0:
            raise RuntimeError(f"failed to inspect worktree: {result.output}")
        paths = []
        for line in result.output.splitlines():
            if len(line) < 4:
                continue
            path = line[3:].strip().replace("\\", "/")
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            paths.append(path)
        return tuple(sorted(set(paths)))

    def _create_candidate(self, task: RunnerTask, session_id: str) -> RunnerExecutionResult:
        add = self._run(("git", "add", "--all"), cwd=self._worktree)
        if add.exit_code != 0:
            raise RuntimeError(f"failed to stage candidate: {add.output}")
        patch = self._run(
            ("git", "diff", "--cached", "--binary", task.repository.base_revision),
            cwd=self._worktree,
        )
        if patch.exit_code != 0 or not patch.output.strip():
            return self._failed("Tests passed but no candidate diff was produced", session_id)

        patch_path = self._artifact_dir / "candidate.patch"
        patch_path.write_text(patch.output, encoding="utf-8")
        patch_hash = _sha256_file(patch_path)

        commit = self._run(
            (
                "git",
                "-c",
                "user.name=RepoMesh Harness",
                "-c",
                "user.email=repomesh@local.invalid",
                "commit",
                "-m",
                "fix: apply coding-agent candidate",
            ),
            cwd=self._worktree,
        )
        if commit.exit_code != 0:
            raise RuntimeError(f"failed to create candidate commit: {commit.output}")

        sha = self._run(("git", "rev-parse", "HEAD"), cwd=self._worktree)
        self.candidate_sha = sha.output.strip()
        return RunnerExecutionResult(
            status=RunnerResultStatus.SUCCEEDED,
            summary=f"Candidate {self.candidate_sha} passed authoritative tests",
            native_session_id=session_id,
            artifacts=(
                ArtifactRef(
                    kind="candidate_patch",
                    uri=patch_path.resolve().as_uri(),
                    content_hash=f"sha256:{patch_hash}",
                ),
            ),
            test_command=" ".join(self._test_command),
        )

    def _failed(self, summary: str, session_id: str) -> RunnerExecutionResult:
        return RunnerExecutionResult(
            status=RunnerResultStatus.FAILED,
            summary=summary,
            native_session_id=session_id,
            test_command=" ".join(self._test_command),
        )

    def _input_required(self, summary: str, session_id: str) -> RunnerExecutionResult:
        return RunnerExecutionResult(
            status=RunnerResultStatus.INPUT_REQUIRED,
            summary=summary,
            native_session_id=session_id,
            test_command=" ".join(self._test_command),
        )

    def _head_revision(self) -> str:
        result = self._run(("git", "rev-parse", "HEAD"), cwd=self._worktree)
        if result.exit_code != 0:
            raise RuntimeError(f"failed to read worktree HEAD: {result.output}")
        return result.output.strip()

    @staticmethod
    def _run(arguments: tuple[str, ...], *, cwd: Path) -> ProcessResult:
        completed = subprocess.run(
            arguments,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return ProcessResult(completed.returncode, completed.stdout)

    def _write_json(self, name: str, payload: dict[str, object]) -> None:
        self._artifact_dir.joinpath(name).write_text(
            json.dumps(payload, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_output(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=repository,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stdout)
    return completed.stdout.strip()


def _build_task(repository: Path, base_sha: str, instruction: str) -> RunnerTask:
    task_file = repository / "TASK.md"
    task_hash = _sha256_file(task_file)
    return RunnerTask(
        organization_id=uuid4(),
        project_id=uuid4(),
        task_id=uuid4(),
        run_id=uuid4(),
        correlation_id=uuid4(),
        attempt=1,
        adapter_id="claude-code",
        instruction=instruction,
        repository=RepositoryCheckout(
            repository_id=uuid4(),
            url=repository.resolve().as_uri(),
            base_revision=base_sha,
        ),
        context_bundle=ContextBundleRef(
            bundle_id=uuid4(),
            version=1,
            manifest_uri=task_file.resolve().as_uri(),
            content_hash=f"sha256:{task_hash}",
        ),
        permissions=RunnerPermissions(
            mode=RunnerPermissionMode.ACCEPT_EDITS,
            allowed_tools=("read", "edit", "test"),
            network_targets=("*",),
        ),
        idempotency_key=f"claude-smoke:{uuid4()}",
        issued_at=datetime.now(UTC),
    )


async def _run_smoke(arguments: argparse.Namespace) -> int:
    repository = arguments.repository.resolve()
    base_sha = _git_output(repository, "rev-parse", "HEAD")
    run_id = uuid4()
    artifact_dir = arguments.output_dir.resolve() / str(run_id)
    artifact_dir.mkdir(parents=True)
    worktree = artifact_dir / "worktree"

    if arguments.verify_answer_and_resume:
        instruction = (
            "Run this staged recovery verification exactly as written. Phase 1: do not read "
            "files, edit files, or run commands. Return exactly this single line and stop: "
            "REPOMESH_INPUT_REQUIRED: Does shipping participate in the discount or tax base? "
            "Phase 2 begins only after a Worker Agent answers. Then read README.md and TASK.md. "
            "Modify only src/checkout_fixture/pricing.py so discounts exclude shipping, but "
            "deliberately leave shipping in the tax base for this attempt. Run "
            "`python scripts/run_tests.py` exactly once. The expected tax test failure is part "
            "of this verification: stop immediately after reporting it and do not fix tax yet. "
            "Phase 3 begins only after authoritative Runner feedback. Then fix the remaining "
            "tax calculation, rerun the tests, and stop. Never commit changes."
        )
    else:
        instruction = (
            "Read README.md and TASK.md, then fix the documented bug. "
            "Only src/checkout_fixture/pricing.py is an accepted candidate path. "
            "Run `python scripts/run_tests.py`. Do not commit changes."
        )
    task = _build_task(repository, base_sha, instruction)
    (artifact_dir / "runner-task.json").write_text(
        json.dumps(task.to_wire(), indent=2, ensure_ascii=True),
        encoding="utf-8",
    )

    executor = ClaudeFixtureExecutor(
        repository=repository,
        worktree=worktree,
        artifact_dir=artifact_dir,
        allowed_paths=frozenset(arguments.allowed_path),
        test_command=tuple(arguments.test_command),
        timeout_seconds=arguments.timeout_seconds,
        max_test_attempts=arguments.max_test_attempts,
        worker_agent=ContextWorkerAgent() if arguments.verify_answer_and_resume else None,
    )
    runner = ExecuteRunnerTask(executor, JsonlEventSink(artifact_dir / "events.jsonl"))
    result = await runner.execute(task)
    summary = {
        "status": result.status.value,
        "summary": result.summary,
        "baseSha": base_sha,
        "candidateSha": executor.candidate_sha,
        "changedFiles": list(executor.changed_files),
        "nativeSessionId": result.native_session_id,
        "agentAttempts": executor.agent_attempts,
        "testAttempts": executor.test_attempts,
        "workerAnswers": [
            {
                "question": answer.question,
                "answer": answer.answer,
                "evidence": list(answer.evidence),
            }
            for answer in executor.worker_answers
        ],
        "artifacts": [artifact.to_wire() for artifact in result.artifacts],
        "worktree": str(worktree),
        "runDirectory": str(artifact_dir),
    }
    (artifact_dir / "smoke-result.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=True))
    return 0 if result.status is RunnerResultStatus.SUCCEEDED else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".repomesh-smoke-runs"),
    )
    parser.add_argument(
        "--allowed-path",
        action="append",
        default=["src/checkout_fixture/pricing.py"],
    )
    parser.add_argument(
        "--test-command",
        nargs="+",
        default=["python", "scripts/run_tests.py"],
    )
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--max-test-attempts", type=int, default=3)
    parser.add_argument(
        "--verify-answer-and-resume",
        action="store_true",
        help="Verify Worker Agent context answers and same-session recovery after test failure.",
    )
    return parser


def main() -> int:
    return asyncio.run(_run_smoke(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
