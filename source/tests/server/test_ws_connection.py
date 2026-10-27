"""Connection (hàng đợi gửi riêng, writer, timeout, đóng) và Registry (trần, đúng đích). Không cần mạng: websocket giả.

Ứng viên khóa: thứ tự gửi, SCOREBOARD chỉ giữ bản mới nhất, đúng đích khi đóng theo phiên/người, mã đóng của các trần.
Không khóa: các test dùng timeout thật ngắn (gửi chậm, đóng treo)."""

import asyncio
import time

import pytest

from server.ws.connection import (CODE_IDLE, CODE_TOO_MANY_PER_USER, CODE_TRY_AGAIN_LATER, CODE_UNAUTHENTICATED,
                                  Connection)
from server.ws.registry import Registry


def run(coro):
    return asyncio.run(coro)


class FakeWs:
    def __init__(self, hang_send=False, hang_close=False, fail_send=False):
        self.sent, self.closed_with = [], []
        self.hang_send, self.hang_close, self.fail_send = hang_send, hang_close, fail_send

    async def send_text(self, text):
        if self.fail_send:
            raise RuntimeError("socket đã đóng")
        if self.hang_send:
            await asyncio.sleep(3600)
        self.sent.append(text)

    async def close(self, code):
        self.closed_with.append(code)
        if self.hang_close:
            await asyncio.sleep(3600)


def make(ws=None, user=1, session="s1", queue=32, send_timeout=0.2, close_timeout=0.2):
    return Connection(ws or FakeWs(), user, session, loop=asyncio.get_running_loop(), queue_size=queue,
                      send_timeout_s=send_timeout, close_timeout_s=close_timeout)


