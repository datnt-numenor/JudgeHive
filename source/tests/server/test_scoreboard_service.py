"""ScoreboardService: tính lại gộp (coalesce), single-flight, phiên bản, subscriber, thread riêng, cầu thread → event loop.

Ứng viên khóa: thứ tự và tính đơn điệu của phiên bản, thứ tự gọi subscriber, cầu thread, không SQLite trên event loop.
KHÔNG khóa: mọi test phụ thuộc khoảng thời gian (coalesce, backoff) vì chúng dựa vào đồng hồ thật."""

import asyncio
import logging
import threading
import time

import pytest

import server.scoreboard.service as service_module
from server.db import Database
from server.hooks import LoopHooks
from server.scoreboard.loader import compute_board
from server.scoreboard.ranking import Row
from server.scoreboard.service import ScoreboardService, Snapshot, retry_delay

FIXED_MS = 1_800_000_000_000


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "svc.db"))
    database.migrate()
    with database.write() as c:
        c.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES ('alice', 'x', 'user', 0)")
        c.execute("INSERT INTO problems (id, title, statement, time_limit_ms, memory_limit_kib, hidden, tests_hash, "
                  "created_at, updated_at) VALUES ('sum', 't', 's', 1000, 1000, 0, 'h', 0, 0)")
    return database


def make(db, loop, interval=0.05, compute=None, **kw):
    return ScoreboardService(db, loop=loop, clock=lambda: FIXED_MS, min_interval_s=interval,
                             compute=compute or (lambda d: []), **kw)


class Counter:
    """compute giả: đếm số lần gọi, ghi thread và thời điểm bắt đầu, tùy chọn chặn/gây lỗi."""

    def __init__(self, fail_first=0):
        self.calls = []
        self.fail_first = fail_first
        self.gate = None  # threading.Event: nếu có, compute chờ tới khi gate được set

    def __call__(self, _db):
        self.calls.append((time.monotonic(), threading.current_thread().name))
        if self.gate is not None:
            self.gate.wait(10)
        if self.fail_first > 0:
            self.fail_first -= 1
            raise RuntimeError("giả lập lỗi tính bảng")
        return []


async def settle(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


# ---- vòng đời -----------------------------------------------------------------------------------------------------

def test_start_computes_version_1_on_its_own_thread_and_notifies():
    async def scenario(db):
        loop = asyncio.get_running_loop()
        counter = Counter()
        svc = make(db, loop, compute=counter)
        seen = []
        svc.subscribe(seen.append)
        assert svc.latest() is None
        await svc.start()
        snap = svc.latest()
        assert snap.version == 1 and snap.generated_ms == FIXED_MS and snap.rows == ()
        assert seen == [snap]
        assert counter.calls[0][1].startswith("scoreboard-db")
        assert counter.calls[0][1] != threading.current_thread().name
        await svc.stop()

    run_with_db(scenario)


def run_with_db(scenario):
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as tmp:
        database = Database(os.path.join(tmp, "x.db"))
        database.migrate()
        try:
            asyncio.run(scenario(database))
        finally:
            database.close_thread()


def test_a_failure_in_the_first_computation_makes_start_raise_and_stop_is_still_clean():
    async def scenario(db):
        svc = make(db, asyncio.get_running_loop(), compute=lambda d: 1 / 0)
        with pytest.raises(ZeroDivisionError):
            await svc.start()
        await svc.stop()
        assert not [t for t in threading.enumerate() if t.name.startswith("scoreboard-db")]

    run_with_db(scenario)


def test_stop_is_idempotent_leaves_no_thread_and_blocks_further_computation():
    async def scenario(db):
        counter = Counter()
        svc = make(db, asyncio.get_running_loop(), compute=counter)
        await svc.start()
        await svc.stop()
        await svc.stop()
        assert not [t for t in threading.enumerate() if t.name.startswith("scoreboard-db")]
        before = len(counter.calls)
        svc.mark_dirty()
        await asyncio.sleep(0.2)
        assert len(counter.calls) == before

    run_with_db(scenario)


# ---- coalesce, single-flight, phiên bản ---------------------------------------------------------------------------

def test_a_burst_of_marks_is_coalesced_and_computations_are_spaced_by_the_interval():
    async def scenario(db):
        counter = Counter()
        svc = make(db, asyncio.get_running_loop(), interval=0.2, compute=counter)
        await svc.start()
        for _ in range(500):
            svc.mark_dirty()
        await asyncio.sleep(0.8)
        extra = len(counter.calls) - 1
        assert 1 <= extra <= 2, extra
        starts = [t for t, _ in counter.calls]
        assert all(b - a >= 0.2 - 0.02 for a, b in zip(starts, starts[1:])), [b - a for a, b in zip(starts, starts[1:])]
        await svc.stop()

    run_with_db(scenario)


def test_marks_during_a_computation_cause_exactly_one_follow_up():
    async def scenario(db):
        counter = Counter()
        counter.gate = threading.Event()
        svc = make(db, asyncio.get_running_loop(), interval=0.05, compute=counter)
        starter = asyncio.ensure_future(svc.start())
        assert await settle(lambda: len(counter.calls) == 1)
        for _ in range(20):
            svc.mark_dirty()
        counter.gate.set()
        await starter
        assert await settle(lambda: len(counter.calls) == 2)
        await asyncio.sleep(0.3)
        assert len(counter.calls) == 2  # không có lần thứ ba
        assert svc.latest().version == 2
        await svc.stop()

    run_with_db(scenario)


def test_two_computations_never_overlap():
    async def scenario(db):
        active, peak = 0, 0
        lock = threading.Lock()

        def compute(_db):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return []

        svc = make(db, asyncio.get_running_loop(), interval=0.0, compute=compute)
        await svc.start()
        for _ in range(30):
            svc.mark_dirty()
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.2)
        assert peak == 1
        await svc.stop()

    run_with_db(scenario)


