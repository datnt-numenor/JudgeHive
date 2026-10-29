import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeWebSocket } from "../test/fakeWebSocket.js";
import { BACKOFF_MS, SILENCE_MS, backoffDelay, createRealtimeClient, wsUrl } from "./realtime.js";

const URL = "ws://host/ws";
const SCOREBOARD = { type: "SCOREBOARD", version: 1, generated_at: "x", rows: [] };

beforeEach(() => {
  vi.useFakeTimers();
  FakeWebSocket.reset();
});
afterEach(() => vi.useRealTimers());

function setup(extra = {}) {
  const ev = { modes: [], lives: [], messages: [], expired: 0 };
  const client = createRealtimeClient({
    url: URL,
    WebSocketImpl: FakeWebSocket,
    random: () => 0,
    onMode: (m) => ev.modes.push(m),
    onLive: (l) => ev.lives.push(l),
    onMessage: (m) => ev.messages.push(m),
    onExpired: () => {
      ev.expired += 1;
    },
    ...extra,
  });
  client.start();
  return { client, ev };
}

const count = () => FakeWebSocket.instances.length;
const advance = (ms) => vi.advanceTimersByTime(ms);

describe("địa chỉ và backoff", () => {
  it("URL /ws cùng origin: ws cho http, wss cho https", () => {
    expect(wsUrl({ protocol: "http:", host: "localhost:5173" })).toBe("ws://localhost:5173/ws");
    expect(wsUrl({ protocol: "https:", host: "judge.example.org" })).toBe("wss://judge.example.org/ws");
  });

  it("khoảng chờ 1, 2, 4, 8, 10, 10... giây; jitter thêm tối đa 25%", () => {
    expect(BACKOFF_MS).toEqual([1000, 2000, 4000, 8000, 10000]);
    expect([0, 1, 2, 3, 4, 5, 9].map((i) => backoffDelay(i, () => 0))).toEqual([1000, 2000, 4000, 8000, 10000, 10000, 10000]);
    expect([0, 4, 7].map((i) => backoffDelay(i, () => 1))).toEqual([1250, 12500, 12500]);
    expect(backoffDelay(1, () => 0.5)).toBe(2250);
  });
});

describe("kết nối", () => {
  it("start tạo đúng một socket tới URL, gọi start lần nữa không tạo thêm", () => {
    const { client } = setup();
    client.start();
    expect(count()).toBe(1);
    expect(FakeWebSocket.last.url).toBe(URL);
  });

  it("chỉ 'live' khi nhận message hợp lệ đầu tiên; onLive đến trước onMessage của chính message đó", () => {
    const order = [];
    const { ev } = setup({ onLive: () => order.push("live"), onMessage: () => order.push("message") });
    const ws = FakeWebSocket.last;
    ws.open();
    expect(ev.modes).toEqual([]);
    ws.receive("không phải JSON");
    ws.receive("[1,2]");
    ws.receive('{"không có type":1}');
    ws.receive(new ArrayBuffer(4));
    expect(ev.modes).toEqual([]);
    ws.receive(SCOREBOARD);
    expect(ev.modes).toEqual(["live"]);
    expect(order).toEqual(["live", "message"]);
  });

  it("message lạ không làm hỏng kết nối: message hợp lệ sau đó vẫn tới", () => {
    const { ev } = setup();
    const ws = FakeWebSocket.last;
    ws.receive("{hỏng");
    ws.receive({ type: "SCOREBOARD", version: 1, rows: [] });
    ws.receive({ type: "LẠ", x: 1 });
    expect(ev.messages.map((m) => m.type)).toEqual(["SCOREBOARD", "LẠ"]);
    expect(ws.closedWith).toBeNull();
  });

  it("onLive báo reconnect: false lần đầu, true sau khi đã mất kết nối", () => {
    const { ev } = setup();
    FakeWebSocket.last.receive(SCOREBOARD);
    FakeWebSocket.last.serverClose(1006);
    advance(1000);
    FakeWebSocket.last.receive(SCOREBOARD);
    expect(ev.lives).toEqual([{ reconnect: false }, { reconnect: true }]);
    expect(ev.modes).toEqual(["live", "reconnecting", "live"]);
  });
});

