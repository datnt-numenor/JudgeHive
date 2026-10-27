"""REST: số nguyên từ client không bao giờ gây 500 (parse tập trung), client ngắt giữa body, không lộ server header."""

import ast
import logging
import os
import socket
import time

import pytest

import server.rest
from _support import wait_until
from server.rest.parse import INT64_MAX, parse_uint

HUGE = "1" * 5000  # vượt giới hạn 4300 chữ số của int() trong Python 3.11


# ---- parse_uint -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,kwargs,expected", [
    ("5", {}, 5), ("0", {}, 0), ("007", {}, 7), ("9223372036854775807", {}, INT64_MAX),
    ("9223372036854775808", {}, None), ("1" * 19, {}, 1111111111111111111), ("1" * 20, {}, None), (HUGE, {}, None),
    ("", {}, None), (" 5", {}, None), ("5 ", {}, None), ("+5", {}, None), ("-5", {}, None), ("5\n", {}, None),
    ("1_0", {}, None), ("1e3", {}, None), ("0x10", {}, None), ("5.0", {}, None), ("٥", {}, None),
    ("５", {}, None), ("²", {}, None), (None, {}, None), (5, {}, None), ("0", {"low": 1}, None),
    ("200", {"low": 1, "high": 200}, 200), ("201", {"low": 1, "high": 200}, None),
    ("1", {"low": 1, "canonical": True}, 1), ("01", {"low": 1, "canonical": True}, None),
    ("0", {"low": 0, "canonical": True}, 0), ("00", {"canonical": True}, None),
    ("9223372036854775807", {"high": 2**100}, INT64_MAX), ("9223372036854775808", {"high": 2**100}, None),
])
def test_parse_uint(raw, kwargs, expected):
    assert parse_uint(raw, **kwargs) == expected


