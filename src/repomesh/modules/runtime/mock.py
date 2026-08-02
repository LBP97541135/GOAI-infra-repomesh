from .ports import CodingRunRequest, CodingRunResult, RunStatus


class MockCodingAgent:
    name = "mock"

    async def execute(self, request: CodingRunRequest) -> CodingRunResult:
        return CodingRunResult(
            run_id=request.run_id,
            status=RunStatus.SUCCEEDED,
            summary=f"Mock execution completed for task {request.task_id}",
            changed_files=("README.md",),
            test_command="pytest",
        )

