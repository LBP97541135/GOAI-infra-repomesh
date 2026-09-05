# Helper CLI: `repomesh-work.sh`

The helper script ships inside every package at `base/tools/repomesh-work.sh` and versions with
the package, not with the worker image (spike S-8: the spec's command lines are all the worker
needs; no skill installation). It carries no credentials, never touches the network, and only
reads `base/package.json`, `base/base.bundle` and the workspace it creates.

Source of truth for the script: `src/repomesh/integrations/agentteams/task_package/repomesh-work.sh`.
The four command lines below are also `HELPER_COMMANDS` in that package's `__init__.py` and are
written into `base/package.json.helper_commands[]`.

## Command lines (verbatim)

These are the complete command lines, character for character, that the worker types from the
task directory. The observer's auto-approval (D-23) compares an intercepted Tool Guard command
against this list and approves only an exact match; `tests/contracts/test_agentteams_task_v2_contract.py`
asserts that this block, `helper_commands[]` and `HELPER_COMMANDS` are identical.

```text
bash base/tools/repomesh-work.sh init
bash base/tools/repomesh-work.sh test
bash base/tools/repomesh-work.sh bundle
bash base/tools/repomesh-work.sh clean
```

The script's name and the four lines contain no `rm`, `mv`, `sudo`, `su`, `del`, `kill` or
`curl … | sh` token (D-21): the wave-0 name `rm-work.sh` was stopped by the copaw rule
`TOOL_CMD_DANGEROUS_RM` on the `rm` in its own name, eight shell calls out of eight (S-1). Internal
`rm -f` / `rm -rf` inside the script body are fine — the Tool Guard sees only the command line the
worker types.

The worker's spec names `init`, `test` and `bundle` only. `clean` exists for the platform and the
operator and is not part of the worker's instructions.

## Inputs common to every command

- Current directory: irrelevant; the script locates the task directory from its own path
  (`base/tools/` → task root) and refuses to run when the directory name differs from
  `package.json.attempt_id`.
- `base/package.json`: `attempt_id`, `base_sha`, `test_commands[]`, `test_timeout_seconds`,
  `workspace_root`.
- Workspace: `<workspace_root>/<attempt_id>` (default `/work/<attempt_id>`). The environment
  variable `REPOMESH_WORK_ROOT` overrides `workspace_root`. Helper state (test results, a
  scratch index) lives in `<workspace_root>/.repomesh-work-state/<attempt_id>/`. Both are outside
  the copaw sync root, so the task directory never carries a checkout (D-5).
- Requirements: `bash`, `git`, `python3` on `PATH`.

## `init`

Creates the workspace from `base/base.bundle`: `git clone` of the bundle, `checkout -B work
<base_sha>`, a local committer identity (`RepoMesh Worker <worker@repomesh.invalid>`), and an
`info/exclude` for `__pycache__/`, `*.pyc`, `.pytest_cache/`, `node_modules/`. Idempotent: an
existing workspace is reported (HEAD and the number of changed paths) and left alone.

- Reads: `base/package.json`, `base/base.bundle`.
- Writes: the workspace; nothing in the task directory.
- Exit 0 on success (also when the workspace already existed); 2 when the bundle is missing or
  the resulting HEAD is not `base_sha`.

## `test`

Runs every `test_commands[]` entry in order, through the shell, inside the workspace, each with
`test_timeout_seconds`. Records `{command, exit_code, duration_seconds, excerpt}` (last 40 lines
of combined output) and the git tree hash of the whole working tree after the run.

- Reads: workspace, `base/package.json`.
- Writes: `<state>/test-results.json`; nothing in the task directory.
- Prints each command's excerpt and `[exit N]`; on success the last line is
  `repomesh-work: all tests passed (N command(s)); next: bash base/tools/repomesh-work.sh bundle`.
- Exit 0 when every command exited 0; 1 when any failed (a timeout is recorded as exit 124);
  2 when the workspace does not exist.

## `bundle`

Turns the workspace into a candidate. Refuses when the working tree changed since the last `test`
(tree hash mismatch) — the evidence must describe the exact tree being bundled. Stages
everything, commits, squashes to exactly one commit on top of `base_sha`, then writes the four
deliverables into the task directory:

| File | Content |
| --- | --- |
| `candidate/candidate.bundle` | `git bundle create … <base_sha>..refs/heads/work` |
| `candidate/candidate.diff` | `git diff <base_sha> HEAD` |
| `candidate/changes.json` | `candidate.schema.json#/$defs/changes` |
| `candidate/evidence.json` | `candidate.schema.json#/$defs/evidence` |

A red last test run does not stop `bundle`; `evidence.json` records the red exit codes and the
Leader and verifier see them. The worker is told to submit `SUCCESS` only on green.

- Reads: workspace, `<state>/test-results.json`, `base/package.json`.
- Writes: `candidate/` (replaced wholesale); a commit in the workspace.
- Prints the candidate head, the changed files and the four deliverable paths in the exact form
  `submit_task` expects (`shared/tasks/<attempt_id>/candidate/<file>`).
- Exit 0 on success; 2 when there are no test results, the tree changed after `test`, there are
  no changes relative to `base_sha`, or the candidate's parent is not `base_sha`.

## `clean`

Deletes the workspace and the helper state for this attempt. Never touches the task directory.
Exit 0.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | done |
| 1 | `test`: at least one frozen command failed |
| 2 | precondition failed (message on stderr, prefixed `repomesh-work: error:`) |
| 64 | usage: unknown or missing sub-command |

## Tool Guard rule-set fixture

copaw's Tool Guard evaluates every `execute_shell_command` against a regex rule set before the
worker may run it, and asks the room for `/approve` on a HIGH or CRITICAL hit. The rule set does
not exist in the vendored source under `components/agentteams/copaw` (which only carries the
`ToolGuardConfig` model); it ships inside the copaw runtime package of the worker image at
`copaw/security/tool_guard/rules/dangerous_shell_commands.yaml` and is matched by
`copaw/security/tool_guard/guardians/rule_guardian.py` (`re.IGNORECASE`, `re.search`, exclude
patterns first).

`tests/contracts/fixtures/copaw_tool_guard_rules.json` is that YAML exported from a live worker
container (image, digest, copaw version, source file digest and the export command are in the
fixture's `_provenance`). `test_helper_command_lines_pass_the_copaw_tool_guard_rules` runs the
four command lines through every rule with the same semantics and fails on any hit; the control
assertion shows the old `bash base/tools/rm-work.sh init` hitting `TOOL_CMD_DANGEROUS_RM`.
Re-export the fixture when the worker image changes.
