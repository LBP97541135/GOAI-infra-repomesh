import type { ReactNode } from "react";
import { ArrowLeft, BarChart3, BellRing, ScrollText, Waypoints, type LucideIcon } from "lucide-react";

/** 观测板块子页的统一页头：面包屑「← 观测 / 板块名」+ 右侧操作区。
 *  每个板块页自包含，面包屑保证随时能回门户——排版收敛的关键是各页不互相
 *  堆叠内容，只保留这一条回退路径。
 *
 *  2026-09-08 升级（用户裁决）：返回入口从裸文字变按钮式胶囊；标题前加板块
 *  lucide 图标，与门户四大板块瓦片卡同图标语言。 */

const SECTION_ICON: Record<string, LucideIcon> = {
  推理轨迹: Waypoints,
  用量大盘: BarChart3,
  日志: ScrollText,
  告警: BellRing,
};

export function ObserveCrumb({ section, children }: { section: string; children?: ReactNode }) {
  const Icon = SECTION_ICON[section];
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
      <div className="flex items-center gap-2.5">
        <a
          href="#/observe"
          title="返回观测门户"
          className="inline-flex items-center gap-1 rounded-full border border-line px-2.5 py-[3px] text-[11px] text-tx2 transition-colors hover:border-amber/50 hover:text-tx"
        >
          <ArrowLeft size={11} strokeWidth={1.5} />
          观测
        </a>
        <span className="text-[11.5px] text-tx3">/</span>
        {Icon && <Icon size={16} strokeWidth={1.5} className="text-tx2" />}
        <h1 className="text-[16px] font-semibold text-cream">{section}</h1>
      </div>
      {children}
    </div>
  );
}
