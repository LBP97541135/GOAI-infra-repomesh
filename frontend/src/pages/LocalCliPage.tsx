import { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import type { ExternalMemberReadinessView } from "../api/contract";
import { defaultClient } from "../api/client";
import {
  LAUNCHER_BASE,
  getRoster,
  getSecrets,
  probe,
  restartMember,
  stalePidFile,
  startMembers,
  stopMembers,
  type LauncherMember,
  type LauncherProbe,
  type RosterDocument,
  type SecretEntry,
  type StalePidFileDetail,
} from "../api/launcher";
import { ConnectionConfigSection, RosterSection, SecretsSection } from "../components/LocalCliConfig";
import { READINESS_LABEL, READINESS_SKIN, errText, eventTime, shortId } from "../display";
import { StalePidBlock } from "../components/StatusBlocks";

/** 本地 CLI 页（Task 5）。
 *
 *  **两个数据源，两列并排，不合成一列。**「进程在跑」由本机启动器说（它数 PID 文件
 *  与进程），「成员就绪」由 RepoMesh 的租约说（Bridge 每 15 秒续一次，45 秒过期）。
 *  这两者**经常且合法地不一致**：进程刚起来还没续上第一次租约、进程还在但 Bridge
 *  卡住不再上报、进程被杀而租约还剩十几秒。把它们并成一个「状态」列就必须挑一个当真，
 *  而挑错的那一半正是操作者要排查的那一半——物化门只认租约，可要重启的是进程。
 *
 *  **命令卡片一格都不删**，两个状态里都在。启动器是本机的一个可选进程：没装、没起、
 *  Origin 不在白名单，都是常态而不是故障，此时页面退回到命令行那条路，而不是变成
 *  一块「不可用」。 */

const START_COMMAND = "powershell -NoProfile -File .\\scripts\\start-local-cli.ps1";
const DRY_RUN_COMMAND = `${START_COMMAND} -DryRun`;
const STOP_COMMAND =
  "powershell -NoProfile -File .\\scripts\\bridge-e1\\stop_members.ps1 " +
  "-Members .\\scripts\\bridge-e1\\members.json " +
  "-PidDir .\\output\\bridge-team\\e1\\pids";

/** 轮询间隔。取 5 秒与房间流同一档：租约 TTL 45 秒、续期 15 秒一次，5 秒足够让
 *  「刚起来」与「刚掉线」在这页上看得见，也不至于把一台本机进程问出压力。 */
const POLL_MS = 5000;

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

/** 一行成员。就绪那一格有**三种**空，不能糊成一种：
 *   - `known === false`：租约表这一轮（且此前从未）没取到——界面对这个成员的就绪
 *     一无所知。写「未上报」就是拿一次自己的取数失败去指控一个可能好好的成员，
 *     一个配错的动作 token 会让整列对着六个健康成员说它们没上报；
 *   - `known && readiness === null`：取到了，表里没有这个 agent——它确实从没上报过
 *     （或 RepoMesh 不认这个 id）；
 *   - 有值：服务端派生的三态照原样显示。 */
function MemberRow({
  member,
  readiness,
  known,
  busy,
  onRestart,
}: {
  member: LauncherMember;
  readiness: ExternalMemberReadinessView | null;
  known: boolean;
  busy: boolean;
  onRestart: () => void;
}) {
  /** 就绪未知时**只按进程判**：拿一个取不到的事实去点亮每一行的「重启」，
   *  是把「我不知道」说成「都坏了」。 */
  const healthy = known ? member.running && readiness?.status === "ready" : member.running;

  return (
    <tr className="border-b border-panel">
      <td className="py-2 pr-3 align-top">
        <div className="font-mono text-[12px] text-tx">{member.displayName}</div>
        <div className="mt-px font-mono text-[10.5px] text-tx3">
          {member.role} · {shortId(member.agentId)}
        </div>
      </td>

      <td className="py-2 pr-3 align-top">
        <span className="flex items-center gap-1.5 text-[11.5px]">
          <i className={`size-1.5 rounded-full ${member.running ? "bg-olive" : "bg-tx3"}`} />
          {member.running ? "运行中" : "未运行"}
        </span>
        {/* 日志路径挂 title 而不占一列：它是绝对路径，排进表里会把两列挤没，
            但成员起不来时它就是下一步要看的东西 */}
        <div className="mt-px font-mono text-[10.5px] text-tx3" title={member.logPath ?? "启动器未记录日志路径"}>
          {member.pid === null ? "无 PID 文件" : `PID ${member.pid}`}
        </div>
      </td>

      <td className="py-2 pr-3 align-top">
        {!known ? (
          <span
            className="rounded-hard border border-line px-1.5 py-px text-[10.5px] text-tx3"
            title="租约状态这一轮没取到（原因见表下）——这不是对该成员的判断"
          >
            未能获取
          </span>
        ) : readiness === null ? (
          <span className="rounded-hard border border-line px-1.5 py-px text-[10.5px] text-tx3">未上报</span>
        ) : (
          <>
            <span className={`rounded-hard border px-1.5 py-px text-[10.5px] ${READINESS_SKIN[readiness.status]}`}>
              {READINESS_LABEL[readiness.status]}
            </span>
            <div className="mt-px font-mono text-[10.5px] text-tx3">
              {readiness.stoppedAt === null
                ? `上报 ${eventTime(readiness.reportedAt)} · 到期 ${eventTime(readiness.expiresAt)}`
                : `已报停止 ${eventTime(readiness.stoppedAt)}`}
            </div>
          </>
        )}
      </td>

      <td className="py-2 text-right align-top">
        {/* 「进程在跑但租约不 ready」也给重启：那正是 Bridge 还活着却不再上报的形态，
            而物化门只认租约，光看进程列会以为没事 */}
        {healthy ? (
          <span className="text-[10.5px] text-tx3">—</span>
        ) : (
          <button
            className="rounded-hard border border-amber/60 px-2 py-1 text-[11px] text-amber hover:bg-amber/10 hover:text-amber-hi disabled:cursor-not-allowed disabled:opacity-40"
            disabled={busy}
            onClick={onRestart}
          >
            重启
          </button>
        )}
      </td>
    </tr>
  );
}

/** `embedded`：收进设置页「本地 CLI」分类时为 true——去掉页面级大标题与外框，
 *  分类标题由设置页提供；独立路由（#/settings/local-cli 旧链接）仍带头部。 */
export function LocalCliPage({ embedded = false }: { embedded?: boolean }) {
  /** null = 首次探测还没回来。三态本身在 `api/launcher.ts` 的 `LauncherProbe`。 */
  const [launcher, setLauncher] = useState<LauncherProbe | null>(null);
  const [readiness, setReadiness] = useState<ExternalMemberReadinessView[] | null>(null);
  const [readinessError, setReadinessError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  /** 在途写操作的键："start" / "stop" / 某个 agentId。同时只允许一个。 */
  const [busy, setBusy] = useState<string | null>(null);
  const [opError, setOpError] = useState<string | null>(null);
  const [blocked, setBlocked] = useState<StalePidFileDetail | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  // 配置文档（roster + 密钥掩码）：只在挂载与保存后取，不跟 5s 轮询——
  // 它们是操作者编辑的对象，不该在打字时被轮询重置
  const [roster, setRoster] = useState<RosterDocument | null>(null);
  const [secrets, setSecrets] = useState<SecretEntry[] | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [configTick, setConfigTick] = useState(0);
  const [showCommands, setShowCommands] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getRoster(), getSecrets()])
      .then(([rosterRes, secretsRes]) => {
        if (cancelled) return;
        setRoster(rosterRes.document);
        setSecrets(secretsRes.entries);
        setConfigError(null);
      })
      .catch((err: unknown) => {
        if (!cancelled) setConfigError(errText(err));
      });
    return () => {
      cancelled = true;
    };
  }, [configTick]);

  const reloadConfig = () => {
    setConfigTick((n) => n + 1);
    setTick((n) => n + 1);
  };

  // 5s 心跳（同 observe 各页与房间流的写法）
  useEffect(() => {
    const t = window.setInterval(() => setTick((n) => n + 1), POLL_MS);
    return () => window.clearInterval(t);
  }, []);

  // 两个源同一拍取：并排的两列必须来自同一次刷新，否则它们的不一致有一半是取数时差
  useEffect(() => {
    let cancelled = false;
    probe().then((result) => {
      if (cancelled) return;
      setLauncher(result);
      setLastRefresh(new Date());
    });
    defaultClient()
      .getExternalMemberReadiness()
      .then((page) => {
        if (cancelled) return;
        setReadiness(page.members);
        setReadinessError(null);
      })
      // 刷新失败保留上一轮的行，只标注这一轮没取到——清空会让整列在一次瞬时失败里消失
      .catch((err: unknown) => !cancelled && setReadinessError(errText(err)));
    return () => {
      cancelled = true;
    };
  }, [tick]);

  /** 三个写操作共用一条路：置忙 → 清上一次的拒绝 → 成功就重取两个源。
   *  **不消费返回体**（它只有进程事实，没有租约），一律等下一拍的两个源。 */
  const run = (key: string, operation: () => Promise<unknown>) => {
    setBusy(key);
    setOpError(null);
    setBlocked(null);
    operation()
      .then(() => setTick((n) => n + 1))
      .catch((err: unknown) => {
        setBlocked(stalePidFile(err));
        setOpError(errText(err));
      })
      .finally(() => setBusy(null));
  };

  const status = launcher?.kind === "ok" ? launcher.status : null;
  /** 至少收到过一份租约表。**这一位就是「未能获取」与「未上报」的分界**：没有它，
   *  一个配错的动作 token 会让整列理直气壮地说六个健康成员都没上报过。 */
  const readinessKnown = readiness !== null;
  const readinessOf = (agentId: string) => readiness?.find((m) => m.agentId === agentId) ?? null;

  return (
    <div className={embedded ? "" : "max-w-[860px]"}>
      {!embedded && (
        <div className="flex items-baseline gap-3 border-b border-line pb-3">
          <h1 className="text-[16px] font-semibold text-cream">本地 CLI</h1>
          <span className="microlabel">External · Codex</span>
        </div>
      )}

      <p className="mt-3 text-[11.5px] text-tx2">
        一键启动由本机启动器（{LAUNCHER_BASE}）代为执行；启动器未运行时，用下方命令。
      </p>

      {launcher?.kind === "ok" && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="rounded-hard border border-olive px-2 py-px text-[11px] text-olive">启动器已连接</span>
          <span className="rounded-hard border border-line px-2 py-px text-[11px] text-tx2">
            进程 {launcher.status.members.filter((m) => m.running).length}/{launcher.status.members.length} 运行
          </span>
          {readiness !== null && (
            <span className="rounded-hard border border-line px-2 py-px text-[11px] text-tx2">
              就绪 {readiness.filter((r) => r.status === "ready").length}/{readiness.length}
            </span>
          )}
          <span className="text-[10.5px] text-tx3">
            {lastRefresh ? `刷新于 ${lastRefresh.toLocaleTimeString()} · ` : ""}每 {POLL_MS / 1000} 秒自动
          </span>
          <button
            type="button"
            className="rounded-hard border border-line px-2 py-px text-[11px] text-tx2 transition-colors hover:border-amber hover:text-amber-hi"
            onClick={() => setTick((n) => n + 1)}
          >
            刷新
          </button>
        </div>
      )}

      {launcher === null && <p className="mt-5 text-[12px] text-tx2">正在探测本机启动器…</p>}

      {launcher?.kind === "launcher_unavailable" && (
        <div className="mt-5 rounded-hard border border-line bg-panel px-4 py-3">
          <div className="eyebrow mb-1">未连接本机启动器</div>
          <p className="text-[12px] text-tx2">
            {LAUNCHER_BASE} 没有应答：启动器未运行，或控制台地址不在其 allowedOrigins 白名单里。
            下方命令照常可用。
          </p>
          <p className="mt-1.5 font-mono text-[10.5px] break-all text-tx3">{launcher.message}</p>
        </div>
      )}

      {launcher?.kind === "refused" && (
        <div className="mt-5 rounded-hard border border-salmon/60 bg-salmon/10 px-4 py-3">
          <div className="eyebrow mb-1 text-salmon">本机启动器答了一个错误码</div>
          <p className="text-[12px] text-salmon">启动器返回了错误，原文如下：</p>
          <p className="mt-1.5 font-mono text-[10.5px] break-all text-tx3">{launcher.message}</p>
        </div>
      )}

      {status && (
        <section className="mt-5">
          <div className="mb-2 flex items-baseline justify-between gap-3">
            <span className="eyebrow">
              本机启动器 · roster <span className="font-mono normal-case">{status.rosterVersion}</span>
            </span>
            <span className="text-[10.5px] text-tx3">每 {POLL_MS / 1000} 秒刷新</span>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              className="rounded-hard bg-amber px-4 py-2 text-[12.5px] font-extrabold text-on-amber hover:bg-amber-hi disabled:cursor-not-allowed disabled:opacity-40"
              disabled={busy !== null}
              onClick={() => run("start", startMembers)}
            >
              {busy === "start" ? "启动中…" : "启动并检查本地 CLI"}
            </button>
            <button
              className="rounded-hard border border-line px-3 py-2 text-[12.5px] text-tx hover:border-amber hover:text-amber-hi disabled:cursor-not-allowed disabled:opacity-40"
              disabled={busy !== null}
              onClick={() => run("stop", stopMembers)}
            >
              {busy === "stop" ? "停止中…" : "停止全部"}
            </button>
          </div>

          {blocked && <StalePidBlock detail={blocked} />}

          {opError && !blocked && (
            // 启动器 detail 原文。404（重启一个它不认的成员）、连不上（没起，或来源不在
            // 白名单——写请求带自定义头，那一趟被拦在预检，连发都没发出去）各是一件不同的
            // 事，归并成「操作失败」会把可自助解决的配置问题说成故障
            <div className="mt-3 border-l-2 border-salmon bg-salmon-well px-3 py-2 text-[12px] leading-[1.7] break-words text-salmon-hi">
              <b className="mr-1.5 font-mono tracking-[0.08em]">启动器拒绝</b>
              {opError}
            </div>
          )}

          {status.members.length === 0 ? (
            <p className="py-6 text-center text-[12.5px] text-tx3">
              启动器的 roster 里没有成员（config 的 subset 过滤掉了全部条目？）
            </p>
          ) : (
            <table className="mt-3 w-full">
              <thead>
                <tr className="border-b border-line text-left">
                  <th className="pb-1.5 text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">成员</th>
                  <th
                    className="pb-1.5 text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase"
                    title="本机启动器数 PID 文件与进程得出的事实"
                  >
                    进程
                  </th>
                  <th
                    className="pb-1.5 text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase"
                    title="RepoMesh 的租约状态：Bridge 自己上报，45 秒过期。物化门只认这一列"
                  >
                    就绪
                  </th>
                  <th className="pb-1.5 text-right text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">
                    操作
                  </th>
                </tr>
              </thead>
              <tbody>
                {status.members.map((member) => (
                  <MemberRow
                    key={member.agentId}
                    member={member}
                    readiness={readinessOf(member.agentId)}
                    known={readinessKnown}
                    busy={busy !== null}
                    onRestart={() => run(member.agentId, () => restartMember(member.agentId))}
                  />
                ))}
              </tbody>
            </table>
          )}

          <p className="pt-3 text-[11px] text-tx3">
            两列可能短暂不一致，物化门只认<b className="text-tx2">就绪</b>列。
            {readinessError && ` 就绪列取用失败：${readinessError.slice(0, 80)}`}
          </p>
        </section>
      )}

      {launcher?.kind === "ok" && (
        <>
          {configError && (
            <div className="mt-5 rounded-hard border border-salmon/60 bg-salmon/10 px-3 py-2 text-[11.5px] text-salmon">
              {configError}
            </div>
          )}
          {roster !== null && secrets !== null && (
            <>
              <ConnectionConfigSection
                key={`conn-${configTick}`}
                roster={roster}
                onSaved={reloadConfig}
                onError={setConfigError}
              />
              <SecretsSection
                key={`sec-${configTick}`}
                roster={roster}
                secrets={secrets}
                onSaved={reloadConfig}
                onError={setConfigError}
              />
              <RosterSection
                key={`ros-${configTick}`}
                roster={roster}
                onSaved={reloadConfig}
                onError={setConfigError}
              />
            </>
          )}
        </>
      )}

      <section className="mt-5">
        <button
          type="button"
          className="flex w-full items-baseline gap-2 text-left"
          onClick={() => setShowCommands((v) => !v)}
        >
          <span className="eyebrow">命令行入口 <ChevronDown size={11} strokeWidth={1.5} className={showCommands ? "" : "-rotate-90"} /></span>
          <span className="text-[10.5px] text-tx3">启动器不在时走这条</span>
        </button>
        {showCommands && (
          <>
            <p className="mb-2 mt-2 text-[11.5px] text-tx3">在仓库根目录的 PowerShell 中执行：</p>
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
          </>
        )}
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
            上表的两列各有出处：进程列来自本机启动器，就绪列来自 RepoMesh 租约。本页不合成第三种状态。
          </dd>
        </dl>
      </section>
    </div>
  );
}
