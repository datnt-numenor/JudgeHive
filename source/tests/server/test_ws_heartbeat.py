"""Nhịp PING, đóng kết nối im lặng (4408) và kiểm lại phiên (4401). Không cần mạng: websocket giả, `live_sessions` giả.

Ứng viên khóa: PING có `seq` tăng dần trên từng kết nối; im lặng quá hạn thì 4408; phiên không còn thì 4401 và phiên khác còn nguyên;
một bước lỗi không làm hỏng bước khác. Không khóa: test chạy vòng `run()` bằng khoảng thời gian thật ngắn."""

import asyncio
import json
import logging
import time

from server.ws.connection import CODE_IDLE, CODE_UNAUTHENTICATED, Connection
from server.ws.heartbeat import Heartbeat
from server.ws.registry import Registry


def run(coro):
    return asyncio.run(coro)


class FakeWs:
    def __init__(self):
        self.sent, self.closed_with = [], []

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def close(self, code):
        self.closed_with.append(code)


def conn(registry, user, session, idle_for=0.0):
    loop = asyncio.get_running_loop()
    c = Connection(FakeWs(), user, session, loop=loop, queue_size=32, send_timeout_s=1.0, close_timeout_s=1.0)
    c.last_received = loop.time() - idle_for
    registry.add(c)
    c.start()
    return c


async def settle(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return predicate()


def beat(registry, alive, interval=30.0, idle=60.0):
    async def live(hashes):
        return await alive(hashes) if asyncio.iscoroutinefunction(alive) else alive(hashes)

    return Heartbeat(registry, live, loop=asyncio.get_running_loop(), interval_s=interval, idle_timeout_s=idle)


def test_each_tick_sends_a_ping_with_an_increasing_seq_per_connection():
    async def scenario():
        registry = Registry(10, 100)
        a, b = conn(registry, 1, "a"), conn(registry, 2, "b")
        hb = beat(registry, lambda hashes: set(hashes))
        for _ in range(3):
            await hb.tick()
        assert await settle(lambda: len(a.ws.sent) == 3 and len(b.ws.sent) == 3)
        assert [m for m in a.ws.sent] == [{"type": "PING", "seq": n} for n in (1, 2, 3)]
        assert [m["seq"] for m in b.ws.sent] == [1, 2, 3]
        for c in (a, b):
            await c.stop()

    run(scenario())


def test_a_connection_silent_for_more_than_the_timeout_is_closed_with_4408():
    async def scenario():
        registry = Registry(10, 100)
        stale, fresh = conn(registry, 1, "a", idle_for=61), conn(registry, 2, "b", idle_for=59)
        hb = beat(registry, lambda hashes: set(hashes), idle=60)
        await hb.tick()
        assert await settle(lambda: stale.closed.is_set())
        assert stale.ws.closed_with == [CODE_IDLE]
        await asyncio.sleep(0.05)
        assert not fresh.closed.is_set()
        for c in (stale, fresh):
            await c.stop()

    run(scenario())


def test_receiving_anything_resets_the_idle_clock():
    async def scenario():
        registry = Registry(10, 100)
        c = conn(registry, 1, "a", idle_for=100)
        c.last_received = asyncio.get_running_loop().time()  # vừa nhận một frame
        await beat(registry, lambda hashes: set(hashes)).tick()
        await asyncio.sleep(0.05)
        assert not c.closed.is_set()
        await c.stop()

    run(scenario())


def test_sessions_that_are_gone_are_closed_with_4401_and_the_rest_stay():
    async def scenario():
        registry = Registry(10, 100)
        gone1, gone2 = conn(registry, 1, "het-han"), conn(registry, 1, "het-han")
        kept, other_user = conn(registry, 1, "con-song"), conn(registry, 2, "nguoi-khac")
        seen = []

        async def alive(hashes):
            seen.append(set(hashes))
            return {"con-song", "nguoi-khac"}

        await beat(registry, alive).tick()
        assert seen == [{"het-han", "con-song", "nguoi-khac"}]  # một truy vấn cho mọi phiên đang mở
        assert await settle(lambda: gone1.closed.is_set() and gone2.closed.is_set())
        assert gone1.ws.closed_with == gone2.ws.closed_with == [CODE_UNAUTHENTICATED]
        await asyncio.sleep(0.05)
        assert not kept.closed.is_set() and not other_user.closed.is_set()
        for c in (gone1, gone2, kept, other_user):
            await c.stop()

    run(scenario())


def test_no_connections_means_no_session_query():
    async def scenario():
        calls = []

        async def alive(hashes):
            calls.append(hashes)
            return set()

        await beat(Registry(10, 100), alive).tick()
        assert calls == []

    run(scenario())


def test_a_failing_session_check_does_not_stop_pings_or_the_loop(caplog):
    async def scenario():
        registry = Registry(10, 100)
        c = conn(registry, 1, "a")

        async def broken(hashes):
            raise RuntimeError("DB hỏng")

        hb = beat(registry, broken)
        with caplog.at_level(logging.ERROR):
            await hb.tick()
            await hb.tick()
        assert await settle(lambda: len(c.ws.sent) == 2)  # PING vẫn đi dù bước kiểm phiên lỗi
        assert not c.closed.is_set()  # lỗi kiểm phiên không đóng ai
        assert "kiểm" in caplog.text or "recheck" in caplog.text or "_recheck_sessions" in caplog.text
        await c.stop()

    run(scenario())


def test_run_ticks_repeatedly_at_the_interval():
    async def scenario():
        registry = Registry(10, 100)
        c = conn(registry, 1, "a")
        hb = beat(registry, lambda hashes: set(hashes), interval=0.05)
        task = asyncio.ensure_future(hb.run())
        assert await settle(lambda: len(c.ws.sent) >= 3)
        task.cancel()
        await asyncio.wait({task})
        await c.stop()

    run(scenario())
