"""REST: tạo/sửa đề và xem siêu dữ liệu bộ test của admin, `POST /api/admin/problems`, `PUT /api/admin/problems/{id}`,
`GET /api/admin/problems/{id}/tests` (README 3.6, D35, D11, D23(6)(7)(9)).

Server thật (uvicorn, dispatcher và cổng worker thật). Các test D11 quét toàn bộ response tìm nội dung test ẩn."""

import json
import logging

import pytest

from _rest_support import HIDDEN_TESTS, SAMPLE_TESTS, uuid_token
from _support import insert_submission
from server import problems

HIDDEN_MARKERS = [t.input.decode().strip() for t in HIDDEN_TESTS] + [t.output.decode().strip() for t in HIDDEN_TESTS]
DETAIL_KEYS = {"id", "title", "time_limit_ms", "memory_limit_kib", "hidden", "statement", "samples"}
TEST_KEYS = {"position", "is_sample", "input_bytes", "output_bytes", "sha256", "input", "output"}


def root_cookie(web):
    if not hasattr(web, "_root_cookie"):
        web._root_cookie = web.login("root")
    return web._root_cookie


def valid(**over):
    body = {"id": "new-problem", "title": "Đề mới", "statement": "Cho a, b.\nIn a + b.", "time_limit_ms": 1500,
            "memory_limit_kib": 131072,
            "tests": [{"input": "1 2\n", "output": "3\n", "is_sample": True},
                      {"input": "SECRET-IN-1\n", "output": "SECRET-OUT-1\n", "is_sample": False},
                      {"input": "SECRET-IN-2\n", "output": "SECRET-OUT-2\n", "is_sample": False}]}
    body.update(over)
    return body


def content(**over):
    body = valid(**over)
    del body["id"]
    return body


def create(web, body=None, cookie=None):
    return web.post("/api/admin/problems", valid() if body is None else body, cookie=cookie or root_cookie(web))


def put(web, pid, body=None, cookie=None):
    return web.request("PUT", f"/api/admin/problems/{pid}", obj=content() if body is None else body,
                       cookie=cookie or root_cookie(web))


def problem_row(web, pid):
    row = web.db.conn().execute("SELECT * FROM problems WHERE id = ?", (pid,)).fetchone()
    return None if row is None else dict(row)


def stored_tests(web, pid):
    return [(r["position"], r["is_sample"], bytes(r["input"]), bytes(r["output"])) for r in web.db.conn().execute(
        "SELECT position, is_sample, input, output FROM tests WHERE problem_id = ? ORDER BY position", (pid,))]


def count_problems(web):
    return web.db.conn().execute("SELECT COUNT(*) FROM problems").fetchone()[0]


# ---- phân quyền ---------------------------------------------------------------------------------------------------

def test_endpoints_are_admin_only_and_403_comes_before_body_and_id_errors(web):
    alice = web.login("alice")
    for call in (lambda: web.post("/api/admin/problems", {"bogus": 1}, cookie=alice),
                 lambda: web.request("PUT", "/api/admin/problems/nope", obj={"bogus": 1}, cookie=alice),
                 lambda: web.request("PUT", "/api/admin/problems/sum", body=b"not json", cookie=alice),
                 lambda: web.get("/api/admin/problems/nope/tests?x=1", cookie=alice),
                 lambda: web.get("/api/admin/problems/sum/tests", cookie=alice)):
        r = call()
        assert (r.status, r.error_code) == (403, "FORBIDDEN")
    assert web.post("/api/admin/problems", valid()).status == 401
    assert web.get("/api/admin/problems/sum/tests").status == 401
    assert problem_row(web, "new-problem") is None


def test_a_busy_upload_gate_does_not_hide_a_403(web):
    gate = web.app.state.rest.upload_gate
    assert gate.try_enter()
    try:
        r = web.post("/api/admin/problems", valid(), cookie=web.login("alice"))
        assert (r.status, r.error_code) == (403, "FORBIDDEN")
    finally:
        gate.leave()


# ---- tạo đề -------------------------------------------------------------------------------------------------------

