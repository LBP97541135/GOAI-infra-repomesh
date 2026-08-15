import type { ReactNode } from "react";

/** 观测板块子页的统一页头：面包屑「← 观测 / 板块名」+ 右侧操作区。
 *  每个板块页自包含，面包屑保证随时能回门户——排版收敛的关键是各页不互相
 *  堆叠内容，只保留这一条回退路径。 */

export function ObserveCrumb({ section, children }: { section: string; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
      <div className="flex items-baseline gap-2">
        <a href="#/observe" className="text-[12px] text-tx3 hover:text-amber-hi">
          ← 观测
        </a>
        <span className="text-[11.5px] text-tx3">/</span>
        <h1 className="text-[16px] font-semibold text-cream">{section}</h1>
      </div>
      {children}
    </div>
  );
}
