"""Đọc body JSON của `POST` (README mục 3.2): 415 (Content-Type) rồi 413 (kích thước) rồi 400 (JSON hỏng).

Là dependency `async` chỉ đọc stream của request, không đụng SQLite (event loop không gọi SQLite, PLAN 2.1). Giới hạn tính
theo byte nhận từ mạng, kể cả body chunked (không có `Content-Length`)."""

import json

from fastapi import Request
from starlette.requests import ClientDisconnect

from server.rest.errors import ApiError, invalid
from server.rest.parse import is_ascii_digits, parse_uint

BODY_LIMIT_SUBMIT = 512 * 1024
BODY_LIMIT_DEFAULT = 128 * 1024


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


def json_object_dependency(limit):
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
            async for chunk in request.stream():
                total += len(chunk)
                if total > limit:
                    raise _too_large()
                chunks.append(chunk)
        except ClientDisconnect:
            # Client ngắt giữa chừng: không còn ai nhận response, và đây không phải lỗi server (không 500, không traceback).
            raise invalid() from None
        try:
            value = json.loads(b"".join(chunks).decode("utf-8"), parse_constant=_reject_constant)
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
