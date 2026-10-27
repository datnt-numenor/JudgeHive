import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, NetworkError } from "./api.js";
import { startPolling } from "./poll.js";

class FakeDoc extends EventTarget {
  hidden = false;
  setHidden(value) {
    this.hidden = value;
    this.dispatchEvent(new Event("visibilitychange"));
  }
}

const unauth = () => new ApiError(401, "UNAUTHENTICATED", "m");
const sub = (state) => ({ submission_id: 1, state });

let doc;
beforeEach(() => {
  vi.useFakeTimers();
  doc = new FakeDoc();
});
afterEach(() => vi.useRealTimers());

function start(fetchOnce, extra = {}) {
  const onResult = vi.fn();
  const onError = vi.fn();
  const stop = startPolling({ fetchOnce, onResult, onError, isDone: (d) => d.state === "judged", doc, ...extra });
  return { stop, onResult, onError };
}

const tick = (ms) => vi.advanceTimersByTimeAsync(ms);

describe("startPolling", () => {
  it("gọi ngay, rồi 1000 ms một lần, và dừng khi state là judged", async () => {
    const replies = [sub("queued"), sub("judging"), sub("judged")];
    const fetchOnce = vi.fn(async () => replies.shift());
    const { onResult } = start(fetchOnce);
    await tick(0);
    expect(fetchOnce).toHaveBeenCalledTimes(1);
    await tick(999);
    expect(fetchOnce).toHaveBeenCalledTimes(1);
    await tick(1);
    expect(fetchOnce).toHaveBeenCalledTimes(2);
    await tick(1000);
    expect(fetchOnce).toHaveBeenCalledTimes(3);
    expect(onResult).toHaveBeenCalledTimes(3);
    await tick(10000);
    expect(fetchOnce).toHaveBeenCalledTimes(3);
  });

  it("khoảng nghỉ tính từ lúc nhận phản hồi, không chồng request", async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    const fetchOnce = vi.fn(async () => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await new Promise((r) => setTimeout(r, 1500)); // máy chủ chậm hơn khoảng poll
      inFlight -= 1;
      return sub("queued");
    });
    start(fetchOnce);
    await tick(0);
    expect(fetchOnce).toHaveBeenCalledTimes(1);
    await tick(1500 + 999);
    expect(fetchOnce).toHaveBeenCalledTimes(1);
    await tick(1);
    expect(fetchOnce).toHaveBeenCalledTimes(2);
    await tick(10000);
    expect(maxInFlight).toBe(1);
  });

  it("401 UNAUTHENTICATED: dừng, báo lỗi nghiêm trọng, không gọi lại", async () => {
    const fetchOnce = vi.fn(async () => {
      throw unauth();
    });
    const { onError } = start(fetchOnce);
    await tick(20000);
    expect(fetchOnce).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledWith(expect.any(ApiError), { fatal: true });
  });

  it("404 (lỗi không tạm thời): dừng", async () => {
    const fetchOnce = vi.fn(async () => {
      throw new ApiError(404, "SUBMISSION_NOT_FOUND", "m");
    });
    const { onError } = start(fetchOnce);
    await tick(20000);
    expect(fetchOnce).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][1]).toEqual({ fatal: true });
  });

  it("lỗi mạng: giãn khoảng nghỉ gấp đôi mỗi lần, tối đa 10 s, và về 1 s khi thành công", async () => {
    const times = [];
    let fail = true;
    const fetchOnce = vi.fn(async () => {
      times.push(Date.now());
      if (fail) throw new NetworkError("network");
      return sub("queued");
    });
    const { onError } = start(fetchOnce);
    await tick(60000);
    const gaps = times.slice(1).map((t, i) => t - times[i]);
    expect(gaps.slice(0, 5)).toEqual([2000, 4000, 8000, 10000, 10000]);
    expect(onError.mock.calls.every(([, info]) => info.fatal === false)).toBe(true);

    fail = false;
    await tick(10000); // lần tiếp theo thành công
    const before = times.length;
    await tick(1000);
    expect(times.length).toBe(before + 1); // đã về khoảng 1 s
  });

  it("tab ẩn thì không gọi; hiện lại thì gọi ngay", async () => {
    const fetchOnce = vi.fn(async () => sub("queued"));
    start(fetchOnce);
    await tick(0);
    expect(fetchOnce).toHaveBeenCalledTimes(1);
    doc.setHidden(true);
    await tick(30000);
    expect(fetchOnce).toHaveBeenCalledTimes(1);
    doc.setHidden(false);
    await tick(0);
    expect(fetchOnce).toHaveBeenCalledTimes(2);
    await tick(1000);
    expect(fetchOnce).toHaveBeenCalledTimes(3);
  });

  it("tab ẩn lúc đang chờ phản hồi: không lên lịch tiếp cho tới khi hiện lại, và không chồng request", async () => {
    let release;
    const fetchOnce = vi.fn(() => new Promise((resolve) => (release = resolve)));
    start(fetchOnce);
    await tick(0);
    doc.setHidden(true);
    doc.setHidden(false); // hiện lại khi lượt đầu chưa xong
    await tick(0);
    expect(fetchOnce).toHaveBeenCalledTimes(1);
    release(sub("queued"));
    await tick(0);
    expect(fetchOnce).toHaveBeenCalledTimes(1);
    await tick(1000);
    expect(fetchOnce).toHaveBeenCalledTimes(2);
  });

  it("stop() hủy request đang bay và không gọi nữa", async () => {
    let signal;
    const fetchOnce = vi.fn((s) => {
      signal = s;
      return new Promise(() => {});
    });
    const { stop } = start(fetchOnce);
    await tick(0);
    stop();
    expect(signal.aborted).toBe(true);
    await tick(20000);
    expect(fetchOnce).toHaveBeenCalledTimes(1);
  });

  it("sau stop() phản hồi đến muộn không gọi onResult", async () => {
    let release;
    const fetchOnce = vi.fn(() => new Promise((resolve) => (release = resolve)));
    const { stop, onResult } = start(fetchOnce);
    await tick(0);
    stop();
    release(sub("queued"));
    await tick(5000);
    expect(onResult).not.toHaveBeenCalled();
    expect(fetchOnce).toHaveBeenCalledTimes(1);
  });
});
