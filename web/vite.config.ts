import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Proxy the API through the dev server so the browser sees a single origin. That is what
    // makes the gateway's session cookie work without CORS handling and without relaxing
    // SameSite — Open Issue 015 requires httpOnly / Secure / SameSite, and a cross-origin dev
    // setup would force a weaker cookie that then differs from production.
    proxy: {
      "/auth": { target: "http://localhost:8000", changeOrigin: true },
      "/orders": { target: "http://localhost:8000", changeOrigin: true },
      "/symbols": { target: "http://localhost:8000", changeOrigin: true },
      "/portfolio": { target: "http://localhost:8000", changeOrigin: true },
      "/backtests": { target: "http://localhost:8000", changeOrigin: true },
      "/health": { target: "http://localhost:8000", changeOrigin: true },
      // 5.4c connects the WebSocket here; the gateway hands it to fan-out from 5.2b.
      "/stream": { target: "ws://localhost:8000", ws: true },
    },
  },
});
