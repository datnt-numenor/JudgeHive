"""Một kết nối WebSocket phía server (README mục 4.4): hàng đợi gửi riêng, writer task, gửi có timeout.

Mọi thứ ở đây chỉ chạy trên event loop. `ws` là đối tượng kiểu Starlette `WebSocket` (cần `send_text` và `close`), nên test
dùng được một bản giả. Một kết nối chậm hay treo chỉ ảnh hưởng chính nó: writer của nó chờ riêng, hàng đợi của nó đầy thì nó
bị đóng, còn các kết nối khác không bao giờ chờ nó."""

import asyncio
import collections
import itertools
import logging

log = logging.getLogger("judgehive.ws")

CODE_UNAUTHENTICATED = 4401  # hết phiên, logout, đăng nhập thay phiên, mất quyền
CODE_TOO_MANY_PER_USER = 4429
CODE_IDLE = 4408
CODE_TRY_AGAIN_LATER = 1013  # quá tải toàn server, hoặc kết nối nhận quá chậm
CODE_GOING_AWAY = 1001
CODE_BINARY = 1003
CODE_TOO_BIG = 1009

_ids = itertools.count(1)


class Connection:
    def __init__(self, ws, user_id, session_hash, *, loop, queue_size, send_timeout_s, close_timeout_s):
        self.id = next(_ids)
        self.ws, self.user_id, self.session_hash = ws, user_id, session_hash
        self._loop = loop
        self._queue_size, self._send_timeout, self._close_timeout = queue_size, send_timeout_s, close_timeout_s
        self._out = collections.deque()
        self._scoreboard = None  # văn bản SCOREBOARD đang chờ gửi; bản mới thay bản cũ chưa gửi
        self._wakeup = asyncio.Event()
        self.closed = asyncio.Event()  # đặt khi kết nối đã đóng hoặc không còn nhận được nữa
        self.closing = False
        self.last_received = loop.time()
        self.ping_seq = 0
        self.ignored_messages = 0
        self._writer_task = None
        self._close_task = None

    # ------------------------------------------------------------------ đưa message vào hàng đợi (không bao giờ chờ)

    def offer(self, text):
        """Xếp một message vào hàng đợi gửi. Hàng đợi đầy (kết nối nhận quá chậm) thì đóng kết nối với mã 1013. Trả
        True nếu đã xếp."""
        if self.closing:
            return False
        if len(self._out) >= self._queue_size:
            log.warning("kết nối %s (user %s) nhận quá chậm, hàng đợi %d đầy: đóng 1013", self.id, self.user_id,
                        self._queue_size)
            self.close_soon(CODE_TRY_AGAIN_LATER)
            return False
        self._out.append(text)
        self._wakeup.set()
        return True

    def offer_scoreboard(self, text):
        """Chỉ giữ SCOREBOARD mới nhất: bản chưa gửi bị thay (README 4.4)."""
        if self.closing:
            return
        self._scoreboard = text
        self._wakeup.set()

    # ------------------------------------------------------------------ writer

    def start(self):
        self._writer_task = self._loop.create_task(self._writer())

    def _next(self):
        if self._out:
            return self._out.popleft()
        text, self._scoreboard = self._scoreboard, None
        return text

    async def _writer(self):
        try:
            while True:
                await self._wakeup.wait()
                self._wakeup.clear()
                while True:
                    text = self._next()
                    if text is None:
                        break
                    try:
                        await asyncio.wait_for(self.ws.send_text(text), self._send_timeout)
                    except asyncio.TimeoutError:
                        log.warning("kết nối %s (user %s): một lần gửi quá %.1f s: đóng 1013", self.id, self.user_id,
                                    self._send_timeout)
                        await self.close(CODE_TRY_AGAIN_LATER)
                        return
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001  (client đã đi: không phải lỗi của server)
                        log.debug("kết nối %s: gửi lỗi (%r), coi là đã đóng", self.id, e)
                        self.mark_gone()
                        return
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------ đóng

    def mark_gone(self):
        """Kết nối không còn dùng được (client đã đi hoặc đã đóng xong): đánh dấu và đánh thức chủ của nó."""
        self.closing = True
        self.closed.set()

    def close_soon(self, code):
        """Đóng từ nơi không `await` được (callback đồng bộ). Không bao giờ ném; chặn `offer` ngay."""
        self.closing = True
        if self._close_task is None and not self.closed.is_set():
            self._close_task = self._loop.create_task(self._close_now(code))

    async def close(self, code):
        """Đóng với `code`, tối đa `close_timeout_s` (đóng một kết nối chết có thể không trả lời). Idempotent: mọi lời
        gọi cùng chờ một lần đóng; hủy người gọi không làm dừng việc đóng."""
        self.close_soon(code)
        if self._close_task is not None:
            await asyncio.shield(self._close_task)

    async def _close_now(self, code):
        try:
            await asyncio.wait_for(self.ws.close(code), self._close_timeout)
        except Exception as e:  # noqa: BLE001  (timeout, hoặc đã đóng sẵn)
            log.debug("kết nối %s: đóng %s không sạch (%r)", self.id, code, e)
        finally:
            self.mark_gone()

    async def stop(self):
        """Hủy writer (gọi khi dọn). Không đóng socket."""
        task = self._writer_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait({task})
