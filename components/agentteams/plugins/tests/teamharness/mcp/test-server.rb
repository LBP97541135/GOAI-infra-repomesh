#!/usr/bin/env ruby
# frozen_string_literal: true

require "json"
require "open3"
require "pathname"

repo_root = Pathname.new(__dir__).join("../../../..").expand_path
mcp_dir = repo_root / "plugins/teamharness/mcp"

def fail!(message)
  warn "ERROR: #{message}"
  exit 1
end

python_test = <<~PY
  import json
  import pathlib
  import sys

  sys.path.insert(0, str(pathlib.Path("#{mcp_dir}")))
  from server import handle_request

  init = handle_request({
      "jsonrpc": "2.0",
      "id": 1,
      "method": "initialize",
      "params": {},
  })
  if init["result"]["serverInfo"]["name"] != "teamharness":
      raise AssertionError(f"unexpected server info: {init!r}")

  tools = handle_request({
      "jsonrpc": "2.0",
      "id": 2,
      "method": "tools/list",
      "params": {},
  })["result"]["tools"]
  names = [tool["name"] for tool in tools]
  expected = ["health", "message", "inbox", "roomflow", "filesync", "artifact", "projectflow", "taskflow"]
  if names != expected:
      raise AssertionError(f"unexpected tools: {names!r}")

  unknown = handle_request({
      "jsonrpc": "2.0",
      "id": 3,
      "method": "tools/call",
      "params": {"name": "missing", "arguments": {}},
  })
  payload = json.loads(unknown["result"]["content"][0]["text"])
  if payload.get("error") != "unknown_tool":
      raise AssertionError(f"unexpected unknown-tool response: {payload!r}")

  print(json.dumps({"ok": True, "tools": names}, ensure_ascii=False))
PY

# "python3" is not a usable interpreter everywhere: on Windows it commonly
# resolves to the App Execution Alias stub, which exits non-zero and prints
# nothing at all. Try each candidate and keep the first that actually runs.
attempts = []
stdout = stderr = nil
status = nil
%w[python3 python].each do |interpreter|
  begin
    stdout, stderr, status = Open3.capture3(
      interpreter, "-", stdin_data: python_test, chdir: repo_root.to_s
    )
  rescue Errno::ENOENT
    attempts << "#{interpreter}: not found"
    next
  end
  break if status.success?

  attempts << "#{interpreter}: exit #{status.exitstatus}#{stderr.to_s.empty? ? " (no output)" : "\n#{stderr}"}"
  status = nil
end

unless status&.success?
  fail!(["teamharness MCP server test failed", *attempts].join("\n"))
end

puts JSON.pretty_generate(JSON.parse(stdout))
