from fastapi import APIRouter, Request

from repomesh.modules.agent_runtime.application import ExecuteCodingRun
from repomesh.modules.agent_runtime.ports import CodingRunRequest

from .models import CodingRunCreate, CodingRunView, RunEventView

router = APIRouter(tags=["agent-runtime"])


@router.post("/coding-runs/mock", response_model=CodingRunView, status_code=202)
async def run_mock_agent(body: CodingRunCreate, request: Request) -> CodingRunView:
    container = request.app.state.container
    agent = container.mock_coding_agent_factory(body.scenario)
    result = await ExecuteCodingRun(agent).execute(
        CodingRunRequest(
            task_id=body.task_id,
            repository_url=str(body.repository_url),
            instruction=body.instruction,
            base_revision=body.base_revision,
        )
    )
    return CodingRunView(
        run_id=result.run_id,
        status=result.status,
        adapter=agent.name,
        summary=result.summary,
        changed_files=result.changed_files,
        test_command=result.test_command,
        events=tuple(
            RunEventView(type=event.type, message=event.message) for event in result.events
        ),
    )
