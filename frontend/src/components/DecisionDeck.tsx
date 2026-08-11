import type { Decision, DecisionKind } from "../types";

/** 决策夹：VARIADEX 档案柜隐喻。待决策事项是牛皮纸文件夹，堆叠收在输入框后方，
 *  彩色标签错位露头；点后排置顶、可整堆收起、处理完即消化移除。deck 末位为最前。 */

const KIND_LABEL: Record<DecisionKind, string> = { approve: "审批", watch: "关注" };
const KIND_TAB: Record<DecisionKind, string> = {
  approve: "bg-olive",
  watch: "bg-amber",
};

const folderFace = "border border-[#8a7648] bg-kraft";

export function DecisionDeck({
  deck,
  hidden,
  variant = "docked",
  onToggleHidden,
  onBringToFront,
  onAction,
}: {
  deck: Decision[];
  hidden: boolean;
  /** docked：v1 交付控制台，柜沿升起、底缘藏于薄条之后；
   *  inline：v2 issue 详情页，文档流中的一个区块。视觉语言（牛皮纸档案柜 +
   *  彩色标签错位露头）两者一致，只有定位与投影方向不同。 */
  variant?: "docked" | "inline";
  onToggleHidden: () => void;
  onBringToFront: (id: string) => void;
  onAction: (decision: Decision, actionIdx: number) => void;
}) {
  if (deck.length === 0) return null;

  const docked = variant === "docked";
  const shell = docked ? "relative z-[4] -mb-3 flex-none px-[22px]" : "relative";
  // docked 从柜沿升起故投影朝上；inline 在文档流里，投影朝下才合物理
  const shadow = docked ? "shadow-[0_-4px_14px_rgba(0,0,0,0.35)]" : "shadow-[0_6px_16px_rgba(0,0,0,0.3)]";

  const stackBar = (
    <button
      className={`inline-flex items-center gap-[7px] rounded-t-[3px] ${folderFace} border-b-0 ml-1 px-3.5 pt-[5px] pb-[9px] font-mono text-[12px] font-bold tracking-[0.04em] text-[#3a2f18] hover:bg-[#e6d4a8]`}
      onClick={onToggleHidden}
    >
      <span>▤</span> {deck.length} 项待决策 <span className="font-medium text-[#7a6a42]">{hidden ? "▴" : "▾ 收起"}</span>
    </button>
  );

  return (
    <div className={shell}>
      {stackBar}
      {!hidden && (
        <div className="relative">
          {deck.map((dc, i) => {
            const front = i === deck.length - 1;
            const tabX = 18 + (deck.length - 1 - i) * 118;
            return (
              <div key={dc.id} className={`relative pt-[26px] ${i === 0 ? "" : "-mt-1.5"}`}>
                <span
                  className={`absolute top-0 z-[2] rounded-t-hard px-[18px] pt-1 pb-2.5 font-mono text-[11.5px] font-extrabold tracking-[0.14em] text-[#16120a] ${KIND_TAB[dc.kind]}`}
                  style={{ left: tabX }}
                >
                  {KIND_LABEL[dc.kind]}
                </span>
                {front ? (
                  <div
                    className={`relative z-[3] rounded-t-[3px] ${folderFace} px-[18px] pt-3 pb-[26px] ${shadow}`}
                  >
                    <b className="block text-[13.5px] text-paper-ink">{dc.title}</b>
                    <p className="mt-1 max-w-[640px] text-[12.5px] text-[#57492c]">{dc.body}</p>
                    <div className="mt-2.5 flex flex-wrap gap-2">
                      {dc.actions.map((a, j) => (
                        <button
                          key={a}
                          className={
                            j === 0
                              ? "rounded-hard border border-[#17130a] bg-[#17130a] px-3.5 py-1.5 text-[12.5px] font-bold text-cream"
                              : "rounded-hard border border-[#8a7648] bg-transparent px-3.5 py-1.5 text-[12.5px] text-[#3a2f18] hover:border-[#17130a]"
                          }
                          onClick={() => onAction(dc, j)}
                        >
                          {a}
                        </button>
                      ))}
                    </div>
                  </div>
                ) : (
                  <button
                    className={`relative z-[3] flex w-full items-baseline gap-2.5 rounded-t-[3px] ${folderFace} px-4 pt-[9px] pb-3.5 text-left ${shadow} hover:bg-[#e6d4a8]`}
                    onClick={() => onBringToFront(dc.id)}
                  >
                    <b className="text-[12.5px] text-[#3a2f18]">{dc.title}</b>
                    <span className="ml-auto font-mono text-[10.5px] text-[#8a7a52]">点击置顶</span>
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
