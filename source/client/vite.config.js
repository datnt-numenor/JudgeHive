import { defineConfig } from "vite";
export default defineConfig({ server: { proxy: {
  "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
  "/ws": { target: "ws://127.0.0.1:8000", ws: true, changeOrigin: false },
}}});
