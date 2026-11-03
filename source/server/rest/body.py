"""Đọc body JSON của `POST` (README mục 3.2): 415 (Content-Type) rồi 413 (kích thước) rồi 400 (JSON hỏng).

Là dependency `async` chỉ đọc stream của request, không đụng SQLite (event loop không gọi SQLite, PLAN 2.1). Giới hạn tính
theo byte nhận từ mạng, kể cả body chunked (không có `Content-Length`)."""

import asyncio
import json
import re

from fastapi import Request
from starlette.requests import ClientDisconnect

from common import protocol
from server.rest.errors import ApiError, invalid
from server.rest.parse import is_ascii_digits, parse_uint

BODY_LIMIT_SUBMIT = 512 * 1024
BODY_LIMIT_DEFAULT = 128 * 1024
BODY_LIMIT_PROBLEM = protocol.MAX_LINE_SERVER_TO_WORKER + 1024 * 1024  # D35: 17 MiB, upload đề cùng bộ test
UPLOAD_READ_TIMEOUT_S = 120  # D35 [ĐỀ XUẤT]: thời gian tối đa để nhận hết body upload đề


def _too_large():
    # Đóng kết nối sau khi trả lời: phần body còn lại chưa đọc.
    return ApiError(413, "BODY_TOO_LARGE", "request body too large", {"Connection": "close"})


def _check_content_type(value):
    media, _, params = (value or "").partition(";")
    if media.strip().lower() != "application/json":
        raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json")
    for item in params.split(";"):
        name, _, val = item.partition("=")
        if name.strip().lower() == "charset" and val.strip().strip('"').lower() not in ("utf-8", "utf8"):
            raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "charset must be utf-8")


def _reject_constant(name):
    raise ValueError(name)  # NaN, Infinity, -Infinity không phải JSON hợp lệ


# Một mẫu cho mọi token cấu trúc của JSON: chuỗi hoặc một ký tự `{ } [ ] , :`. Chuỗi dùng lượng từ chiếm hữu (Python >= 3.11)
# và luôn khớp được từ dấu `"` mở (tới `"` đóng hoặc hết body, kể cả `\` lẻ ở cuối), nên không có lần thử thất bại nào để
# quét lại từ đầu: thời gian tuyến tính theo độ dài body, kể cả với chuỗi không đóng. Quét ở mức byte là an toàn với UTF-8
# (byte nhiều-byte đều >= 0x80, không trùng `"` hay `\`).
_TOKEN = re.compile(rb'"[^"\\]*+(?:\\(?:.|\Z)[^"\\]*+)*+(?:"|\Z)|[{}\[\],:]', re.DOTALL)
_TOKEN_BYTES = (b'"', b"{", b"}", b"[", b"]", b",", b":")


def token_budget_exceeded(raw, max_tokens):
    """True nếu `raw` có hơn `max_tokens` token cấu trúc ngoài nội dung chuỗi (mỗi chuỗi tính một token).

    Chặn khuếch đại bộ nhớ của `json.loads`: `{},` dài 3 byte thành một dict ~64 byte. Chạy trước `json.loads`.
    Đường nhanh: tổng số lần xuất hiện thô của các ký tự token (chặn trên của số token thật) không quá `max_tokens` thì
    qua luôn. Ngược lại đếm token thật và dừng ngay khi vượt, nên body xấu bị từ chối sau vài nghìn token."""
    if sum(raw.count(c) for c in _TOKEN_BYTES) <= max_tokens:
        return False
    count = 0
    for _ in _TOKEN.finditer(raw):
        count += 1
        if count > max_tokens:
            return True
    return False


def json_object_dependency(limit, timeout_name=None, max_tokens=None):
    """`timeout_name`: tên hằng số của module này giữ số giây tối đa để đọc hết body (đọc lại ở mỗi request); quá hạn thì
    400 `INVALID_REQUEST`. Dùng cho upload lớn, nơi dependency đứng sau một cổng đồng thời: client gửi chậm hoặc đứng im
    không được giữ cổng mãi (D35). `max_tokens`: số token cấu trúc tối đa của body (xem `token_budget_exceeded`); vượt thì 400."""
    async def read_json_object(request: Request):
        _check_content_type(request.headers.get("content-type"))
        declared = request.headers.get("content-length")
        if declared is not None:
            if not is_ascii_digits(declared):
                raise invalid()
            length = parse_uint(declared)  # None khi dài quá 19 chữ số: chắc chắn vượt giới hạn
            if length is None or length > limit:
                raise _too_large()
        chunks, total = [], 0
        try:
            async with asyncio.timeout(globals()[timeout_name] if timeout_name else None):
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > limit:
                        raise _too_large()
                    chunks.append(chunk)
        except TimeoutError:
            raise invalid() from None  # đọc body quá hạn: nhả cổng, báo 400
        except ClientDisconnect:
            # Client ngắt giữa chừng: không còn ai nhận response, và đây không phải lỗi server (không 500, không traceback).
            raise invalid() from None
        raw = b"".join(chunks)
        del chunks
        if max_tokens is not None and token_budget_exceeded(raw, max_tokens):
            raise invalid()
        try:
            value = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
        except (ValueError, RecursionError):  # UnicodeDecodeError là ValueError
            raise invalid() from None
        if not isinstance(value, dict):
            raise invalid()
        return value

    return read_json_object


body_default = json_object_dependency(BODY_LIMIT_DEFAULT)
body_submit = json_object_dependency(BODY_LIMIT_SUBMIT)


def utf8_size(text, field):
    """Số byte UTF-8 của `text`; chuỗi có surrogate lẻ (JSON `"\\ud800"`) không mã hóa được nên là 400."""
    try:
        return len(text.encode("utf-8"))
    except UnicodeEncodeError:
        raise invalid(field) from None
