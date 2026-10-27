"""Phục vụ giao diện web: `/assets` bằng StaticFiles, fallback SPA trong bộ xử lý 404, không route catch-all.

Server thật (uvicorn trong một thread) với thư mục giao diện dựng tạm trong tmp_path."""

import os

import pytest

from _rest_support import populate
from server import config

INDEX = b'<!doctype html><html><body><div id="root"></div><script type="module" src="/assets/app-abc123.js"></script></body></html>\n'
APP_JS = b'console.log("app");\n'
STYLE_CSS = b"body { margin: 0; }\n"
SECRET = b"SECRET-OUTSIDE-THE-STATIC-DIR"


@pytest.fixture
def static_dir(tmp_path):
    """tmp/static/{index.html, assets/{app-abc123.js, style-xyz.css, sub/inner.js}} và tmp/secret.txt ở NGOÀI thư mục tĩnh."""
    root = tmp_path / "static"
    (root / "assets" / "sub").mkdir(parents=True)
    (root / "index.html").write_bytes(INDEX)
    (root / "assets" / "app-abc123.js").write_bytes(APP_JS)
    (root / "assets" / "style-xyz.css").write_bytes(STYLE_CSS)
    (root / "assets" / "sub" / "inner.js").write_bytes(b"inner\n")
    (tmp_path / "secret.txt").write_bytes(SECRET)
    (root / "secret-sibling.txt").write_bytes(b"sibling\n")  # nằm trong thư mục tĩnh nhưng ngoài assets/
    return root


@pytest.fixture
def site(make_web, static_dir):
    """Server có giao diện, đã chạy, có tài khoản và đề để thử API."""
    w = make_web(static_dir=str(static_dir))
    populate(w.db)
    return w.start()


def is_index(resp):
    return resp.status == 200 and resp.body == INDEX


def assert_not_html_page(resp):
    assert b'id="root"' not in resp.body and "text/html" not in resp.headers.get("content-type", "")


# --- trang của SPA ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/", "/login", "/problems/sum", "/problems/", "/submissions", "/submissions/42", "/some/deep/spa/route",
    "/problems/sum?x=1&y=2", "/v1.2/home", "/UPPER", "/%E1%BB%87",
])
def test_spa_routes_without_extension_get_index_html(site, path):
    r = site.get(path)
    assert is_index(r), (path, r.status, r.body[:80])
    assert r.headers["content-type"] == "text/html; charset=utf-8"
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_head_on_a_spa_route_returns_headers_and_no_body(site):
    r = site.request("HEAD", "/problems/sum")
    assert r.status == 200 and r.body == b""
    assert r.headers["content-type"] == "text/html; charset=utf-8"
    assert int(r.headers["content-length"]) == len(INDEX)


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_other_methods_on_a_spa_route_are_not_given_the_page(site, method):
    r = site.request(method, "/problems/sum", obj={})
    assert r.status in (404, 405)
    assert_not_html_page(r)


# --- file có đuôi không tồn tại: 404, không trả HTML ----------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/missing.js", "/favicon.ico", "/index.html", "/robots.txt", "/static/app.css", "/deep/dir/file.map", "/x.",
    "/assets/missing.js", "/assets/sub/missing.css",
])
def test_missing_file_with_an_extension_is_404_not_the_page(site, path):
    r = site.get(path)
    assert r.status == 404, (path, r.status)
    assert_not_html_page(r)
    assert site.request("HEAD", path).status == 404


# --- /assets -------------------------------------------------------------------------------------------------------

def test_assets_are_served_with_long_lived_cache_headers(site):
    r = site.get("/assets/app-abc123.js")
    assert r.status == 200 and r.body == APP_JS
    assert r.headers["content-type"].split(";")[0] in ("text/javascript", "application/javascript")
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert r.headers["x-content-type-options"] == "nosniff"
    css = site.get("/assets/style-xyz.css")
    assert css.status == 200 and css.body == STYLE_CSS and css.headers["content-type"].split(";")[0] == "text/css"
    assert site.get("/assets/sub/inner.js").body == b"inner\n"


def test_conditional_request_for_an_asset_is_304(site):
    first = site.get("/assets/app-abc123.js")
    etag = first.headers["etag"]
    again = site.get("/assets/app-abc123.js", headers={"If-None-Match": etag})
    assert again.status == 304 and again.body == b""


def test_asset_head_has_headers_and_no_body(site):
    r = site.request("HEAD", "/assets/app-abc123.js")
    assert r.status == 200 and r.body == b"" and int(r.headers["content-length"]) == len(APP_JS)


@pytest.mark.parametrize("path", ["/assets", "/assets/", "/assets/sub", "/assets/sub/", "/assets/missing", "/assets/nope/nope"])
def test_assets_paths_never_fall_back_to_the_page(site, path):
    r = site.get(path)
    assert r.status == 404, (path, r.status)
    assert_not_html_page(r)


def test_posting_to_an_asset_is_not_the_page(site):
    r = site.request("POST", "/assets/app-abc123.js", obj={})
    assert r.status == 405
    assert_not_html_page(r)


def test_only_assets_is_mounted_not_the_directory_root(site):
    # file nằm cạnh index.html nhưng ngoài assets/: không được phục vụ
    assert site.get("/secret-sibling.txt").status == 404