def test_no_int_call_on_client_data_outside_parse_uint():
    """Mọi `int(...)` trong `server/rest/` phải nằm trong danh sách đã duyệt: đây là các chỗ KHÔNG nhận chuỗi của
    client. Thêm một `int()` mới (ví dụ cho query hay header) làm test này fail, buộc đi qua `parse.parse_uint`."""
    allowed = {
        ("errors.py", "retry_after_s"),  # số giây do server tính
        ("parse.py", "raw"),  # chính hàm parse, đã kiểm độ dài và ký tự trước
        ("state.py", "time.time() * 1000"),
        ("state.py", "cfg.login_rate_window_s * 1000"),
        ("submission_routes.py", "cfg.submit_rate_window_s * 1000"),
    }
    found = set()
    root = os.path.dirname(server.rest.__file__)
    for name in sorted(os.listdir(root)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(root, name), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "int":
                found.add((name, ast.unparse(node.args[0]) if node.args else ""))
    assert found == allowed, found ^ allowed


# ---- không 500 với số quá dài ở mọi chỗ ----------------------------------------------------------------------

def test_huge_numbers_in_the_query_are_400_not_500(web):
    cookie = web.login("alice")
    for query in (f"limit={HUGE}", f"before_id={HUGE}", f"limit={'1' * 20}", f"before_id={'1' * 20}",
                  f"limit=5&before_id={HUGE}", f"limit={'0' * 5000}"):
        r = web.get("/api/submissions?" + query, cookie=cookie)
        assert r.status == 400 and r.error_code == "INVALID_REQUEST", (query[:30], r.status)


def test_boundary_values_in_the_query(web):
    cookie = web.login("alice")
    assert web.get(f"/api/submissions?before_id={INT64_MAX}", cookie=cookie).status == 200
    assert web.get(f"/api/submissions?before_id={INT64_MAX + 1}", cookie=cookie).status == 400
    assert web.get("/api/submissions?limit=0000000000000000000200", cookie=cookie).status == 400  # 22 chữ số
    assert web.get("/api/submissions?limit=00200", cookie=cookie).status == 200


def test_huge_submission_id_in_the_path_is_404_not_500(web):
    cookie = web.login("alice")
    for sid in (HUGE, "1" * 20, str(INT64_MAX + 1), str(INT64_MAX), "0" * 5000):
        r = web.get(f"/api/submissions/{sid}", cookie=cookie)
        assert r.status == 404 and r.error_code == "SUBMISSION_NOT_FOUND", (sid[:30], r.status)


def test_very_long_problem_id_in_the_path_is_404(web):
    r = web.get("/api/problems/" + "a" * 5000, cookie=web.login("alice"))
    assert r.status == 404 and r.error_code == "PROBLEM_NOT_FOUND"


def test_huge_integers_in_json_bodies_are_400(web):
    cookie = web.login("alice")
    for body in (b'{"username": "alice", "password": ' + HUGE.encode() + b"}",
                 b'{"problem_id": "sum", "language": "python", "client_token": ' + HUGE.encode() + b', "code": "x"}'):
        for path in ("/api/login", "/api/submissions"):
            r = web.post(path, body=body, cookie=cookie)
            assert r.status == 400 and r.error_code == "INVALID_REQUEST", (path, r.status)


def raw_request(web, content_length, body=b""):
    head = (b"POST /api/login HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nConnection: close\r\n"
            b"Content-Length: " + content_length + b"\r\n\r\n")
    return web.raw(head + body)


@pytest.mark.parametrize("length", [HUGE.encode(), b"9" * 20, b"9223372036854775808", b"9223372036854775807",
                                    b"99999999999"])
def test_enormous_content_length_is_413_or_rejected_by_the_http_layer_never_500(web, length):
    out = raw_request(web, length)
    assert out.startswith(b"HTTP/1.1 4"), out[:80]  # 413 từ ứng dụng, hoặc 400 nếu tầng HTTP chặn trước
    assert web.get("/api/health").status == 200


def test_content_length_that_is_not_a_number_is_rejected_never_500(web):
    for length in (b"abc", b"-5", b"+5", b"1e3", b"5 5"):
        out = raw_request(web, length)
        assert out.startswith(b"HTTP/1.1 4"), (length, out[:80])


def test_content_length_over_the_limit_but_short_is_413_from_the_application(web):
    out = raw_request(web, b"200000")  # 200 KB > 128 KB, không gửi body
    assert out.startswith(b"HTTP/1.1 413") and b"BODY_TOO_LARGE" in out


# ---- client ngắt giữa chừng (G4) ------------------------------------------------------------------------------

def test_client_disconnecting_mid_body_is_not_a_server_error(web, caplog):
    cookie = web.login("alice")
    head = (b"POST /api/submissions HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nConnection: close\r\n"
            b"Cookie: jh_session=" + cookie.encode() + b"\r\nContent-Length: 5000\r\n\r\n" + b'{"problem_id": "su')
    with caplog.at_level(logging.DEBUG):
        for _ in range(3):
            with socket.create_connection(("127.0.0.1", web.port), timeout=5) as s:
                s.sendall(head)
                time.sleep(0.1)  # để server bắt đầu đọc body
            # đóng socket khi mới có một phần body
        assert wait_until(lambda: web.get("/api/health").status == 200)
        time.sleep(0.5)  # chờ server xử lý http.disconnect
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == [], [(r.name, r.getMessage()[:80]) for r in errors]
    assert web.get("/api/me", cookie=cookie).status == 200
    assert web.db.conn().execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 0


def test_client_disconnecting_mid_body_of_login_is_not_a_server_error(web, caplog):
    head = (b"POST /api/login HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nConnection: close\r\n"
            b"Content-Length: 500\r\n\r\n" + b'{"username": "ali')
    with caplog.at_level(logging.DEBUG):
        with socket.create_connection(("127.0.0.1", web.port), timeout=5) as s:
            s.sendall(head)
            time.sleep(0.1)
        assert wait_until(lambda: web.get("/api/health").status == 200)
        time.sleep(0.5)
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
