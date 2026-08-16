import { Component, type ErrorInfo, type ReactNode } from "react";
import { errText } from "../display";

/** 区块级错误边界（A-4 兜底修）。
 *
 *  病灶不是某一个 `.match`：**任何**渲染期异常都会顺着 React 的错误传播一路上抛，
 *  没有边界就整棵树卸载——一条畸形数据把整页打成白屏，用户连「重试物化」都够不着。
 *  这与「诚实降级」是同一条红线的两面：坏掉的那块要说自己坏了，好的那些块要照常给。
 *
 *  用法：把详情页的每个区块各自包一层，`block` 给这一块的名字。粒度就是版面上
 *  肉眼可分的那些块——粒度再粗就会连坐，再细则每个 span 都要包一层。
 *
 *  **不是 try/catch 的替代品**：能预见的空值该在消费处如实渲染（见 RoundsPanel 的
 *  「数据不完整」），边界只接住没预见到的那些。边界频繁触发说明上游漏判，不是它的功劳。
 *
 *  零新颜色语义：salmon 在本仓既有语义就是失败（ErrorPanel / 八相 failed 同款）。
 *
 *  只能是 class——`getDerivedStateFromError` / `componentDidCatch` 至今没有 hook 版，
 *  这也是全仓唯一一个 class 组件，不引第三方库。 */

interface Props {
  /** 区块名，出现在降级块的标题里（「轮次」「房间」…） */
  block: string;
  /** 变化即复位。传 issue id：容器不随 issue 切换重挂载，不复位的话
   *  A 单的区块错误会挂在 B 单头上，看着像 B 也坏了。 */
  resetKey?: string;
  children: ReactNode;
}

interface State {
  message: string | null;
  /** 上一次渲染时看到的 `resetKey`，用于识别「换了一单」——存进 state 而不是在
   *  `componentDidUpdate` 里 setState，后者会多触发一轮 render（oxlint 也会报）。 */
  seenKey?: string;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { message: null };

  static getDerivedStateFromError(error: unknown): Partial<State> {
    return { message: errText(error) };
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    // 换 issue 即复位。错误刚被捕获的那一轮 key 没变，返回 null，message 保留。
    if (props.resetKey !== state.seenKey) return { message: null, seenKey: props.resetKey };
    return null;
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    // 控制台留全栈：降级块只给 message，排查要的组件栈在这里
    console.error(`[ErrorBoundary] 区块「${this.props.block}」渲染失败`, error, info.componentStack);
  }

  render() {
    if (this.state.message === null) return this.props.children;
    return (
      <div className="mt-3 rounded-hard border border-salmon/60 bg-salmon/10 px-3.5 py-3">
        <div className="eyebrow mb-1 text-salmon">{this.props.block}</div>
        <p className="text-[12px] text-salmon">本区块渲染失败：{this.state.message}</p>
        <p className="mt-1 text-[11px] text-tx3">
          只有这一块塌了，本页其余区块的数据照常呈现。原因多半是该 issue 的某条数据形状与契约不符
          （完整堆栈在浏览器控制台）。
        </p>
      </div>
    );
  }
}
