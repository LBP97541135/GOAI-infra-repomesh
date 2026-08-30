import fs from "node:fs";
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

function generatedActionToken(): string | null {
  const secretFile = path.resolve(import.meta.dirname, "../.secrets/platform.env");
  if (!fs.existsSync(secretFile)) return null;
  const line = fs
    .readFileSync(secretFile, "utf8")
    .split(/\r?\n/)
    .find((entry) => entry.startsWith("REPOMESH_AGENT_ACTION_TOKEN="));
  return line?.slice("REPOMESH_AGENT_ACTION_TOKEN=".length).trim() || null;
}

export default defineConfig(() => {
  const actionToken = generatedActionToken();
  return {
    plugins: [react(), tailwindcss()],
    // Local platform startup persists the generated action token outside the
    // frontend tree. Inject it at transform time so the console and API always
    // share one credential without asking the operator to copy it. Container
    // builds do not carry this file and keep using VITE_API_TOKEN as before.
    define: actionToken
      ? { "import.meta.env.VITE_API_TOKEN": JSON.stringify(actionToken) }
      : undefined,
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
  };
});
