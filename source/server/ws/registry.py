"""Danh sách kết nối WebSocket đang mở (README mục 4.1, 4.5). Chỉ đọc/ghi trên event loop; thread khác phải đi qua
`loop.call_soon_threadsafe` (xem `WsHub.close_session_threadsafe`)."""

import asyncio
import logging

from server.ws.connection import CODE_GOING_AWAY, CODE_TOO_MANY_PER_USER, CODE_TRY_AGAIN_LATER

log = logging.getLogger("judgehive.ws")


class Registry:
    def __init__(self, max_per_user, max_total):
        self.max_per_user, self.max_total = max_per_user, max_total
        self._by_user = {}
        self._by_session = {}
        self._count = 0

    def __len__(self):
        return self._count

    def check_caps(self, user_id):
        """Mã đóng nếu không được nhận thêm kết nối của `user_id`, hoặc None. Giới hạn của người đó kiểm trước
        (lỗi của chính họ: nối lại cũng vô ích), rồi mới tới trần toàn server (quá tải tạm thời)."""
        if len(self._by_user.get(user_id, ())) >= self.max_per_user:
            return CODE_TOO_MANY_PER_USER
        if self._count >= self.max_total:
            return CODE_TRY_AGAIN_LATER
        return None

    def add(self, conn):
        """Gọi ngay sau `check_caps`, không `await` giữa hai lời gọi (để kiểm và thêm là một bước nguyên tử)."""
        self._by_user.setdefault(conn.user_id, set()).add(conn)
        self._by_session.setdefault(conn.session_hash, set()).add(conn)
        self._count += 1

    def remove(self, conn):
        for index, key in ((self._by_user, conn.user_id), (self._by_session, conn.session_hash)):
            conns = index.get(key)
            if conns is not None and conn in conns:
                conns.discard(conn)
                if not conns:
                    del index[key]
                if index is self._by_user:
                    self._count -= 1

    def conns_of_user(self, user_id):
        return tuple(self._by_user.get(user_id, ()))

    def all(self):
        return [c for conns in self._by_user.values() for c in conns]

    def sessions(self):
        return set(self._by_session)

    # ------------------------------------------------------------------ gửi

    def offer_status(self, user_id, text):
        """Gửi `text` tới mọi kết nối của đúng `user_id` (không ai khác)."""
        for conn in self.conns_of_user(user_id):
            conn.offer(text)

    def offer_scoreboard_all(self, text):
        for conn in self.all():
            conn.offer_scoreboard(text)

    # ------------------------------------------------------------------ đóng hàng loạt (chạy trên event loop)

    def close_session(self, session_hash, code):
        for conn in tuple(self._by_session.get(session_hash, ())):
            conn.close_soon(code)

    def close_user(self, user_id, code):
        for conn in self.conns_of_user(user_id):
            conn.close_soon(code)

    async def close_all(self, code=CODE_GOING_AWAY):
        conns = self.all()
        if conns:
            await asyncio.gather(*(c.close(code) for c in conns), return_exceptions=True)
