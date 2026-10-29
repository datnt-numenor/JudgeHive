"""STATUS: dựng message, consumer sự kiện, phân loại frame của client, và cầu thread → event loop của `LoopHooks.status`.

Ứng viên khóa: dạng message STATUS, đúng đích, thứ tự theo từng bài nộp từ nhiều thread, hook không ném, `PONG` hợp lệ."""

import asyncio
import json
import logging
import threading
import time

import pytest

from server.hooks import LoopHooks
from server.ws.events import Event, StatusConsumer, dumps, parse_client_message, status_message


def run(coro):
    return asyncio.run(coro)


async def settle(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return predicate()


class FakeRegistry:
    """Đủ giao diện cho StatusConsumer: ai đang nghe và nhận gì."""

    def __init__(self, listening=(1,)):
        self.listening, self.received = set(listening), []

    def conns_of_user(self, user_id):
        return (object(),) if user_id in self.listening else ()

    def offer_status(self, user_id, text):
        self.received.append((user_id, json.loads(text)))


VIEW = {"submission_id": 7, "problem_id": "sum", "problem_title": "Sum", "language": "python",
        "received_at": "2026-10-28T00:00:00.000Z", "state": "judged", "verdict": "AC", "time_ms": 5,
        "memory_kib": 1024, "failed_test": None, "failed_is_sample": None, "re_kind": None, "rejudged_at": None}


# ---- dựng message -------------------------------------------------------------------------------------------------

def test_status_messages_for_each_event():
    assert status_message(Event(7, 1, "queued", {})) == {"type": "STATUS", "submission_id": 7, "state": "queued"}
    assert status_message(Event(7, 1, "queued", {"reason": "redelivery"})) == {
        "type": "STATUS", "submission_id": 7, "state": "queued"}  # info thừa không lộ ra
    assert status_message(Event(7, 1, "judging", {"attempt_id": 3})) == {
        "type": "STATUS", "submission_id": 7, "state": "judging"}
    assert status_message(Event(7, 1, "progress", {"tests_done": 1, "tests_total": 4})) == {
        "type": "STATUS", "submission_id": 7, "state": "judging", "progress": {"tests_done": 1, "tests_total": 4}}
    assert status_message(Event(7, 1, "done", {"verdict": "AC"}), VIEW) == {
        "type": "STATUS", "submission_id": 7, "state": "judged", "submission": VIEW}


def test_done_without_a_snapshot_and_unknown_events_produce_nothing():
    assert status_message(Event(7, 1, "done", {"verdict": "AC"}), None) is None
    assert status_message(Event(7, 1, "bogus", {})) is None


def test_progress_with_bad_numbers_degrades_to_plain_judging():
    for info in ({}, {"tests_done": "1", "tests_total": 4}, {"tests_done": None, "tests_total": 4}):
        assert status_message(Event(7, 1, "progress", info)) == {"type": "STATUS", "submission_id": 7,
                                                                  "state": "judging"}


def test_status_never_carries_code_or_hidden_data():
    for event in (Event(7, 1, "queued", {"code": "secret"}), Event(7, 1, "judging", {"stdout": "x"}),
                  Event(7, 1, "progress", {"tests_done": 1, "tests_total": 2, "input": "hidden"})):
        text = dumps(status_message(event))
        assert "secret" not in text and "hidden" not in text and "stdout" not in text


def test_dumps_is_compact_utf8_json():
    assert dumps({"a": "Tổng", "b": [1, 2]}) == '{"a":"Tổng","b":[1,2]}'


# ---- client gửi lên -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ('{"type": "PONG", "seq": 7}', "pong"), ('{"type":"PONG","seq":0}', "pong"), ('{"type":"PONG","seq":-3}', "pong"),
    ('{"type":"PONG","seq":7,"extra":1}', "pong"),
    ('{"type":"PONG"}', "ignored"), ('{"type":"PONG","seq":"7"}', "ignored"), ('{"type":"PONG","seq":7.5}', "ignored"),
    ('{"type":"PONG","seq":true}', "ignored"), ('{"type":"PONG","seq":null}', "ignored"),
    ('{"type":"pong","seq":1}', "ignored"), ('{"type":"PING","seq":1}', "ignored"), ('{"type":"STATUS"}', "ignored"),
    ('{"seq":1}', "ignored"), ('[]', "ignored"), ('"PONG"', "ignored"), ('null', "ignored"), ('5', "ignored"),
    ('not json', "ignored"), ('', "ignored"), ('{"type":"PONG","seq":1', "ignored"),
    ("[" * 5000, "ignored"), ('{"a":' * 5000, "ignored"),  # lồng quá sâu: RecursionError, không phải ValueError
])
def test_parse_client_message(text, expected):
    assert parse_client_message(text) == expected


# ---- consumer -----------------------------------------------------------------------------------------------------

def test_consumer_delivers_events_in_order_to_listeners_only():
    async def scenario():
        registry = FakeRegistry(listening={1})
        fetched = []

        async def fetch(sid, uid):
            fetched.append((sid, uid))
            return {**VIEW, "submission_id": sid}

        consumer = StatusConsumer(registry, fetch, maxsize=100)
        task = asyncio.ensure_future(consumer.run())
        consumer.offer(Event(1, 1, "queued", {}))
        consumer.offer(Event(1, 2, "queued", {}))  # user 2 không có kết nối: bỏ qua
        consumer.offer(Event(1, 1, "judging", {}))
        consumer.offer(Event(1, 1, "progress", {"tests_done": 1, "tests_total": 2}))
        consumer.offer(Event(1, 2, "done", {"verdict": "AC"}))  # không ai nghe: không được truy vấn DB
        consumer.offer(Event(1, 1, "done", {"verdict": "AC"}))
        assert await settle(lambda: len(registry.received) == 4)
        assert [m["state"] for _, m in registry.received] == ["queued", "judging", "judging", "judged"]
        assert all(uid == 1 for uid, _ in registry.received)
        assert fetched == [(1, 1)]
        task.cancel()

    run(scenario())