describe("mã đóng (README 4.7, 5.3)", () => {
  it("4401: ngừng nối lại, báo hết phiên đúng một lần, không còn timer", () => {
    const { ev } = setup();
    FakeWebSocket.last.receive(SCOREBOARD);
    FakeWebSocket.last.serverClose(4401);
    expect(ev.expired).toBe(1);
    expect(ev.modes.at(-1)).toBe("stopped");
    expect(vi.getTimerCount()).toBe(0);
    advance(120000);
    expect(count()).toBe(1);
  });

  it("4429: chuyển 'limited', không bao giờ tự nối lại, không báo hết phiên", () => {
    const { ev } = setup();
    FakeWebSocket.last.receive(SCOREBOARD);
    FakeWebSocket.last.serverClose(4429);
    expect(ev.modes.at(-1)).toBe("limited");
    expect(ev.expired).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    advance(600000);
    expect(count()).toBe(1);
  });

  it("4429 ngay sau handshake (chưa có message nào) cũng ngừng hẳn", () => {
    const { ev } = setup();
    FakeWebSocket.last.open();
    FakeWebSocket.last.serverClose(4429);
    expect(ev.modes).toEqual(["limited"]);
    advance(600000);
    expect(count()).toBe(1);
  });

  it.each([1000, 1003, 1006, 1008, 1009, 1012, 1013, 4408, 4999])("mã %i: nối lại sau 1 giây", (code) => {
    const { ev } = setup();
    FakeWebSocket.last.receive(SCOREBOARD);
    FakeWebSocket.last.serverClose(code);
    expect(ev.modes.at(-1)).toBe("reconnecting");
    advance(999);
    expect(count()).toBe(1);
    advance(1);
    expect(count()).toBe(2);
    expect(ev.expired).toBe(0);
  });

  it("mất kết nối khi chưa kịp nhận gì (lỗi mạng ngay lúc nối): cũng nối lại", () => {
    const { ev } = setup();
    FakeWebSocket.last.serverClose(1006);
    expect(ev.modes).toEqual(["reconnecting"]);
    advance(1000);
    expect(count()).toBe(2);
  });

  it("new WebSocket ném lỗi (ví dụ URL sai): coi như mất kết nối, thử lại sau", () => {
    let calls = 0;
    class Throwing {
      constructor() {
        calls += 1;
        throw new SyntaxError("bad url");
      }
    }
    const { ev } = setup({ WebSocketImpl: Throwing });
    expect(ev.modes).toEqual(["reconnecting"]);
    advance(1000);
    expect(calls).toBe(2);
  });
});

describe("backoff khi nối lại", () => {
  it("nối hỏng liên tiếp: 1, 2, 4, 8, 10, 10 giây", () => {
    setup();
    const waits = [];
    for (let i = 0; i < 6; i += 1) {
      const before = count();
      FakeWebSocket.last.serverClose(1006);
      let waited = 0;
      while (count() === before && waited < 20000) {
        advance(500);
        waited += 500;
      }
      waits.push(waited);
    }
    expect(waits).toEqual([1000, 2000, 4000, 8000, 10000, 10000]);
  });

  it("jitter cộng thêm vào khoảng chờ", () => {
    setup({ random: () => 1 });
    FakeWebSocket.last.serverClose(1006);
    advance(1249);
    expect(count()).toBe(1);
    advance(1);
    expect(count()).toBe(2);
  });

  it("đặt lại về 1 giây khi nhận message hợp lệ đầu tiên", () => {
    setup();
    FakeWebSocket.last.serverClose(1006); // chờ 1 s
    advance(1000);
    FakeWebSocket.last.serverClose(1006); // chờ 2 s
    advance(2000);
    FakeWebSocket.last.receive(SCOREBOARD);
    FakeWebSocket.last.serverClose(1006);
    advance(999);
    expect(count()).toBe(3);
    advance(1);
    expect(count()).toBe(4);
  });

  it("KHÔNG đặt lại khi chỉ mở được rồi bị đóng 1013 ngay (quá tải): khoảng chờ vẫn tăng", () => {
    setup();
    FakeWebSocket.last.serverClose(1006); // chờ 1 s
    advance(1000);
    FakeWebSocket.last.open();
    FakeWebSocket.last.serverClose(1013); // chờ 2 s, không phải 1 s
    advance(1999);
    expect(count()).toBe(2);
    advance(1);
    expect(count()).toBe(3);
    FakeWebSocket.last.open();
    FakeWebSocket.last.serverClose(4408); // chờ 4 s
    advance(3999);
    expect(count()).toBe(3);
    advance(1);
    expect(count()).toBe(4);
  });
});

