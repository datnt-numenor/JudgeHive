"""Framing JSON-lines và hằng số giao thức của cổng worker (README mục "Giao tiếp").

Module này không làm I/O: bên gọi tự đọc/ghi socket. Chỉ dùng thư viện chuẩn.
"""

import json
import re
from dataclasses import dataclass

# Giá trị đã chốt trong README / PLAN.
PROTOCOL_VERSION = 1
MAX_LINE_WORKER_TO_SERVER = 1_048_576  # 1 MB (README 2.2)
MAX_LINE_SERVER_TO_WORKER = 16 * 1_048_576  # 16 MB (README 2.2)
MAX_TESTS_TOTAL_BYTES = 8 * 1_048_576  # tổng test mỗi đề (D3)
MAX_CODE_BYTES = 64 * 1024  # code nộp, byte UTF-8 (D3, D15)
DEFAULT_HTTP_PORT = 8000  # D15
DEFAULT_WORKER_PORT = 9000  # D15

# [ĐỀ XUẤT] (README mục 2.6, D1, D16); chỉnh sau khi đo ở PR spike executor.
PING_INTERVAL_S = 5
PEER_DEAD_AFTER_S = 15
SEND_TIMEOUT_S = 30
REGISTER_TIMEOUT_S = 10
MAX_UNAUTHENTICATED = 16
RECONNECT_BACKOFF_BASE_S = 1
RECONNECT_BACKOFF_CAP_S = 10
COMPILE_TIMEOUT_MAX_S = 10
SLACK_S = 5
MAX_TIME_FACTOR = 3
PER_TEST_OVERHEAD_S = 3

REASON_INVALID_UTF8 = "INVALID_UTF8"
REASON_INVALID_JSON = "INVALID_JSON"
REASON_NOT_OBJECT = "NOT_OBJECT"
REASON_MISSING_TYPE = "MISSING_TYPE"

_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


@dataclass(frozen=True)
class Message:
    """Một object JSON hợp lệ, có `type` là chuỗi."""

    obj: dict


@dataclass(frozen=True)
class Malformed:
    """Một dòng đủ độ dài nhưng không phải message hợp lệ; `reason` chỉ để ghi log."""

    reason: str


@dataclass(frozen=True)
class LineTooLong:
    """Dòng vượt giới hạn; sau sự kiện này decoder dừng và kết nối phải đóng."""


class EncodeError(ValueError):
    """Không serialize được message, hoặc dòng kết quả vượt giới hạn."""


def _reject_constant(name):
    raise ValueError(f"constant {name} is not valid JSON")


def _has_lone_surrogate(root):
    stack = [root]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if _LONE_SURROGATE.search(item):
                return True
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return False


def _parse_line(line):
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError:
        return Malformed(REASON_INVALID_UTF8)
    try:
        obj = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return Malformed(REASON_INVALID_JSON)
    if not isinstance(obj, dict):
        return Malformed(REASON_NOT_OBJECT)
    if _has_lone_surrogate(obj):
        return Malformed(REASON_INVALID_JSON)
    if not isinstance(obj.get("type"), str):
        return Malformed(REASON_MISSING_TYPE)
    return Message(obj)


class LineDecoder:
    """Tách byte nhận được thành message, theo framing một object JSON mỗi dòng.

    Độ dài một dòng tính theo byte, gồm cả `\\n` kết thúc. Lỗi parse chỉ làm hỏng
    dòng đó; dòng quá dài làm decoder dừng (`failed = True`).
    """

    def __init__(self, max_line_bytes):
        if max_line_bytes < 2:
            raise ValueError("max_line_bytes must be at least 2")
        self._max = max_line_bytes
        self._buf = bytearray()
        self._scanned = 0  # các byte đầu buffer tới đây đã biết không có `\n`
        self.failed = False

    def feed(self, data):
        if self.failed:
            return []
        self._buf += data
        events = []
        buf = self._buf
        start = 0
        while True:
            idx = buf.find(b"\n", max(start, self._scanned))
            if idx == -1:
                break
            if idx + 1 - start > self._max:
                return self._fail(events)
            events.append(_parse_line(bytes(buf[start:idx])))
            start = idx + 1
            self._scanned = start
        if start:
            del buf[:start]
        self._scanned = len(buf)
        if len(buf) >= self._max:
            return self._fail(events)
        return events

    def _fail(self, events):
        self.failed = True
        self._buf = bytearray()
        events.append(LineTooLong())
        return events


def encode(obj, *, max_line_bytes=None):
    """Serialize một message thành một dòng: UTF-8 thô, kết thúc bằng `\\n`."""
    if not isinstance(obj, dict):
        raise EncodeError("message must be a dict")
    try:
        data = json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise EncodeError(str(exc)) from exc
    data += b"\n"
    if max_line_bytes is not None and len(data) > max_line_bytes:
        raise EncodeError(f"line is {len(data)} bytes, limit is {max_line_bytes}")
    return data
