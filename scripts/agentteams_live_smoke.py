#!/usr/bin/env python3
"""Live AgentTeams Controller and Matrix smoke test without printing secrets."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = _PROJECT_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from repomesh.integrations.agentteams import (  # noqa: E402
    AgentTeamsControlPlaneClient,
    AgentTeamsMatrixClient,
)
from repomesh.modules.agent_runtime.ports import (  # noqa: E402
    ManagerProjection,
    TeamMemberProjection,
    TeamProjection,
    TeamRole,
    WorkerProjection,
)


async def run(args: argparse.Namespace) -> int:
    controller_url = os.getenv(
        "REPOMESH_AGENTTEAMS_CONTROLLER_URL", "http://localhost:8090"
    )
    controller_token = os.getenv("REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN")
    matrix_url = os.getenv("REPOMESH_AGENTTEAMS_MATRIX_URL", "http://localhost:6167")
    matrix_token = os.getenv("REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN")
    controller = AgentTeamsControlPlaneClient(controller_url, token=controller_token)
    try:
        if not await controller.health():
            print(f"FAIL controller is not healthy: {controller_url}")
            return 2
        version = await controller.version()
        print(f"PASS controller health: {version.controller} ({version.kube_mode})")

        if args.provision:
            manager_name = f"{args.prefix}-manager"
            leader_name = f"{args.prefix}-leader"
            worker_name = f"{args.prefix}-worker"
            manager = await controller.ensure_manager(
                ManagerProjection(manager_name, args.model),
                idempotency_key=f"smoke:{args.prefix}:manager",
            )
            leader = await controller.ensure_worker(
                WorkerProjection(leader_name, args.model),
                idempotency_key=f"smoke:{args.prefix}:leader",
            )
            worker = await controller.ensure_worker(
                WorkerProjection(worker_name, args.model),
                idempotency_key=f"smoke:{args.prefix}:worker",
            )
            team = await controller.ensure_team(
                TeamProjection(
                    name=f"{args.prefix}-team",
                    members=(
                        TeamMemberProjection(leader_name, TeamRole.LEADER),
                        TeamMemberProjection(worker_name, TeamRole.WORKER),
                    ),
                    description="RepoMesh live smoke team",
                ),
                idempotency_key=f"smoke:{args.prefix}:team",
            )
            print(
                "PASS resources: "
                f"manager={manager.phase} leader={leader.phase} "
                f"worker={worker.phase} team={team.phase}"
            )
            print(
                "INFO rooms: "
                f"team={'ready' if team.team_room_id else 'missing'} "
                f"leader={'ready' if team.leader_room_id else 'missing'}"
            )

        if not matrix_token:
            print("SKIP Matrix: REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN is not configured")
            return 3
        matrix = AgentTeamsMatrixClient(matrix_url, matrix_token)
        try:
            user_id = await matrix.whoami()
            batch = await matrix.sync_once(timeout_ms=0)
            print(
                f"PASS Matrix identity={user_id} joined_text_events={len(batch.messages)}"
            )
            if args.room_id:
                event_id = await matrix.send_task(
                    args.room_id,
                    "RepoMesh live smoke message",
                    transaction_id=f"smoke-{args.prefix}",
                )
                print(f"PASS Matrix send event={event_id}")
        finally:
            await matrix.close()
    finally:
        await controller.close()
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--provision", action="store_true")
    result.add_argument("--model", default="qwen3.5-plus")
    result.add_argument("--prefix", default="repomesh-smoke")
    result.add_argument("--room-id")
    return result


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parser().parse_args())))
