import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "./auth.jsx";
import { createRealtimeClient, wsUrl } from "./lib/realtime.js";

const RealtimeContext = createContext(null);

export function useRealtime() {
  const value = useContext(RealtimeContext);
  if (!value) throw new Error("useRealtime phải nằm trong RealtimeProvider");
  return value;
}

/** Chế độ mà trang phải tự gọi lại REST định kỳ (README 4.7): đang nối lại hoặc bị 4429. */
export function isPollingMode(mode) {
  return mode === "reconnecting" || mode === "limited";
}

/** Gọi `handler(msg)` cho mỗi message `STATUS` đến. `handler` luôn là bản mới nhất, không cần bọc `useCallback`. */
export function useStatusEvents(handler) {
  const { subscribeStatus } = useRealtime();
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => subscribeStatus((msg) => ref.current(msg)), [subscribeStatus]);
}

function validScoreboard(msg) {
  return Number.isInteger(msg?.version) && Array.isArray(msg?.rows);
}

/**
 * Giữ MỘT kết nối `/ws` cho cả ứng dụng: mở khi đã đăng nhập, đóng (1000, không nối lại) khi đăng xuất.
 * Phải nằm trong AuthProvider và Router.
 */
export function RealtimeProvider({ children }) {
  const { status, user, expire } = useAuth();
  const [mode, setMode] = useState("connecting");
  const [scoreboard, setScoreboard] = useState(null);
  const [resyncKey, setResyncKey] = useState(0);
  const listeners = useRef(new Set());
  // Mốc `version` (README 4.7 mục 4) chỉ có nghĩa trong một lần chạy của server. `epoch` tăng mỗi khi `mode` đổi: phản
  // hồi REST phát đi ở epoch cũ bị bỏ, để một phản hồi đến muộn không chặn nhầm bản SCOREBOARD mới có version nhỏ hơn.
  const floor = useRef({ epoch: 0, version: null });

  const bumpEpoch = () => {
    floor.current = { epoch: floor.current.epoch + 1, version: null };
  };

  const acceptScoreboard = useCallback((data, epoch) => {
    if (!validScoreboard(data)) return;
    const f = floor.current;
    if (epoch !== undefined && epoch !== f.epoch) return;
    if (f.version !== null && data.version <= f.version) return;
    f.version = data.version;
    setScoreboard(data);
  }, []);

  const getEpoch = useCallback(() => floor.current.epoch, []);

  const subscribeStatus = useCallback((fn) => {
    listeners.current.add(fn);
    return () => listeners.current.delete(fn);
  }, []);

  // `expire` đổi danh tính mỗi lần chuyển trang (nó dùng `navigate`): giữ trong ref để không đóng/mở lại kết nối.
  const expireRef = useRef(expire);
  expireRef.current = expire;

  const userId = user?.id;
  useEffect(() => {
    if (status !== "user") return undefined;
    setMode("connecting");
    const client = createRealtimeClient({
      url: wsUrl(),
      onMode: (next) => {
        bumpEpoch();
        setMode(next);
      },
      onLive: ({ reconnect }) => {
        if (reconnect) setResyncKey((k) => k + 1);
      },
      onMessage: (msg) => {
        if (msg.type === "SCOREBOARD") acceptScoreboard(msg);
        else if (msg.type === "STATUS" && Number.isInteger(msg.submission_id)) {
          for (const fn of [...listeners.current]) fn(msg);
        }
      },
      onExpired: () => expireRef.current(),
    });
    client.start();
    return () => {
      client.stop();
      bumpEpoch();
    };
  }, [status, userId, acceptScoreboard]);

  // Đăng xuất: bản bảng xếp hạng cũ không được còn cho người dùng kế tiếp.
  useEffect(() => {
    if (status !== "user") setScoreboard(null);
  }, [status]);

  const value = useMemo(
    () => ({ mode, scoreboard, resyncKey, subscribeStatus, acceptScoreboard, getEpoch }),
    [mode, scoreboard, resyncKey, subscribeStatus, acceptScoreboard, getEpoch],
  );
  return <RealtimeContext.Provider value={value}>{children}</RealtimeContext.Provider>;
}
