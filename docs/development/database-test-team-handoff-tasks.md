# Database Test Team Handoff Tasks

| ID | Status | Task |
|---|---|---|
| DTH-1 | complete | Freeze handoff authority, plan shape, failure semantics, and permissions |
| DTH-2 | complete | Implement immutable handoff plan and SHA-fenced planner |
| DTH-3 | complete | Dispatch plan to the cross-repository Test Team when its project topology is ready |
| DTH-4 | complete | Accept Test Team plan and evidence with Task/SHA fencing |
| DTH-5 | complete | Start Branch validation automatically after Test Team evidence is complete |
| DTH-6 | partial | Handoff status API is available; delivery-console visual surface remains pending |
| DTH-7 | blocked-external | Run the handoff through a live Polar Branch provider |

Current local verification: handoff planner and evidence fencing tests pass; DTH-3 through DTH-6
remain the next implementation increments. The existing business Worker database automation is
still retained as a fail-closed fallback until the Test Team dispatch path is live.
