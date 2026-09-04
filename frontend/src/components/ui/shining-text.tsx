import { motion } from "motion/react";

/** ShiningText：流光扫过文字（shadcn 生态组件，2026-09-04 集成）。
 *
 *  用途：标示「正在运行」的指令/步骤——发现链在途步骤的状态字与
 *  「XX 执行中」占位行。深色底上呈现 #404040 → #fff → #404040 的
 *  2s 线性流光循环，与本控制台「琥珀 = 运行中」的语义互补。
 *
 *  对原版的适配（其余视觉与动效参数原样保留）：
 *   - 去掉 `"use client"`——本仓库是 Vite，不是 Next.js；
 *   - `motion.h1` 改 `motion.span`（挂载点在 <p>/行内，h1 会破坏文档语义）；
 *   - `font-regular` 非 Tailwind 类名，移除；
 *   - 仓库未配 `@/` 别名，消费方按相对路径引入。
 *
 *  @param text      要展示的文字（如「需求分析执行中…」）
 *  @param className 透传字号/边距等覆盖（默认 text-base，行内场景请覆盖） */
export function ShiningText({ text, className = "" }: { text: string; className?: string }) {
  return (
    <motion.span
      className={`inline-block bg-[linear-gradient(110deg,#404040,35%,#fff,50%,#404040,75%,#404040)] bg-[length:200%_100%] bg-clip-text text-base text-transparent ${className}`}
      initial={{ backgroundPosition: "200% 0" }}
      animate={{ backgroundPosition: "-200% 0" }}
      transition={{
        repeat: Infinity,
        duration: 2,
        ease: "linear",
      }}
    >
      {text}
    </motion.span>
  );
}