describe("PING / PONG", () => {
  it("trả PONG lặp lại đúng seq; PING không được chuyển cho onMessage", () => {
    const { ev } = setup();
    const ws = FakeWebSocket.last;
    ws.receive(SCOREBOARD);
    ws.receive({ type: "PING", seq: 7 });
    ws.receive({ type: "PING", seq: 0 });
    expect(ws.sent).toEqual([
      { type: "PONG", seq: 7 },
      { type: "PONG", seq: 0 },
    ]);
    expect(ev.messages.map((m) => m.type)).toEqual(["SCOREBOARD"]);
  });

  it.each([["thiếu", {}], ["chuỗi", { seq: "7" }], ["số thực", { seq: 7.5 }], ["null", { seq: null }]])("seq %s: không trả lời", (_, extra) => {
    setup();
    const ws = FakeWebSocket.last;
    ws.receive(SCOREBOARD);
    ws.receive({ type: "PING", ...extra });
    expect(ws.sent).toEqual([]);
  });

  it("mọi frame client gửi lên đều là PONG (không có gì khác)", () => {
    setup();
    const ws = FakeWebSocket.last;
    ws.receive(SCOREBOARD);
    ws.receive({ type: "STATUS", submission_id: 1, state: "queued" });
    ws.receive({ type: "PING", seq: 1 });
    ws.receive({ type: "PING", seq: 2 });
    expect(ws.sent.every((f) => f.type === "PONG")).toBe(true);
  });
});

describe("quá 60 giây im lặng", () => {
  it("59,999 giây thì chưa đóng; 60 giây thì đóng bằng 4000 và nối lại theo backoff", () => {
    const { ev } = setup();
    const ws = FakeWebSocket.last;
    ws.receive(SCOREBOARD);
    advance(SILENCE_MS - 1);
    expect(ws.closedWith).toBeNull();
    advance(1);
    expect(ws.closedWith).toBe(4000);
    expect(ev.modes.at(-1)).toBe("reconnecting");
    expect(count()).toBe(1);
    advance(1000);
    expect(count()).toBe(2);
  });

  it("gỡ handler trước khi đóng: sự kiện đóng của socket cũ đến muộn không kích hoạt nối lại lần hai", () => {
    setup();
    const old = FakeWebSocket.last;
    old.receive(SCOREBOARD);
    advance(SILENCE_MS);
    expect(old.onclose).toBeNull();
    expect(old.onmessage).toBeNull();
    old.serverClose(1006); // đến muộn
    advance(1000);
    expect(count()).toBe(2);
    advance(120000 - 1000);
    // kết nối mới cũng im: chỉ có các lần nối lại theo lịch, không có lần dư do sự kiện đóng cũ
    expect(vi.getTimerCount()).toBeLessThanOrEqual(1);
  });

  it("mọi type đều đặt lại đồng hồ, kể cả PING và frame bị bỏ qua", () => {
    setup();
    const ws = FakeWebSocket.last;
    ws.receive(SCOREBOARD);
    for (const frame of [{ type: "PING", seq: 1 }, { type: "STATUS", submission_id: 1, state: "queued" }, { type: "LẠ" }, "{hỏng"]) {
      advance(SILENCE_MS - 1);
      ws.receive(frame);
    }
    advance(SILENCE_MS - 1);
    expect(ws.closedWith).toBeNull();
    advance(1);
    expect(ws.closedWith).toBe(4000);
  });

  it("socket không bao giờ nhận được gì cũng bị bỏ sau 60 giây", () => {
    setup();
    const ws = FakeWebSocket.last;
    advance(SILENCE_MS);
    expect(ws.closedWith).toBe(4000);
  });
});

describe("stop", () => {
  it("đóng bằng 1000, gỡ handler, không nối lại; sự kiện đóng đến muộn không làm gì", () => {
    const { client, ev } = setup();
    const ws = FakeWebSocket.last;
    ws.receive(SCOREBOARD);
    client.stop();
    expect(ws.closedWith).toBe(1000);
    expect(ws.onclose).toBeNull();
    ws.serverClose(4401); // đến muộn
    expect(ev.expired).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    advance(120000);
    expect(count()).toBe(1);
  });

  it("stop trong lúc chờ nối lại hủy lịch nối", () => {
    const { client } = setup();
    FakeWebSocket.last.serverClose(1006);
    client.stop();
    advance(60000);
    expect(count()).toBe(1);
  });

  it("stop khi socket còn đang nối vẫn đóng được", () => {
    const { client } = setup();
    client.stop();
    expect(FakeWebSocket.last.closedWith).toBe(1000);
    client.start();
    expect(count()).toBe(1);
  });
});
