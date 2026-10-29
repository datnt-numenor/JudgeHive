"""Vòng nhận frame của client (README 4.3), kiểm trực tiếp bằng websocket giả, không phụ thuộc cờ của uvicorn.

Ứng viên khóa: mã đóng 1003 và 1009 do chính ứng dụng quyết định (phòng thủ thứ hai sau `--ws-max-size`), message lạ bị bỏ qua."""

import asyncio

from server.ws.connection import Connection
from server.ws.reader import MAX_LOGGED_IGNORED, read_loop


def run(coro):
    return asyncio.run(coro)


class ScriptedWs:
    """`receive()` trả lần lượt các message đã định sẵn, rồi `websocket.disconnect`."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.sent, self.closed_with = [], []

    async def receive(self):
        if self._messages:
            item = self._messages.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        return {"type": "websocket.disconnect"}

    async def send_text(self, text):
        self.sent.append(text)

    async def close(self, code):
        self.closed_with.append(code)


def text(value):
    return {"type": "websocket.receive", "text": value}


def make(ws):
    return Connection(ws, 1, "s", loop=asyncio.get_running_loop(), queue_size=32, send_timeout_s=1.0, close_timeout_s=1.0)


def test_a_binary_frame_closes_1003():
    async def scenario():
        ws = ScriptedWs([{"type": "websocket.receive", "bytes": b"\x00\x01"}, text("unreachable")])
        conn = make(ws)
        await read_loop(conn, 4096, asyncio.get_running_loop())
        assert ws.closed_with == [1003] and conn.closed.is_set()

    run(scenario())


def test_a_text_frame_over_the_limit_closes_1009_counting_utf8_bytes():
    async def scenario():
        for payload, expect_close in (("x" * 4096, False), ("x" * 4097, True), ("é" * 2048, False), ("é" * 2049, True)):
            ws = ScriptedWs([text(payload)])
            conn = make(ws)
            await read_loop(conn, 4096, asyncio.get_running_loop())
            assert (ws.closed_with == [1009]) is expect_close, (len(payload), ws.closed_with)

    run(scenario())


def test_more_than_the_allowed_frames_in_the_window_closes_1008():
    async def scenario():
        pong = text('{"type":"PONG","seq":1}')
        ws = ScriptedWs([pong] * 20)  # đúng 20 frame trong cửa sổ: còn được
        conn = make(ws)
        await read_loop(conn, 4096, asyncio.get_running_loop(), max_frames=20, window_s=10.0)
        assert ws.closed_with == []
        ws = ScriptedWs([pong] * 21 + [text("unreachable")])  # frame thứ 21: đóng 1008, không đọc tiếp
        conn = make(ws)
        await read_loop(conn, 4096, asyncio.get_running_loop(), max_frames=20, window_s=10.0)
        assert ws.closed_with == [1008] and conn.closed.is_set()
        assert len(ws._messages) == 1

    run(scenario())


def test_frames_spread_over_more_than_the_window_are_never_limited():
    async def scenario():
        class Slow(ScriptedWs):
            async def receive(self):
                await asyncio.sleep(0.03)
                return await super().receive()

        pong = text('{"type":"PONG","seq":1}')
        # Cách nhau >= 0,03 giây (sleep không bao giờ ngắn hơn), nên hai frame cách nhau một frame thì cách >= 0,06 giây, vượt cửa sổ 0,05 giây
        ws = Slow([pong] * 12)
        conn = make(ws)
        await read_loop(conn, 4096, asyncio.get_running_loop(), max_frames=2, window_s=0.05)
        assert ws.closed_with == []

    run(scenario())


def test_ignored_frames_count_toward_the_rate_limit():
    async def scenario():
        ws = ScriptedWs([text("not json")] * 6)
        conn = make(ws)
        await read_loop(conn, 4096, asyncio.get_running_loop(), max_frames=5, window_s=10.0)
        assert ws.closed_with == [1008]

    run(scenario())


def test_unknown_malformed_and_deeply_nested_messages_are_ignored_and_the_loop_continues():
    async def scenario():
        junk = ['{"type":"HELLO"}', "not json", "[]", '{"type":"PONG"}', '{"type":"PONG","seq":"1"}', "null", "[" * 3000]
        ws = ScriptedWs([text(j) for j in junk] + [text('{"type":"PONG","seq":1}')])
        conn = make(ws)
        before = conn.last_received
        await asyncio.sleep(0.01)
        await read_loop(conn, 4096, asyncio.get_running_loop())
        assert ws.closed_with == []  # không đóng vì message lạ
        assert conn.ignored_messages == len(junk)  # PONG hợp lệ không tính là bị bỏ qua
        assert conn.last_received > before  # mọi frame làm mới mốc im lặng, kể cả frame bị bỏ qua
        assert conn.closed.is_set()  # kết thúc vì client ngắt kết nối

    run(scenario())


def test_only_the_first_few_ignored_messages_are_logged(caplog):
    import logging

    async def scenario():
        ws = ScriptedWs([text("không phải json")] * 50)
        conn = make(ws)
        with caplog.at_level(logging.INFO, logger="judgehive.ws"):
            await read_loop(conn, 4096, asyncio.get_running_loop())
        assert caplog.text.count("bỏ qua message của client") == MAX_LOGGED_IGNORED

    run(scenario())


def test_a_receive_error_ends_the_loop_quietly_and_marks_the_connection_gone():
    async def scenario():
        ws = ScriptedWs([text('{"type":"PONG","seq":1}'), RuntimeError("socket đã đóng")])
        conn = make(ws)
        await read_loop(conn, 4096, asyncio.get_running_loop())
        assert conn.closed.is_set() and ws.closed_with == []

    run(scenario())


def test_cancelling_the_loop_propagates_and_does_not_mark_the_connection_gone():
    async def scenario():
        class Hanging(ScriptedWs):
            async def receive(self):
                await asyncio.sleep(3600)

        conn = make(Hanging([]))
        task = asyncio.ensure_future(read_loop(conn, 4096, asyncio.get_running_loop()))
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.wait({task})
        assert task.cancelled() and not conn.closed.is_set()

    run(scenario())


def test_an_empty_text_frame_is_ignored_not_fatal():
    async def scenario():
        ws = ScriptedWs([text("")])
        conn = make(ws)
        await read_loop(conn, 4096, asyncio.get_running_loop())
        assert ws.closed_with == [] and conn.ignored_messages == 1

    run(scenario())
