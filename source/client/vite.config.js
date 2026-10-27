import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: trình duyệt chỉ nói chuyện với Vite (cùng origin); /api và /ws được chuyển sang backend.
// Không cấu hình CORS (PLAN mục 5). Đổi địa chỉ backend bằng JH_BACKEND khi cần.
// 127.0.0.1 thay vì localhost: trên Windows localhost có thể phân giải sang ::1 trước, nơi Docker không lắng nghe.
const backend = process.env.JH_BACKEND || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  // Server chỉ phục vụ `index.html` và `/assets/*` (không có route khớp mọi đường dẫn), nên `dist/` chỉ được có hai thứ đó.
  // Vite chép thư mục `public/` thẳng vào gốc `dist/`, nơi server không phục vụ: tắt hẳn việc chép để không có file "lạc"
  // trong image. Muốn dùng ảnh, font... thì import từ `src/`, Vite sẽ đặt chúng (kèm băm nội dung) vào `assets/`.
  build: { assetsDir: "assets", copyPublicDir: false },
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
