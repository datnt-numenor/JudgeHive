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

    def status(self, submission_id, user_id, event, **info):
        self._post(log.info, "STATUS submission=%s user=%s event=%s %s", submission_id, user_id, event, info)

    def scoreboard_dirty(self):
        self._post(log.info, "SCOREBOARD dirty")

    def _post(self, fn, *args):
        try:
            self._loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:  # event loop đã đóng (đang tắt server)
            pass
