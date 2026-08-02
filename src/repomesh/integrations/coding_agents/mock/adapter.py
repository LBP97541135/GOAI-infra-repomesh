from enum import StrEnum

from repomesh.modules.agent_runtime.ports import (
    CodingRunRequest,
    CodingRunResult,
    RunEvent,
    RunStatus,
)


class MockScenario(StrEnum):
    SUCCESS = "success"
    TEST_FAILED = "test_failed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    QUESTION_REQUIRED = "question_required"


class MockCodingAgent:
    name = "mock"

    def __init__(self, scenario: MockScenario = MockScenario.SUCCESS) -> None:
        self._scenario = scenario

    async def execute(self, request: CodingRunRequest) -> CodingRunResult:
        if self._scenario is MockScenario.SUCCESS:
            return CodingRunResult(
                run_id=request.run_id,
                status=RunStatus.SUCCEEDED,
                summary=f"Mock execution completed for task {request.task_id}",
                changed_files=("README.md",),
                test_command="pytest",
                events=(
                    RunEvent("run.started", "Mock worker started"),
                    RunEvent("tests.passed", "pytest completed successfully"),
                    RunEvent("run.finished", "Candidate changes collected"),
                ),
            )
        if self._scenario is MockScenario.TEST_FAILED:
            return self._result(
                request,
                RunStatus.FAILED,
                "Candidate produced but validation failed",
                "tests.failed",
            )
        if self._scenario is MockScenario.TIMEOUT:
            return self._result(
                request, RunStatus.TIMED_OUT, "Run exceeded its lease", "run.timeout"
            )
        if self._scenario is MockScenario.CANCELLED:
            return self._result(request, RunStatus.CANCELLED, "Run was cancelled", "run.cancelled")
        if self._scenario is MockScenario.INTERRUPTED:
            return self._result(
                request,
                RunStatus.INTERRUPTED,
                "Run stopped at a recoverable checkpoint",
                "run.interrupted",
            )
        if self._scenario is MockScenario.QUESTION_REQUIRED:
            return self._result(
                request,
                RunStatus.WAITING_FOR_INPUT,
                "Worker requires a manager decision",
                "question.created",
            )
        return self._result(request, RunStatus.FAILED, "Agent process failed", "run.failed")

    @staticmethod
    def _result(
        request: CodingRunRequest, status: RunStatus, summary: str, event_type: str
    ) -> CodingRunResult:
        return CodingRunResult(
            run_id=request.run_id,
            status=status,
            summary=summary,
            test_command="pytest" if event_type == "tests.failed" else None,
            events=(RunEvent("run.started", "Mock worker started"), RunEvent(event_type, summary)),
        )
