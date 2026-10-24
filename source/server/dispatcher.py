"""Dispatcher (PLAN mục 2.1, D1, D3, D6): một thread giao bài trong `PriorityQueue` cho worker rảnh, ghi verdict, và đưa
lại hàng đợi khi worker chết.

Một vòng giao: giữ chỗ một worker rảnh, rồi mới `get()` một bài; transaction `BEGIN IMMEDIATE` tăng `attempt_id` và số
lần giao, ghi `delivered_test_hash`, xóa `rejudge_requested`; đo độ dài dòng `JOB` đã encode; cuối cùng mới gửi."""

import logging
import queue
import threading
import time

from common import protocol
from server import problems
from server.worker_server import JobState

log = logging.getLogger("judgehive.dispatcher")

LEVEL_REDELIVERY, LEVEL_NEW, LEVEL_REJUDGE = 0, 1, 2  # giao lại > bài mới > rejudge (D1)
# Backoff khi ghi DB lỗi (vòng giao và thread ghi bài mất worker): 0,5 s, 1 s, 2 s, ... tối đa 30 s, về lại 0,5 s sau
# lần thành công. [ĐỀ XUẤT] DB hỏng lâu thì không quay vòng nóng, mỗi lần thất bại đều có log kèm khoảng chờ kế tiếp.
RETRY_BASE_S = 0.5
RETRY_CAP_S = 30.0


def retry_delay(failures):
    """Khoảng chờ sau lần thất bại liên tiếp thứ `failures` (từ 1)."""
    return min(RETRY_BASE_S * 2 ** (failures - 1), RETRY_CAP_S)

_SE_BODY = {"verdict": "SE", "time_ms": None, "memory_kib": None, "failed_test": None, "failed_is_sample": None,
            "re_kind": None, "ce_message": None, "sample_stdout": None, "se_detail": None}


def _now_ms():
    return int(time.time() * 1000)


