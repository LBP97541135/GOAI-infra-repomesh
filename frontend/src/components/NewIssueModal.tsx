import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import { resolveGovernanceAgent, type GovernanceAgent } from "../api/decisions";
import { parseRequirementDocument } from "../api/issues";
import { errText } from "../display";
import { Modal } from "./Modal";

/** 新建 issue 弹窗（CONS-41 → B-1 接线）：处理者芯片 / 需求文本 / Ctrl+Enter 提交。
 *  按原型 redesign-issue-centric.html。
 *
 *  写路径 = 契约 v0.3 §1（POST /issues，建首份虚拟草稿快照）。replay 模式不写
 *  后端（回放夹具世界不可篡改），提交时如实说明并保留草稿。
 *
 *  需求文档真实上传：📎 把 .txt/.md/.docx/.pdf/.odt/.rtf 发到 POST
 *  /issues/parse-document 解析成纯文本。解析结果**不在界面展示正文**，只在原需求
 *  文本区显示上传成功状态（文件名 + 字符数），提交时作为需求文本提交；解析只读
 *  后端、不写数据，replay 模式同样可用。文本一旦变化即视为新的逻辑创建、换幂等键。
 *
 *  处理者芯片显示花名册派生的 Org Leader（resolveGovernanceAgent 单点）；解析
 *  不到时提交按钮不禁用——服务端 403/404 的原因比前端预判更准，错误原样呈现。 */

