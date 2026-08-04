#!/usr/bin/env ruby
# frozen_string_literal: true

require "json"
require "open3"
require "pathname"
require "rbconfig"
require "tmpdir"

repo_root = Pathname.new(__dir__).join("../../../../..").expand_path
mcp_dir = repo_root / "plugins/teamharness/mcp"
windows = RbConfig::CONFIG["host_os"] =~ /mswin|mingw|cygwin/ ? true : false

def fail!(message)
  warn "ERROR: #{message}"
  exit 1
end

# "python3" is not a usable interpreter everywhere: on Windows it commonly
# resolves to the App Execution Alias stub, which exits non-zero and prints
# nothing at all. Resolve it up front -- the mc stub launcher needs the answer
# too, not just the test run.
python = %w[python3 python].find do |candidate|
  out, _err, status = Open3.capture3(candidate, "-c", "print('ok')")
  status.success? && out.include?("ok")
rescue Errno::ENOENT
  false
end
fail!("no usable python interpreter (tried python3, python)") unless python

Dir.mktmpdir("teamharness-filesync-") do |dir|
  root = Pathname.new(dir)
  workspace = root / "workspace"
  bin_dir = root / "bin"
  log_path = root / "mc.log"
  bin_dir.mkpath

  # Forward-slash forms for embedding in assertions. The server emits native
  # separators, so every comparison normalizes to this shape rather than
  # assuming POSIX -- see `norm` below.
  ws = workspace.to_s.tr("\\", "/")
  log_posix = log_path.to_s.tr("\\", "/")

  # A stub cannot shadow `mc` on Windows, and the reason is structural rather
  # than fixable here: `filesync` spawns `["mc", ...]` with shell=False, which
  # goes through CreateProcess, and CreateProcess appends only `.exe` to an
  # extension-less name. PATHEXT -- and therefore `.cmd`/`.bat` -- is a cmd.exe
  # feature. (`shutil.which("mc")` *does* find a `.cmd`, which makes this
  # especially easy to get wrong: the resolution the test can observe is not
  # the resolution the tool performs.) An extension-less shell script fares no
  # better, since it is not a valid image.
  #
  # Left unguarded the consequence is worse than a failure: the mc-dependent
  # assertions silently run against whatever real `mc` the operator has
  # installed. So on Windows those cases are skipped by name, and the rest --
  # path normalization, the rejection rules, prefix resolution, and the
  # unconfigured-storage refusal, none of which reach mc -- still run.
  stub_usable = !windows

  # The mc stand-in is Python rather than bash so that one definition serves
  # every platform that can use it at all.
  stub_path = bin_dir / "mc_stub.py"
  stub_path.write(<<~PY)
    import os
    import sys

    LOG = "#{log_posix}"
    args = sys.argv[1:]
    joined = " ".join(args)

    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(joined + "\\n")
        handle.write("ENV MC_HOST_agentteams=%s\\n" % os.environ.get("MC_HOST_agentteams", ""))

    if "agentteams/agentteams-storage" in joined and not os.environ.get("MC_HOST_agentteams"):
        sys.stderr.write("missing MC_HOST_agentteams\\n")
        raise SystemExit(3)

    if "tasks/denied" in joined:
        sys.stderr.write("mc.bin: <ERROR> Unable to list comparison retrying.. Access Denied.\\n")
        raise SystemExit(0)

    if args[:1] == ["ls"]:
        sys.stdout.write("2026-06-03 12:00:00      42 projects/demo/plan.md\\n")
  PY

  if stub_usable
    launcher = bin_dir / "mc"
    launcher.write(<<~SH)
      #!/usr/bin/env sh
      exec "#{python}" "#{stub_path}" "$@"
    SH
    launcher.chmod(0o755)
  end

  python_test = <<~PY
    import json
    import os
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path("#{mcp_dir}")))
    from server import call_tool

    # False where a fake `mc` cannot shadow the real one -- see the Ruby side.
    # Everything guarded by it actually spawns mc; everything else does not.
    STUB = #{stub_usable ? "True" : "False"}

    def norm(value):
        """Normalize a path-bearing value for comparison.

        The server builds local paths with the platform separator, so asserting
        against a POSIX-shaped literal compares the operating system rather than
        the behaviour under test. Remote object keys never contain a backslash,
        so this is a no-op for them.
        """
        return str(value).replace("\\\\", "/")

    def norm_all(values):
        return [norm(item) for item in values or []]

    common = {
        "workspaceDir": "#{ws}",
        "storage": {
            "sharedPrefix": "mock/shared",
            "globalSharedPrefix": "mock/global-shared",
        },
    }

    def payload(args):
        merged = dict(common)
        merged.update(args)
        result = call_tool("filesync", merged)
        return json.loads(result["content"][0]["text"])

    def payload_without_storage(args):
        result = call_tool("filesync", args)
        return json.loads(result["content"][0]["text"])

    dry = payload({
        "action": "pull",
        "path": "shared/projects/demo",
        "dryRun": True,
    })
    if not dry.get("ok"):
        raise AssertionError(f"dry-run pull failed: {dry!r}")
    if dry.get("path") != "shared/projects/demo/":
        raise AssertionError(f"path was not normalized: {dry!r}")
    if dry.get("kind") != "shared":
        raise AssertionError(f"kind mismatch: {dry!r}")
    if norm(dry.get("localPath")) != "#{ws}/shared/projects/demo":
        raise AssertionError(f"local path mismatch: {dry!r}")
    if dry.get("remotePath") != "mock/shared/projects/demo/":
        raise AssertionError(f"remote path mismatch: {dry!r}")
    if norm_all(dry.get("command")) != ["mc", "mirror", "mock/shared/projects/demo/", "#{ws}/shared/projects/demo", "--overwrite"]:
        raise AssertionError(f"dry-run command mismatch: {dry!r}")

    board_dry = payload({
        "action": "pull",
        "path": "shared/board/aone-feed",
        "dryRun": True,
    })
    if not board_dry.get("ok"):
        raise AssertionError(f"shared board dry-run pull failed: {board_dry!r}")
    if board_dry.get("path") != "shared/board/aone-feed/":
        raise AssertionError(f"shared board path was not normalized: {board_dry!r}")
    if norm(board_dry.get("localPath")) != "#{ws}/shared/board/aone-feed":
        raise AssertionError(f"shared board local path mismatch: {board_dry!r}")
    if board_dry.get("remotePath") != "mock/shared/board/aone-feed/":
        raise AssertionError(f"shared board remote path mismatch: {board_dry!r}")

    blocked_global_push = payload({
        "action": "push",
        "path": "global-shared/readme.md",
        "dryRun": True,
    })
    if blocked_global_push.get("ok") is not False or "read-only" not in blocked_global_push.get("error", ""):
        raise AssertionError(f"global-shared push was not rejected: {blocked_global_push!r}")

    blocked_escape = payload({
        "action": "pull",
        "path": "../shared/tasks/t-001",
        "dryRun": True,
    })
    if blocked_escape.get("ok") is not False or "relative shared path" not in blocked_escape.get("error", ""):
        raise AssertionError(f"workspace escape was not rejected: {blocked_escape!r}")

    # A dry run exercises the same argv construction without spawning mc, so
    # the command-shape assertions stay available everywhere.
    pushed_file_dry = payload({
        "action": "push",
        "path": "shared/tasks/t-001/result.md",
        "dryRun": True,
    })
    if norm_all(pushed_file_dry.get("command")) != ["mc", "cp", "#{ws}/shared/tasks/t-001/result.md", "mock/shared/tasks/t-001/result.md"]:
        raise AssertionError(f"single-file push command mismatch: {pushed_file_dry!r}")

    pulled_file_dry = payload({
        "action": "pull",
        "path": "shared/tasks/t-001/result.md",
        "dryRun": True,
    })
    if norm_all(pulled_file_dry.get("command")) != ["mc", "cp", "mock/shared/tasks/t-001/result.md", "#{ws}/shared/tasks/t-001/result.md"]:
        raise AssertionError(f"single-file pull command mismatch: {pulled_file_dry!r}")

    pushed = pushed_file = pulled_file = listed = stat = None
    if STUB:
        result_path = pathlib.Path("#{ws}") / "shared/tasks/t-001/result.md"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text("# Result\\n", encoding="utf-8")
        pushed = payload({
            "action": "push",
            "path": "shared/tasks/t-001",
            "exclude": ["*.tmp"],
        })
        if not pushed.get("ok") or pushed.get("path") != "shared/tasks/t-001/":
            raise AssertionError(f"push failed: {pushed!r}")
        if pushed.get("exclude") != ["*.tmp"]:
            raise AssertionError(f"push exclude mismatch: {pushed!r}")

        pushed_file = payload({
            "action": "push",
            "path": "shared/tasks/t-001/result.md",
        })
        if not pushed_file.get("ok"):
            raise AssertionError(f"single-file push failed: {pushed_file!r}")

        pulled_file = payload({
            "action": "pull",
            "path": "shared/tasks/t-001/result.md",
        })
        if not pulled_file.get("ok"):
            raise AssertionError(f"single-file pull failed: {pulled_file!r}")

        denied_dir = pathlib.Path("#{ws}") / "shared/tasks/denied"
        denied_dir.mkdir(parents=True, exist_ok=True)
        (denied_dir / "result.md").write_text("# Denied\\n", encoding="utf-8")
        denied = payload({
            "action": "push",
            "path": "shared/tasks/denied",
        })
        if denied.get("ok") is not False or "Access Denied" not in denied.get("error", ""):
            raise AssertionError(f"zero-exit mc error was not detected: {denied!r}")

        listed = payload({
            "action": "list",
            "path": "shared/projects/demo",
        })
        if not listed.get("ok") or listed.get("entries") != ["2026-06-03 12:00:00      42 projects/demo/plan.md"]:
            raise AssertionError(f"list failed: {listed!r}")

        stat = payload({
            "action": "stat",
            "path": "shared/tasks/t-001/result.md",
        })
        if not stat.get("ok") or stat.get("exists") is not True:
            raise AssertionError(f"stat failed: {stat!r}")
        if stat.get("remotePath") != "mock/shared/tasks/t-001/result.md":
            raise AssertionError(f"stat remote path mismatch: {stat!r}")

    # With no storage configured anywhere, a push must refuse rather than
    # succeed locally. The default used to fall back to a bare "shared", which
    # looks like an object-storage path but is a relative one: mc copied the
    # file next to itself, exited 0, and filesync answered {"ok": true} for a
    # deliverable that never left the machine. The credential guard could not
    # catch it either -- it only demands keys once the remote is alias-
    # qualified, so losing the alias skipped the check for the missing alias.
    os.environ.pop("AGENTTEAMS_STORAGE_PREFIX", None)
    os.environ.pop("AGENTTEAMS_SHARED_STORAGE_PREFIX", None)
    os.environ.pop("TEAMHARNESS_RUNTIME_CONFIG", None)
    unconfigured_file = pathlib.Path("#{ws}") / "shared/tasks/unconfigured/result.md"
    unconfigured_file.parent.mkdir(parents=True, exist_ok=True)
    unconfigured_file.write_text("# Unconfigured\\n", encoding="utf-8")
    unconfigured = payload_without_storage({
        "workspaceDir": "#{ws}",
        "action": "push",
        "path": "shared/tasks/unconfigured/result.md",
    })
    if unconfigured.get("ok") is not False:
        raise AssertionError(f"unconfigured storage did not refuse: {unconfigured!r}")
    if "not configured" not in unconfigured.get("error", ""):
        raise AssertionError(f"unconfigured storage error is unclear: {unconfigured!r}")
    if "shared/tasks/unconfigured" in str(unconfigured.get("command") or ""):
        raise AssertionError(f"unconfigured storage still built a command: {unconfigured!r}")

    runtime_config = pathlib.Path("#{root.to_s.tr("\\", "/")}") / "runtime.yaml"
    runtime_config.write_text(
        "storage:\\n"
        "  sharedPrefix: teams/demo-team/shared\\n"
        "  globalSharedPrefix: shared\\n",
        encoding="utf-8",
    )
    os.environ["TEAMHARNESS_RUNTIME_CONFIG"] = str(runtime_config)
    os.environ["AGENTTEAMS_STORAGE_PREFIX"] = "agentteams/agentteams-storage"
    from_runtime = payload_without_storage({
        "workspaceDir": "#{ws}",
        "action": "list",
        "path": "shared/tasks/demo",
        "dryRun": True,
    })
    if from_runtime.get("remotePath") != "agentteams/agentteams-storage/teams/demo-team/shared/tasks/demo/":
        raise AssertionError(f"runtime storage prefix mismatch: {from_runtime!r}")

    pushed_runtime = None
    if STUB:
        runtime_result = pathlib.Path("#{ws}") / "shared/tasks/runtime-push/result.md"
        runtime_result.parent.mkdir(parents=True, exist_ok=True)
        runtime_result.write_text("# Runtime Push\\n", encoding="utf-8")
        pushed_runtime = payload_without_storage({
            "workspaceDir": "#{ws}",
            "action": "push",
            "path": "shared/tasks/runtime-push",
        })
        if not pushed_runtime.get("ok"):
            raise AssertionError(f"runtime-prefix push failed: {pushed_runtime!r}")
        if pushed_runtime.get("remotePath") != "agentteams/agentteams-storage/teams/demo-team/shared/tasks/runtime-push/":
            raise AssertionError(f"runtime-prefix push remote mismatch: {pushed_runtime!r}")

    summary = {
        "ok": True,
        "mcStubUsed": STUB,
        "dryRunPath": dry["path"],
        "remotePath": dry["remotePath"],
        "runtimeRemotePath": from_runtime["remotePath"],
        "pushFileCommand": norm_all(pushed_file_dry["command"]),
        "unconfiguredError": unconfigured["error"],
    }
    if STUB:
        summary.update({
            "runtimePushRemotePath": pushed_runtime["remotePath"],
            "pushPath": pushed["path"],
            "entries": listed["entries"],
            "statPath": stat["remotePath"],
        })
    print(json.dumps(summary, ensure_ascii=False))
  PY

  env = {
    "PATH" => "#{bin_dir}#{File::PATH_SEPARATOR}#{ENV.fetch("PATH", "")}",
    "AGENTTEAMS_FS_ENDPOINT" => "https://oss.example.test",
    "AGENTTEAMS_FS_ACCESS_KEY" => "access-key",
    "AGENTTEAMS_FS_SECRET_KEY" => "secret-key"
  }
  stdout, stderr, status = Open3.capture3(
    env, python, "-", stdin_data: python_test, chdir: repo_root.to_s
  )
  unless status.success?
    fail!([
      "teamharness filesync MCP test failed",
      "#{python}: exit #{status.exitstatus}",
      stderr.to_s.empty? ? "(no output)" : stderr
    ].join("\n"))
  end

  unless stub_usable
    puts JSON.pretty_generate(
      JSON.parse(stdout).merge(
        "skipped" => "mc-dependent cases: a stub cannot shadow `mc` for " \
                     "CreateProcess, which appends only .exe to an " \
                     "extension-less name"
      )
    )
    next
  end

  # The log records the argv the server built, so local paths arrive with the
  # platform separator here too.
  commands = log_path.read.lines.map { |line| line.strip.tr("\\", "/") }
  fail!("mc mirror was not called: #{commands.inspect}") unless commands.include?(
    "mirror #{ws}/shared/tasks/t-001/ mock/shared/tasks/t-001/ --overwrite --exclude *.tmp"
  )
  fail!("mc cp push was not called: #{commands.inspect}") unless commands.include?(
    "cp #{ws}/shared/tasks/t-001/result.md mock/shared/tasks/t-001/result.md"
  )
  fail!("mc cp pull was not called: #{commands.inspect}") unless commands.include?(
    "cp mock/shared/tasks/t-001/result.md #{ws}/shared/tasks/t-001/result.md"
  )
  fail!("mc ls was not called: #{commands.inspect}") unless commands.include?(
    "ls --recursive mock/shared/projects/demo/"
  )
  fail!("mc stat was not called: #{commands.inspect}") unless commands.include?(
    "stat mock/shared/tasks/t-001/result.md"
  )
  fail!("runtime-prefix mc mirror was not called: #{commands.inspect}") unless commands.include?(
    "mirror #{ws}/shared/tasks/runtime-push/ agentteams/agentteams-storage/teams/demo-team/shared/tasks/runtime-push/ --overwrite"
  )
  fail!("runtime-prefix mc mirror did not receive MC_HOST_agentteams: #{commands.inspect}") unless commands.any? do |line|
    line.start_with?("ENV MC_HOST_agentteams=https://access-key:secret-key@oss.example.test")
  end
  # The refusal has to happen before mc is reached, not after it quietly
  # succeeds against the local filesystem.
  fail!("unconfigured push still invoked mc: #{commands.inspect}") if commands.any? do |line|
    line.include?("shared/tasks/unconfigured")
  end

  puts JSON.pretty_generate(JSON.parse(stdout).merge("mcCommands" => commands))
end
