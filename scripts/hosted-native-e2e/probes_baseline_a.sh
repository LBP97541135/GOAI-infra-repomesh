#!/usr/bin/env bash
# Baseline probes that do not change state: scenes 01, 03, 05, 06, 11 (+ secret probe), 13, 21, 22.
source "$(dirname "$0")/probe_lib.sh"
TEAM=repomesh-team-dfb8a4cda6f74ee795e4197963151308
TEAM_TA=repomesh-team-22c4d38e465c42c8b93b20ae97cc250d
TASK_PC=b6e0bc59-255a-4edc-a186-47f5eeb3050c
TASK_TA=54250ad9-fda6-4e72-8c3e-b63174dfef2a
ROOM_PC='!3IU075BSWiAQORHR4e:matrix-local.agentteams.io:18080'
export TEAM TEAM_TA TASK_PC TASK_TA ROOM_PC
# key prefixes for the secret probe: computed into variables, never echoed
export GK="$(docker exec agentteams-worker-agt-worker-dfb8a4cda6f7 sh -c 'printf %s "$AGENTTEAMS_WORKER_GATEWAY_KEY" | cut -c1-8')"
export MK="$(sed -n 's/^REPOMESH_MODEL_API_KEY=//p' D:/Project4work/GOAI-infra-repomesh/.env | tail -1 | cut -c1-8)"

probe 01 'curl -sS -H "Authorization: Bearer $T" $API/setup/status | python -c "import json,sys; d=json.load(sys.stdin); print(\"administrator:\", d[\"checks\"][\"administrator\"]); print(\"checks:\", json.dumps(d[\"checks\"])); print(\"counts:\", d[\"counts\"])"'

probe 03 'curl -sS -H "Authorization: Bearer $T" $API/console/repositories | python -c "import json,sys; rows=json.load(sys.stdin)[\"repositories\"]; print(len(rows), \"repositories\"); [print(r[\"name\"], \"| test_commands:\", r[\"test_commands\"], \"| teams:\", [(t[\"runtime_status\"]) for t in r[\"teams\"]]) for r in rows]"'

probe 05 'docker exec agentteams-controller agt get workers -o json | python -c "import json,sys; d=json.load(sys.stdin); ws=d[\"workers\"]; print(\"total:\", d[\"total\"]); [print(w[\"name\"], \"|\", w.get(\"phase\"), \"|\", w.get(\"runtime\"), \"|\", w.get(\"team\")) for w in ws]; print(\"running:\", sum(1 for w in ws if w.get(\"phase\")==\"Running\"))"'

probe 06 'docker ps --filter name=agentteams-worker- --format "{{.Names}}\t{{.Status}}\t{{.Image}}" | sort; echo "count: $(docker ps -q --filter name=agentteams-worker- | wc -l | tr -d " ")"'

probe 11 'echo "## task package on the shared drive (baseline = v1 manifest, no base/)"; docker exec agentteams-controller mc ls agentteams/agentteams-storage/teams/$TEAM/shared/tasks/$TASK_PC/; docker exec agentteams-controller mc cat agentteams/agentteams-storage/teams/$TEAM/shared/tasks/$TASK_PC/manifest.json; echo "## team-room messages (m.room.message only, oldest first)"; curl -sS -H "Authorization: Bearer $MT" "$MX/rooms/$(python -c "import urllib.parse,os; print(urllib.parse.quote(os.environ[\"ROOM_PC\"], safe=\"\"))")/messages?dir=b&limit=200" | python -c "
import json,sys,datetime
d=json.load(sys.stdin)
msgs=[e for e in d[\"chunk\"] if e[\"type\"]==\"m.room.message\"]
print(\"message events:\", len(msgs))
for e in reversed(msgs):
    ts=datetime.datetime.utcfromtimestamp(e[\"origin_server_ts\"]/1000).strftime(\"%Y-%m-%dT%H:%M:%SZ\")
    body=e[\"content\"].get(\"body\",\"\")
    print(ts, e[\"sender\"], \"|\", body[:400].replace(\"\\n\",\" / \"))
    print(\"   contains start_assigned_task:\", \"start_assigned_task\" in body, \"| mentions tasks/<attempt>:\", \"tasks/\" in body)
"'

probe 11s 'echo "## secret probe: grep the 8-char prefixes of the worker gateway key and the model key across all task-dir text files (counts only)"; echo "positive control (must be 1):"; printf "x%sy\n" "$GK" | grep -c "$GK"; printf "x%sy\n" "$MK" | grep -c "$MK"; for t in $TEAM/shared/tasks/$TASK_PC $TEAM_TA/shared/tasks/$TASK_TA; do for f in spec.md meta.json manifest.json result.md; do n=$(docker exec agentteams-controller mc cat agentteams/agentteams-storage/teams/$t/$f 2>/dev/null | grep -c -e "$GK" -e "$MK"); echo "$t/$f gateway_or_model_key_hits=$n"; done; done'

probe 13 'echo "## pricing-core worker task dir (worker never replied on 09-02)"; docker exec agentteams-controller mc ls agentteams/agentteams-storage/teams/$TEAM/shared/tasks/$TASK_PC/; echo "## test-assets worker task dir (worker submitted BLOCKED on 09-02)"; docker exec agentteams-controller mc ls agentteams/agentteams-storage/teams/$TEAM_TA/shared/tasks/$TASK_TA/; echo "## result.md first line"; docker exec agentteams-controller mc cat agentteams/agentteams-storage/teams/$TEAM_TA/shared/tasks/$TASK_TA/result.md | head -1; echo "## meta.json status"; docker exec agentteams-controller mc cat agentteams/agentteams-storage/teams/$TEAM_TA/shared/tasks/$TASK_TA/meta.json | python -c "import json,sys; d=json.load(sys.stdin); print({k:d.get(k) for k in (\"status\",\"acknowledged_at\",\"submitted_at\")}); print(\"repomesh block present:\", \"repomesh\" in d)"; echo "## candidate/ present?"; docker exec agentteams-controller mc ls agentteams/agentteams-storage/teams/$TEAM/shared/tasks/$TASK_PC/candidate/ 2>&1 | head -2'

probe 21 '$PSQL -c "select count(*) as error_rows_mentioning_mcp_worker from observability.log_entries where message like '"'"'%mcp/worker%'"'"' and level='"'"'ERROR'"'"'"; $PSQL -c "select level, count(*) from observability.log_entries group by level order by 1"; $PSQL -c "select to_char(ts, '"'"'YYYY-MM-DD HH24:MI:SS'"'"'), left(message, 160) from observability.log_entries where level='"'"'ERROR'"'"' order by ts desc limit 5"'

probe 22 'curl -sS -H "Authorization: Bearer $T" $API/observe/alert-rules | python -c "import json,sys; rs=json.load(sys.stdin)[\"rules\"]; print(len(rs), \"rules\"); [print(r[\"name\"], r[\"metric\"], r[\"operator\"], r[\"threshold\"], \"window\", r[\"window_minutes\"], \"enabled\", r[\"enabled\"]) for r in rs]"; echo "## events (7d)"; curl -sS -H "Authorization: Bearer $T" "$API/observe/alerts?days=7"; echo; echo "## active"; curl -sS -H "Authorization: Bearer $T" $API/observe/alerts/active; echo; echo "## operations status"; curl -sS -H "Authorization: Bearer $T" $API/observe/operations/status | python -c "import json,sys; d=json.load(sys.stdin); print(\"intake_paused:\", d[\"intake_paused\"], \"writes_degraded:\", d[\"writes_degraded\"])"'
