#!/usr/bin/env python3
"""End-to-end pipeline runner.

Calls the RepoMesh API to execute the full chain:
  requirement analysis → discovery → confirmation → integration → materialize

Usage:
  # Start the API server first:
  uvicorn repomesh.bootstrap.app:create_app --factory --port 8000

  # Then run this script:
  python scripts/run_pipeline.py \
      --requirement "修复通知邮件发送异常" \
      --project-id <uuid> --leader-agent-id <uuid>

  # Or with a requirement file:
  python scripts/run_pipeline.py \
      --requirement-file PRD.md \
      --project-id <uuid> --leader-agent-id <uuid>
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid

import requests

DEFAULT_URL = "http://localhost:8000/api/v1"


def _auth_headers() -> dict[str, str]:
    """The repository-intelligence writes now require the action token.

    They used to be reachable with no credential at all; this script was the
    one in-repo caller relying on that. Export REPOMESH_AGENT_ACTION_TOKEN to
    match the server, or every POST here comes back 401/503.
    """
    token = os.environ.get("REPOMESH_AGENT_ACTION_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _post(url: str, payload: dict, label: str) -> dict:
    """POST JSON and return parsed response, with timing + error handling."""
    print(f"\n{'='*60}")
    print(f"[{label}] POST {url}")
    print(f"  payload keys: {list(payload.keys())}")
    t0 = time.time()
    resp = requests.post(url, json=payload, timeout=120, headers=_auth_headers())
    elapsed = time.time() - t0
    print(f"  status: {resp.status_code}  ({elapsed:.1f}s)")

    if resp.status_code != 200:
        print(f"  ERROR: {resp.text[:500]}")
        resp.raise_for_status()

    data = resp.json()
    return data


def _summarize(label: str, data: dict, fields: list[str]) -> None:
    """Print a compact summary of interesting fields."""
    print(f"\n--- {label} summary ---")
    for f in fields:
        if f in data:
            val = data[f]
            if isinstance(val, list):
                print(f"  {f}: {len(val)} items")
                for item in val[:5]:
                    print(f"    - {item}")
                if len(val) > 5:
                    print(f"    ... and {len(val) - 5} more")
            elif isinstance(val, str) and len(val) > 200:
                print(f"  {f}: {val[:200]}...")
            else:
                print(f"  {f}: {val}")


def run_pipeline(
    base_url: str,
    requirement: str,
    project_id: str,
    leader_agent_id: str,
    *,
    skip_analysis: bool = False,
    skip_materialize: bool = False,
    idempotency_prefix: str | None = None,
    auto_yes: bool = False,
) -> None:
    """Run the full pipeline."""
    if idempotency_prefix is None:
        idempotency_prefix = f"pipeline-{uuid.uuid4().hex[:8]}"

    # ---------------------------------------------------------------
    # Step 0: Requirement Analysis
    # ---------------------------------------------------------------
    if not skip_analysis:
        analysis = _post(
            f"{base_url}/requirement-analysis",
            {"requirement": requirement},
            "Step 0: Requirement Analysis",
        )
        _summarize("Analysis", analysis, [
            "sufficient", "confidence", "missing_dimensions",
            "questions", "extracted_keywords",
        ])

        if not analysis["sufficient"]:
            print("\n[!] Requirement is NOT sufficient.")
            if analysis["questions"]:
                print("    Follow-up questions:")
                for i, q in enumerate(analysis["questions"], 1):
                    print(f"      {i}. {q}")
                print("\n    Please answer these questions and re-run")
                print("    with an updated requirement.")
                if not _ask_continue(auto_yes=auto_yes):
                    sys.exit(0)
        else:
            print("\n[OK] Requirement is sufficient, proceeding.")
    else:
        print("\n[SKIP] Requirement analysis skipped.")

    # ---------------------------------------------------------------
    # Step 1: Discovery
    # ---------------------------------------------------------------
    candidates = _post(
        f"{base_url}/discovery",
        {"requirement": requirement, "limit": 10},
        "Step 1: Discovery",
    )
    # API returns a list of DiscoveryCandidate directly
    if isinstance(candidates, dict):
        candidates = candidates.get("candidates", [])

    print("\n--- Discovery summary ---")
    print(f"  candidates: {len(candidates)}")
    for c in candidates[:10]:
        name = c.get("repository_name") or c.get("repository", "?")
        score = c.get("score", 0)
        rationale = c.get("rationale", "")[:80]
        print(f"    - {name} (score={score:.2f}): {rationale}")

    if not candidates:
        print("\n[ERROR] No candidate repositories found. Stopping.")
        sys.exit(1)

    candidate_names = [
        c.get("repository_name") or c.get("repository", "")
        for c in candidates
    ]
    # Build evidence dict for confirmation
    evidence = {
        name: (c.get("rationale", ""), c.get("score", 0.5))
        for name, c in zip(candidate_names, candidates, strict=True)
    }

    # ---------------------------------------------------------------
    # Step 2: Confirmation
    # ---------------------------------------------------------------
    confirmation = _post(
        f"{base_url}/confirmation",
        {
            "candidate_repos": candidate_names,
            "requirement": requirement,
            "discovery_evidence": {
                k: list(v) for k, v in evidence.items()
            },
        },
        "Step 2: Confirmation",
    )
    required = confirmation.get("required", [])
    maybe = confirmation.get("maybe", [])
    excluded = confirmation.get("excluded", [])
    print("\n--- Confirmation summary ---")
    print(f"  required ({len(required)}): {[r['repository'] for r in required]}")
    print(f"  maybe    ({len(maybe)}): {[r['repository'] for r in maybe]}")
    print(f"  excluded ({len(excluded)}): {[r['repository'] for r in excluded]}")
    final_repos = confirmation.get("final_repos", [])
    print(f"  final_repos: {final_repos}")

    if not required and not maybe:
        print("\n[ERROR] No repositories confirmed as required or maybe.")
        print("        Check LLM output or requirement quality.")
        sys.exit(1)

    # ---------------------------------------------------------------
    # Step 3: Integration
    # ---------------------------------------------------------------
    integration = _post(
        f"{base_url}/integration",
        {
            "requirement": requirement,
            "confirmation": confirmation,
        },
        "Step 3: Integration",
    )
    _summarize("Integration", integration, [
        "engineering_spec", "contracts", "execution_batches",
    ])
    print(f"  task_dag count: {len(integration.get('task_dag', []))}")
    for t in integration.get("task_dag", []):
        print(f"    - {t['repository']}: depends_on={t.get('depends_on', [])}")

    # ---------------------------------------------------------------
    # Step 4: Materialize
    # ---------------------------------------------------------------
    if skip_materialize:
        print("\n[SKIP] Materialize skipped (no Matrix / testing only).")
        _print_dag_summary(integration)
        return

    # ---------------------------------------------------------------
    # Step 3.5: Ensure project topology exists (only when materializing)
    # ---------------------------------------------------------------
    actual_leader_id = _ensure_topology(
        project_id, leader_agent_id, integration.get("task_dag", [])
    )

    try:
        materialize = _post(
            f"{base_url}/bridge/materialize",
            {
                "engineering_spec": integration["engineering_spec"],
                "contracts": integration["contracts"],
                "task_dag": integration["task_dag"],
                "execution_batches": integration["execution_batches"],
                "requirement": requirement,
                "project_id": project_id,
                "leader_agent_id": actual_leader_id or leader_agent_id,
                "idempotency_prefix": idempotency_prefix,
            },
            "Step 4: Materialize",
        )
        _summarize("Materialize", materialize, [
            "engineering_spec_id", "contract_spec_ids",
            "task_ids", "plan_id",
        ])
        print(f"\n{'='*60}")
        print("Pipeline COMPLETE!")
        print(f"  Spec:  {materialize['engineering_spec_id']}")
        print(f"  Tasks: {len(materialize['task_ids'])} created")
        print(f"  Plan:  {materialize.get('plan_id', 'N/A')}")

    except requests.HTTPError as e:
        if e.response.status_code == 503:
            print("\n[503] Execution plane unavailable (Matrix not configured).")
            print("      Spec and Task creation skipped.")
            print("      The plan is valid — see DAG summary below.")
            _print_dag_summary(integration)
        else:
            raise


def _print_dag_summary(integration: dict) -> None:
    """Print the DAG batches for visual inspection."""
    batches = integration.get("execution_batches", [])
    print("\n--- DAG Execution Batches ---")
    for i, batch in enumerate(batches, 1):
        print(f"  Batch {i}: {batch}")


def _ask_continue(*, auto_yes: bool = False) -> bool:
    """Ask user whether to continue despite insufficient requirement."""
    if auto_yes:
        print("\n  [auto-yes] Continuing anyway (--yes flag).")
        return True
    try:
        answer = input("\n  Continue anyway? [y/N] ").strip().lower()
        return answer == "y"
    except (EOFError, KeyboardInterrupt):
        return False


def _ensure_topology(
    project_id: str,
    leader_agent_id: str,
    task_dag: list[dict],
) -> str | None:
    """Bootstrap a project: register agents, create topology, create Matrix rooms.

    Full flow:
    1. RegisterNativeAgent → ensure manager/worker in controller + principal in directory
    2. Create ProjectAgentTopology
    3. ReconcileProjectAgentTopology → create AgentTeams teams (Matrix rooms), write back room_id
    """
    import asyncio

    result: str | None = None

    async def _create() -> None:
        nonlocal result
        import sys as _sys

        _sys.path.insert(0, "src")
        _sys.dont_write_bytecode = True

        from repomesh.bootstrap.app import build_default_container
        from repomesh.integrations.agentteams.principal_registration import (
            RegisterNativeAgentRequest,
        )
        from repomesh.integrations.agentteams.project_topology import (
            ReconcileProjectAgentTopology,
        )
        from repomesh.modules.agent_directory.application import CreateAgentRequest
        from repomesh.modules.agent_directory.contracts import AgentRole
        from repomesh.modules.agent_runtime.ports.agent_team import (
            DesiredRuntimeState,
            ManagerProjection,
            WorkerProjection,
        )
        from repomesh.modules.project.domain import (
            ProjectAgentTopology,
            RepositoryTeam,
        )
        from repomesh.settings import get_settings
        from repomesh.shared.domain import new_id

        container = build_default_container()
        store = container.project_topology_store
        catalog = container.repository_catalog

        # Check if topology already exists
        existing = await store.get(uuid.UUID(project_id))
        if existing is not None:
            print(f"\n[Topology] Already exists for project {project_id}")
            result = str(existing.organization_leader_id)
            return

        control_plane = container.agent_team_control_plane
        if control_plane is None:
            print("\n[Topology] ERROR: AgentTeams control plane not configured.")
            return

        # The container's registrar carries the worker task-control MCP URL;
        # constructing RegisterNativeAgent bare is how every bootstrap-created
        # worker lost its ``mcpServers`` projection and could never accept a
        # dispatched task.
        registrar = container.native_agent_registration()

        # Get repository IDs from catalog
        all_profiles = await catalog.list()
        name_to_id = {p.name: p.id for p in all_profiles}

        # Include all repos mentioned in task_dag
        repo_names = {t.get("repository", "") for t in task_dag}
        if not repo_names:
            repo_names = set(name_to_id.keys())

        org_id = new_id()
        # The same source the console's ProjectRuntimeProjection reads. These
        # two paths must ask for field-identical resources (contract §8.7) and
        # `runtime` used to be OPENCLAW written out in both; one setting means
        # there is no second value left to drift.
        runtimes = get_settings()
        # ... and the same model the projection registers with: a hardcoded
        # one here produced workers the gateway refuses to serve.
        model = runtimes.deepseek_model
        teams = []

        # --- Register Org Leader (Manager) ---
        org_leader_name = f"agt-org-{org_id.hex}"
        print(f"\n[Bootstrap] Registering org leader as Manager: {org_leader_name}")
        manager_proj = ManagerProjection(
            name=org_leader_name,
            model=model,
            runtime=runtimes.agentteams_manager_runtime,
            # Field-for-field parity with the console's projection (contract
            # §8.7): the controller's image fallback is role-blind, so a
            # manager asked for without an image boots on the worker image
            # and exits before its first Matrix sync.
            image=runtimes.agentteams_manager_image,
            skills=("planning", "coordination"),
        )
        org_result = await registrar.execute(
            RegisterNativeAgentRequest(
                principal=CreateAgentRequest(
                    organization_id=org_id,
                    role=AgentRole.ORGANIZATION_LEADER,
                    leader_agent_id=None,
                    repository_id=None,
                    responsibility_paths=("*",),
                    agentteams_resource_name=org_leader_name,
                ),
                manager=manager_proj,
            ),
            idempotency_key=f"bootstrap-org-{org_id.hex}",
        )
        actual_org_leader_id = org_result.principal.id
        print(f"  → org leader agent: {actual_org_leader_id}")

        # --- Register repo leaders + workers ---
        for repo_name in sorted(repo_names):
            if repo_name not in name_to_id:
                continue
            repo_id = name_to_id[repo_name]
            short = repo_id.hex[:12]

            # Repo leader (Worker, reports to org leader)
            repo_leader_name = f"agt-leader-{short}"
            print(f"[Bootstrap] Registering repo leader: {repo_leader_name} ({repo_name})")
            leader_result = await registrar.execute(
                RegisterNativeAgentRequest(
                    principal=CreateAgentRequest(
                        organization_id=org_id,
                        role=AgentRole.REPOSITORY_LEADER,
                        leader_agent_id=actual_org_leader_id,
                        repository_id=repo_id,
                        responsibility_paths=("*",),
                        agentteams_resource_name=repo_leader_name,
                    ),
                    worker=WorkerProjection(
                        name=repo_leader_name,
                        model=model,
                        runtime=runtimes.agentteams_worker_runtime,
                        skills=("code-review", "planning"),
                        state=DesiredRuntimeState.RUNNING,
                    ),
                ),
                idempotency_key=f"bootstrap-leader-{short}",
            )
            actual_repo_leader_id = leader_result.principal.id

            # Worker (reports to repo leader)
            worker_name = f"agt-worker-{short}"
            print(f"[Bootstrap] Registering worker: {worker_name} ({repo_name})")
            worker_result = await registrar.execute(
                RegisterNativeAgentRequest(
                    principal=CreateAgentRequest(
                        organization_id=org_id,
                        role=AgentRole.WORKER,
                        leader_agent_id=actual_repo_leader_id,
                        repository_id=repo_id,
                        responsibility_paths=("*",),
                        agentteams_resource_name=worker_name,
                    ),
                    worker=WorkerProjection(
                        name=worker_name,
                        model=model,
                        runtime=runtimes.agentteams_worker_runtime,
                        skills=("coding",),
                        state=DesiredRuntimeState.RUNNING,
                    ),
                ),
                idempotency_key=f"bootstrap-worker-{short}",
            )
            actual_worker_id = worker_result.principal.id

            teams.append(RepositoryTeam(
                project_id=uuid.UUID(project_id),
                repository_id=repo_id,
                leader_agent_id=actual_repo_leader_id,
                worker_agent_ids=(actual_worker_id,),
            ))

        if not teams:
            print("\n[Topology] WARNING: No matching repos found in catalog.")
            return

        # --- Create topology ---
        topology = ProjectAgentTopology(
            organization_id=org_id,
            project_id=uuid.UUID(project_id),
            organization_leader_id=actual_org_leader_id,
            repository_teams=tuple(teams),
        )
        await store.add(
            topology,
            idempotency_key=f"topo-{project_id}",
            request_fingerprint=f"topo-{project_id}",
        )
        print(f"\n[Topology] Created for project {project_id} ({len(teams)} repos)")

        # --- Reconcile: create AgentTeams teams + Matrix rooms ---
        print("[Bootstrap] Creating AgentTeams teams and Matrix rooms...")
        reconciler = ReconcileProjectAgentTopology(
            container.agent_directory,
            store,
            control_plane,
        )
        view = await reconciler.execute(uuid.UUID(project_id))
        ready = sum(
            1 for t in view.repository_teams if t.runtime_status.value == "ready"
        )
        print(f"[Bootstrap] Teams reconciled: {ready}/{len(view.repository_teams)} ready")
        for t in view.repository_teams:
            print(
                f"  team {t.agentteams_team_name}: "
                f"room={t.room_id}, leader_room={t.leader_room_id}"
            )

        result = str(actual_org_leader_id)

    asyncio.run(_create())
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the full RepoMesh pipeline end-to-end."
    )
    req_group = parser.add_mutually_exclusive_group(required=True)
    req_group.add_argument(
        "--requirement", "-r",
        help="Requirement text (inline).",
    )
    req_group.add_argument(
        "--requirement-file", "-f",
        help="Path to a requirement document file.",
    )
    parser.add_argument(
        "--project-id", required=True,
        help="Project UUID.",
    )
    parser.add_argument(
        "--leader-agent-id", required=True,
        help="Leader agent UUID.",
    )
    parser.add_argument(
        "--base-url", default=DEFAULT_URL,
        help=f"API base URL (default: {DEFAULT_URL}).",
    )
    parser.add_argument(
        "--skip-analysis", action="store_true",
        help="Skip requirement sufficiency analysis.",
    )
    parser.add_argument(
        "--skip-materialize", action="store_true",
        help="Skip materialize (useful when Matrix is not configured).",
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Auto-confirm requirement analysis (skip interactive prompt).",
    )
    parser.add_argument(
        "--idempotency-prefix", default=None,
        help="Idempotency prefix for materialize (default: random).",
    )
    args = parser.parse_args()

    # Load requirement
    if args.requirement_file:
        with open(args.requirement_file, encoding="utf-8") as f:
            requirement = f.read()
        print(f"Loaded requirement from {args.requirement_file} ({len(requirement)} chars)")
    else:
        requirement = args.requirement

    run_pipeline(
        base_url=args.base_url,
        requirement=requirement,
        project_id=args.project_id,
        leader_agent_id=args.leader_agent_id,
        skip_analysis=args.skip_analysis,
        skip_materialize=args.skip_materialize,
        idempotency_prefix=args.idempotency_prefix,
        auto_yes=args.yes,
    )


if __name__ == "__main__":
    main()
