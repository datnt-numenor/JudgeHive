"""Điểm móc để PR 7 đẩy `STATUS`/`SCOREBOARD` xuống WebSocket (PLAN mục 2.1).

Cổng worker và dispatcher gọi các hàm này từ thread của chúng; bản thật chuyển sang event loop bằng
`call_soon_threadsafe` (hiện chỉ ghi log). Hàm móc không bao giờ được raise vào thread gọi."""

import logging

log = logging.getLogger("judgehive.hooks")


class Hooks:
    """Bản rỗng; test dùng lớp con để ghi lại sự kiện."""

    def status(self, submission_id, user_id, event, **info):
        """`event`: `queued` (xếp hàng hoặc giao lại), `judging`, `progress`, `done`. `info` gồm các field nhẹ
        (không chứa test hay stdout của test ẩn)."""

    def scoreboard_dirty(self):
        """Verdict thay đổi, cần tính lại bảng xếp hạng (coalesce ở PR 7)."""


class LoopHooks(Hooks):
    def __init__(self, loop):
        self._loop = loop
        self._scoreboard = None
        self._status_consumer = None

    def bind_status(self, consumer):
        """Gắn `StatusConsumer` (WebSocket); từ đó `status` chuyển sự kiện sang event loop cho nó."""
        self._status_consumer = consumer

    def bind_scoreboard(self, service):
        """Gắn `ScoreboardService`; từ đó `scoreboard_dirty` đánh dấu cần tính lại bảng xếp hạng."""
        self._scoreboard = service

    def status(self, submission_id, user_id, event, **info):
        # Gọi từ thread dispatcher, cổng worker hay threadpool của REST: chỉ chuyển sang event loop rồi trả về, không bao
        # giờ ném. Mọi thứ còn lại (tra DB, gửi xuống WebSocket) chạy trên event loop hoặc thread riêng của WsHub.
        consumer = self._status_consumer
        if consumer is None:
            self._post(log.info, "STATUS submission=%s user=%s event=%s %s", submission_id, user_id, event, info)
        else:
            from server.ws.events import Event

            self._post(consumer.offer, Event(submission_id, user_id, event, info))

    def scoreboard_dirty(self):
        # Gọi từ thread dispatcher/cổng worker sau khi commit: chỉ chuyển sang event loop rồi trả về, không bao giờ ném.
        service = self._scoreboard
        if service is None:
            self._post(log.info, "SCOREBOARD dirty")
        else:
            self._post(service.mark_dirty)

    def _post(self, fn, *args):
        try:
            self._loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:  # event loop đã đóng (đang tắt server)
            pass
