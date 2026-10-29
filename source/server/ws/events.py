"""STATUS: từ sự kiện của dispatcher/cổng worker (`Hooks.status`, chạy ở thread khác) tới đúng các kết nối của chủ bài nộp
(README mục 4.2).

Cầu thread: `LoopHooks.status` chỉ gọi `loop.call_soon_threadsafe(consumer.offer, event)` rồi trả về. Từ đó mọi thứ chạy
trên event loop. Một task duy nhất (`StatusConsumer.run`) xử lý sự kiện lần lượt, nên thứ tự theo từng bài nộp được giữ.
Riêng `done` cần ảnh chụp bài nộp từ DB: việc đó giao cho một thread riêng của hub (event loop không gọi SQLite)."""

import asyncio
import json
import logging
from typing import Any, NamedTuple

log = logging.getLogger("judgehive.ws")


class Event(NamedTuple):
    submission_id: int
    user_id: int
    event: str  # queued, judging, progress, done (Hooks.status)
    info: dict


def dumps(message):
    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))


def status_message(event, snapshot=None):
    """Message STATUS (dict) cho `event`, hoặc None nếu không có gì để gửi.

    `queued`/`judging`: chỉ `submission_id` và `state`. `progress`: `state` là `judging` kèm `progress`. `done`: `state` là
    `judged` kèm `submission` (đối tượng bài nộp của REST, đã qua whitelist theo verdict); thiếu `snapshot` thì None.
    Không bao giờ có `code`, `ce_message`, `sample_*` hay test ẩn."""
    base = {"type": "STATUS", "submission_id": event.submission_id}
    if event.event == "queued":
        return {**base, "state": "queued"}
    if event.event == "judging":
        return {**base, "state": "judging"}
    if event.event == "progress":
        done, total = event.info.get("tests_done"), event.info.get("tests_total")
        if not (isinstance(done, int) and isinstance(total, int)):
            return {**base, "state": "judging"}
        return {**base, "state": "judging", "progress": {"tests_done": done, "tests_total": total}}
    if event.event == "done":
        if snapshot is None:
            return None
        return {**base, "state": "judged", "submission": snapshot}
    return None


class StatusConsumer:
    def __init__(self, registry, fetch_view, *, maxsize):
        """`fetch_view(submission_id, user_id)`: coroutine trả đối tượng bài nộp (dict) hoặc None; chạy DB ở thread riêng."""
        self._registry, self._fetch_view = registry, fetch_view
        self._queue = asyncio.Queue(maxsize=maxsize)
        self._dropped = 0
        self._stopped = False

    def offer(self, event):
        """Chỉ gọi trên event loop (qua `call_soon_threadsafe`). Không bao giờ ném. Đầy thì bỏ sự kiện và ghi log: `STATUS` chỉ
        là gợi ý, nguồn thật là REST."""
        if self._stopped:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped += 1
            if self._dropped == 1 or self._dropped % 1000 == 0:
                log.error("hàng đợi sự kiện STATUS đầy, đã bỏ %d sự kiện", self._dropped)

    def stop(self):
        self._stopped = True

    async def run(self):
        while True:
            event = await self._queue.get()
            try:
                await self._handle(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("xử lý sự kiện STATUS lỗi: %r", event)

    async def _handle(self, event):
        if not self._registry.conns_of_user(event.user_id):
            return  # không ai đang nghe: khỏi tốn truy vấn DB
        snapshot = None
        if event.event == "done":
            snapshot = await self._fetch_view(event.submission_id, event.user_id)
        message = status_message(event, snapshot)
        if message is not None:
            self._registry.offer_status(event.user_id, dumps(message))


def parse_client_message(text):
    """Phân loại một frame văn bản từ client (README 4.3): `"pong"` nếu là `PONG` hợp lệ (có `seq` nguyên), ngược lại
    `"ignored"` (type khác, thiếu hoặc sai kiểu `seq`, không phải object JSON)."""
    try:
        value: Any = json.loads(text)
    except (ValueError, RecursionError):  # JSON lồng quá sâu ném RecursionError, không phải ValueError
        return "ignored"
    if not isinstance(value, dict) or value.get("type") != "PONG":
        return "ignored"
    seq = value.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int):
        return "ignored"
    return "pong"
