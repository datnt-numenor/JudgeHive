"""WebSocket `/ws` (README mục 4): server đẩy `STATUS`, `SCOREBOARD`, `PING`; client chỉ gửi `PONG`.

`WsHub` gom registry (kết nối đang mở), consumer sự kiện STATUS, nhịp PING/kiểm tra phiên và một thread DB riêng. Mọi trạng thái
chỉ đọc/ghi trên event loop; thread khác (dispatcher, cổng worker, threadpool của REST) chỉ đi qua `call_soon_threadsafe`.
Event loop không bao giờ gọi SQLite: tra phiên, dựng ảnh chụp `done` và kiểm lại phiên đều chạy trong thread `ws-db`."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from server.ws.connection import CODE_GOING_AWAY, CODE_UNAUTHENTICATED
from server.ws.events import StatusConsumer, dumps
from server.ws.heartbeat import Heartbeat
from server.ws.registry import Registry

log = logging.getLogger("judgehive.ws")

_IN_CHUNK = 500  # số tham số `?` tối đa mỗi truy vấn kiểm phiên


class WsHub:
    def __init__(self, cfg, db, scoreboard, *, loop, clock):
        self.cfg, self.db, self.scoreboard, self.loop, self.clock = cfg, db, scoreboard, loop, clock
        self.registry = Registry(cfg.ws_max_per_user, cfg.ws_max_total)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ws-db")
        self.consumer = StatusConsumer(self.registry, self.fetch_view, maxsize=cfg.ws_event_queue_size)
        self.heartbeat = Heartbeat(self.registry, self.live_sessions, loop=loop, interval_s=cfg.ws_ping_interval_s,
                                   idle_timeout_s=cfg.ws_idle_timeout_s)
        self._tasks = []
        self._unsubscribe = None
        self._scoreboard_cache = (None, None)  # (snapshot, văn bản JSON đã dựng)
        self._stopped = False

    @property
    def stopped(self):
        return self._stopped

    # ------------------------------------------------------------------ vòng đời

    async def start(self):
        self._unsubscribe = self.scoreboard.subscribe(self.on_scoreboard)
        self._tasks = [self.loop.create_task(self.consumer.run(), name="ws-consumer"),
                       self.loop.create_task(self.heartbeat.run(), name="ws-heartbeat")]

    async def stop(self):
        """Dừng nhận việc, đóng mọi kết nối (1001), hủy task, đóng kết nối SQLite của thread riêng rồi tắt thread."""
        if self._stopped:
            return
        self._stopped = True
        self.consumer.stop()
        if self._unsubscribe is not None:
            self._unsubscribe()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.wait(self._tasks)
        try:
            await self.registry.close_all(CODE_GOING_AWAY)
        finally:
            await self.loop.run_in_executor(self._executor, self.db.close_thread)
            await self.loop.run_in_executor(None, self._executor.shutdown)

    # ------------------------------------------------------------------ SCOREBOARD

    def scoreboard_text(self):
        """Văn bản `SCOREBOARD` của bản gần nhất (dựng một lần cho mỗi bản), hoặc None trước khi có bản đầu."""
        snapshot = self.scoreboard.latest()
        if snapshot is None:
            return None
        cached, text = self._scoreboard_cache
        if cached is not snapshot:
            text = dumps({"type": "SCOREBOARD", **snapshot.as_json()})
            self._scoreboard_cache = (snapshot, text)
        return text

    def on_scoreboard(self, snapshot):
        """Subscriber của `ScoreboardService` (event loop, đúng thứ tự phiên bản, không đồng thời)."""
        text = self.scoreboard_text()
        if text is not None:
            self.registry.offer_scoreboard_all(text)

    # ------------------------------------------------------------------ việc cần DB (thread ws-db)

    def _in_db_thread(self, fn, *args):
        return self.loop.run_in_executor(self._executor, fn, *args)

    async def lookup_session(self, token):
        from server.rest import sessions

        return await self._in_db_thread(lambda: sessions.lookup(self.db, token, self.clock()))

    async def fetch_view(self, submission_id, user_id):
        from server.rest import views

        def query():
            row = self.db.conn().execute(
                f"SELECT {views.LIST_COLUMNS} FROM submissions s JOIN problems p ON p.id = s.problem_id "
                "WHERE s.id = ? AND s.user_id = ?", (submission_id, user_id)).fetchone()
            return None if row is None else views.submission_view(row)

        return await self._in_db_thread(query)

    async def live_sessions(self, hashes):
        """Tập hash phiên (trong `hashes`) còn hiệu lực."""
        hashes = list(hashes)

        def query():
            now, alive = self.clock(), set()
            conn = self.db.conn()
            for i in range(0, len(hashes), _IN_CHUNK):
                chunk = hashes[i:i + _IN_CHUNK]
                marks = ",".join("?" * len(chunk))
                for row in conn.execute(f"SELECT token_hash FROM sessions WHERE token_hash IN ({marks}) "
                                        "AND expires_at > ?", (*chunk, now)):
                    alive.add(row[0])
            return alive

        return await self._in_db_thread(query)

    # ------------------------------------------------------------------ gọi từ thread khác (threadpool của REST)

    def close_session_threadsafe(self, session_hash, code=CODE_UNAUTHENTICATED):
        """Đóng WebSocket của một phiên (logout, đăng nhập thay phiên). Không bao giờ ném."""
        try:
            self.loop.call_soon_threadsafe(self.registry.close_session, session_hash, code)
        except RuntimeError:  # event loop đã đóng (đang tắt server)
            pass

    def close_user_threadsafe(self, user_id, code=CODE_UNAUTHENTICATED):
        try:
            self.loop.call_soon_threadsafe(self.registry.close_user, user_id, code)
        except RuntimeError:
            pass


def install(app):
    """Đăng ký route `/ws`. `app.state.ws` (WsHub) do lifespan dựng."""
    from server.ws.endpoint import ws_endpoint

    app.add_api_websocket_route("/ws", ws_endpoint)
