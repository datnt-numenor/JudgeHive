"""REST: định dạng lỗi chung, body/Content-Type, health, các đường không tồn tại (README 3.2, 3.5, D9)."""

import json
import logging

import pytest

from _rest_support import PASSWORD, uuid_token

ERROR_KEYS = {"code", "message"}


def assert_error(resp, status, code):
    assert resp.status == status, (resp.status, resp.body)
    assert resp.headers["content-type"] == "application/json; charset=utf-8"
    assert resp.headers["cache-control"] == "no-store"
    body = resp.json()
    assert list(body) == ["error"] and set(body["error"]) == ERROR_KEYS
    assert body["error"]["code"] == code and isinstance(body["error"]["message"], str)


def test_health_is_public_and_minimal(web):
    r = web.get("/api/health")
    assert r.status == 200 and r.json() == {"status": "ok"}
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-type"] == "application/json; charset=utf-8"


def test_unknown_api_path_is_a_json_404(web):
    for path in ("/api/nope", "/api/problems/x/y", "/api/submissions/1/extra", "/api"):
        assert_error(web.get(path), 404, "NOT_FOUND")


def test_trailing_slash_is_not_redirected(web):
    for path in ("/api/problems/", "/api/me/", "/api/submissions/"):
        r = web.get(path)
        assert_error(r, 404, "NOT_FOUND")
        assert "location" not in r.headers


@pytest.mark.parametrize("method,path,allow", [
    ("GET", "/api/login", "POST"), ("PUT", "/api/me", "GET"), ("DELETE", "/api/problems", "GET"),
    ("POST", "/api/problems/sum", "GET"), ("POST", "/api/health", "GET"), ("PATCH", "/api/submissions/1", "GET"),
])
def test_wrong_method_is_405_with_allow(web, method, path, allow):
    r = web.request(method, path, obj={})
    assert_error(r, 405, "METHOD_NOT_ALLOWED")
    assert allow in r.headers["allow"]


def test_submissions_collection_allows_get_and_post(web):
    r = web.request("DELETE", "/api/submissions")
    assert_error(r, 405, "METHOD_NOT_ALLOWED")
    assert {"GET", "POST"} <= {m.strip() for m in r.headers["allow"].split(",")}


def test_documentation_endpoints_are_not_exposed(web):
    for path in ("/docs", "/redoc", "/openapi.json", "/api/openapi.json", "/api/docs"):
        assert web.get(path).status == 404


def test_no_cors_headers_and_preflight_is_refused(web):
    r = web.get("/api/health", headers={"Origin": "http://evil.example"})
    assert not any(h.startswith("access-control-") for h in r.headers)
    pre = web.request("OPTIONS", "/api/login", headers={"Origin": "http://evil.example",
                                                         "Access-Control-Request-Method": "POST"})
    assert pre.status == 405 and not any(h.startswith("access-control-") for h in pre.headers)


# ---- Content-Type, kích thước, JSON hỏng -------------------------------------------------------------------------

@pytest.mark.parametrize("content_type", [None, "text/plain", "application/x-www-form-urlencoded", "application/jsonx",
                                          "text/json", "application/json; charset=latin-1", "multipart/form-data"])
def test_post_needs_json_content_type(web, content_type):
    r = web.post("/api/login", body=b'{"username":"alice","password":"x"}', content_type=content_type)
    assert_error(r, 415, "UNSUPPORTED_MEDIA_TYPE")


@pytest.mark.parametrize("content_type", ["application/json", "application/json; charset=utf-8",
                                          "Application/JSON;charset=UTF-8", 'application/json; charset="utf-8"'])
def test_json_content_type_variants_are_accepted(web, content_type):
    r = web.post("/api/login", body=json.dumps({"username": "alice", "password": PASSWORD}).encode(),
                 content_type=content_type)
    assert r.status == 200


@pytest.mark.parametrize("body", [b"", b"{", b"not json", b"[]", b'"x"', b"null", b"1", b"true", b'{"username": NaN}',
                                  b'{"username": Infinity}', b'{"username": -Infinity}', b"\xff\xfe{}",
                                  b'{"a":1}garbage', b"[" * 100_000])
def test_body_that_is_not_a_json_object_is_400(web, body):
    assert_error(web.post("/api/login", body=body), 400, "INVALID_REQUEST")


def test_deeply_nested_json_is_400_not_500(web):
    body = b'{"a":' + b"[" * 50_000 + b"]" * 50_000 + b"}"
    assert_error(web.post("/api/login", body=body), 400, "INVALID_REQUEST")


def test_duplicate_keys_take_the_last_value(web):
    r = web.post("/api/login", body=b'{"username":"nobody","username":"alice","password":"%s"}' % PASSWORD.encode())
    assert r.status == 200 and r.json()["user"]["username"] == "alice"


def test_extra_fields_are_ignored_and_role_cannot_be_chosen(web):
    r = web.post("/api/login", {"username": "alice", "password": PASSWORD, "role": "admin", "user_id": 3, "x": [1]})
    assert r.status == 200 and r.json()["user"] == {"id": 1, "username": "alice", "role": "user"}


