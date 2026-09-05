# hosted-native-e2e helper scripts (2026-09-03)

Driver scripts used for the baseline acceptance run and the wave-0 spike; copied verbatim from the session scratchpad.
Records: `docs/startup-records/2026-09-03-hosted-native-e2e-baseline.md`, `docs/startup-records/2026-09-03-hosted-native-spike.md`.

- `probe_lib.sh` + `probes_baseline_{a,b,c}.sh`: one probe per acceptance-script scene, transcripts to `output/hosted-native-e2e/<date>/NN.txt`; `ONLY=NN` runs a single scene. Tokens are read from `.secrets` and masked on disk.
- `baseline_issue.py`: scenes 07/08 (create the issue, run the four discovery steps, no materialize).
- `render_probe_html.py` + `shoot_terminal.sh`: render transcripts as terminal-style pages and screenshot them with headless Chrome (1440x900 dark).
- `spike/build_package.py` (+ `config.json`, templates, `rm-work.sh`, `base.bundle`): assemble a task package v2 (construction or review) for the pricing-core team.
- `spike/send_room.py`, `spike/approve.sh`: post into the team/leader room as @admin; `/approve` must be sent with `--bare` (body exactly `/approve`, mention only in `m.mentions`) and `MSYS_NO_PATHCONV=1`.
- `spike/auto_approve.py`: stand-in for platform auto-approval of the helper commands (copaw Tool Guard).
- `spike/watch.py`, `spike/restart_drill.sh`: observers for rooms / shared drive / worker container, and the mid-construction `docker restart` drill.

Paths inside the scripts are absolute to `D:/Project4work/GOAI-infra-repomesh`; adjust before reuse elsewhere.
