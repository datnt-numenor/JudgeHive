"""Dịch vụ bảng xếp hạng (PLAN mục 2.1, D6, D12): giữ bản bảng đã tính gần nhất, tính lại khi được đánh dấu, gộp (coalesce)
tối đa một lần mỗi `min_interval_s`, và phát bản mới cho các subscriber.

Mọi trạng thái (`_latest`, `_version`, `_dirty`, `_task`, danh sách subscriber) chỉ được đọc/ghi trên event loop. Thread khác
(dispatcher, cổng worker) chỉ gọi `mark_dirty` qua `loop.call_soon_threadsafe` (xem `hooks.LoopHooks`). Truy vấn SQLite
chạy trong MỘT thread riêng của dịch vụ, nên event loop không bao giờ chờ SQLite và không có hai lần tính chạy song song.

Single-flight: số phiên bản tăng trên event loop ngay trước khi giao việc tính cho thread, nên bản mang phiên bản `v`
phản ánh mọi commit đã xảy ra trước lúc `v` được cấp (kể cả commit đã gọi `mark_dirty` trước đó). Mọi `mark_dirty` đến
trong lúc đang tính chỉ dựng cờ; sau lần tính xong dịch vụ chờ đủ `min_interval_s` kể từ lần bắt đầu trước rồi tính một lần
nữa. Subscriber được gọi theo đúng thứ tự phiên bản và không bao giờ đồng thời."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from server.scoreboard.loader import compute_board
from server.timefmt import iso_ms

log = logging.getLogger("judgehive.scoreboard")

RETRY_BASE_S = 1.0
RETRY_CAP_S = 30.0


def retry_delay(failures):
    """Khoảng chờ sau lần tính lỗi liên tiếp thứ `failures` (từ 1): 1, 2, 4, ... tối đa 30 giây."""
    return min(RETRY_BASE_S * 2 ** (failures - 1), RETRY_CAP_S)


@dataclass(frozen=True)
class Snapshot:
    """Một bản bảng đã tính. Bất biến, nên đọc từ bất kỳ đâu cũng an toàn."""

    version: int
    generated_ms: int
    rows: tuple

    def as_json(self):
        """Thân của `GET /api/scoreboard` (README 3.3). Message WebSocket thêm field `type` (B2)."""
        return {
            "version": self.version,
            "generated_at": iso_ms(self.generated_ms),
            "rows": [{"rank": r.rank, "username": r.username, "solved": r.solved, "wrong": r.wrong,
                      "last_ac_at": None if r.last_ac_ms is None else iso_ms(r.last_ac_ms)} for r in self.rows],
        }


class ScoreboardService:
    def __init__(self, db, *, loop, clock, min_interval_s=1.0, compute=compute_board):
        """`clock()` trả thời gian hiện tại (ms) cho `generated_at`. `compute(db)` chạy trong thread riêng của dịch vụ và
        dùng `db.conn()` của thread đó (cùng pragma với mọi chỗ khác); test truyền hàm giả để đếm hoặc gây lỗi."""
        self._db, self._loop, self._clock = db, loop, clock
        self._min_interval_s = min_interval_s
        self._compute = compute
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scoreboard-db")
        self._latest = None
        self._version = 0
        self._dirty = False
        self._task = None
        self._ready = None
        self._last_start = None  # loop.time() của lần bắt đầu tính gần nhất
        self._subscribers = []
        self._stopped = False
        self._closed = False

    # ------------------------------------------------------------------ vòng đời

    async def start(self):
        """Tính bản đầu tiên (phiên bản 1) rồi mới trả về. Lỗi thì ném ra, để server không khởi động với bảng rỗng giả."""
        self._ready = self._loop.create_future()
        self._dirty = True
        self._task = self._loop.create_task(self._run())
        await self._ready

    async def stop(self):
        """Dừng: không tính thêm, hủy tác vụ, đóng kết nối SQLite của thread riêng rồi tắt thread. Gọi lại là vô hại."""
        self._stopped = True
        if self._closed:
            return
        self._closed = True
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        # Lần tính đang chạy trong thread (nếu có) vẫn chạy nốt; đóng kết nối xếp hàng sau nó trên cùng thread.
        await self._loop.run_in_executor(self._executor, self._db.close_thread)
        await self._loop.run_in_executor(None, self._executor.shutdown)

    # ------------------------------------------------------------------ giao diện cho nơi khác

    def latest(self):
        """Bản gần nhất, hoặc None trước khi `start()` xong."""
        return self._latest

    def mark_dirty(self):
        """Đánh dấu cần tính lại. Chỉ gọi trên event loop. Không bao giờ ném."""
        if self._stopped:
            return
        self._dirty = True
        if self._task is None or self._task.done():
            self._task = self._loop.create_task(self._run())

    def subscribe(self, callback):
        """`callback(snapshot)` được gọi trên event loop mỗi khi có bản mới, theo thứ tự phiên bản, không đồng thời. Phải
        trả về nhanh và không chờ gì. Trả hàm hủy đăng ký."""
        self._subscribers.append(callback)

        def unsubscribe():
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return unsubscribe

    # ------------------------------------------------------------------ bên trong

    async def _run(self):
        failures = 0
        try:
            while self._dirty and not self._stopped:
                if self._last_start is not None:
                    wait = self._last_start + self._min_interval_s - self._loop.time()
                    if wait > 0:
                        await asyncio.sleep(wait)
                    if self._stopped:
                        return
                self._dirty = False
                self._last_start = self._loop.time()
                try:
                    await self._compute_once()
                    failures = 0
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if self._latest is None:  # lần tính đầu: để lỗi nổi lên ở start()
                        if self._ready is not None and not self._ready.done():
                            self._ready.set_exception(e)
                        return
                    failures += 1
                    delay = retry_delay(failures)
                    log.exception("tính bảng xếp hạng lỗi (lần liên tiếp thứ %d), thử lại sau %.1f s", failures, delay)
                    self._dirty = True
                    await asyncio.sleep(delay)
        finally:
            # Hủy hoặc thoát vì lỗi lần đầu: khỏi để start() chờ mãi.
            if self._ready is not None and not self._ready.done():
                self._ready.cancel()

    async def _compute_once(self):
        self._version += 1  # trên event loop, trước khi đọc DB
        version = self._version
        rows = await self._loop.run_in_executor(self._executor, self._compute, self._db)
        snapshot = Snapshot(version, self._clock(), tuple(rows))
        self._latest = snapshot
        if self._ready is not None and not self._ready.done():
            self._ready.set_result(None)
        self._notify(snapshot)

    def _notify(self, snapshot):
        for callback in list(self._subscribers):
            try:
                callback(snapshot)
            except Exception:
                log.exception("subscriber của bảng xếp hạng lỗi")