def test_create_stores_a_hidden_web_problem_and_returns_only_the_samples(web):
    r = create(web)
    assert r.status == 201 and set(r.json()) == DETAIL_KEYS
    view = r.json()
    assert (view["id"], view["title"], view["hidden"]) == ("new-problem", "Đề mới", True)
    assert view["samples"] == [{"input": "1 2\n", "output": "3\n"}]
    assert (view["time_limit_ms"], view["memory_limit_kib"]) == (1500, 131072)
    row = problem_row(web, "new-problem")
    assert (row["hidden"], row["source"]) == (1, "web")  # D8, D23(7)
    assert row["tests_hash"] == problems.compute_tests_hash(
        [problems.TestCase(s, i, o) for _, s, i, o in stored_tests(web, "new-problem")])
    for marker in ("SECRET-IN-1", "SECRET-OUT-2"):  # D11
        assert marker.encode() not in r.body


def test_created_problem_is_invisible_to_users_and_visible_to_admin(web):
    create(web)
    alice = web.login("alice")
    assert web.get("/api/problems/new-problem", cookie=alice).status == 404
    assert "new-problem" not in [p["id"] for p in web.get("/api/problems", cookie=alice).json()["problems"]]
    assert web.get("/api/problems/new-problem", cookie=root_cookie(web)).json()["hidden"] is True
    r = web.request("PATCH", "/api/admin/problems/new-problem", obj={"hidden": False}, cookie=root_cookie(web))
    assert r.status == 200 and web.get("/api/problems/new-problem", cookie=alice).status == 200


def test_tests_are_stored_samples_first_keeping_relative_order(web):
    tests = [{"input": "h1", "output": "o", "is_sample": False}, {"input": "s1", "output": "o", "is_sample": True},
             {"input": "h2", "output": "o", "is_sample": False}, {"input": "s2", "output": "o", "is_sample": True}]
    assert create(web, valid(tests=tests)).status == 201
    assert [(pos, s, i) for pos, s, i, _ in stored_tests(web, "new-problem")] == [
        (1, 1, b"s1"), (2, 1, b"s2"), (3, 0, b"h1"), (4, 0, b"h2")]


def test_duplicate_id_is_409_even_for_a_hidden_problem_and_changes_nothing(web):
    for pid in ("sum", "secret"):
        before = (problem_row(web, pid), stored_tests(web, pid))
        r = create(web, valid(id=pid))
        assert (r.status, r.error_code) == (409, "PROBLEM_ID_TAKEN")
        assert (problem_row(web, pid), stored_tests(web, pid)) == before


def test_log_names_admin_action_problem_and_count_only(web, caplog):
    with caplog.at_level(logging.INFO):
        create(web)
        put(web, "new-problem", {k: v for k, v in content().items() if k != "tests"})
        web.get("/api/admin/problems/new-problem/tests", cookie=root_cookie(web))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "create problem=new-problem tests=3" in text and "replace problem=new-problem tests=kept" in text
    assert "SECRET-IN" not in text and "Cho a, b." not in text


# ---- kiểm hợp lệ --------------------------------------------------------------------------------------------------

def one_test(**over):
    return {"input": "1\n", "output": "1\n", "is_sample": True, **over}


