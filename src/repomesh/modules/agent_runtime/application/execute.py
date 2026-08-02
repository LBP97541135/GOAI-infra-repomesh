from repomesh.modules.agent_runtime.ports import CodingAgent, CodingRunRequest, CodingRunResult


class ExecuteCodingRun:
    def __init__(self, agent: CodingAgent) -> None:
        self._agent = agent

    async def execute(self, request: CodingRunRequest) -> CodingRunResult:
        return await self._agent.execute(request)
