"""TCP là luồng byte: readline đệm đến '\n', không coi mỗi recv là một message."""
import asyncio
import json

MAX_LINE = 8 * 1024 * 1024  # JSON có thể escape dữ liệu test thành nhiều byte hơn.
TYPES = {"REGISTER", "REGISTER_OK", "JOB", "PROGRESS", "RESULT", "ERROR"}


def encode(message):
    if not isinstance(message, dict) or message.get("type") not in TYPES:
        raise ValueError("Loại message không hợp lệ")
    data = (json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if len(data) > MAX_LINE:
        raise ValueError("Message quá lớn")
    return data


async def receive(reader):
    try:
        line = await reader.readuntil(b"\n")
    except asyncio.IncompleteReadError as exc:
        raise ConnectionError("TCP đã đóng, hoặc message chưa đủ") from exc
    except asyncio.LimitOverrunError as exc:
        raise ValueError("Message quá lớn") from exc
    if len(line) > MAX_LINE:
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
