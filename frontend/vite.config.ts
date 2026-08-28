import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    port: 5280,
    strictPort: true,
    // 联调：同源代理到读模型 API 专用实例（后端未开 CORS）；
    // VITE_API_BASE 留空即走同源。生产部署由网关同源托管，无此代理。
    //
    // 默认打 8000——容器内、接入 agentteams-net 的全执行面 API（scripts/
    // start-platform.sh 起的那套，能 materialize / 派单 / 真跑 agent）。
    // 目标可由 REPOMESH_API_TARGET 覆盖：dev-up.sh 起的“计划态”后端在 8100，
    // 它自己会把这个变量指回 8100，所以那条路不受此默认值影响。**代理而非
    // VITE_API_BASE 跨源**是必须的——登录会话是 httpOnly cookie，跨源要 CORS
    // 配 credentials，同源代理下什么都不用配。
    proxy: {
      "/api": {
        target: process.env.REPOMESH_API_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
