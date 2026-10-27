// Theo dõi một bài nộp bằng cách gọi lại API (README 3.3: khoảng 1 giây một lần cho tới khi `state` là `judged`).
//  - Khoảng nghỉ tính từ lúc nhận phản hồi trước (chuỗi setTimeout, không bao giờ có hai request chồng nhau).
//  - 401 UNAUTHENTICATED: dừng hẳn (đưa về trang đăng nhập là việc của handler chung trong `api.js`).
//  - Lỗi tạm thời (mạng, hết giờ chờ, 5xx, 429): giãn khoảng nghỉ gấp đôi mỗi lần lỗi liên tiếp, tối đa `maxIntervalMs`.
//  - Lỗi khác (ví dụ 404): dừng.
//  - Tab ẩn: không gọi; tab hiện lại thì gọi ngay.
import { ApiError, isTransient } from "./api.js";

export const POLL_INTERVAL_MS = 1000;
export const POLL_MAX_INTERVAL_MS = 10000; // [ĐỀ XUẤT]

/** Trả hàm `stop()`. `fetchOnce(signal)` trả dữ liệu; `isDone(dữ liệu)` true thì tự dừng sau khi gọi `onResult`. */
export function startPolling({
  fetchOnce,
  onResult,
  onError = () => {},
  isDone,
  intervalMs = POLL_INTERVAL_MS,
  maxIntervalMs = POLL_MAX_INTERVAL_MS,
  doc = document,
}) {
  let stopped = false;
  let paused = false;
  let inFlight = false;
  let timer = null;
  let failures = 0;
  let controller = null;

  const clear = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  };

  const schedule = (ms) => {
    if (stopped) return;
    if (doc.hidden) {
      paused = true;
      return;
    }
    timer = setTimeout(tick, ms);
  };

  async function tick() {
    timer = null;
    if (stopped || inFlight) return;
    if (doc.hidden) {
      paused = true;
      return;
    }
    inFlight = true;
    controller = new AbortController();
    let data;
    let error = null;
    try {
      data = await fetchOnce(controller.signal);
    } catch (e) {
      error = e;
    }
    inFlight = false;
    if (stopped) return;
    if (error) {
      const unauth = error instanceof ApiError && error.status === 401 && error.code === "UNAUTHENTICATED";
      if (unauth || !isTransient(error)) {
        stop();
        onError(error, { fatal: true });
        return;
      }
      failures += 1;
      onError(error, { fatal: false });
      schedule(Math.min(maxIntervalMs, intervalMs * 2 ** failures));
      return;
    }
    failures = 0;
    onResult(data);
    if (isDone(data)) {
      stop();
      return;
    }
    schedule(intervalMs);
  }

  const onVisibility = () => {
    if (stopped) return;
    if (doc.hidden) {
      clear();
      paused = true;
      return;
    }
    if (paused) {
      paused = false;
      clear();
      tick();
    }
  };

  function stop() {
    if (stopped) return;
    stopped = true;
    clear();
    controller?.abort();
    doc.removeEventListener("visibilitychange", onVisibility);
  }

  doc.addEventListener("visibilitychange", onVisibility);
  tick();
  return stop;
}
