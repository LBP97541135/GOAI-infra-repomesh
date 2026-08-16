import type { TaskAgentReport } from "../types";
import { unverifiedMarkerLabel } from "../display";
import { marksUnverified } from "../viewmodel";

/** A-18：coding agent 对自己这一趟的自述，逐字摆出来。
 *
 *  缺陷本体：live 任务 6ba476ab 以 `runner.completed` 收尾、读模型给 succeeded、
 *  界面画绿「已交付」；而 agent 自己在 `summary` 里写着 "I could not execute
 *  anything to verify it"、"Please re-run before merging."，同一份载荷里
 *  `testResults: []`、`testCommand: null`、`artifacts: []`。这些字全都在读模型的
 *  数据里，界面全页 grep 不到一个——而这块不可见正好压在整条链第一个不可逆动作
 *  （merge 审批）之前。
 *
 *  三条渲染红线：
 *
 *  1. **原话不动**。`summaryText` 与 `blockers` 逐字渲染，不摘要、不截断、不高亮
 *     关键词、不改写语气。长就给滚动条，不给省略号——省略号会正好吃掉那句
 *     「Please re-run before merging.」。
 *  2. **不数没数过的东西**。`blockers` 为空只说明载荷没有结构化声明过（契约 6.12），
 *     不是「没有 blocker」。故有声明才写「N 条 blocker」，没有就只写「未验证」，
 *     原因留给原话去说。
 *  3. **琥珀不是赭红**。未验证是警告不是失败：这一趟确实跑完了、也确实产出了 commit，
 *     只是没有可核验的执行记录。用失败色等于说它失败了，同样不准。 */

/** 未验证标记（琥珀）。
 *
 *  `compact` 是给 168px 宽的 DAG 节点用的：那里塞不下整句，只印「未验证」，有结构化
 *  声明时后缀条数。**缩写的是排版不是事实**——整句在同一节点的 `title` 上，图脚也把
 *  它写全了；缩写绝不能把「有 N 条 blocker」缩没。 */
export function UnverifiedMarker({
  blockerCount,
  compact = false,
  title,
}: {
  blockerCount: number;
  compact?: boolean;
  title?: string;
}) {
  return (
    <span
      className={
        compact
          ? "flex-none rounded-[1px] bg-amber px-1 font-mono text-[9px] font-bold tracking-normal text-[#191308] normal-case"
          : "rounded-hard border border-amber px-1.5 py-px font-mono text-[10px] font-bold text-amber"
      }
      title={title}
    >
      {compact
        ? blockerCount > 0
          ? `未验证 · ${blockerCount} blocker`
          : "未验证"
        : unverifiedMarkerLabel(blockerCount)}
    </span>
  );
}

/** 一条任务的自述块。`verified` 为 true 时也渲染——「跑了什么、退出码多少」同样是
 *  事实，只有把两种都摆出来，未验证那条才不像是界面临时冒出来的一个惊叹号。 */
