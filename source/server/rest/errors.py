"""Định dạng lỗi và response của REST (README mục 3.2, 3.5, D9).

Mọi lỗi của `/api/*` có đúng dạng `{"error": {"code", "message"}}`. `message` do server viết cố định, không bao giờ
chứa lại giá trị client đã gửi (mật khẩu, mã nguồn, token...), chỉ có thể nêu tên field."""

import logging

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match

from server import static

log = logging.getLogger("judgehive.rest")

JSON_TYPE = "application/json; charset=utf-8"


class ApiJSONResponse(JSONResponse):
    """JSON UTF-8 với `Cache-Control: no-store` (README 3.2). Mọi response của `/api/*` đi qua lớp này hoặc `no_content`."""

    media_type = JSON_TYPE

    def __init__(self, content, status_code=200, headers=None):
        super().__init__(content, status_code=status_code, headers={"Cache-Control": "no-store", **(headers or {})})


def no_content(headers=None):
    return Response(status_code=204, headers={"Cache-Control": "no-store", **(headers or {})})


class ApiError(Exception):
    def __init__(self, status, code, message, headers=None):
        super().__init__(code)
        self.status, self.code, self.message, self.headers = status, code, message, dict(headers or {})


def invalid(field=None):
    return ApiError(400, "INVALID_REQUEST", f"field '{field}' is invalid" if field else "request is invalid")


def unauthenticated():
    return ApiError(401, "UNAUTHENTICATED", "authentication required")


def problem_not_found():
    return ApiError(404, "PROBLEM_NOT_FOUND", "problem not found")


def submission_not_found():
    return ApiError(404, "SUBMISSION_NOT_FOUND", "submission not found")


def rate_limited(retry_after_s):
    return ApiError(429, "RATE_LIMITED", "too many requests", {"Retry-After": str(max(1, int(retry_after_s)))})


def error_response(status, code, message, headers=None):
    return ApiJSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)


def _is_api(request):
    return request.url.path.startswith("/api/") or request.url.path == "/api"


async def handle_api_error(request, exc):
    return error_response(exc.status, exc.code, exc.message, exc.headers)


def _allowed_methods(request):
    """Hợp các method của mọi route khớp đường dẫn. Starlette chỉ báo method của route đầu tiên khớp một phần, nên
    `/api/submissions` (GET và POST là hai route) sẽ thiếu một method trong `Allow`."""
    methods = set()
    for route in request.app.routes:
        match, _ = route.matches(request.scope)
        if match != Match.NONE:
            methods |= route.methods or set()
    return sorted(methods)


async def handle_http_exception(request, exc):
    if not _is_api(request):
        if exc.status_code == 404:
            fallback = static.spa_fallback(request)  # trang của SPA (GET/HEAD, không đuôi file, không /api /ws /assets)
            if fallback is not None:
                return fallback
        from fastapi.exception_handlers import http_exception_handler
        return await http_exception_handler(request, exc)
    headers = {}
    if exc.status_code == 404:
        code, message = "NOT_FOUND", "not found"
    elif exc.status_code == 405:
        code, message = "METHOD_NOT_ALLOWED", "method not allowed"
        allow = _allowed_methods(request)
        if allow:
            headers["Allow"] = ", ".join(allow)
    else:
        code, message = "INTERNAL_ERROR", "internal error"
        return error_response(500, code, message)
    return error_response(exc.status_code, code, message, headers)


async def handle_validation_error(request, exc):
    # Mặc định của FastAPI trả lại cả `input` đã gửi; không bao giờ dùng (D9).
    return error_response(400, "INVALID_REQUEST", "request is invalid")


async def handle_unexpected(request, exc):
    log.error("lỗi không dự kiến ở %s %s", request.method, request.url.path, exc_info=exc)
    return error_response(500, "INTERNAL_ERROR", "internal error")


def install_handlers(app):
    app.add_exception_handler(ApiError, handle_api_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unexpected)
