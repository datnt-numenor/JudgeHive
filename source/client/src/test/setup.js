import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { FakeWebSocket } from "./fakeWebSocket.js";

// Mọi test dùng WebSocket giả, không bao giờ nối mạng thật. Mặc định socket đứng ở trạng thái "đang nối" (không mở, không
// đóng), nên trang chạy như lúc chưa có cập nhật tức thời; test nào cần thì điều khiển `FakeWebSocket.last`.
beforeEach(() => {
  FakeWebSocket.reset();
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => cleanup());