class Dispatcher:
    def __init__(self, cfg, db, server, hooks):
        self.cfg, self.db, self.server, self.hooks = cfg, db, server, hooks
        self.queue = queue.PriorityQueue()  # phần tử (mức, submission_id)
        self._lost = queue.Queue()  # (job, reason) chờ ghi lại; xử lý ở thread riêng, không chặn thread gọi
        self._stop = threading.Event()
        self._threads = []

    # ------------------------------------------------------------------ vòng đời

    def start(self):
        for target, name in ((self._run, "dispatcher"), (self._lost_loop, "dispatcher-lost")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2)
        self.db.close_thread()

    def enqueue(self, submission_id, level):
        self.queue.put((level, submission_id))

    def load_pending(self):
        """Khởi động lại server (D1, D6, D18): bài `judging` được hoàn lại một lần giao (lần giao đó bị cắt bởi server,
        không phải lỗi của worker), rồi mọi bài `queued` vào hàng đợi ở mức gốc (mới, hoặc rejudge). Trả số bài nạp."""
        with self.db.write() as c:
            c.execute("UPDATE submissions SET state = 'queued', delivery_count = MAX(delivery_count - 1, 0) "
                      "WHERE state = 'judging'")
            rows = c.execute("SELECT id, is_rejudge FROM submissions WHERE state = 'queued' ORDER BY id").fetchall()
        for row in rows:
            self.enqueue(row["id"], LEVEL_REJUDGE if row["is_rejudge"] else LEVEL_NEW)
        log.info("nạp lại %d bài vào hàng đợi", len(rows))
        return len(rows)

    # ------------------------------------------------------------------ vòng giao

    def _run(self):
        failures = 0
        try:
            while not self._stop.is_set():
                conn = self.server.reserve_idle()
                if conn is None:
                    break
                item = self._take(conn)
                if item is None:  # dừng, hoặc worker đã giữ chỗ chết khi đang chờ bài
                    self.server.release(conn)
                    continue
                if not self.server.is_alive(conn):
                    self.queue.put(item)  # chưa gửi nên chưa phải một lần giao: về đúng mức gốc
                    continue
                try:
                    self._deliver(conn, item)
                    failures = 0
                except Exception:
                    failures += 1
                    delay = retry_delay(failures)
                    log.exception("vòng giao bài %s lỗi (lần liên tiếp thứ %d), thử lại sau %.1f s", item[1], failures,
                                  delay)
                    self.server.release(conn)
                    self.queue.put(item)
                    self._stop.wait(delay)
        finally:
            self.db.close_thread()

    def _take(self, conn):
        while not self._stop.is_set():
            try:
                return self.queue.get(timeout=0.2)
            except queue.Empty:
                if not self.server.is_alive(conn):
                    return None
        return None

    def _deliver(self, conn, item):
        _, sid = item
        data = job = failed = None
        with self.db.write() as c:
            row = c.execute("SELECT s.*, p.time_limit_ms AS p_time, p.memory_limit_kib AS p_memory, "
                            "p.tests_hash AS p_hash FROM submissions s JOIN problems p ON p.id = s.problem_id "
                            "WHERE s.id = ?", (sid,)).fetchone()
            if row is None or row["state"] != "queued":
                log.info("bỏ bài %s: không còn ở trạng thái queued", sid)
                row = None
            else:
                tests = c.execute("SELECT is_sample, input, output FROM tests WHERE problem_id = ? ORDER BY position",
                                  (row["problem_id"],)).fetchall()
                tests.sort(key=lambda t: 0 if t["is_sample"] else 1)  # test mẫu trước, giữ thứ tự tương đối (D15)
                attempt = row["attempt_id"] + 1
                if row["delivery_count"] >= self.cfg.max_deliveries:
                    failed = "đã đủ số lần giao"
                else:
                    try:
                        data = protocol.encode(
                            problems.make_job_message(sid, attempt, row["language"], row["code"], row["p_time"],
                                                      row["p_memory"],
                                                      [(t["is_sample"], t["input"], t["output"]) for t in tests]),
                            max_line_bytes=protocol.MAX_LINE_SERVER_TO_WORKER)
                    except protocol.EncodeError as e:
                        failed = f"không dựng được dòng JOB: {e}"  # lưới an toàn D3
                if failed is None:
                    c.execute("UPDATE submissions SET attempt_id = ?, delivery_count = delivery_count + 1, "
                              "delivered_test_hash = ?, rejudge_requested = 0, state = 'judging' WHERE id = ?",
                              (attempt, row["p_hash"], sid))
                    job = JobState(sid, attempt, row["user_id"], len(tests), tuple(bool(t["is_sample"]) for t in tests),
                                   row["p_time"], bool(row["is_rejudge"]))
                else:
                    log.error("bài %s: SE không gửi JOB: %s", sid, failed)
                    level = self._finish_submission(c, row, _SE_BODY)
        if row is None:
            self.server.release(conn)
            return
        if failed is not None:
            self._after_finish(row, _SE_BODY, level)
            self.server.release(conn)
            return
        # Từ đây DB đã ghi `judging`: mọi lỗi phải đưa bài về hàng đợi qua job_lost, không để lọt ra _run (vì _deliver lần
        # sau sẽ bỏ bài không còn `queued`). job_lost idempotent nên gọi thừa cũng không sao.
        try:
            if not self.server.assign_job(conn, job):
                self.job_lost(job, "worker chết trước khi gửi JOB")  # lần giao này đã tính
                return
            self._hook_status(job.submission_id, job.user_id, "judging", attempt_id=job.attempt_id)
            self.server.send_job(conn, data)
        except Exception:
            log.exception("giao bài %s lỗi sau khi ghi DB", sid)
            self.server.mark_dead(conn, "lỗi dispatcher khi gửi JOB")
            self.job_lost(job, "lỗi dispatcher khi giao")

    # ------------------------------------------------------------------ kết quả và mất worker

    def handle_result(self, job, body):
        """Commit verdict của một `RESULT` hợp lệ (đã khớp lần giao và qua kiểm tra ngữ nghĩa). True nếu đã ghi."""
        with self.db.write() as c:
            row = c.execute("SELECT * FROM submissions WHERE id = ?", (job.submission_id,)).fetchone()
            if row is None or row["state"] != "judging" or row["attempt_id"] != job.attempt_id:
                log.info("bỏ RESULT cũ của bài %s attempt %s", job.submission_id, job.attempt_id)
                return False
            if body["verdict"] == "SE":
                log.warning("bài %s: worker báo SE: %r", job.submission_id, (body["se_detail"] or "")[:200])
            level = self._finish_submission(c, row, body)
        self._after_finish(row, body, level)
        return True

    def job_lost(self, job, reason):
        """Worker mất khi đang giữ `job` (D1, D16). Chỉ xếp hàng: thread giám sát và thread kết nối gọi hàm này không
        được chờ SQLite (busy_timeout có thể tới 5 giây làm hoãn PING và watchdog của mọi worker)."""
        self._lost.put((job, reason))

    def _lost_loop(self):
        """Ghi lại các bài bị mất worker. Lỗi DB thì thử lại cho tới khi được: bài không bao giờ bị bỏ ở `judging`."""
        failures = 0
        try:
            while not self._stop.is_set():
                try:
                    job, reason = self._lost.get(timeout=0.2)
                except queue.Empty:
                    continue
                try:
                    self._job_lost(job, reason)
                    failures = 0
                except Exception:
                    failures += 1
                    delay = retry_delay(failures)
                    log.exception("ghi lại bài %s bị mất worker lỗi (lần liên tiếp thứ %d), thử lại sau %.1f s",
                                  job.submission_id, failures, delay)
                    self._lost.put((job, reason))
                    self._stop.wait(delay)
        finally:
            self.db.close_thread()

    def _job_lost(self, job, reason):
        """Giao lại, hoặc SE nếu đã đủ số lần giao. Idempotent: gọi lại khi bài đã được xử lý thì không làm gì."""
        finished = False
        with self.db.write() as c:
            row = c.execute("SELECT * FROM submissions WHERE id = ?", (job.submission_id,)).fetchone()
            if row is None or row["state"] != "judging" or row["attempt_id"] != job.attempt_id:
                return
            if row["delivery_count"] >= self.cfg.max_deliveries:
                level = self._finish_submission(c, row, _SE_BODY)
                finished = True
            else:
                c.execute("UPDATE submissions SET state = 'queued' WHERE id = ?", (job.submission_id,))
                level = LEVEL_REJUDGE if row["is_rejudge"] else LEVEL_REDELIVERY
        log.info("bài %s (attempt %s) mất worker: %s", job.submission_id, job.attempt_id, reason)
        if finished:
            self._after_finish(row, _SE_BODY, level)
            return
        self.enqueue(job.submission_id, level)
        if level == LEVEL_REDELIVERY:  # bài rejudge giao lại không đẩy STATUS queued (D6)
            self._hook_status(job.submission_id, row["user_id"], "queued", reason="redelivery")

    def _finish_submission(self, c, row, body):
        """Trong transaction: ghi verdict (SE của lần rejudge thì chỉ đặt `rejudge_failed`, giữ verdict cũ, D6), rồi
        xử lý `rejudge_requested`. Trả mức hàng đợi cần đưa bài vào sau commit, hoặc None."""
        now = _now_ms()
        if body["verdict"] == "SE" and row["is_rejudge"]:
            c.execute("UPDATE submissions SET state = 'done', is_rejudge = 0, rejudge_failed = 1, finished_at = ? "
                      "WHERE id = ?", (now, row["id"]))
        else:
            sample = body["failed_is_sample"]
            c.execute("UPDATE submissions SET state = 'done', verdict = ?, time_ms = ?, memory_kib = ?, "
                      "failed_test = ?, failed_is_sample = ?, re_kind = ?, ce_message = ?, sample_stdout = ?, "
                      "verdict_test_hash = delivered_test_hash, finished_at = ?, rejudge_failed = 0, "
                      "rejudged_at = CASE WHEN is_rejudge = 1 THEN ? ELSE rejudged_at END, is_rejudge = 0 "
                      "WHERE id = ?",
                      (body["verdict"], body["time_ms"], body["memory_kib"], body["failed_test"],
                       None if sample is None else int(sample), body["re_kind"], body["ce_message"],
                       body["sample_stdout"], now, now, row["id"]))
        if row["rejudge_requested"]:
            c.execute("UPDATE submissions SET state = 'queued', is_rejudge = 1, rejudge_requested = 0 WHERE id = ?",
                      (row["id"],))
            return LEVEL_REJUDGE
        return None

    def _after_finish(self, row, body, level):
        self._hook_status(row["id"], row["user_id"], "done", verdict=body["verdict"])
        try:
            self.hooks.scoreboard_dirty()
        except Exception:
            log.exception("hook scoreboard lỗi")
        if level is not None:
            self.enqueue(row["id"], level)

    def _hook_status(self, submission_id, user_id, event, **info):
        try:
            self.hooks.status(submission_id, user_id, event, **info)
        except Exception:
            log.exception("hook status lỗi")