# --- fallback không nuốt API và WebSocket -------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/api", "/api/nope", "/api/problems/x/y", "/api/problems/", "/api/me/extra"])
def test_unknown_api_paths_stay_json_404(site, path):
    r = site.get(path)
    assert r.status == 404 and r.error_code == "NOT_FOUND"
    assert r.headers["content-type"] == "application/json; charset=utf-8"


def test_api_method_mismatch_stays_json_405(site):
    r = site.request("GET", "/api/login")
    assert r.status == 405 and r.error_code == "METHOD_NOT_ALLOWED" and r.headers["allow"] == "POST"
    assert site.request("DELETE", "/api/problems").status == 405
    assert site.request("POST", "/api/me", obj={}).status == 405


def test_api_still_works_next_to_the_site(site):
    assert site.get("/api/health").json() == {"status": "ok"}
    assert site.get("/api/me").status == 401  # không phiên: JSON 401, không phải trang
    cookie = site.login("alice")
    assert site.get("/api/me", cookie=cookie).json()["user"]["username"] == "alice"


@pytest.mark.parametrize("path", ["/ws", "/ws/", "/ws/anything", "/api/ws"])
def test_websocket_prefix_is_reserved(site, path):
    r = site.get(path)
    assert r.status == 404
    assert_not_html_page(r)


def test_path_that_only_starts_with_ws_or_api_text_is_still_a_page(site):
    assert is_index(site.get("/wsx"))
    assert is_index(site.get("/apiary"))
    assert is_index(site.get("/assetsx"))


# --- không bao giờ ghép đường dẫn của client vào đường dẫn file ---------------------------------------------------------

HOSTILE = [
    "/assets/../secret.txt", "/assets/%2e%2e/secret.txt", "/assets/%2e%2e%2fsecret.txt", "/assets/..%2fsecret.txt",
    "/assets/..%5csecret.txt", "/assets/sub/../../secret.txt", "/assets/sub/%2e%2e/%2e%2e/secret.txt",
    "/assets/%2Fetc%2Fpasswd", "/assets//etc/passwd", "/assets/%00", "/assets/app-abc123.js%00.png",
    "/assets/....//....//secret.txt", "/assets/..;/secret.txt", "/%2e%2e/secret.txt", "/%2e%2e/%2e%2e/etc/passwd",
    "/../secret.txt", "/assets/../index.html", "/assets/../../etc/passwd",
]


@pytest.mark.parametrize("path", HOSTILE)
def test_path_traversal_never_reads_outside_the_assets_directory(site, path):
    r = site.raw(f"GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode("ascii"))
    assert SECRET not in r and b"sibling" not in r and b"root:" not in r
    status_line = r.split(b"\r\n", 1)[0]
    # hoặc bị từ chối, hoặc (đường dẫn không đuôi, ngoài /assets) rơi về đúng index.html cố định, không bao giờ file khác
    if b" 200 " in status_line:
        assert r.endswith(INDEX), (path, r[:200])


def test_symlink_inside_assets_cannot_escape_the_directory(site, static_dir):
    os.symlink(static_dir.parent / "secret.txt", static_dir / "assets" / "link.txt")
    r = site.get("/assets/link.txt")
    assert r.status == 404 and SECRET not in r.body


def test_symlink_to_a_directory_cannot_escape_either(site, static_dir, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.js").write_bytes(SECRET)
    os.symlink(outside, static_dir / "assets" / "dir-link")
    r = site.get("/assets/dir-link/leak.js")
    assert r.status == 404 and SECRET not in r.body


def test_the_page_response_does_not_depend_on_the_request_path(site):
    bodies = {site.get(p).body for p in ("/a", "/a/b/c", "/%2e%2e/%2e%2e/etc/passwd", "/x?../../secret.txt")}
    assert bodies == {INDEX}


# --- cấu hình: không có giao diện thì chỉ có API ----------------------------------------------------------------------

def test_without_a_static_directory_the_server_is_api_only(make_web, tmp_path):
    w = make_web(static_dir=str(tmp_path / "does-not-exist"))
    populate(w.db)
    w.start()
    assert w.get("/").status == 404 and w.get("/problems/sum").status == 404
    assert w.get("/assets/app.js").status == 404
    assert w.get("/api/health").status == 200


def test_a_directory_without_index_html_serves_nothing(make_web, static_dir):
    (static_dir / "index.html").unlink()
    w = make_web(static_dir=str(static_dir))
    populate(w.db)
    w.start()
    assert w.get("/").status == 404
    assert w.get("/assets/app-abc123.js").status == 404  # không có index thì không gắn gì, kể cả /assets


def test_index_without_assets_directory_still_serves_the_page(make_web, tmp_path):
    root = tmp_path / "bare"
    root.mkdir()
    (root / "index.html").write_bytes(INDEX)
    w = make_web(static_dir=str(root))
    populate(w.db)
    w.start()
    assert is_index(w.get("/login"))
    assert w.get("/assets/app.js").status == 404


def test_static_dir_comes_from_the_environment():
    assert config.static_dir_from_env({}) == "/app/static"
    assert config.static_dir_from_env({"JH_STATIC_DIR": "/srv/web"}) == "/srv/web"
    cfg = config.load_config({"JH_WORKER_SECRET": "s", "JH_STATIC_DIR": "/srv/web"})
    assert cfg.static_dir == "/srv/web"
