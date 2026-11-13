"""Mỗi kết nối worker lấy bài từ cùng asyncio.Queue: FIFO, không cần dispatcher thread."""
import asyncio
import contextlib
import hmac
import itertools
import logging
import math
from common.protocol import MAX_LINE_TO_SERVER, receive, send, job_timeout
from server.db import now

log = logging.getLogger(__name__)
VERDICTS = {"AC", "WA", "TLE", "MLE", "RE", "CE", "SE"}
# Số kết nối TCP đang chờ REGISTER; vượt trần thì đóng ngay, không đọc byte nào.
MAX_UNREGISTERED = 16


class Engine:
    def __init__(self, db, hub, secret):
        self.db, self.hub, self.secret = db, hub, secret
        self.queue = asyncio.Queue()
        self.workers = {}
        self.ids = itertools.count(1)
        self.tasks = set()
        self.stopping = False
        self.unregistered = 0

    def recover(self):
        with self.db.conn:
            self.db.conn.execute("UPDATE submissions SET state='done',verdict='SE',finished_at=?,"
                                 "message='Server khởi động lại sau lần giao cuối' "
                                 "WHERE state='judging' AND attempt_id>=3", (now(),))
            self.db.conn.execute("UPDATE submissions SET state='queued',tests_done=0 WHERE state='judging'")
        for row in self.db.all("SELECT id FROM submissions WHERE state='queued' ORDER BY id"):
            self.queue.put_nowait(row["id"])

    def view(self):
        return [{k: v for k, v in worker.items() if k != "writer"} for worker in self.workers.values()]

    async def handle(self, reader, writer):
        if self.unregistered >= MAX_UNREGISTERED:
            writer.close()
            return
        task = asyncio.current_task()
        self.tasks.add(task)
        worker = None
        job = None
        reading = taking = None
        try:
            self.unregistered += 1
            try:
                registration = await asyncio.wait_for(receive(reader, MAX_LINE_TO_SERVER), 10)
            finally:
                self.unregistered -= 1
            secret = registration.get("secret")
            # So bytes: compare_digest với str ném TypeError khi có ký tự ngoài ASCII.
            if (registration.get("type") != "REGISTER" or not isinstance(secret, str)
                    or not hmac.compare_digest(secret.encode("utf-8", "surrogatepass"), self.secret.encode())
                    or not isinstance(registration.get("name"), str) or not 1 <= len(registration["name"]) <= 64):
                await send(writer, {"type": "ERROR", "message": "Đăng ký worker không hợp lệ"})
                return
            wid = next(self.ids)
            worker = {"id": wid, "name": registration["name"], "state": "idle", "submission_id": None,
                      "writer": writer}
            self.workers[wid] = worker
            await send(writer, {"type": "REGISTER_OK", "worker_id": wid})
            await self.hub.publish()
            reading = asyncio.create_task(receive(reader, MAX_LINE_TO_SERVER))
            while not self.stopping:
                taking = asyncio.create_task(self.queue.get())
                done, _ = await asyncio.wait([reading, taking], return_when=asyncio.FIRST_COMPLETED)
                if reading in done:
                    # EOF và queue.get có thể hoàn tất cùng lúc: không làm mất bài đã lấy.
                    if taking.done() and not taking.cancelled():
                        self.queue.put_nowait(taking.result())
                    await reading
                    raise ValueError("Worker rảnh gửi message không mong đợi")
                sid = taking.result()
                taking = None
                row = self.db.one("SELECT s.*,p.time_limit_ms,p.memory_limit_kib FROM submissions s "
                                  "JOIN problems p ON p.id=s.problem_id WHERE s.id=?", (sid,))
                if not row or row["state"] != "queued":
                    continue
                attempt = row["attempt_id"] + 1
                self.db.execute("UPDATE submissions SET state='judging',attempt_id=?,tests_done=0 WHERE id=?",
                                (attempt, sid))
                tests = self.db.all("SELECT input,output FROM tests WHERE problem_id=? ORDER BY position",
                                    (row["problem_id"],))
                job = {"type": "JOB", "submission_id": sid, "attempt_id": attempt,
                       "language": row["language"], "code": row["code"], "tests": tests,
                       "time_limit_ms": row["time_limit_ms"], "memory_limit_kib": row["memory_limit_kib"]}
                worker.update(state="judging", submission_id=sid)
                deadline = asyncio.get_running_loop().time() + job_timeout(job)
                await send(writer, job)
                await self.hub.publish(sid)
                while True:
                    # PROGRESS không thay đổi deadline. Đây là timeout công việc, không heartbeat.
                    remaining = deadline - asyncio.get_running_loop().time()
                    message = await asyncio.wait_for(reading, max(0, remaining))
                    if (type(message.get("submission_id")) is not int or type(message.get("attempt_id")) is not int
                            or message["submission_id"] != sid or message["attempt_id"] != attempt):
                        raise ValueError("Kết quả sai lần giao")
                    if message["type"] == "PROGRESS":
                        count = message.get("tests_done")
                        previous = self.db.one("SELECT tests_done FROM submissions WHERE id=?", (sid,))["tests_done"]
                        if type(count) is not int or not previous < count <= len(tests):
                            raise ValueError("Tiến độ không hợp lệ")
                        self.db.execute("UPDATE submissions SET tests_done=? WHERE id=?", (count, sid))
                        await self.hub.publish(sid)
                        reading = asyncio.create_task(receive(reader, MAX_LINE_TO_SERVER))
                        continue
                    verdict, elapsed = message.get("verdict"), message.get("time_ms")
                    detail = message.get("message", "")
                    if (message["type"] != "RESULT" or verdict not in VERDICTS
                            or type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0
                            or not isinstance(detail, str) or len(detail.encode()) > 8192):
                        raise ValueError("Kết quả không hợp lệ")
                    # Chỉ kết nối giữ job này mới được ghi kết quả của attempt hiện tại.
                    self.db.execute("UPDATE submissions SET state='done',verdict=?,time_ms=?,message=?,finished_at=?,"
                                    "tests_done=CASE WHEN ?='AC' THEN ? ELSE tests_done END "
                                    "WHERE id=? AND attempt_id=? AND state='judging'",
                                    (verdict, int(elapsed), detail, now(), verdict, len(tests), sid, attempt))
                    job = None
                    worker.update(state="idle", submission_id=None)
                    await self.hub.publish(sid)
                    reading = asyncio.create_task(receive(reader, MAX_LINE_TO_SERVER))
                    break
        except (ConnectionError, OSError, ValueError, TimeoutError) as exc:
            log.info("Worker ngắt: %s", exc)
        except Exception:
            log.exception("Lỗi xử lý worker")
        finally:
            for pending in (taking, reading):
                if pending is not None:
                    pending.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await pending
            writer.close()
            if worker:
                # Worker nối lại sẽ nhận id mới; không giữ bản ghi cũ trong danh sách.
                self.workers.pop(worker["id"], None)
            if job and not self.stopping:
                sid, attempt = job["submission_id"], job["attempt_id"]
                if attempt >= 3:
                    self.db.execute("UPDATE submissions SET state='done',verdict='SE',finished_at=?,"
                                    "message='Không nhận được kết quả sau 3 lần giao' WHERE id=? AND attempt_id=?",
                                    (now(), sid, attempt))
                else:
                    self.db.execute("UPDATE submissions SET state='queued',tests_done=0 WHERE id=? AND attempt_id=?",
                                    (sid, attempt))
                    self.queue.put_nowait(sid)
            if not self.stopping:
                await self.hub.publish(job["submission_id"] if job else None)
            self.tasks.discard(task)

    async def stop(self):
        self.stopping = True
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)