def test_default_body_limit_is_128_kb(web):
    cookie = web.login()
    ok = json.dumps({"username": "alice", "password": "x", "pad": "p" * (128 * 1024 - 100)}).encode()
    assert len(ok) < 128 * 1024 and web.post("/api/login", body=ok).status == 401
    big = json.dumps({"username": "alice", "password": "x", "pad": "p" * (128 * 1024)}).encode()
    assert_error(web.post("/api/login", body=big), 413, "BODY_TOO_LARGE")
    assert_error(web.post("/api/logout", body=big, cookie=cookie), 413, "BODY_TOO_LARGE")


def test_submit_body_limit_is_512_kb(web):
    cookie = web.login()
    r = web.post("/api/submissions", body=b'{"pad":"' + b"p" * (512 * 1024) + b'"}', cookie=cookie)
    assert_error(r, 413, "BODY_TOO_LARGE")
    # dưới 512 KB nhưng code vượt 64 KB: lỗi khác (CODE_TOO_LARGE), cho thấy hai giới hạn độc lập
    code = "#" * (100 * 1024)
    r = web.submit(cookie, uuid_token(1), code=code)
    assert_error(r, 413, "CODE_TOO_LARGE")


def test_oversized_content_length_is_rejected_without_reading_the_body(web):
    head = (b"POST /api/login HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nConnection: close\r\n"
            b"Content-Length: 9999999\r\n\r\n")
    out = web.raw(head)
    assert out.startswith(b"HTTP/1.1 413") and b"BODY_TOO_LARGE" in out


def chunked(parts):
    out = b""
    for p in parts:
        out += b"%x\r\n" % len(p) + p + b"\r\n"
    return out + b"0\r\n\r\n"


def test_chunked_body_is_read_and_parsed(web):
    payload = json.dumps({"username": "alice", "password": PASSWORD}).encode()
    req = (b"POST /api/login HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nConnection: close\r\n"
           b"Transfer-Encoding: chunked\r\n\r\n" + chunked([payload[:10], payload[10:]]))
    out = web.raw(req)
    assert out.startswith(b"HTTP/1.1 200") and b'"alice"' in out


def test_chunked_body_over_the_limit_is_413(web):
    parts = [b"x" * 65536] * 3  # 192 KB > 128 KB, không có Content-Length để chặn sớm
    req = (b"POST /api/login HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nConnection: close\r\n"
           b"Transfer-Encoding: chunked\r\n\r\n" + chunked(parts))
    out = web.raw(req)
    assert out.startswith(b"HTTP/1.1 413") and b"BODY_TOO_LARGE" in out


# ---- 500 và không rò thông tin -----------------------------------------------------------------------------------

def test_unexpected_exception_is_a_generic_500(make_web, caplog):
    from _rest_support import populate

    def prepare(app):
        @app.get("/api/boom")
        def boom():
            raise RuntimeError("secret-internal-detail")

    web = make_web(prepare=prepare)
    populate(web.db)
    web.start()
    with caplog.at_level(logging.ERROR):
        r = web.get("/api/boom")
    assert_error(r, 500, "INTERNAL_ERROR")
    assert b"secret-internal-detail" not in r.body and b"RuntimeError" not in r.body


def test_error_messages_never_echo_client_values(web):
    cookie = web.login()
    marker = "ZZ-MARKER-ZZ"
    cases = [
        web.post("/api/login", {"username": marker, "password": marker}),
        web.post("/api/login", {"username": 5, "password": marker}),
        web.submit(cookie, marker, problem_id=marker),
        web.submit(cookie, marker, language=marker),
        web.submit(cookie, uuid_token(1), problem_id="nope-" + marker),
        web.post("/api/submissions", body=("{" + marker).encode(), cookie=cookie),
        web.get("/api/submissions?limit=" + marker, cookie=cookie),
        web.get("/api/problems/" + marker, cookie=cookie),
        web.get("/api/submissions/" + marker, cookie=cookie),
        web.get("/api/" + marker),
    ]
    for r in cases:
        assert r.status >= 400 and marker.encode() not in r.body, r.body


def test_no_route_declares_validated_parameters():
    """FastAPI trả lại `input` của client trong lỗi validation mặc định; để chắc không bao giờ xảy ra, không route nào
    khai báo query hoặc body có kiểu (tham số đường dẫn là `str` nên không thể sai kiểu)."""
    from server.main import create_app

    from _support import make_config
    import tempfile
    import pathlib

    app = create_app(make_config(pathlib.Path(tempfile.mkdtemp())))
    seen = 0
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None or not route.path.startswith("/api"):
            continue
        seen += 1
        assert not dependant.query_params and not dependant.body_params and not dependant.header_params, route.path
        assert not dependant.cookie_params, route.path
        assert all(p.field_info.annotation is str for p in dependant.path_params), route.path
    assert seen == 9, seen  # login, logout, me, health, problems x2, submissions x3
