// Kết nối WebSocket `/ws` (README mục 4), viết thành máy trạng thái thuần JS để test không cần React.
//
// `mode`:
//  - "connecting": lần nối đầu chưa nhận được gì.
//  - "live": kết nối hiện tại đã nhận ít nhất một message hợp lệ (SCOREBOARD luôn đến ngay khi nối, mục 4.1).
//  - "reconnecting": mất kết nối, đang chờ hoặc đang nối lại (README 4.7 mục 2).
//  - "limited": đóng bằng 4429, không bao giờ tự nối lại (4.7 mục 1).
//  - "stopped": đóng bằng 4401 (đã gọi `onExpired`), hoặc đã gọi `stop()`.
//
// Quy tắc (README 4.7):
//  - 4401: ngừng nối lại. 4429: ngừng nối lại. Mọi mã khác (1006, 1008, 1012, 1013, 4408...): nối lại sau 1, 2, 4, 8,
//    10, 10... giây cộng jitter.
//  - Khoảng chờ đặt lại về 1 giây khi nhận message hợp lệ đầu tiên của một kết nối (không phải lúc `onopen`), để lỗi
//    1013/4408 đến ngay sau handshake không làm mọi client nối lại dồn dập mỗi giây.
//  - Quá 60 giây không nhận được frame nào: tự đóng bằng 4000 (gỡ handler trước, mục 4.7 mục 5) rồi nối lại.
//  - PING thì trả PONG lặp lại đúng `seq`. Frame lạ (nhị phân, không phải JSON object, thiếu `type`) bị bỏ qua.

export const BACKOFF_MS = [1000, 2000, 4000, 8000, 10000];
export const JITTER_FRACTION = 0.25; // [ĐỀ XUẤT]: jitter ngẫu nhiên từ 0 đến 25% khoảng chờ
export const SILENCE_MS = 60000;

export const CLOSE_NORMAL = 1000;
export const CLOSE_REPLACED = 4000; // mã của client, server không bao giờ gửi
export const CLOSE_UNAUTHENTICATED = 4401;
export const CLOSE_TOO_MANY_CONNECTIONS = 4429;

/** Khoảng chờ (ms) trước lần nối lại thứ `attempt` (từ 0). */
export function backoffDelay(attempt, random = Math.random) {
  const base = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
  return Math.round(base * (1 + random() * JITTER_FRACTION));
}

/** URL `/ws` cùng origin với trang. */
export function wsUrl(loc = globalThis.location) {
  return `${loc.protocol === "https:" ? "wss" : "ws"}://${loc.host}/ws`;
}

function parseFrame(data) {
  if (typeof data !== "string") return null;
  let msg;
  try {
    msg = JSON.parse(data);
  } catch {
    return null;
  }
  return msg !== null && typeof msg === "object" && !Array.isArray(msg) && typeof msg.type === "string" ? msg : null;
}

/**
 * Trả `{start, stop}`.
 *  - `onMode(mode)`: mỗi khi `mode` đổi.
 *  - `onLive({reconnect})`: kết nối mới nhận message hợp lệ đầu tiên; gọi TRƯỚC `onMessage` của chính message đó.
 *    `reconnect` true nếu trước đó đã có lần mất kết nối.
 *  - `onMessage(msg)`: mọi message hợp lệ trừ PING (đã trả lời xong).
 *  - `onExpired()`: nhận 4401.
 */
export function createRealtimeClient({
  url,
  onMode = () => {},
  onLive = () => {},
  onMessage = () => {},
  onExpired = () => {},
  WebSocketImpl,
  random = Math.random,
}) {
  let ws = null;
  let mode = "connecting";
  let attempt = 0;
  let lost = false;
  let stopped = false;
  let reconnectTimer = null;
  let silenceTimer = null;

  const setMode = (next) => {
    if (next === mode) return;
    mode = next;
    onMode(next);
  };

  const clearSilence = () => {
    if (silenceTimer !== null) clearTimeout(silenceTimer);
    silenceTimer = null;
  };

  const detach = (socket) => {
    socket.onopen = null;
    socket.onmessage = null;
    socket.onclose = null;
    socket.onerror = null;
  };

  const armSilence = () => {
    clearSilence();
    silenceTimer = setTimeout(onSilence, SILENCE_MS);
  };

  function onSilence() {
    silenceTimer = null;
    const socket = ws;
    if (!socket || stopped) return;
    ws = null;
    detach(socket);
    try {
      socket.close(CLOSE_REPLACED);
    } catch {
      /* đã đóng */
    }
    handleClose(CLOSE_REPLACED);
  }

  function handleClose(code) {
    if (stopped) return;
    if (code === CLOSE_UNAUTHENTICATED) {
      setMode("stopped");
      onExpired();
      return;
    }
    if (code === CLOSE_TOO_MANY_CONNECTIONS) {
      setMode("limited");
      return;
    }
    lost = true;
    setMode("reconnecting");
    reconnectTimer = setTimeout(connect, backoffDelay(attempt, random));
    attempt += 1;
  }

  function connect() {
    reconnectTimer = null;
    if (stopped) return;
    const Impl = WebSocketImpl ?? globalThis.WebSocket;
    let socket;
    try {
      socket = new Impl(url);
    } catch {
      handleClose(1006);
      return;
    }
    ws = socket;
    let live = false;
    armSilence();

    socket.onmessage = (event) => {
      if (ws !== socket) return;
      armSilence(); // mọi frame, kể cả frame bị bỏ qua, là bằng chứng kết nối còn sống
      const msg = parseFrame(event.data);
      if (!msg) return;
      if (!live) {
        live = true;
        attempt = 0;
        const reconnect = lost;
        setMode("live");
        onLive({ reconnect });
      }
      if (msg.type === "PING") {
        if (Number.isInteger(msg.seq) && socket.readyState === 1) {
          try {
            socket.send(JSON.stringify({ type: "PONG", seq: msg.seq }));
          } catch {
            /* đang đóng: sự kiện đóng sẽ tới */
          }
        }
        return;
      }
      onMessage(msg);
    };
    socket.onclose = (event) => {
      if (ws !== socket) return;
      ws = null;
      clearSilence();
      handleClose(event?.code);
    };
  }

  return {
    start() {
      if (!stopped && ws === null && reconnectTimer === null) connect();
    },
    /** Đóng hẳn bằng 1000 và không bao giờ nối lại. Gỡ handler trước nên sự kiện đóng đến muộn không làm gì. */
    stop() {
      stopped = true;
      clearSilence();
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      reconnectTimer = null;
      const socket = ws;
      ws = null;
      if (socket) {
        detach(socket);
        try {
          socket.close(CLOSE_NORMAL);
        } catch {
          /* đã đóng */
        }
      }
    },
  };
}
