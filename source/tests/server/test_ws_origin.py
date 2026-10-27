"""Kiểm tra Origin của /ws (README 4.1): hàm thuần. Ứng viên khóa: toàn bộ file."""

import pytest

from server.config import ServerConfig, allowed_origins_from_env
from server.ws.origin import origin_allowed, parse_host_header, parse_origin


@pytest.mark.parametrize("origin,host,allowed", [
    ("http://localhost:8000", "localhost:8000", ()),
    ("https://localhost:8000", "localhost:8000", ()),          # https cũng được: cùng host và cổng
    ("http://LocalHost:8000", "localhost:8000", ()),            # host không phân biệt hoa thường
    ("http://localhost:8000", "LOCALHOST:8000", ()),
    ("http://localhost", "localhost", ()),                      # cổng 80 mặc định cả hai phía
    ("http://localhost:80", "localhost", ()),                   # cổng mặc định có thể bỏ ở Host
    ("http://localhost", "localhost:80", ()),                   # hoặc bỏ ở Origin
    ("https://judge.example.org", "judge.example.org", ()),     # 443 mặc định
    ("https://judge.example.org:443", "judge.example.org", ()),
    ("http://127.0.0.1:8000", "127.0.0.1:8000", ()),
    ("http://[::1]:8000", "[::1]:8000", ()),                    # IPv6
    ("http://localhost:5173", "localhost:8000", ("http://localhost:5173",)),   # theo danh sách cấu hình
    ("https://judge.example.org", "10.0.0.5:8000", ("https://judge.example.org",)),
    ("http://localhost:5173", "x:1", ("http://other:1", "http://LOCALHOST:5173")),
])
def test_allowed(origin, host, allowed):
    assert origin_allowed(origin, host, allowed)


@pytest.mark.parametrize("origin,host,allowed", [
    (None, "localhost:8000", ()),                               # thiếu Origin
    ("", "localhost:8000", ()),
    ("null", "localhost:8000", ()),                              # trang sandbox/file
    ("http://evil.example", "localhost:8000", ()),               # host khác
    ("http://localhost:9999", "localhost:8000", ()),             # cổng khác
    ("http://localhost", "localhost:8000", ()),                  # 80 khác 8000
    ("https://localhost", "localhost:8000", ()),
    ("http://localhost:8000.evil.example", "localhost:8000", ()),
    ("http://evil.example/localhost:8000", "localhost:8000", ()),
    ("http://localhost:8000@evil.example", "localhost:8000", ()),
    ("http://user@localhost:8000", "localhost:8000", ()),        # userinfo không thuộc origin
    ("http://localhost:8000/path", "localhost:8000", ()),        # origin không có đường dẫn
    ("http://localhost:8000?x=1", "localhost:8000", ()),
    ("ftp://localhost:8000", "localhost:8000", ()),              # scheme lạ
    ("ws://localhost:8000", "localhost:8000", ()),
    ("localhost:8000", "localhost:8000", ()),                    # thiếu scheme
    ("//localhost:8000", "localhost:8000", ()),
    ("http://localhost:abc", "localhost:abc", ()),               # cổng không phải số
    ("http://localhost:99999", "localhost:99999", ()),           # cổng ngoài khoảng
    ("http://localhost:8000", None, ()),                         # thiếu Host
    ("http://localhost:8000", "", ()),
    ("http://localhost:8000", "evil.example:8000", ()),
    ("http://localhost:8000", "localhost:8000@evil.example", ()),
    ("http://localhost:5173", "localhost:8000", ("http://localhost:5174",)),   # danh sách không khớp
    ("http://localhost:5173", "localhost:8000", ("garbage", "", "null")),
    ("https://judge.example.org", "10.0.0.5:8000", ("http://judge.example.org",)),  # scheme khác trong danh sách
])
def test_rejected(origin, host, allowed):
    assert not origin_allowed(origin, host, allowed)


def test_non_string_inputs_are_rejected_not_raised():
    for bad in (5, b"http://localhost", ["http://localhost"], object()):
        assert not origin_allowed(bad, "localhost", ())
        assert not origin_allowed("http://localhost", bad, ())


def test_parse_origin_normalizes():
    assert parse_origin("HTTP://Example.COM") == ("http", "example.com", 80)
    assert parse_origin("https://example.com:8443") == ("https", "example.com", 8443)
    assert parse_origin("http://example.com/") == ("http", "example.com", 80)  # dấu "/" cuối được chấp nhận
    assert parse_origin("ftp://example.com") is None and parse_origin("http://") is None


def test_parse_host_header_uses_the_default_port_of_the_pages_scheme():
    assert parse_host_header("example.com", "http") == ("example.com", 80)
    assert parse_host_header("example.com", "https") == ("example.com", 443)
    assert parse_host_header("example.com:81", "https") == ("example.com", 81)
    assert parse_host_header("", "http") is None and parse_host_header("a/b", "http") is None


def test_allowed_origins_come_from_the_environment_list():
    assert allowed_origins_from_env({}) == ()
    assert allowed_origins_from_env({"JH_ALLOWED_ORIGINS": ""}) == ()
    assert allowed_origins_from_env({"JH_ALLOWED_ORIGINS": " http://a:1 , https://b.example ,, "}) == (
        "http://a:1", "https://b.example")
    assert ServerConfig().allowed_origins == ()
