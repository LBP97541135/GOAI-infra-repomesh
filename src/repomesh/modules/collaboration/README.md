# Collaboration

Owns structured task notifications, reports, questions, answers, progress updates and decisions.
Every outbound message is authorized against the project topology before it is routed:

- Organization Leader <-> Repository Leader uses the Team `leaderDMRoomID`.
- Repository Leader <-> Worker uses the Team `teamRoomID`.
- Worker-to-Worker and Organization-Leader-to-Worker messages are denied.

Messages use the `repomesh.collaboration.v1` JSON envelope and a stable Matrix transaction id.
Delivery state and Matrix event ids are persisted separately from task state. A failed delivery can
therefore be retried without creating another task or Matrix event. Chat remains a transport and is
never treated as the source of truth for project progress.