export function AgentVerificationBlock({ report }: { report: TaskAgentReport }) {
  /** 三种色调，按事实分，不叠加（A-18 第四面）：
   *
   *   - **失败（赭红）**：这一趟没跑通。`verified` 当然也是 false，但那不是新消息，
   *     所以**不再挂琥珀标记**——失败已经是更响的那句话，两个标记叠着只会让「跑成了
   *     却没验证」这一类失去分量。此时下面的原文就是失败理由，是本块的主角。
   *   - **未验证（琥珀）**：跑完了、也产出了 commit，但没有可核验的执行记录。
   *   - **已验证（橄榄绿）**：跑了什么、退出码多少，摆出来当对照组。 */
  const failed = report.displayStatus === "failed";
  const unverified = marksUnverified(report);

  const tone = failed
    ? "border-salmon bg-[#2b1712]"
    : unverified
      ? "border-amber bg-[#2a2110]"
      : "border-olive bg-panel-2";

  return (
    <div className={`rounded-hard border-l-2 px-3 py-2 ${tone}`}>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        {failed ? (
          <span className="rounded-hard border border-salmon px-1.5 py-px font-mono text-[10px] font-bold text-salmon">
            失败
          </span>
        ) : unverified ? (
          <UnverifiedMarker blockerCount={report.blockers.length} />
        ) : (
          <span className="rounded-hard border border-olive px-1.5 py-px font-mono text-[10px] font-bold text-olive">
            已验证
          </span>
        )}
        <span className="min-w-0 flex-1 truncate text-[12px] text-cream">{report.title}</span>
      </div>

      {/* 结构化事实：`verified` 就是从这三行算出来的（契约 §5.4），摆出来让人自己核 */}
      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[10.5px] text-tx2">
        <span>
          测试命令 {report.testCommand ? <b className="text-tx">{report.testCommand}</b> : "未记录"}
        </span>
        <span>
          测试记录 <b className="text-tx">{report.testResults.length}</b> 条
        </span>
        <span>
          产物 <b className="text-tx">{report.artifactCount}</b> 件
        </span>
      </div>

      {report.testResults.length > 0 && (
        <div className="mt-1 grid gap-0.5 font-mono text-[10.5px]">
          {report.testResults.map((result, i) => (
            <div key={`${result.command}-${i}`} className="flex items-baseline gap-1.5">
              <span className={result.exitCode === 0 ? "text-olive" : "text-salmon"}>
                exit {result.exitCode}
              </span>
              <span className="min-w-0 flex-1 truncate text-tx2">{result.command}</span>
              {result.summary && <span className="truncate text-tx3">{result.summary}</span>}
            </div>
          ))}
        </div>
      )}

      {report.blockers.length > 0 && (
        <div className="mt-2">
          <div className="microlabel pb-1">AGENT 声明的 BLOCKER（原话）</div>
          <ol className="max-h-[220px] list-decimal overflow-y-auto pl-5 text-[12px] leading-[1.65] text-tx">
            {report.blockers.map((blocker, i) => (
              <li key={i} className="whitespace-pre-wrap">
                {blocker}
              </li>
            ))}
          </ol>
        </div>
      )}

      {report.summaryText && (
        <div className="mt-2">
          <div className={`microlabel pb-1 ${failed ? "text-salmon" : ""}`}>
            {/* 失败时这段原文就是**失败理由**——`changed_path_denied: tests/…` 这类，
                直接指出该改什么。叫它「回报原文」会让人以为是可跳过的说明文字。 */}
            {failed ? "失败理由（RUNNER 原文）" : "AGENT 回报原文"}
            {report.blockers.length === 0 && unverified && (
              // 空的 blockers 不是「没有 blocker」，这句话必须说出来，否则读者会
              // 把「界面没数出 blocker」当成「agent 没提出 blocker」。
              <span className="pl-1.5 text-[10px] tracking-normal text-tx3 normal-case">
                （本次载荷未结构化声明 blocker，未验证的原因只在下面这段原话里）
              </span>
            )}
          </div>
          <pre
            className={`max-h-[260px] overflow-y-auto rounded-hard border px-2.5 py-2 font-mono text-[11.5px] leading-[1.6] whitespace-pre-wrap ${
              failed ? "border-salmon/50 bg-[#211008] text-[#e8a184]" : "border-line bg-[#161209] text-tx"
            }`}
          >
            {report.summaryText}
          </pre>
        </div>
      )}

      {failed && !report.summaryText && (
        <p className="mt-2 text-[11.5px] text-tx2">
          这一趟失败了，但载荷里没有理由文本——界面无从说明为什么，服务端日志是唯一去处。
        </p>
      )}

      {unverified && !report.summaryText && report.blockers.length === 0 && (
        <p className="mt-2 text-[11.5px] text-tx2">
          载荷里没有测试记录，agent 也没有留下任何说明——「没验证」是这条任务给出的全部事实。
        </p>
      )}
    </div>
  );
}