BAD_BODIES = [
    pytest.param(valid(id="Upper"), id="id uppercase"),
    pytest.param(valid(id=""), id="id empty"),
    pytest.param(valid(id="a" * 65), id="id too long"),
    pytest.param(valid(id="bad.id"), id="id charset"),
    pytest.param(valid(id=5), id="id type"),
    pytest.param(valid(title=""), id="title empty"),
    pytest.param(valid(title="   "), id="title blank"),
    pytest.param(valid(title="t" * 201), id="title too long"),
    pytest.param(valid(title=5), id="title type"),
    pytest.param(valid(statement=""), id="statement empty"),
    pytest.param(valid(statement="s" * 65537), id="statement too long"),
    pytest.param(valid(statement="é" * 32769), id="statement too long in bytes"),
    pytest.param(valid(statement=None), id="statement null"),
    pytest.param(valid(time_limit_ms=99), id="time low"),
    pytest.param(valid(time_limit_ms=5001), id="time high"),
    pytest.param(valid(time_limit_ms=True), id="time bool"),
    pytest.param(valid(time_limit_ms=1000.0), id="time float"),
    pytest.param(valid(time_limit_ms="1000"), id="time string"),
    pytest.param(valid(memory_limit_kib=65535), id="memory low"),
    pytest.param(valid(memory_limit_kib=524289), id="memory high"),
    pytest.param(valid(memory_limit_kib=None), id="memory null"),
    pytest.param(valid(tests=[]), id="no tests"),
    pytest.param(valid(tests=[one_test() for _ in range(101)]), id="101 tests"),
    pytest.param(valid(tests="x"), id="tests type"),
    pytest.param(valid(tests=[5]), id="test type"),
    pytest.param(valid(tests=[{"input": "1", "output": "1"}]), id="test missing is_sample"),
    pytest.param(valid(tests=[one_test(extra=1)]), id="test extra key"),
    pytest.param(valid(tests=[one_test(is_sample=1)]), id="is_sample int"),
    pytest.param(valid(tests=[one_test(input=5)]), id="input type"),
    pytest.param(valid(tests=[one_test(output=None)]), id="output null"),
    pytest.param({**valid(), "hidden": False}, id="extra top-level field"),
    pytest.param({k: v for k, v in valid().items() if k != "title"}, id="missing title"),
    pytest.param({k: v for k, v in valid().items() if k != "tests"}, id="missing tests"),
    pytest.param({k: v for k, v in valid().items() if k != "id"}, id="missing id"),
]


@pytest.mark.parametrize("body", BAD_BODIES)
def test_create_rejects_bad_input_without_storing_anything(web, body):
    count = count_problems(web)
    r = create(web, body)
    assert (r.status, r.error_code) == (400, "INVALID_REQUEST"), r.body
    assert count_problems(web) == count
    assert "SECRET" not in r.json()["error"]["message"]  # message không lặp lại giá trị đã gửi


@pytest.mark.parametrize("over", [dict(id="a" * 64), dict(id="a-b_9"), dict(title="t" * 200), dict(statement="s"),
                                  dict(statement="s" * 65536), dict(statement="é" * 32768),
                                  dict(time_limit_ms=100), dict(time_limit_ms=5000),
                                  dict(memory_limit_kib=65536), dict(memory_limit_kib=524288),
                                  dict(tests=[one_test() for _ in range(100)]),
                                  dict(tests=[one_test(input="", output="")])])
def test_create_accepts_the_documented_boundaries(web, over):
    r = create(web, valid(**over))
    assert r.status == 201, r.body


def test_lone_surrogates_are_400_not_500(web):
    for field in ("title", "statement"):
        raw = json.dumps(valid(), ensure_ascii=False).replace("Đề mới" if field == "title" else "Cho a, b.", "\\ud800")
        r = web.request("POST", "/api/admin/problems", body=raw.encode(), cookie=root_cookie(web))
        assert (r.status, r.error_code) == (400, "INVALID_REQUEST"), field
    bad = json.dumps(valid(), ensure_ascii=False).replace("SECRET-IN-1", "\\udc00")
    r = web.request("POST", "/api/admin/problems", body=bad.encode(), cookie=root_cookie(web))
    assert (r.status, r.error_code) == (400, "INVALID_REQUEST")


def test_total_test_size_limit_is_8_mib_raw(web):
    half = 4 * 1024 * 1024
    ok = valid(tests=[{"input": "a" * half, "output": "b" * half, "is_sample": True}])
    assert create(web, ok).status == 201  # đúng 8 MiB
    over = valid(id="over", tests=[{"input": "a" * half, "output": "b" * (half + 1), "is_sample": True}])
    r = create(web, over)
    assert (r.status, r.error_code) == (400, "INVALID_REQUEST") and problem_row(web, "over") is None


def test_the_worst_case_job_line_limit_applies_even_below_8_mib(web):
    """2,8 triệu ký tự điều khiển: 2,8 MB thô nhưng mỗi ký tự thành 6 byte trong dòng JOB (D3): vượt 16 MiB."""
    body = valid(tests=[{"input": "\x01" * 2_800_000, "output": "", "is_sample": True}])
    r = create(web, body)
    assert (r.status, r.error_code) == (400, "INVALID_REQUEST") and problem_row(web, "new-problem") is None


