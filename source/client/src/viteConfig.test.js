import { describe, expect, it } from "vitest";
import config from "../vite.config.js";

// README 4.1: server so `Origin` với `Host`; proxy dev của Vite phải giữ nguyên `Host` (changeOrigin: false) và chuyển WebSocket.
describe("proxy của Vite dev server", () => {
  const { proxy } = config.server;

  it("/ws chuyển WebSocket (ws: true) và giữ nguyên Host (changeOrigin: false)", () => {
    expect(proxy["/ws"].ws).toBe(true);
    expect(proxy["/ws"].changeOrigin).toBe(false);
  });

  it("/api và /ws cùng trỏ về backend, không có CORS", () => {
    expect(proxy["/api"].target).toBe(proxy["/ws"].target);
    expect(JSON.stringify(config)).not.toMatch(/cors/i);
  });
});
