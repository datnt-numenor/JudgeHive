import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: trình duyệt chỉ nói chuyện với Vite (cùng origin); /api và /ws được chuyển sang backend.
// Không cấu hình CORS (PLAN mục 5). Đổi địa chỉ backend bằng JH_BACKEND khi cần.
const backend = process.env.JH_BACKEND || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: backend },
      "/ws": { target: backend, ws: true },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.js"],
    restoreMocks: true,
  },
});
