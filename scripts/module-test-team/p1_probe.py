"""P-1 probe: run INSIDE the AgentTeams worker image, on agentteams-net.

Asks the questions the acceptance criterion asks, in order: is there a docker
CLI / compose / socket in the worker's world; failing that, what does the
controller's /docker/ passthrough allow — and specifically the calls compose
would have to make (network create, volume create, bind mounts, own naming).
Every step prints command-equivalent, status, and an excerpt — the minimal
evidence set shape.
"""
import json, os, shutil, sys, urllib.request, urllib.error

BASE = (os.environ.get("CTRL") or os.environ["AGENTTEAMS_CONTROLLER_URL"]) + "/docker"
TOKEN = os.environ.get("TOKEN") or os.environ["AGENTTEAMS_AUTH_TOKEN"]

def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + TOKEN,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode(errors="replace")
            return r.status, txt
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:  # noqa: BLE001
        return -1, repr(e)

def step(sid, desc, status, excerpt):
    print(f"[{sid}] {desc}\n     status={status} excerpt={excerpt[:160]!r}")

# --- T1/T2: local docker capability ---
step("T1", "which docker / docker compose", 0,
     f"docker={shutil.which('docker')} docker-compose={shutil.which('docker-compose')}")
step("T2", "/var/run/docker.sock present?", 0, str(os.path.exists("/var/run/docker.sock")))

# --- T3: proxy reachable ---
s, t = call("GET", "/version"); step("T3", "GET /docker/version", s, t)

# --- T4/T5: what compose needs first ---
s, t = call("POST", "/networks/create", {"Name": "itest-p1-net"}); step("T4", "POST /docker/networks/create", s, t)
s, t = call("POST", "/volumes/create", {"Name": "itest-p1-vol"}); step("T5", "POST /docker/volumes/create", s, t)

# --- T6: own naming convention ---
s, t = call("POST", "/containers/create?name=itest-p1-svc",
            {"Image": "alpine:latest", "Cmd": ["sleep", "30"]}); step("T6", "create name=itest-p1-svc", s, t)

# --- T7: bind mount (a worktree would need this) ---
s, t = call("POST", "/containers/create?name=agentteams-worker-itest-p1",
            {"Image": "alpine:latest", "Cmd": ["sleep", "30"],
             "HostConfig": {"Binds": ["/tmp:/work"]}}); step("T7", "create with Binds", s, t)

# --- T8: the degraded path: prefixed, no binds, on agentteams-net ---
s, t = call("POST", "/containers/create?name=agentteams-worker-itest-p1",
            {"Image": "alpine:latest", "Cmd": ["sleep", "30"],
             "HostConfig": {"NetworkMode": "agentteams-net"}}); step("T8a", "create prefixed/no-bind", s, t)
cid = None
try:
    cid = json.loads(t).get("Id")
except Exception:  # noqa: BLE001
    pass
if cid:
    s, t = call("POST", f"/containers/{cid}/start"); step("T8b", "start", s, t)
    s, t = call("GET", f"/containers/{cid}/json"); 
    try:
        st = json.loads(t)["State"]["Status"]
    except Exception:  # noqa: BLE001
        st = t
    step("T8c", "inspect state", s, str(st))
    s, t = call("POST", f"/containers/{cid}/kill"); step("T8d", "kill", s, t)
    s, t = call("DELETE", f"/containers/{cid}"); step("T8e", "delete (cleanup)", s, t)
print("probe done")
