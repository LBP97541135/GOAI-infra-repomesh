# The console, surface by surface

Six drawings, one per entry in the console's left rail. Every colour is a token
from `frontend/src/index.css`, every row is a real record from a seeded local
stack, and the phase colours come from the single skin table in
`frontend/src/display.ts`. The console itself ships in Chinese; the labels here
are translated so this page reads alongside the English README.

## Issues

The flat list. Structure is identical from row to row and only the badges
change — that is deliberate, and it is the one design rule the whole console is
built on: a sense of progress lives in the badge, never in the layout.

![The issue list: a navigation rail and a flat list of issues, each row carrying its
delivery phase as a badge rather than as a separate column](assets/console.svg)

## Human review

A queue that crosses issues, because the only question it answers is "how many
things are waiting on me today". Splitting it per issue would destroy the
queue. The evidence version is printed because the decision is pinned to it: if
an agent updates the evidence after you decide, the decision no longer applies
and the gate reopens.

![The human review queue: one card per checkpoint awaiting a decision, carrying its
status, its checkpoint and the evidence version the decision is pinned to](assets/console-reviews.svg)

## Repositories

One card per repository in the catalog, with a chip for every team stationed on
it. The chip carries the issue and the build result and nothing else — the
issue *title* is not in this endpoint's response, and joining a paginated
endpoint to fetch it would put partial results on screen dressed as the whole.

![The repository grid: resident team count, open issues and active tasks per
repository, with a chip per team stationed on it](assets/console-repositories.svg)

## Teams

Two badges that look like they should be one, and must not be. **Team ready**
is the persisted topology — the historical fact that this team was built.
**Runtime** is what the AgentTeams controller observes right now. Merge them
and a quiet controller prints "team failed" for a team that was built and whose
rooms still exist.

![The teams page: the persisted build result and the live runtime observation kept
in two separate badges, with the leader, the workers and both Matrix rooms](assets/console-teams.svg)

## Agents

The roster. The uptime column says "not wired up" on every row because the
controller's response carries no timestamp at all — filling it with a number
would be an invention, and deleting the column would make the gap disappear
instead of fixing it. "Status" is the enabled state held in `agent_directory`,
not an observed awake/asleep state; there is no data source for the latter.

![The agent roster: role, enabled state, active task count, the repository and issue
each agent belongs to, and the runtime the controller reports](assets/console-agents.svg)

## Observe

The portal, not a data page: three health numbers over four section cards, each
carrying its own live count. The sections map one for one onto the
observability surface — reasoning traces, usage metrics, logs, alerts.

![The observability centre: three health numbers over four section cards —
reasoning traces, usage, logs and alerts — each with its own live count](assets/console-observe.svg)
