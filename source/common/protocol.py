"""TCP là luồng byte: readline đệm đến '\n', không coi mỗi recv là một message."""
import asyncio
import json

MAX_LINE = 8 * 1024 * 1024  # Chiều server → worker: JOB có code và test, JSON có thể escape thành nhiều byte hơn.
# Chiều worker → server chỉ có REGISTER, PROGRESS, RESULT (message ≤ 8 KiB) nên buffer nhỏ,
# kể cả trước khi worker xác thực.
MAX_LINE_TO_SERVER = 64 * 1024
TYPES = {"REGISTER", "REGISTER_OK", "JOB", "PROGRESS", "RESULT", "ERROR"}


def encode(message):
    if not isinstance(message, dict) or message.get("type") not in TYPES:
        raise ValueError("Loại message không hợp lệ")
    data = (json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if len(data) > MAX_LINE:
        raise ValueError("Message quá lớn")
    return data


async def receive(reader, max_line=MAX_LINE):
    try:
        line = await reader.readuntil(b"\n")
    except asyncio.IncompleteReadError as exc:
        raise ConnectionError("TCP đã đóng, hoặc message chưa đủ") from exc
    except asyncio.LimitOverrunError as exc:
        raise ValueError("Message quá lớn") from exc
    if len(line) > max_line:
        raise ValueError("Message quá lớn")
    message = json.loads(line.decode("utf-8"))
    if not isinstance(message, dict) or message.get("type") not in TYPES:
        raise ValueError("Message không hợp lệ")
    return message


async def send(writer, message):
    writer.write(encode(message))
    await asyncio.wait_for(writer.drain(), timeout=10)


def job_timeout(job):
    # Đây là hạn hoàn thành công việc, không phải kiểm tra kết nối định kỳ.
    # Mỗi test cộng 10s tạo/xóa container; thêm 60s chuẩn bị volume và dọn bài.
    return 20 + len(job["tests"]) * (job["time_limit_ms"] / 1000 + 10) + 60
