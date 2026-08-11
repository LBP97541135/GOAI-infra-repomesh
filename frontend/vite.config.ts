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
    proxy: {
      "/api": { target: "http://127.0.0.1:8100", changeOrigin: true },
    },
  },
});
