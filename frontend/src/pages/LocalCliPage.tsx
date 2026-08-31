import { useState } from "react";

const START_COMMAND = "powershell -NoProfile -File .\\scripts\\start-local-cli.ps1";
const DRY_RUN_COMMAND = `${START_COMMAND} -DryRun`;
const STOP_COMMAND =
  "powershell -NoProfile -File .\\scripts\\bridge-e1\\stop_members.ps1 " +
  "-Members .\\scripts\\bridge-e1\\members.json " +
  "-PidDir .\\output\\bridge-team\\e1\\pids";

function CommandCard({
  title,
  command,
  note,
}: {
  title: string;
  command: string;
  note: string;
}) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");

  const copy = () => {
    navigator.clipboard
      .writeText(command)
      .then(() => {
        setCopyState("copied");
        window.setTimeout(() => setCopyState("idle"), 1800);
      })
      .catch(() => setCopyState("failed"));
  };

  return (
    <div className="rounded-hard border border-line bg-panel px-3 py-3">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div>
          <div className="text-[12.5px] font-semibold text-cream">{title}</div>
          <div className="mt-px text-[10.5px] text-tx3">{note}</div>
        </div>
        <button
          className="flex-none rounded-hard border border-amber/60 px-2 py-1 text-[11px] text-amber hover:bg-amber/10 hover:text-amber-hi"
          onClick={copy}
        >
          {copyState === "copied" ? "已复制" : copyState === "failed" ? "复制失败" : "复制命令"}
        </button>
      </div>
      <pre className="overflow-x-auto rounded-hard border border-line bg-ink-deep px-3 py-2 font-mono text-[11px] leading-5 text-tx">
        <code>{command}</code>
      </pre>
    </div>
  );
}

function Requirement({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex gap-2 border-b border-panel py-2 text-[11.5px] text-tx2">
      <span className="mt-[2px] text-olive">◆</span>
      <span>{children}</span>
    </li>
  );
}

export function LocalCliPage() {
  return (
    <div className="max-w-[860px]">
      <div className="flex items-baseline gap-3 border-b border-line pb-3">
        <h1 className="text-[16px] font-semibold text-cream">本地 CLI</h1>
        <span className="microlabel">External · Codex</span>
      </div>

      <div className="mt-4 rounded-hard border border-amber/40 bg-amber/5 px-4 py-3">
        <div className="eyebrow mb-1">启动边界</div>
        <p className="text-[12px] text-tx2">
          浏览器不直接启动宿主机进程，也不读取 credential env。请在 RepoMesh 仓库根目录的
          PowerShell 中执行下面命令；脚本只拉起已 provision、已生成 enrollment 的 External
          成员，不创建账号、Team 或数据库记录。
        </p>
      </div>

      <section className="mt-5">
        <div className="eyebrow mb-2">一键拉起</div>
        <div className="grid gap-3">
          <CommandCard
            title="先预检命令"
            command={DRY_RUN_COMMAND}
            note="不启动 Bridge；展示成员、角色与统一 workspace root。"
          />
          <CommandCard
            title="启动全部本地成员"
            command={START_COMMAND}
            note="每个成员一个隐藏进程；PID 与日志写入 output/bridge-team/e1。"
          />
          <CommandCard
            title="停止全部本地成员"
            command={STOP_COMMAND}
            note="停止前按 PID 和命令行复核进程身份，避免误杀。"
          />
        </div>
      </section>

      <section className="mt-5">
        <div className="eyebrow mb-1">启动前置</div>
        <ul>
          <Requirement>
            `scripts/bridge-e1/members.json` 已配置，且六个 External member 已完成 provision/binding。
          </Requirement>
          <Requirement>
            `output/bridge-team/e1/enrollments` 中已有对应 enrollment，credential locator 只引用环境变量。
          </Requirement>
          <Requirement>
            `output/bridge-team/e1-members.env` 已包含成员自己的 RepoMesh 与 Matrix token；页面和脚本均不回显值。
          </Requirement>
          <Requirement>
            每个成员的私有 `codex-home` 已有 `auth.json`；Leader 不获得 workspace，Worker 统一使用控制面 workspace root。
          </Requirement>
        </ul>
      </section>

      <section className="mt-5 rounded-hard border border-line px-3 py-3">
        <div className="eyebrow mb-1">默认路径</div>
        <dl className="grid grid-cols-[150px_1fr] gap-x-3 gap-y-2 text-[11.5px]">
          <dt className="text-tx3">Python</dt>
          <dd className="font-mono text-tx">.venv\Scripts\python.exe</dd>
          <dt className="text-tx3">Workspace root</dt>
          <dd className="font-mono text-tx">
            $env:REPOMESH_RUNNER_WORKSPACE_ROOT；未设置时为仓库同级 .repomesh-e1\workspaces
          </dd>
          <dt className="text-tx3">PID / logs</dt>
          <dd className="font-mono text-tx">output\bridge-team\e1\pids / logs</dd>
          <dt className="text-tx3">状态事实</dt>
          <dd className="text-tx2">
            本页不伪造“运行中”；实际状态以 Agents/Teams 读模型、PID 文件和日志为准。
          </dd>
        </dl>
      </section>
    </div>
  );
}
