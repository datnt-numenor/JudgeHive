"""Phục vụ giao diện web đã build, cùng origin với API (không CORS).

Chỉ hai thứ, và không có route nào khớp mọi đường dẫn:
  - `/assets/*`: file thật trong thư mục `assets/` (Vite đặt mọi file tĩnh ở đó, tên có băm nội dung nên cache dài hạn).
  - Fallback của SPA: đường dẫn GET/HEAD không khớp route nào, không thuộc `/api`, `/ws`, `/assets` và không có đuôi file
    thì trả `index.html`. Chạy trong bộ xử lý 404 (`rest/errors.py`), nên không bao giờ che 404 hay 405 của API.

Đường dẫn của client không bao giờ được ghép vào đường dẫn file: `index.html` có đường dẫn cố định, còn `/assets` do
`StaticFiles` của Starlette lo (chặn `..`, đường dẫn tuyệt đối, symlink thoát thư mục, byte NUL). Không có thư mục
`index.html` (chạy dev, test) thì không gắn gì và server chỉ có API."""

import os

from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

ASSETS_PREFIX = "/assets"
# Chỉ cache dài hạn thư mục `assets/` (tên file có băm nội dung); `index.html` luôn phải hỏi lại để bản mới có hiệu lực ngay.
ASSETS_CACHE = "public, max-age=31536000, immutable"
INDEX_HEADERS = {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"}
# Tiền tố không bao giờ rơi về index.html: API và WebSocket (PR sau) có 404 riêng, `/assets` là file thật hoặc 404.
_RESERVED = ("/api", "/ws", ASSETS_PREFIX)


class ImmutableAssets(StaticFiles):
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = ASSETS_CACHE
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


def install(app, static_dir):
    """Gắn `/assets` và ghi đường dẫn `index.html` vào `app.state.static_index` (None nếu không phục vụ giao diện)."""
    app.state.static_index = None
    if not static_dir:
        return
    index = os.path.join(static_dir, "index.html")
    if not os.path.isfile(index):
        return
    app.state.static_index = index
    assets = os.path.join(static_dir, ASSETS_PREFIX.lstrip("/"))
    if os.path.isdir(assets):
        app.mount(ASSETS_PREFIX, ImmutableAssets(directory=assets), name="assets")


def _has_extension(path):
    return "." in path.rsplit("/", 1)[-1]


def spa_fallback(request):
    """Response `index.html` nếu `request` (đã 404) đáng được rơi về SPA, ngược lại None (giữ 404 như cũ)."""
    index = getattr(request.app.state, "static_index", None)
    if index is None or request.method not in ("GET", "HEAD"):
        return None
    path = request.url.path
    if any(path == prefix or path.startswith(prefix + "/") for prefix in _RESERVED):
        return None
    if _has_extension(path):
        return None  # file không tồn tại (/missing.js): 404, không trả HTML cho một yêu cầu file
    return FileResponse(index, media_type="text/html; charset=utf-8", headers=INDEX_HEADERS)
