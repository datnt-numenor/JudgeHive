"""Kiểm tra `Origin` của handshake `/ws` (README mục 4.1): chống chiếm kết nối từ trang web khác (CSWSH).

Hàm thuần, không phụ thuộc FastAPI. Origin được phép là:
  - origin có cùng host và cổng với header `Host` của request, scheme `http` hoặc `https` (host so sánh không phân biệt
    hoa thường; cổng mặc định 80 của `http` và 443 của `https` có thể bỏ); hoặc
  - một origin nằm trong danh sách cấu hình (`JH_ALLOWED_ORIGINS`), so sánh theo scheme, host và cổng đã chuẩn hóa.
Thiếu `Origin`, `null`, hoặc không phải dạng `scheme://host[:port]` thì từ chối."""

from urllib.parse import urlsplit

DEFAULT_PORTS = {"http": 80, "https": 443}


def parse_origin(value):
    """`(scheme, host, port)` đã chuẩn hóa, hoặc None nếu `value` không phải một origin hợp lệ (http/https)."""
    if not isinstance(value, str) or value in ("", "null"):
        return None
    try:
        parts = urlsplit(value)
        scheme = parts.scheme.lower()
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if scheme not in DEFAULT_PORTS or not host:
        return None
    # Origin chỉ có scheme, host, cổng: có đường dẫn, query, fragment hay userinfo thì không phải origin.
    if parts.path not in ("", "/") or parts.query or parts.fragment or "@" in parts.netloc:
        return None
    return scheme, host.lower(), port if port is not None else DEFAULT_PORTS[scheme]


def parse_host_header(value, scheme):
    """`(host, port)` từ header `Host`; cổng thiếu thì lấy cổng mặc định của `scheme` (trang đã tải bằng scheme đó)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parts = urlsplit("//" + value)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if not host or parts.path or parts.query or parts.fragment or "@" in parts.netloc:
        return None
    return host.lower(), port if port is not None else DEFAULT_PORTS[scheme]


def origin_allowed(origin, host_header, allowed=()):
    parsed = parse_origin(origin)
    if parsed is None:
        return False
    scheme, host, port = parsed
    same_host = parse_host_header(host_header, scheme)
    if same_host is not None and same_host == (host, port):
        return True
    for entry in allowed:
        if parse_origin(entry) == parsed:
            return True
    return False
