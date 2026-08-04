# Create a Remote Member (containerManaged: false)

A remote member is a Worker whose process runs on the **operator's own
machine** — driven by a local coding CLI such as Codex CLI or Claude Code —
while the controller provisions everything else: Matrix identity, personal
room, scoped storage credentials. There is no container, and there must never
be one.

You can complete the cluster half of this flow. The laptop half belongs to the
operator, and no amount of orchestration moves it to you: the bridge process
runs under their OS login, using their CLI subscription. Your job ends with a
correct handout.

## When this document applies

Admin asks for a member that runs "on my machine / laptop", "via Codex",
"via Claude Code", "as a remote member", or explicitly `containerManaged:
false`. Do **not** offer the four container runtimes — that question is
meaningless here and asking it misleads. The CLI choice replaces it (Step 1).

Do not confuse this with the `create-worker.md` table row for "local worker /
run on my machine" — that row provisions a **container** with access to a
mounted directory. A remote member has no container at all. When the admin's
wording is ambiguous between the two, ask one question: "should this run inside
an AgentTeams-managed container, or as your own CLI process on your machine?"

## Step 1: Confirm with admin, in one turn

1. **Name** — same rules as any Worker (`^[a-z0-9][a-z0-9-]*$`, > 3 chars).
2. **CLI** — `codex-cli` or `claude-code`. This is recorded in the bootstrap
   handout, not in the Worker CR; the cluster does not know or care.
3. **Team** — which existing team to join, if any.
4. **Role** — one line for `spec.identity`.

Skills, SOUL, and model need no confirmation: prompts and skills reach a
remote member through asset projection from the TeamHarness package, and the
model is whatever the operator's CLI subscription provides.

## Step 2: Create the Worker CR

`agt create worker` cannot express `containerManaged: false` — it has no such
flag. Use a manifest with `agt apply -f`:

```yaml
apiVersion: agentteams.io/v1beta1
kind: Worker
metadata:
  name: <NAME>
spec:
  model: <ANY_VALID_MODEL>    # not consumed for remote members; required by validation
  runtime: openclaw           # same: validation only, nothing runs it
  containerManaged: false     # the one line that matters
  identity: <ROLE, one line, ending with "runs on the operator's machine via <CLI>">
```

Write the file, then **verify it is non-empty before applying** (the same
0-byte trap the SOUL warning in `create-worker.md` exists for):

```bash
wc -c /tmp/<NAME>.yaml        # must be > 0
agt apply -f /tmp/<NAME>.yaml
```

**Do not add this Worker to `~/pending-workers.json`.** The drain loop polls
for `phase=Running`, and a remote member's phase stays `Pending` forever — the
phase field tracks a container that does not exist. Queuing it would leave a
permanently undrainable entry.

## Step 3: Join the team

Team membership lives on the **Team** CR, not the Worker — applying the Worker
manifest joins nothing. Fetch the current roster and append; `--workers`
replaces the whole list:

```bash
agt get team <TEAM> -o json     # read spec.workerMembers / workerNames
agt update team --name <TEAM> --workers <existing...,NAME>
```

Verify: `agt get team <TEAM> -o json` shows the member, and the member's
Matrix user has a pending invite to the team room (the bridge auto-accepts it
from `@admin`/`@manager` when it starts).

## Step 4: Hand out the bootstrap

Post this in the room, filled in from `agt get team` / `agt get workers`
output. Everything in it is a routing fact already visible to every room
member; the bootstrap loader **structurally refuses** secret-bearing keys
(`matrixToken`, `apiKey`, `accessKey`, ...), so there is no way to get this
step wrong by including too much:

```yaml
apiVersion: agentteams.io/v1beta1
kind: MemberRuntimeConfig
team:
  name: <TEAM>
  teamRoomId: "<teamRoomID from agt get teams>"
  leaderName: <LEADER>
member:
  name: <NAME>
  matrixUserId: "<matrixUserID from agt get workers>"
  personalRoomId: "<roomID from agt get workers>"
local:
  runtime: <codex-cli | claude-code>
  workspace: <OPERATOR FILLS IN: absolute path on their machine>
  # driverArgs grant the agent authority on the operator's machine and are
  # the operator's call, never yours. Include these lines commented out:
  # driverArgs:                                  # codex-cli
  #   - --sandbox
  #   - workspace-write
  #   - -c
  #   - mcp_servers.teamharness.default_tools_approval_mode="approve"
  # driverArgs:                                  # claude-code
  #   - --permission-mode
  #   - acceptEdits
  #   - --allowedTools
  #   - mcp__teamharness
```

Then tell the operator, in the same message:

1. **Credentials are fetched by you, not sent by me** — from the host:
   `docker exec agentteams-controller cat /data/worker-creds/<NAME>.env`
   (embedded mode). Export `AGENTTEAMS_WORKER_MATRIX_TOKEN` and the storage
   variables into the shell that will run the bridge. Never paste them into
   any room, including this one.
2. **Start**: `python -m bridge.supervisor --bootstrap ./bootstrap.yaml` from
   the TeamHarness `remote/` directory.
3. **If the CLI is not signed in**, the bridge waits and says exactly what to
   run (`codex login` / `claude`); signing in in another terminal is picked up
   without a restart.

You must never read, fetch, or relay the contents of `/data/worker-creds/` —
the handout carries the *path*, the operator carries the values.

## Step 5: Verify — and what "running" means here

`phase` will not tell you anything; it stays `Pending` by design. The only
truthful check is behavioral: after the operator confirms the bridge started,
@mention the member in the team room and expect a threaded reply.

## Orchestration afterwards

Task flow is identical to any worker — delegation through the Team Leader,
`taskflow` ack/submit, `TASK_COMPLETED` reports. Two differences matter:

- `find-worker.sh` reports these members with `container_status: "remote"`.
  **Never** run `lifecycle-worker.sh` (any action) against one — there is no
  container to ensure, stop, or recreate, and ensure-ready would try to
  create one.
- `availability: idle` means only "no tracked tasks". Whether the bridge is
  actually up is knowable only by the ack deadline: a remote member that does
  not ack within the usual window is offline (operator's machine asleep,
  bridge stopped). Notify the admin and reassign; do not attempt repair.
