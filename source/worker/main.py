"""Vòng đọc TCP luôn chạy song song với thread Docker, không có heartbeat."""
import asyncio
import contextlib
import logging
import os
import signal
import socket
import threading
from common.protocol import MAX_LINE, encode, receive, send
from worker.runner import grade

log = logging.getLogger(__name__)


def validate_job(job):
    # Worker tin server (server giữ đề và đã kiểm tra); chỉ kiểm những gì cần để không chạy sai bài.
    # JOB sai kiểu ở chỗ khác sẽ làm grade ném lỗi và được báo SE.
    if (job.get("type") != "JOB" or job.get("language") not in {"python", "cpp", "java"}
            or type(job.get("submission_id")) is not int or type(job.get("attempt_id")) is not int
            or not isinstance(job.get("tests"), list)):
        raise ValueError("JOB không hợp lệ")


async def connection(host, port, secret, name, stop):
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port, limit=MAX_LINE), 10)
    reading = running = stopping = None
    cancel = threading.Event()
    try:
        await send(writer, {"type": "REGISTER", "name": name, "secret": secret})
        if (await asyncio.wait_for(receive(reader), 10))["type"] != "REGISTER_OK":
            raise ValueError("Server từ chối đăng ký")
        log.info("Đã đăng ký với %s:%s", host, port)
        reading = asyncio.create_task(receive(reader))
        # stopping: SIGTERM phải đi qua finally để kill container, không để process chết ngang.
        stopping = asyncio.create_task(stop.wait())
        loop = asyncio.get_running_loop()
        while not stop.is_set():
            done, _ = await asyncio.wait([reading, stopping], return_when=asyncio.FIRST_COMPLETED)
            if stopping in done:
                return
            job = reading.result()
            validate_job(job)
            cancel = threading.Event()
            ids = {"submission_id": job["submission_id"], "attempt_id": job["attempt_id"]}

            def progress(n):
                # Thread chấm không được gọi thẳng writer (StreamWriter không an toàn giữa các thread):
                # nhờ event loop ghi hộ. PROGRESS nhỏ, tối đa 20 cái mỗi bài nên không cần drain.
                loop.call_soon_threadsafe(writer.write, encode({"type": "PROGRESS", **ids, "tests_done": n}))

            # grade gọi Docker CLI (chặn) nên chạy ở thread; vòng đọc TCP vẫn chạy để phát hiện mất server.
            running = asyncio.create_task(asyncio.to_thread(grade, job, cancel, progress))
            reading = asyncio.create_task(receive(reader))
            done, _ = await asyncio.wait([reading, running, stopping], return_when=asyncio.FIRST_COMPLETED)
            if stopping in done:
                return
            if reading in done:
                # EOF: finally đặt cancel và đợi dọn Docker trước khi nối lại.
                reading.result()
                raise ValueError("Server gửi bài mới khi worker đang bận")
            result = running.result()
            running = None
            if result is not None:
                await send(writer, {"type": "RESULT", **ids, **result})
    finally:
        cancel.set()
        writer.close()
        for task in (reading, stopping):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        if running:
            # Không cancel được một thread. Nếu bỏ đi mà không chờ thì container vẫn chạy trong lúc
            # worker đã nối lại; shield để chờ runner thật sự kill container và xóa volume.
            await asyncio.shield(running)
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    secret = os.environ.get("JH_WORKER_SECRET", "")
    if len(secret) < 16:
        raise RuntimeError("JH_WORKER_SECRET cần ít nhất 16 ký tự")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    while not stop.is_set():
        try:
            await connection(os.environ.get("JH_SERVER_HOST", "server"), int(os.environ.get("JH_WORKER_PORT", "9000")),
                             secret, os.environ.get("JH_WORKER_NAME", socket.gethostname()), stop)
        except (OSError, ConnectionError, ValueError, TimeoutError):
            log.exception("Kết nối đóng/lỗi; dọn xong rồi thử lại sau 3 giây")
        if not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), 3)
            except TimeoutError:
                pass


if __name__ == "__main__":
    asyncio.run(main())