def test_versions_increase_strictly_and_subscribers_see_them_in_order_without_overlap():
    async def scenario(db):
        svc = make(db, asyncio.get_running_loop(), interval=0.0, compute=lambda d: [])
        seen, inside = [], [0]

        def subscriber(snap):
            inside[0] += 1
            assert inside[0] == 1, "subscriber bị gọi đồng thời"
            seen.append(snap.version)
            inside[0] -= 1

        svc.subscribe(subscriber)
        await svc.start()
        for _ in range(15):
            svc.mark_dirty()
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.2)
        assert seen == sorted(seen) and len(set(seen)) == len(seen) and seen[0] == 1
        assert seen[-1] == svc.latest().version
        await svc.stop()

    run_with_db(scenario)


def test_a_failing_subscriber_does_not_stop_the_others_or_the_service(caplog):
    async def scenario(db):
        svc = make(db, asyncio.get_running_loop(), interval=0.0, compute=lambda d: [])
        good = []
        svc.subscribe(lambda snap: 1 / 0)
        svc.subscribe(good.append)
        with caplog.at_level(logging.ERROR):
            await svc.start()
            svc.mark_dirty()
            assert await settle(lambda: svc.latest().version >= 2)
        assert [s.version for s in good][:2] == [1, 2]
        await svc.stop()

    run_with_db(scenario)


def test_unsubscribe_stops_delivery():
    async def scenario(db):
        svc = make(db, asyncio.get_running_loop(), interval=0.0, compute=lambda d: [])
        seen = []
        unsubscribe = svc.subscribe(seen.append)
        await svc.start()
        unsubscribe()
        unsubscribe()  # gọi lại vô hại
        svc.mark_dirty()
        assert await settle(lambda: svc.latest().version >= 2)
        assert [s.version for s in seen] == [1]
        await svc.stop()

    run_with_db(scenario)


def test_failures_keep_the_old_snapshot_and_retry_until_success(monkeypatch):
    monkeypatch.setattr(service_module, "RETRY_BASE_S", 0.2)

    async def scenario(db):
        counter = Counter()
        svc = make(db, asyncio.get_running_loop(), interval=0.0, compute=counter)
        await svc.start()
        old = svc.latest()
        counter.fail_first = 2
        svc.mark_dirty()
        assert await settle(lambda: len(counter.calls) == 2)  # lần lỗi đầu tiên
        assert svc.latest() is old  # lỗi thì giữ nguyên bản đang có
        assert await settle(lambda: svc.latest().version > old.version, timeout=5)
        assert len(counter.calls) == 4  # 1 (đầu) + 2 lỗi + 1 thành công
        await svc.stop()

    run_with_db(scenario)


def test_retry_delay_doubles_up_to_the_cap():
    assert [retry_delay(n) for n in (1, 2, 3, 4, 5, 6, 7)] == [1, 2, 4, 8, 16, 30, 30]