def test_a_snapshot_that_no_longer_exists_sends_nothing_but_the_consumer_continues():
    async def scenario():
        registry = FakeRegistry()

        async def fetch(sid, uid):
            return None if sid == 1 else VIEW

        consumer = StatusConsumer(registry, fetch, maxsize=10)
        task = asyncio.ensure_future(consumer.run())
        consumer.offer(Event(1, 1, "done", {}))
        consumer.offer(Event(2, 1, "done", {}))
        assert await settle(lambda: len(registry.received) == 1)
        message = registry.received[0][1]
        assert message["submission_id"] == 2 and message["state"] == "judged"  # bài 1 không còn: không gửi gì cho nó
        task.cancel()

    run(scenario())


def test_an_error_while_handling_one_event_does_not_kill_the_consumer(caplog):
    async def scenario():
        registry = FakeRegistry()

        async def fetch(sid, uid):
            if sid == 1:
                raise RuntimeError("DB hỏng")
            return VIEW

        consumer = StatusConsumer(registry, fetch, maxsize=10)
        task = asyncio.ensure_future(consumer.run())
        with caplog.at_level(logging.ERROR):
            consumer.offer(Event(1, 1, "done", {}))
            consumer.offer(Event(2, 1, "queued", {}))
            assert await settle(lambda: len(registry.received) == 1)
        assert registry.received[0][1]["state"] == "queued" and not task.done()
        task.cancel()

    run(scenario())


def test_a_full_event_queue_drops_events_and_logs_without_raising(caplog):
    async def scenario():
        consumer = StatusConsumer(FakeRegistry(), None, maxsize=2)
        with caplog.at_level(logging.ERROR):
            for i in range(10):
                consumer.offer(Event(i, 1, "queued", {}))
        assert "đã bỏ" in caplog.text

    run(scenario())


def test_offer_after_stop_is_ignored():
    async def scenario():
        consumer = StatusConsumer(FakeRegistry(), None, maxsize=2)
        consumer.stop()
        for i in range(5):
            consumer.offer(Event(i, 1, "queued", {}))  # không ném, không đầy

    run(scenario())


# ---- cầu thread → event loop ---------------------------------------------------------------------------------------

def test_events_from_many_threads_keep_their_order_per_submission():
    async def scenario():
        loop = asyncio.get_running_loop()
        registry = FakeRegistry(listening={1})
        consumer = StatusConsumer(registry, None, maxsize=10000)
        task = asyncio.ensure_future(consumer.run())
        hooks = LoopHooks(loop)
        hooks.bind_status(consumer)
        per_thread = 40

        def worker(sid):  # mỗi thread phát chuỗi sự kiện của riêng bài nộp `sid`, theo đúng thứ tự
            hooks.status(sid, 1, "queued")
            hooks.status(sid, 1, "judging", attempt_id=1)
            for done in range(per_thread):
                hooks.status(sid, 1, "progress", tests_done=done, tests_total=per_thread)

        threads = [threading.Thread(target=worker, args=(sid,)) for sid in range(1, 9)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        total = 8 * (2 + per_thread)
        assert await settle(lambda: len(registry.received) == total, timeout=10)
        for sid in range(1, 9):
            mine = [m for _, m in registry.received if m["submission_id"] == sid]
            assert mine[0]["state"] == "queued" and mine[1]["state"] == "judging"
            assert [m["progress"]["tests_done"] for m in mine[2:]] == list(range(per_thread))
        task.cancel()

    run(scenario())


def test_status_hook_never_raises_and_does_not_wait():
    async def scenario():
        loop = asyncio.get_running_loop()
        consumer = StatusConsumer(FakeRegistry(), None, maxsize=10)
        hooks = LoopHooks(loop)
        hooks.bind_status(consumer)
        errors = []

        def worker():
            try:
                for i in range(200):
                    hooks.status(i, 1, "progress", tests_done=1, tests_total=2)  # nhiều hơn sức chứa của hàng đợi
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        t0 = time.monotonic()
        await asyncio.to_thread(worker)
        assert errors == [] and time.monotonic() - t0 < 2

    run(scenario())


def test_status_hook_after_the_loop_is_closed_does_not_raise():
    loop = asyncio.new_event_loop()
    hooks = LoopHooks(loop)

    class Dead:
        def offer(self, event):
            raise AssertionError("không được chạy khi loop đã đóng")

    hooks.bind_status(Dead())
    loop.close()
    result = []
    t = threading.Thread(target=lambda: result.append(hooks.status(1, 1, "queued")))
    t.start()
    t.join()
    assert result == [None]


def test_without_a_bound_consumer_status_only_logs(caplog):
    async def scenario():
        hooks = LoopHooks(asyncio.get_running_loop())
        with caplog.at_level(logging.INFO, logger="judgehive.hooks"):
            await asyncio.to_thread(hooks.status, 9, 2, "done", verdict="WA")
            await asyncio.sleep(0.05)
        assert "STATUS submission=9 user=2 event=done" in caplog.text

    run(scenario())