# ---- giới hạn body và cổng upload ----------------------------------------------------------------------------------

def test_body_over_17_mib_is_413_and_releases_the_upload_gate(web):
    # Content-Length khai báo quá 17 MiB: server từ chối khi đọc header, không cần gửi thật (như test_rest_hardening).
    head = (f"POST /api/admin/problems HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nConnection: close\r\n"
            f"Cookie: jh_session={root_cookie(web)}\r\nContent-Length: {17 * 1024 * 1024 + 1}\r\n\r\n").encode()
    out = web.raw(head)
    assert out.startswith(b"HTTP/1.1 413") and b"BODY_TOO_LARGE" in out
    assert create(web).status == 201  # cổng đã nhả sau lỗi


def test_a_busy_upload_gate_gives_429_with_retry_after_and_does_not_block_other_endpoints(web):
    gate = web.app.state.rest.upload_gate
    assert gate.try_enter()
    try:
        r = create(web)
        assert (r.status, r.error_code) == (429, "RATE_LIMITED") and int(r.headers["retry-after"]) >= 1
        r = put(web, "sum")
        assert (r.status, r.error_code) == (429, "RATE_LIMITED") and int(r.headers["retry-after"]) >= 1
        assert problem_row(web, "new-problem") is None
        assert web.get("/api/admin/problems/sum/tests", cookie=root_cookie(web)).status == 200  # GET không qua cổng
        assert web.get("/api/problems", cookie=root_cookie(web)).status == 200
    finally:
        gate.leave()
    assert create(web).status == 201


def test_a_stalled_upload_holds_the_gate_before_reading_the_body_but_only_until_the_read_deadline(web, monkeypatch):
    """Hai request thật chồng nhau: client treo (khai Content-Length nhưng không gửi body) giữ cổng NGAY từ trước khi
    body tới, nên request thứ hai nhận 429; quá hạn đọc body thì 400 và cổng được nhả."""
    import socket

    from _support import wait_until
    from server.rest import body as body_module

    monkeypatch.setattr(body_module, "UPLOAD_READ_TIMEOUT_S", 1.0)
    head = (f"POST /api/admin/problems HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nConnection: close\r\n"
            f"Cookie: jh_session={root_cookie(web)}\r\nContent-Length: 1000\r\n\r\n").encode()
    gate = web.app.state.rest.upload_gate
    with socket.create_connection(("127.0.0.1", web.port), timeout=10) as stalled:
        stalled.sendall(head)
        wait_until(lambda: gate._active == 1, timeout=5)
        r = create(web)
        assert (r.status, r.error_code) == (429, "RATE_LIMITED")
        out = b""
        while chunk := stalled.recv(65536):
            out += chunk
        assert out.startswith(b"HTTP/1.1 400") and b"INVALID_REQUEST" in out
    assert gate._active == 0
    assert create(web).status == 201