export function NewIssueModal({
  open,
  workspaceLabel,
  organizationId,
  mode,
  onClose,
  onToast,
  onCreate,
}: {
  open: boolean;
  workspaceLabel: string;
  /** 当前工作区（A1）：芯片解析与实际提交必须用同一个键，否则「显示的人」
   *  和「记名的人」可能不同 */
  organizationId: string | null;
  mode: "live" | "replay";
  onClose: () => void;
  onToast: (text: string) => void;
  /** live 创建回路（成功后由外层负责刷新列表与跳转）；失败原因原样抛回。
   *  `filename` 为上传文档的文件名（随需求一并落库），未上传文档时为 null。 */
  onCreate: (text: string, idempotencyKey: string, filename: string | null) => Promise<void>;
}) {
  // 草稿**有意跨开关保留**：误按 Esc 不该让用户重写一遍需求；
  // 提交**成功**才清空（主脑 2026-08-11 裁决，写端点接入后的约定行为）。
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [parsingFile, setParsingFile] = useState(false);
  /** 上传文档的解析正文（隐藏态）：只在提交时作为需求文本，不在界面展示正文。 */
  const [documentText, setDocumentText] = useState<string | null>(null);
  /** 已成功解析的文档来源（上传成功痕迹：文件名 + 字符数）。 */
  const [sourceFile, setSourceFile] = useState<{ name: string; chars: number } | null>(null);
  const [principal, setPrincipal] = useState<GovernanceAgent | null>(null);
  const [principalResolving, setPrincipalResolving] = useState(false);
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  /** A2（§1.3）：幂等键随「逻辑创建」而非「请求」——文本一变/提交成功即换新键，
   *  失败重试沿用同键，超时重点不会创建第二个 issue */
  const keyRef = useRef<string>(crypto.randomUUID());

  useEffect(() => {
    if (!open) return;
    areaRef.current?.focus();
    // 处理者芯片：与治理决策同一个派生单点（缓存命中时无额外请求）。
    // A1：按当前工作区解析——与提交路径同键，芯片显示谁就以谁记名
    let cancelled = false;
    setPrincipalResolving(true);
    resolveGovernanceAgent(organizationId)
      .then((agent) => !cancelled && setPrincipal(agent))
      .catch(() => !cancelled && setPrincipal(null))
      .finally(() => !cancelled && setPrincipalResolving(false));
    return () => {
      cancelled = true;
    };
  }, [open, organizationId]);

  const submit = useCallback(() => {
    if (submitting) return;
    // 需求正文：上传文档优先（不展示正文），否则用手输文本。
    const requirementText = sourceFile ? documentText ?? "" : text.trim();
    if (!requirementText) {
      onToast("请先描述要交付什么，或上传需求文档");
      return;
    }
    if (mode === "replay") {
      // 诚实数据：回放模式不写后端，也不伪造「已创建」
      onToast("回放模式不写后端；加 ?source=live 后可真实创建，需求文本已保留");
      return;
    }
    setSubmitting(true);
    onCreate(requirementText, keyRef.current, sourceFile ? sourceFile.name : null)
      .then(() => {
        setText("");
        setDocumentText(null);
        setSourceFile(null);
        keyRef.current = crypto.randomUUID(); // 本次逻辑创建已完成，下一次换新键
      })
      .catch((err: unknown) => {
        onToast(`创建失败：${errText(err)}`);
      })
      .finally(() => setSubmitting(false));
  }, [text, documentText, sourceFile, mode, submitting, onToast, onCreate]);

  /** 需求文档真实上传：解析成功只在原需求文本区显示上传成功状态（文件名 + 字符数），
   *  正文保存在隐藏态 `documentText` 里、不展示；提交时作为需求文本提交。文本变化即
   *  换幂等键（与手输同语义）。失败 toast 原样呈现（415/413/422）。 */
  const handleFileChange = useCallback(
    async (e: ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      e.target.value = ""; // 允许再次选择同一文件
      if (!file || parsingFile) return;
      setParsingFile(true);
      try {
        const parsed = await parseRequirementDocument(file);
        setDocumentText(parsed.text);
        setSourceFile({ name: parsed.filename, chars: parsed.chars });
        keyRef.current = crypto.randomUUID();
        onToast(
          parsed.truncated
            ? `已上传 ${parsed.filename}（${parsed.chars} 字符，超长已截断）`
            : `已上传 ${parsed.filename}（${parsed.chars} 字符）`,
        );
      } catch (err) {
        onToast(`文档解析失败：${errText(err)}`);
      } finally {
        setParsingFile(false);
      }
    },
    [parsingFile, onToast],
  );

  const removeDocument = useCallback(() => {
    setSourceFile(null);
    setDocumentText(null);
    keyRef.current = crypto.randomUUID();
  }, []);

  useEffect(() => {
    if (!open) return;
    // Ctrl+Enter 按原型挂在 document 上：焦点不在文本框时（如点过「范围」按钮）仍生效。
    // Esc 关闭不再自己处理——共享外壳 Modal 的原生 <dialog> cancel→close 语义接管。
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        submit();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, submit]);

  if (!open) return null;

  const principalChip =
    mode === "replay"
      ? "组织 Leader（回放演示）"
      : principalResolving
        ? "解析中…"
        : principal
          ? `AGENT ${principal.label}`
          : "组织 Leader（未接入）";

  return (
    <Modal
      open={open}
      onClose={onClose}
      className="m-auto w-[560px] max-w-[92vw] rounded-hard border border-line bg-[#1c1710] p-0 text-tx shadow-[0_24px_60px_rgba(0,0,0,0.6)]"
    >
      <div className="flex items-center gap-2 border-b border-line px-4 py-3">
        <span className="text-[12px] text-tx2">{workspaceLabel}</span>
        <span className="text-tx2">›</span>
        <span className="text-[12.5px] text-tx">新建 issue</span>
        <button className="ml-auto text-[14px] text-tx2 hover:text-amber-hi" onClick={onClose}>
          ✕
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2 px-4 pt-2.5">
        <span className="text-[11.5px] text-tx2">处理者</span>
        <span className="rounded-hard border border-line px-2 py-px font-mono text-[11px] text-kraft">
          <i className="not-italic text-tx2">●</i> {principalChip}
        </span>
        <span className="text-[10.5px] text-tx3">Org Leader 负责需求接收与范围提议</span>
      </div>

      {sourceFile ? (
        <div className="flex min-h-[150px] flex-col items-center justify-center gap-3 px-4 py-3.5">
          <div className="rounded-hard border border-line bg-panel px-5 py-4 text-center">
            <div className="font-mono text-[12.5px] text-kraft">{sourceFile.name}</div>
            <div className="mt-1 text-[11px] text-tx2">已解析 {sourceFile.chars} 字符</div>
          </div>
          <button
            className="text-[11px] text-tx3 underline-offset-2 hover:text-amber-hi"
            onClick={removeDocument}
          >
            移除文档
          </button>
        </div>
      ) : (
        <textarea
          ref={areaRef}
          className="min-h-[150px] w-full resize-none bg-transparent px-4 py-3.5 font-sans text-[13px] leading-[1.7] text-tx placeholder:text-tx3 focus:outline-none"
          placeholder='告诉组织要交付什么，例如："在订单结账时记录价格被修改的原因，原因随订单落库并在后台订单详情页展示"'
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            // A2：文本已变 = 新的逻辑创建，换新键（改完再重试不会被旧键归并）
            keyRef.current = crypto.randomUUID();
          }}
        />
      )}

      {/* 真实文件上传：隐藏 input，由 📎 触发；解析只读后端、不写数据 */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".txt,.md,.markdown,.docx,.pdf,.odt,.rtf"
        className="hidden"
        onChange={handleFileChange}
      />

      <div className="flex items-center gap-2.5 border-t border-line px-3.5 py-2.5">
        <button
          className="rounded-hard border border-line px-2.5 py-[3px] text-[11.5px] text-tx2 hover:border-tx2"
          onClick={() => onToast("范围默认由 Org Leader 从仓库摘要提议；手动圈选待仓库读模型（CONS-32）")}
        >
          ▣ 范围 · Org Leader 提议
        </button>
        <button
          className="text-[12px] text-tx2 hover:text-amber-hi disabled:opacity-60"
          onClick={() => fileInputRef.current?.click()}
          disabled={parsingFile}
          title="上传需求文档（.txt/.md/.docx/.pdf/.odt/.rtf）"
        >
          📎
        </button>
        {parsingFile && (
          <span className="font-mono text-[10.5px] text-tx3">解析中…</span>
        )}
        <button
          className="ml-auto rounded-hard bg-amber px-4 py-[7px] text-[12.5px] font-extrabold text-[#191308] hover:bg-amber-hi disabled:opacity-60"
          disabled={submitting}
          onClick={submit}
        >
          {submitting ? "创建中…" : "创建 (Ctrl+Enter)"}
        </button>
      </div>
    </Modal>
  );
}
