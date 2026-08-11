/** 未施工页面的诚实占位（仓库/团队/智能体/设置 = CONS-44，issue 详情 = CONS-42）。
 *  不放假数据、不装满界面：说明该页归属的工作项与依赖的后端端点。 */
export function PlaceholderPage({
  title,
  workItem,
  depends,
  note,
}: {
  title: string;
  workItem: string;
  depends: string;
  note?: string;
}) {
  return (
    <div className="max-w-[860px]">
      <div className="flex items-baseline gap-3 border-b border-line pb-3">
        <h1 className="text-[16px] font-semibold text-cream">{title}</h1>
        <span className="microlabel">{workItem} · 待施工</span>
      </div>
      <div className="mt-5 rounded-hard border border-line bg-panel px-4 py-3.5">
        <div className="eyebrow mb-1.5">尚未接入</div>
        <p className="text-[12.5px] text-tx2">
          本页归属工作项 <b className="font-mono text-tx">{workItem}</b>，依赖 {depends}。
          {note ? ` ${note}` : ""}
        </p>
      </div>
    </div>
  );
}
