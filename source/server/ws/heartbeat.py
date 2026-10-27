"""Nhịp 30 giây của WebSocket (README mục 4.4, 4.5; D14): gửi `PING`, đóng kết nối im lặng quá hạn (4408), kiểm lại phiên
của mọi kết nối (4401). Mỗi bước bắt exception riêng, nên một bước lỗi không làm hỏng các bước sau hay làm task chết."""

import asyncio
import logging

from server.ws.connection import CODE_IDLE, CODE_UNAUTHENTICATED
from server.ws.events import dumps

log = logging.getLogger("judgehive.ws")


class Heartbeat:
    def __init__(self, registry, live_sessions, *, loop, interval_s, idle_timeout_s):
        """`live_sessions(hashes)`: coroutine trả tập hash phiên còn hiệu lực (tra DB ở thread riêng)."""
        self._registry, self._live_sessions, self._loop = registry, live_sessions, loop
        self._interval, self._idle_timeout = interval_s, idle_timeout_s

    async def run(self):
        while True:
            await asyncio.sleep(self._interval)
            await self.tick()

    async def tick(self):
        for step in (self._send_pings, self._close_idle, self._recheck_sessions):
            try:
                await step()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("bước %s của nhịp WebSocket lỗi", step.__name__)

    async def _send_pings(self):
        for conn in self._registry.all():
            conn.ping_seq += 1
            conn.offer(dumps({"type": "PING", "seq": conn.ping_seq}))

    async def _close_idle(self):
        now = self._loop.time()
        for conn in self._registry.all():
            if now - conn.last_received > self._idle_timeout:
                log.info("kết nối %s (user %s) im lặng quá %.0f s: đóng 4408", conn.id, conn.user_id, self._idle_timeout)
                conn.close_soon(CODE_IDLE)

    async def _recheck_sessions(self):
        hashes = self._registry.sessions()
        if not hashes:
            return
        alive = await self._live_sessions(hashes)
        for session_hash in hashes - set(alive):
            self._registry.close_session(session_hash, CODE_UNAUTHENTICATED)
