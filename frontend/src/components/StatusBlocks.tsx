import type { StalePidFileDetail } from "../api/launcher";
import type { RuntimePhase } from "../display";

/** 取数三态的共用小块（C 组收尾）：失败面板 / 加载行 / 网格页脚的探测与数据源标注。
 *  此前七个页面各抄一份同样的 class 与结构——样式取 IssueListPage 那份逐字，
 *  标题与文案由调用方传入保留各自原文（「issue 详情加载失败」vs「团队清单加载失败」
 *  的措辞差异是有意的，不在此归一）。
 *
 *  末尾多一块本机启动器的拒绝渲染（Task 5）：它有两个调用方——本地 CLI 页和物化弹窗
 *  里的「启动并重新检查」都会撞上同一个 409，而那一族的价值全在逐成员的文件名上，
 *  两处各写一份迟早有一处退化成一句「启动失败」。 */

/** 可见失败态面板：红框 + eyebrow 标题 + 原因 + 重试。
 *  `className` 承接各处的外边距差异（列表/网格页 mt-4；详情/房间容器自带返回按钮行，无 mt）。 */
export function ErrorPanel({
  title,
  message,
  onRetry,
  className = "mt-4",
}: {
  title: string;
  message: string;
  onRetry: () => void;
  className?: string;
}) {
  return (
    <div className={`${className} rounded-hard border border-salmon/60 bg-salmon/10 px-4 py-3`.trim()}>
      <div className="eyebrow mb-1 text-salmon">{title}</div>
      <p className="text-[12px] text-salmon">{message}</p>
      <button
        className="mt-2 rounded-hard border border-line px-2.5 py-[3px] text-[11.5px] text-tx2 hover:border-amber hover:text-amber-hi"
        onClick={onRetry}
      >
        重试
      </button>
    </div>
  );
}

/** 居中加载行。默认「加载中…」；房间容器传自己的措辞。 */
export function LoadingLine({ text = "加载中…", className = "" }: { text?: string; className?: string }) {
  return <p className={`${className} py-8 text-center text-[12.5px] text-tx2`.trim()}>{text}</p>;
}

/** 网格页脚的探测阶段 + 数据源标注（团队页/花名册页两段式取数的页脚两行 +
 *  仓库页的数据源行）。渲染为片段，嵌在各页页脚 <p> 的自有说明文字之后。
 *  `showProbing` 承接「首段未回时不提探测」的既有条件（rows !== null）。 */
export function ProbeNote({
  phase,
  showProbing = false,
  probeError = null,
  sourceNote,
}: {
  phase?: RuntimePhase;
  showProbing?: boolean;
  probeError?: string | null;
  sourceNote: string;
}) {
  return (
    <>
      {phase === "loading" && showProbing && " 运行时探测进行中（首屏不等它）。"}
      {phase === "failed" && probeError && ` 运行时探测请求失败：${probeError.slice(0, 60)}`}
      {" "}数据源：{sourceNote}
    </>
  );
}

/** 本机启动器的 PID 文件占位 409（`stale_pid_file`）。**逐成员列出文件名**：这是启动器
 *  唯一一条「你可以自己解决」的拒绝，解法就是删掉这些文件，而文件名只在这份 body 里。
 *  走 `errText` 会把它压成一串被截断的 JSON，解法正好在被切掉的那一半。 */
export function StalePidBlock({
  detail,
  className = "mt-3",
}: {
  detail: StalePidFileDetail;
  className?: string;
}) {
  return (
    <div
      className={`${className} border-l-2 border-salmon bg-[#2b1712] px-3 py-2 text-[12px] leading-[1.7] text-[#e8a184]`.trim()}
    >
      <b className="mr-1.5 font-mono tracking-[0.08em]">PID 文件占位</b>
      {detail.message}
      <ul className="mt-1.5">
        {detail.members.map((member) => (
          <li key={member.pidFile} className="font-mono text-[11px] break-all">
            {member.displayName} · {member.pidFile}
          </li>
        ))}
      </ul>
    </div>
  );
}
