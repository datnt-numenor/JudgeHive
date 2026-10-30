import { useEffect } from "react";
import { useAuth } from "../auth.jsx";
import { api } from "../lib/api.js";
import { formatTime } from "../lib/format.js";
import { startPolling } from "../lib/poll.js";
import { isPollingMode, useRealtime } from "../realtime.jsx";

const FALLBACK_POLL_MS = 5000; // [ĐỀ XUẤT]: khoảng gọi lại `GET /api/scoreboard` khi không có WebSocket

export default function ScoreboardPage() {
  const { user } = useAuth();
  const { mode, scoreboard, acceptScoreboard, getEpoch } = useRealtime();
  const polling = isPollingMode(mode);
  const have = scoreboard !== null;

  // Server gửi SCOREBOARD ngay khi nối (README 4.1), nên REST chỉ cần khi chưa có bản nào (lần đầu, WebSocket chưa tới)
  // và khi không có WebSocket (đang nối lại hoặc bị 4429: gọi định kỳ, README 4.7 mục 1).
  useEffect(() => {
    if (!polling && have) return undefined;
    return startPolling({
      // Gắn epoch lúc phát request: phản hồi của kết nối/lần chạy server cũ sẽ bị bỏ (README 4.7 mục 4).
      fetchOnce: async (signal) => {
        const epoch = getEpoch();
        return { data: await api.scoreboard({ signal }), epoch };
      },
      onResult: ({ data, epoch }) => acceptScoreboard(data, epoch),
      isDone: () => !polling,
      intervalMs: FALLBACK_POLL_MS,
    });
  }, [polling, have, acceptScoreboard, getEpoch]);

  return (
    <>
      <h1>Bảng xếp hạng</h1>
      {!have && <p className="muted">Đang tải…</p>}
      {have && scoreboard.rows.length === 0 && <p className="muted">Chưa có ai nộp bài.</p>}
      {have && scoreboard.rows.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Hạng</th>
              <th>Người dùng</th>
              <th>Đã giải</th>
              <th>Số lần sai</th>
              <th>AC gần nhất</th>
            </tr>
          </thead>
          <tbody>
            {scoreboard.rows.map((r) => (
              <tr key={r.username} className={r.username === user.username ? "me" : undefined}>
                <td>{r.rank}</td>
                <td>{r.username}</td>
                <td>{r.solved}</td>
                <td>{r.wrong}</td>
                <td>{r.last_ac_at ? formatTime(r.last_ac_at) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {have && <p className="muted">Cập nhật lúc {formatTime(scoreboard.generated_at)}</p>}
    </>
  );
}
