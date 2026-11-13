"""Mỗi kết nối worker lấy bài từ cùng asyncio.Queue: FIFO, không cần dispatcher thread."""
import asyncio
import contextlib
import hmac
import itertools
import logging
from common.protocol import MAX_LINE_TO_SERVER, receive, send, job_timeout
from server.db import now

log = logging.getLogger(__name__)
VERDICTS = {"AC", "WA", "TLE", "MLE", "RE", "CE", "SE"}
# Số kết nối TCP đang chờ REGISTER; vượt trần thì đóng ngay, không đọc byte nào.
MAX_UNREGISTERED = 16
REGISTER_TIMEOUT = 10  # giây chờ REGISTER sau khi mở TCP
MAX_TIME_MS = 10**9  # time_ms lớn hơn là sai; tránh tràn INTEGER của SQLite
MAX_ATTEMPTS = 3  # số lần giao tối đa một bài; quá thì SE


class Link:
    """Trạng thái của một kết nối worker."""

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.worker = None   # bản ghi trong Engine.workers sau REGISTER_OK
        self.job = None      # JOB đang giao; còn giá trị khi kết nối hỏng thì bài được giao lại
        self.reading = None  # task đọc message kế tiếp, chạy cả khi worker rảnh

    def read_next(self):
        self.reading = asyncio.create_task(receive(self.reader, MAX_LINE_TO_SERVER))


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
                                 "WHERE state='judging' AND attempt_id>=?", (now(), MAX_ATTEMPTS))
            self.db.conn.execute("UPDATE submissions SET state='queued',tests_done=0 WHERE state='judging'")
        for row in self.db.all("SELECT id FROM submissions WHERE state='queued' ORDER BY id"):
            self.queue.put_nowait(row["id"])

    def view(self):
        return [{k: v for k, v in worker.items() if k != "writer"} for worker in self.workers.values()]

    async def handle(self, reader, writer):
        """Một coroutine cho mỗi kết nối worker: đăng ký, rồi lặp chờ bài → chấm."""
        if self.unregistered >= MAX_UNREGISTERED:
            writer.close()  # vượt trần: đóng ngay, không đọc byte nào
            return
        task = asyncio.current_task()
        self.tasks.add(task)
        link = Link(reader, writer)
        try:
            if await self.register(link):
                while not self.stopping:
                    await self.judge(link, await self.wait_job_or_eof(link))
        except (ConnectionError, OSError, ValueError, TimeoutError) as exc:
            log.info("Worker ngắt: %s", exc)
        except Exception:
            log.exception("Lỗi xử lý worker")
        finally:
            if link.reading is not None:
                link.reading.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await link.reading
            writer.close()
            if link.worker:
                # Worker nối lại sẽ nhận id mới; không giữ bản ghi cũ trong danh sách.
                self.workers.pop(link.worker["id"], None)
            if link.job and not self.stopping:
                self.requeue(link.job)
            if not self.stopping:
                await self.hub.publish(link.job["submission_id"] if link.job else None)
            self.tasks.discard(task)

    async def register(self, link):
        """Đọc REGISTER (tối đa REGISTER_TIMEOUT giây). Sai thì gửi ERROR và trả False."""
        self.unregistered += 1
        try:
            registration = await asyncio.wait_for(receive(link.reader, MAX_LINE_TO_SERVER), REGISTER_TIMEOUT)
        finally:
            self.unregistered -= 1
        secret, name = registration.get("secret"), registration.get("name")
        # compare_digest: thời gian so không lộ secret đúng được bao nhiêu ký tự. So bytes vì compare_digest
        # với str ném TypeError khi có ký tự ngoài ASCII; surrogatepass vì JSON cho phép surrogate lẻ (U+D800).
        if (registration.get("type") != "REGISTER" or not isinstance(secret, str)
                or not hmac.compare_digest(secret.encode("utf-8", "surrogatepass"), self.secret.encode())
                or not isinstance(name, str) or not 1 <= len(name) <= 64):
            await send(link.writer, {"type": "ERROR", "message": "Đăng ký worker không hợp lệ"})
            return False
        wid = next(self.ids)
        link.worker = {"id": wid, "name": name, "state": "idle", "submission_id": None, "writer": link.writer}
        self.workers[wid] = link.worker
        await send(link.writer, {"type": "REGISTER_OK", "worker_id": wid})
        await self.hub.publish()
        link.read_next()
        return True

    async def wait_job_or_eof(self, link):
        """Chờ đồng thời: một bài trong hàng đợi, hoặc worker rảnh đóng kết nối/gửi gì đó.
        Đọc song song khi rảnh để phát hiện EOF ngay và gỡ worker khỏi danh sách; nếu không,
        bài sẽ bị giao vào kết nối đã chết và mất một lượt giao."""
        taking = asyncio.create_task(self.queue.get())
        try:
            done, _ = await asyncio.wait([link.reading, taking], return_when=asyncio.FIRST_COMPLETED)
        finally:
            if not taking.done():
                taking.cancel()
        if link.reading in done:
            # EOF và queue.get có thể hoàn tất cùng lúc: trả bài đã lấy về hàng đợi.
            if taking.done() and not taking.cancelled():
                self.queue.put_nowait(taking.result())
            await link.reading  # EOF thì ném ConnectionError
            raise ValueError("Worker rảnh gửi message không mong đợi")
        return taking.result()

    async def judge(self, link, sid):
        """Giao JOB rồi nhận PROGRESS/RESULT tới khi xong hoặc hết hạn công việc."""
        row = self.db.one("SELECT s.*,p.time_limit_ms,p.memory_limit_kib FROM submissions s "
                          "JOIN problems p ON p.id=s.problem_id WHERE s.id=?", (sid,))
        if not row or row["state"] != "queued":
            return  # bài đã được worker khác lấy hoặc đã xong
        attempt = row["attempt_id"] + 1
        self.db.execute("UPDATE submissions SET state='judging',attempt_id=?,tests_done=0 WHERE id=?", (attempt, sid))
        tests = self.db.all("SELECT input,output FROM tests WHERE problem_id=? ORDER BY position", (row["problem_id"],))
        # Gán link.job TRƯỚC khi gửi: gửi lỗi thì handle vẫn giao lại bài.
        link.job = job = {"type": "JOB", "submission_id": sid, "attempt_id": attempt,
                          "language": row["language"], "code": row["code"], "tests": tests,
                          "time_limit_ms": row["time_limit_ms"], "memory_limit_kib": row["memory_limit_kib"]}
        link.worker.update(state="judging", submission_id=sid)
        await send(link.writer, job)
        # Hạn công việc tính từ lúc gửi JOB xong; PROGRESS không gia hạn. Đây không phải heartbeat.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + job_timeout(job)
        await self.hub.publish(sid)
        done = 0  # tests_done đã nhận, phải tăng chặt
        while True:
            message = await asyncio.wait_for(link.reading, max(0, deadline - loop.time()))
            link.read_next()
            # type(x) is int (không dùng isinstance) vì True cũng là int.
            if (type(message.get("submission_id")) is not int or type(message.get("attempt_id")) is not int
                    or message["submission_id"] != sid or message["attempt_id"] != attempt):
                raise ValueError("Message sai lần giao")
            if message["type"] == "PROGRESS":
                count = message.get("tests_done")
                if type(count) is not int or not done < count <= len(tests):
                    raise ValueError("Tiến độ không hợp lệ")
                done = count
                self.db.execute("UPDATE submissions SET tests_done=? WHERE id=?", (count, sid))
                await self.hub.publish(sid)
                continue
            verdict, elapsed, detail = message.get("verdict"), message.get("time_ms"), message.get("message")
            if (message["type"] != "RESULT" or verdict not in VERDICTS
                    or type(elapsed) is not int or not 0 <= elapsed <= MAX_TIME_MS
                    or not isinstance(detail, str) or len(detail.encode()) > 8192):
                raise ValueError("Kết quả không hợp lệ")
            # Điều kiện attempt_id=? AND state='judging' là chốt chặn thứ hai, phòng lần giao cũ ghi đè lần giao mới.
            self.db.execute("UPDATE submissions SET state='done',verdict=?,time_ms=?,message=?,finished_at=?,"
                            "tests_done=CASE WHEN ?='AC' THEN ? ELSE tests_done END "
                            "WHERE id=? AND attempt_id=? AND state='judging'",
                            (verdict, elapsed, detail, now(), verdict, len(tests), sid, attempt))
            link.job = None
            link.worker.update(state="idle", submission_id=None)
            await self.hub.publish(sid)
            return

    def requeue(self, job):
        """Kết nối hỏng khi đang giữ bài: giao lại, hoặc SE nếu đã giao đủ MAX_ATTEMPTS lần."""
        sid, attempt = job["submission_id"], job["attempt_id"]
        if attempt >= MAX_ATTEMPTS:
            self.db.execute("UPDATE submissions SET state='done',verdict='SE',finished_at=?,"
                            "message='Không nhận được kết quả sau số lần giao tối đa' WHERE id=? AND attempt_id=?",
                            (now(), sid, attempt))
        else:
            self.db.execute("UPDATE submissions SET state='queued',tests_done=0 WHERE id=? AND attempt_id=?",
                            (sid, attempt))
            self.queue.put_nowait(sid)

    async def stop(self):
        self.stopping = True
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)