def test_the_snapshot_contains_every_commit_made_before_the_computation(db):
    async def scenario():
        svc = ScoreboardService(db, loop=asyncio.get_running_loop(), clock=lambda: FIXED_MS, min_interval_s=0.0,
                                compute=compute_board)
        await svc.start()
        assert svc.latest().rows == ()
        with db.write() as c:
            c.execute("INSERT INTO submissions (user_id, problem_id, language, code, submitted_at, state, verdict) "
                      "VALUES (1, 'sum', 'python', 'c', 77, 'done', 'AC')")
        svc.mark_dirty()
        assert await settle(lambda: svc.latest().rows != ())
        assert svc.latest().rows == (Row(1, "alice", 1, 0, 77),)
        await svc.stop()

    run(scenario())


def test_snapshot_json_matches_the_rest_contract():
    snap = Snapshot(3, FIXED_MS, (Row(1, "alice", 2, 1, 1_700_000_000_123), Row(2, "bob", 0, 0, None)))
    assert snap.as_json() == {
        "version": 3, "generated_at": "2027-01-15T08:00:00.000Z",
        "rows": [{"rank": 1, "username": "alice", "solved": 2, "wrong": 1, "last_ac_at": "2023-11-14T22:13:20.123Z"},
                 {"rank": 2, "username": "bob", "solved": 0, "wrong": 0, "last_ac_at": None}]}


# ---- cầu thread → event loop và SQLite -----------------------------------------------------------------------------

def test_the_sqlite_connection_is_never_used_on_the_event_loop_thread(db, monkeypatch):
    used_on = []
    original = Database.conn

    def spy(self):
        used_on.append(threading.get_ident())
        return original(self)

    monkeypatch.setattr(Database, "conn", spy)

    async def scenario():
        loop_thread = threading.get_ident()
        svc = ScoreboardService(db, loop=asyncio.get_running_loop(), clock=lambda: FIXED_MS, min_interval_s=0.0,
                                compute=compute_board)
        await svc.start()
        for _ in range(5):
            svc.mark_dirty()
            await asyncio.sleep(0.02)
        await svc.stop()
        return loop_thread

    loop_thread = run(scenario())
    assert used_on and loop_thread not in used_on


def test_hooks_called_from_other_threads_trigger_a_recomputation_and_never_raise():
    async def scenario(db):
        loop = asyncio.get_running_loop()
        counter = Counter()
        svc = make(db, loop, interval=0.05, compute=counter)
        hooks = LoopHooks(loop)
        hooks.bind_scoreboard(svc)
        await svc.start()
        errors = []

        def worker():
            try:
                for _ in range(50):
                    hooks.scoreboard_dirty()
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert await settle(lambda: svc.latest().version >= 2)
        await asyncio.sleep(0.1)
        assert len(counter.calls) <= 10  # 400 lần gọi không thành 400 lần tính
        await svc.stop()

    run_with_db(scenario)


def test_hooks_without_a_bound_service_only_log_and_do_not_raise(caplog):
    async def scenario():
        hooks = LoopHooks(asyncio.get_running_loop())
        with caplog.at_level(logging.INFO, logger="judgehive.hooks"):
            await asyncio.to_thread(hooks.scoreboard_dirty)
            await asyncio.sleep(0.05)
        assert "SCOREBOARD dirty" in caplog.text

    run(scenario())


def test_hooks_after_the_loop_is_closed_do_not_raise():
    loop = asyncio.new_event_loop()
    hooks = LoopHooks(loop)

    class Dummy:
        def mark_dirty(self):
            raise AssertionError("không được chạy khi loop đã đóng")

    hooks.bind_scoreboard(Dummy())
    loop.close()
    result = []
    t = threading.Thread(target=lambda: result.append(hooks.scoreboard_dirty()))
    t.start()
    t.join()
    assert result == [None]


def test_hooks_status_still_only_logs(caplog):
    async def scenario():
        hooks = LoopHooks(asyncio.get_running_loop())
        with caplog.at_level(logging.INFO, logger="judgehive.hooks"):
            await asyncio.to_thread(hooks.status, 7, 1, "done", verdict="AC")
            await asyncio.sleep(0.05)
        assert "STATUS submission=7 user=1 event=done" in caplog.text

    run(scenario())