async def settle(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return predicate()


# ---- Connection ---------------------------------------------------------------------------------------------------

def test_messages_are_sent_in_the_order_they_were_offered():
    async def scenario():
        conn = make()
        conn.start()
        for i in range(10):
            conn.offer(f"m{i}")
        assert await settle(lambda: len(conn.ws.sent) == 10)
        assert conn.ws.sent == [f"m{i}" for i in range(10)]
        await conn.stop()

    run(scenario())


def test_only_the_latest_scoreboard_is_kept_and_it_goes_after_queued_messages():
    async def scenario():
        conn = make()
        conn.offer("status-1")
        conn.offer_scoreboard("board-1")
        conn.offer("status-2")
        conn.offer_scoreboard("board-2")  # thay bản chưa gửi
        conn.offer_scoreboard("board-3")
        conn.start()
        assert await settle(lambda: len(conn.ws.sent) == 3)
        assert conn.ws.sent == ["status-1", "status-2", "board-3"]
        await conn.stop()

    run(scenario())


def test_a_full_queue_closes_the_slow_connection_with_1013():
    async def scenario():
        conn = make(queue=3)
        assert [conn.offer(f"m{i}") for i in range(3)] == [True, True, True]  # writer chưa chạy: hàng đợi đầy dần
        assert conn.offer("m3") is False
        assert await settle(lambda: conn.closed.is_set())
        assert conn.ws.closed_with == [CODE_TRY_AGAIN_LATER]
        assert conn.offer("sau khi đóng") is False

    run(scenario())


def test_a_send_that_never_finishes_closes_only_that_connection():
    async def scenario():
        slow, good = make(FakeWs(hang_send=True), user=1, send_timeout=0.1), make(FakeWs(), user=2)
        registry = Registry(10, 100)
        for c in (slow, good):
            registry.add(c)
            c.start()
        slow.offer("kẹt")
        for i in range(5):
            registry.offer_status(2, f"cho-bob-{i}")
        assert await settle(lambda: len(good.ws.sent) == 5, timeout=0.5)  # bob không phải chờ alice
        assert not good.closed.is_set()
        assert await settle(lambda: slow.closed.is_set())
        assert slow.ws.closed_with == [CODE_TRY_AGAIN_LATER]
        await slow.stop()
        await good.stop()

    run(scenario())


def test_close_is_idempotent_and_waits_for_one_close_only():
    async def scenario():
        conn = make()
        await asyncio.gather(conn.close(4401), conn.close(4401), conn.close(1013))
        await conn.close(4401)
        assert conn.ws.closed_with == [4401] and conn.closed.is_set()

    run(scenario())


def test_close_of_a_dead_peer_is_bounded_by_the_close_timeout():
    async def scenario():
        conn = make(FakeWs(hang_close=True), close_timeout=0.1)
        t0 = time.monotonic()
        await conn.close(4401)
        assert time.monotonic() - t0 < 1.0 and conn.closed.is_set()

    run(scenario())


def test_cancelling_a_caller_of_close_does_not_abort_the_close():
    async def scenario():
        conn = make(FakeWs(hang_close=True), close_timeout=0.2)
        caller = asyncio.ensure_future(conn.close(4401))
        await asyncio.sleep(0)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert await settle(lambda: conn.closed.is_set())

    run(scenario())


def test_a_send_error_means_the_client_is_gone_not_a_server_problem():
    async def scenario():
        conn = make(FakeWs(fail_send=True))
        conn.start()
        conn.offer("x")
        assert await settle(lambda: conn.closed.is_set())
        assert conn.ws.closed_with == []  # không cố đóng một socket đã hỏng
        await conn.stop()

    run(scenario())


def test_close_soon_blocks_further_offers_immediately_and_closes_in_the_background():
    async def scenario():
        conn = make()
        conn.close_soon(CODE_IDLE)
        assert conn.offer("x") is False
        assert await settle(lambda: conn.closed.is_set())
        assert conn.ws.closed_with == [CODE_IDLE]

    run(scenario())


def test_stop_cancels_the_writer_and_is_safe_twice():
    async def scenario():
        conn = make()
        conn.start()
        await conn.stop()
        await conn.stop()

    run(scenario())


# ---- Registry -----------------------------------------------------------------------------------------------------

def add(registry, user, session, **kw):
    conn = make(user=user, session=session, **kw)
    registry.add(conn)
    return conn


def test_caps_per_user_first_then_server_wide():
    async def scenario():
        registry = Registry(max_per_user=2, max_total=3)
        assert registry.check_caps(1) is None
        add(registry, 1, "a")
        add(registry, 1, "b")
        assert registry.check_caps(1) == CODE_TOO_MANY_PER_USER  # người này đã đủ
        assert registry.check_caps(2) is None
        add(registry, 2, "c")
        assert len(registry) == 3
        assert registry.check_caps(3) == CODE_TRY_AGAIN_LATER  # toàn server đã đủ
        assert registry.check_caps(1) == CODE_TOO_MANY_PER_USER  # cả hai đều vượt: lỗi của chính người dùng trước

    run(scenario())


def test_remove_frees_the_slot_and_cleans_the_indexes():
    async def scenario():
        registry = Registry(1, 5)
        conn = add(registry, 1, "a")
        assert registry.check_caps(1) == CODE_TOO_MANY_PER_USER
        registry.remove(conn)
        registry.remove(conn)  # gọi lại vô hại
        assert len(registry) == 0 and registry.check_caps(1) is None
        assert registry.sessions() == set() and registry.conns_of_user(1) == ()

    run(scenario())


def test_status_goes_only_to_the_owners_connections():
    async def scenario():
        registry = Registry(10, 100)
        a1, a2, b = add(registry, 1, "a1"), add(registry, 1, "a2"), add(registry, 2, "b")
        for c in (a1, a2, b):
            c.start()
        registry.offer_status(1, "cho-alice")
        assert await settle(lambda: a1.ws.sent == ["cho-alice"] and a2.ws.sent == ["cho-alice"])
        await asyncio.sleep(0.05)
        assert b.ws.sent == []
        for c in (a1, a2, b):
            await c.stop()

    run(scenario())


def test_scoreboard_goes_to_everyone():
    async def scenario():
        registry = Registry(10, 100)
        conns = [add(registry, u, f"s{u}") for u in (1, 2, 3)]
        for c in conns:
            c.start()
        registry.offer_scoreboard_all("board")
        assert await settle(lambda: all(c.ws.sent == ["board"] for c in conns))
        for c in conns:
            await c.stop()

    run(scenario())


def test_close_session_closes_only_that_sessions_connections():
    async def scenario():
        registry = Registry(10, 100)
        tab1, tab2 = add(registry, 1, "phien-1"), add(registry, 1, "phien-1")
        other_session, other_user = add(registry, 1, "phien-2"), add(registry, 2, "phien-3")
        registry.close_session("phien-1", CODE_UNAUTHENTICATED)
        assert await settle(lambda: tab1.closed.is_set() and tab2.closed.is_set())
        assert tab1.ws.closed_with == tab2.ws.closed_with == [CODE_UNAUTHENTICATED]
        await asyncio.sleep(0.05)
        assert not other_session.closed.is_set() and not other_user.closed.is_set()
        registry.close_session("không-có", 4401)  # phiên lạ: vô hại

    run(scenario())


def test_close_user_closes_every_session_of_that_user():
    async def scenario():
        registry = Registry(10, 100)
        mine = [add(registry, 1, "a"), add(registry, 1, "b")]
        other = add(registry, 2, "c")
        registry.close_user(1, CODE_UNAUTHENTICATED)
        assert await settle(lambda: all(c.closed.is_set() for c in mine))
        await asyncio.sleep(0.05)
        assert not other.closed.is_set()

    run(scenario())


def test_close_all_uses_the_given_code_for_everybody():
    async def scenario():
        registry = Registry(10, 100)
        conns = [add(registry, u, f"s{u}") for u in (1, 2, 3)]
        await registry.close_all(1001)
        assert all(c.ws.closed_with == [1001] and c.closed.is_set() for c in conns)

    run(scenario())