def test_chunked_body_over_17_mib_is_413_while_streaming_and_the_gate_is_released(web):
    """Không có Content-Length: giới hạn phải chặn được ngay khi đang nhận stream (cổng đã giữ từ trước khi đọc body).
    Một thread đọc liên tục để không mất response 413 khi server đóng kết nối lúc client còn đang gửi."""
    import socket
    import threading

    limit = 17 * 1024 * 1024
    piece = b"a" * (1024 * 1024)
    head = (f"POST /api/admin/problems HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nConnection: close\r\n"
            f"Cookie: jh_session={root_cookie(web)}\r\nTransfer-Encoding: chunked\r\n\r\n").encode()
    received = []
    with socket.create_connection(("127.0.0.1", web.port), timeout=30) as sock:
        def drain():
            try:
                while data := sock.recv(65536):
                    received.append(data)
            except OSError:
                pass

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            sock.sendall(head)
            for _ in range(limit // len(piece) + 1):  # 18 MiB > 17 MiB, mỗi mảnh là một chunk 1 MiB
                sock.sendall(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
            sock.sendall(b"0\r\n\r\n")
        except OSError:
            pass  # server đã trả lời 413 và đóng khi ta còn đang gửi
        reader.join(timeout=30)
    out = b"".join(received)
    assert out.startswith(b"HTTP/1.1 413") and b"BODY_TOO_LARGE" in out, out[:200]
    assert problem_row(web, "new-problem") is None
    assert web.app.state.rest.upload_gate._active == 0
    assert create(web).status == 201


@pytest.mark.parametrize("action", ["create", "put"])
def test_an_admin_demoted_between_authorization_and_the_write_gets_403_and_nothing_is_written(web, monkeypatch, action):
    """`require_admin` đã cho qua, rồi trước khi vào transaction ghi, admin bị hạ quyền (request khác): transaction ghi
    kiểm lại (`_require_active_admin`) và trả 403, không ghi gì, và cổng upload vẫn được nhả. Giả lập khoảng hở bằng cách
    hạ quyền ngay trong `try_enter` của cổng (chạy sau `require_admin`, trước khi đọc body và ghi)."""
    cookie = root_cookie(web)
    gate = web.app.state.rest.upload_gate
    real_try_enter = gate.try_enter

    def demote_then_enter():
        with web.db.write() as c:
            c.execute("UPDATE users SET role = 'user' WHERE username = 'root'")
        return real_try_enter()

    before = (problem_row(web, "sum"), stored_tests(web, "sum"), count_problems(web))
    monkeypatch.setattr(gate, "try_enter", demote_then_enter)
    if action == "create":
        r = create(web, cookie=cookie)
    else:
        r = put(web, "sum", cookie=cookie)
    monkeypatch.undo()
    assert (r.status, r.error_code) == (403, "FORBIDDEN")
    assert (problem_row(web, "sum"), stored_tests(web, "sum"), count_problems(web)) == before
    assert problem_row(web, "new-problem") is None
    assert gate._active == 0


def test_the_gate_is_released_after_every_outcome(web):
    assert create(web, valid(tests=[])).status == 400
    assert create(web).status == 201
    assert create(web).status == 409
    assert put(web, "nope").status == 404
    assert put(web, "new-problem").status == 200
    assert web.app.state.rest.upload_gate.try_enter()  # nếu còn bị giữ thì False
    web.app.state.rest.upload_gate.leave()


def test_back_to_back_uploads_never_get_a_spurious_429(web):
    """Cổng nhả TRƯỚC khi response rời server: client gửi lượt kế tiếp ngay sau khi nhận response không bị 429 oan."""
    cookie = root_cookie(web)
    for i in range(60):
        r = put(web, "sum", cookie=cookie)
        assert r.status == 200, (i, r.status, r.error_code)


# ---- sửa đề -------------------------------------------------------------------------------------------------------

def test_put_without_tests_keeps_the_test_set_and_its_hash_and_marks_the_problem_as_web(web):
    before, tests_before = problem_row(web, "sum"), stored_tests(web, "sum")
    assert before["source"] == "seed"
    r = put(web, "sum", {k: v for k, v in content(title="Tên mới", time_limit_ms=300).items() if k != "tests"})
    assert r.status == 200 and set(r.json()) == DETAIL_KEYS
    after = problem_row(web, "sum")
    assert (after["title"], after["time_limit_ms"], after["memory_limit_kib"], after["source"]) == ("Tên mới", 300, 131072, "web")
    assert after["tests_hash"] == before["tests_hash"] and stored_tests(web, "sum") == tests_before
    assert after["hidden"] == before["hidden"] == 0  # PUT không đổi cờ ẩn
    assert r.json()["hidden"] is False and len(r.json()["samples"]) == 2


def test_put_with_tests_replaces_all_of_them_and_changes_the_hash(web):
    before = problem_row(web, "sum")
    r = put(web, "sum")
    assert r.status == 200
    after = problem_row(web, "sum")
    assert after["tests_hash"] != before["tests_hash"]
    assert [(p, s) for p, s, _, _ in stored_tests(web, "sum")] == [(1, 1), (2, 0), (3, 0)]
    assert r.json()["samples"] == [{"input": "1 2\n", "output": "3\n"}]
    for marker in ("SECRET-IN-1", "SECRET-OUT-2"):
        assert marker.encode() not in r.body


def test_put_keeps_a_hidden_problem_hidden(web):
    assert put(web, "secret").json()["hidden"] is True
    assert problem_row(web, "secret")["hidden"] == 1


def test_put_validation_order_is_body_then_id(web):
    r = put(web, "no-such", content(title=""))
    assert (r.status, r.error_code) == (400, "INVALID_REQUEST")  # 400 trước 404, như các endpoint admin khác
    for pid in ("no-such", "bad.id", "x" * 65):
        r = put(web, pid)
        assert (r.status, r.error_code) == (404, "PROBLEM_NOT_FOUND"), pid


@pytest.mark.parametrize("body", [pytest.param({**content(), "id": "sum"}, id="id in body"),
                                  pytest.param({**content(), "hidden": True}, id="hidden in body"),
                                  pytest.param({k: v for k, v in content().items() if k != "statement"}, id="missing"),
                                  pytest.param(content(tests=[]), id="empty tests"),
                                  pytest.param(content(time_limit_ms=0), id="time")])
def test_put_rejects_bad_input_and_leaves_the_problem_untouched(web, body):
    before = (problem_row(web, "sum"), stored_tests(web, "sum"))
    r = put(web, "sum", body)
    assert (r.status, r.error_code) == (400, "INVALID_REQUEST")
    assert (problem_row(web, "sum"), stored_tests(web, "sum")) == before


def test_failed_check_after_validation_never_leaves_a_half_written_problem(web, monkeypatch):
    """Một lần ghi duy nhất: lỗi xảy ra giữa lúc ghi thì cả đề lẫn bộ test cũ còn nguyên."""
    before = (problem_row(web, "sum"), stored_tests(web, "sum"))
    original = problems.compute_tests_hash

    def boom(tests):
        raise RuntimeError("giữa chừng")

    monkeypatch.setattr(problems, "compute_tests_hash", boom)
    r = put(web, "sum")
    monkeypatch.setattr(problems, "compute_tests_hash", original)
    assert r.status == 500
    assert (problem_row(web, "sum"), stored_tests(web, "sum")) == before
    assert web.app.state.rest.upload_gate.try_enter()  # cổng nhả cả khi lỗi 500
    web.app.state.rest.upload_gate.leave()


# ---- quan hệ với dispatcher, bài đã nộp và rejudge ------------------------------------------------------------------

def test_a_queued_submission_is_judged_with_the_new_limits_and_tests_but_a_delivered_one_keeps_the_old(web):
    alice = web.login("alice")
    delivered_worker = web.worker()
    first = web.submit(alice, uuid_token(1)).json()["submission_id"]
    old_job = delivered_worker.take_job()  # đã nhận JOB theo test cũ
    waiting = web.submit(alice, uuid_token(2)).json()["submission_id"]  # worker duy nhất đang bận: bài này chờ
    from _support import wait_until

    wait_until(lambda: web.db.conn().execute("SELECT state FROM submissions WHERE id = ?", (waiting,)).fetchone()[0] == "queued")
    assert put(web, "sum", content(time_limit_ms=300, tests=[{"input": "7\n", "output": "7\n", "is_sample": True}])).status == 200
    assert old_job["submission_id"] == first and old_job["time_limit_ms"] == 1000 and len(old_job["tests"]) == 4
    delivered_worker.finish_ac(old_job)
    new_job = delivered_worker.take_job()
    assert new_job["submission_id"] == waiting
    assert new_job["time_limit_ms"] == 300 and [t["input"] for t in new_job["tests"]] == ["7\n"]  # theo bản mới lúc giao
    delivered_worker.close()


def test_editing_does_not_rejudge_anything(web):
    alice_id = web.db.conn().execute("SELECT id FROM users WHERE username = 'alice'").fetchone()[0]
    sid = insert_submission(web.db, alice_id, "sum", state="done", verdict="AC", time_ms=5, memory_kib=1, verdict_test_hash="h",
                            finished_at=1)
    before = dict(web.db.conn().execute("SELECT * FROM submissions WHERE id = ?", (sid,)).fetchone())
    assert put(web, "sum").status == 200
    assert dict(web.db.conn().execute("SELECT * FROM submissions WHERE id = ?", (sid,)).fetchone()) == before
    assert web.app.state.dispatcher.queue.qsize() == 0


def test_a_new_problem_is_delivered_to_the_worker_with_its_tests_and_limits(web):
    create(web)
    worker = web.worker()
    r = web.submit(root_cookie(web), uuid_token(3), problem_id="new-problem")
    assert r.status == 201, r.body
    job = worker.take_job()
    assert (job["time_limit_ms"], job["memory_limit_kib"]) == (1500, 131072)
    assert [(t["input"], t["is_sample"]) for t in job["tests"]] == [("1 2\n", True), ("SECRET-IN-1\n", False),
                                                                    ("SECRET-IN-2\n", False)]
    worker.close()


# ---- siêu dữ liệu bộ test -------------------------------------------------------------------------------------------

def test_tests_metadata_has_content_for_samples_only(web):
    create(web, valid(tests=[{"input": "é\n", "output": "x", "is_sample": True},
                             {"input": "SECRET-IN-1\n", "output": "SECRET-OUT-1\n", "is_sample": False}]))
    r = web.get("/api/admin/problems/new-problem/tests", cookie=root_cookie(web))
    assert r.status == 200
    view = r.json()
    assert set(view) == {"problem_id", "tests_hash", "tests"} and view["problem_id"] == "new-problem"
    assert view["tests_hash"] == problem_row(web, "new-problem")["tests_hash"]
    sample, hidden = view["tests"]
    assert set(sample) == set(hidden) == TEST_KEYS
    assert (sample["position"], sample["is_sample"], sample["input"], sample["output"]) == (1, True, "é\n", "x")
    assert (sample["input_bytes"], sample["output_bytes"]) == (3, 1)  # byte UTF-8, không phải số ký tự
    assert sample["sha256"] == problems.test_fingerprint("é\n".encode(), b"x")
    assert (hidden["position"], hidden["is_sample"], hidden["input"], hidden["output"]) == (2, False, None, None)
    assert (hidden["input_bytes"], hidden["output_bytes"]) == (12, 13)
    assert hidden["sha256"] == problems.test_fingerprint(b"SECRET-IN-1\n", b"SECRET-OUT-1\n")
    assert b"SECRET-" not in r.body  # D11


def test_no_admin_problem_endpoint_ever_returns_hidden_test_content(web):
    cookie = root_cookie(web)
    seen = [create(web).body, put(web, "new-problem").body, put(web, "sum").body,
            web.get("/api/admin/problems/sum/tests", cookie=cookie).body,
            web.get("/api/admin/problems/secret/tests", cookie=cookie).body,
            web.get("/api/problems/sum", cookie=cookie).body, web.get("/api/problems", cookie=cookie).body]
    blob = b"".join(seen)
    for marker in HIDDEN_MARKERS + ["SECRET-IN-1", "SECRET-OUT-2"]:
        assert marker.encode() not in blob, marker


def test_tests_metadata_of_a_seeded_problem_and_errors(web):
    r = web.get("/api/admin/problems/sum/tests", cookie=root_cookie(web)).json()
    assert [(t["position"], t["is_sample"]) for t in r["tests"]] == [(1, True), (2, True), (3, False), (4, False)]
    assert r["tests"][0]["input"] == SAMPLE_TESTS[0].input.decode()
    cookie = root_cookie(web)
    for path in ("/api/admin/problems/nope/tests", "/api/admin/problems/bad.id/tests"):
        res = web.get(path, cookie=cookie)
        assert (res.status, res.error_code) == (404, "PROBLEM_NOT_FOUND"), path
    for query in ("?x=1", "?limit=1"):
        res = web.get("/api/admin/problems/sum/tests" + query, cookie=cookie)
        assert (res.status, res.error_code) == (400, "INVALID_REQUEST"), query


def test_methods_not_allowed_on_the_problem_paths(web):
    cookie = root_cookie(web)
    assert web.get("/api/admin/problems", cookie=cookie).status == 405
    assert web.request("DELETE", "/api/admin/problems/sum", cookie=cookie).status == 405
    assert web.post("/api/admin/problems/sum/tests", {}, cookie=cookie).status == 405
