"""REST: nộp bài (`POST /api/submissions`): idempotency theo client_token, validation, thứ tự kiểm tra, rate limit,
nối vào dispatcher (README 3.2, 3.3, D2, D8)."""

import logging
import re
import threading

import pytest

from _rest_support import FakeClock, populate, uuid_token
from _support import wait_until

ISO_MS = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z")


def count_rows(web):
    return web.db.conn().execute("SELECT COUNT(*) FROM submissions").fetchone()[0]


def body(n=1, **over):
    return {"problem_id": "sum", "language": "python", "code": "print(1)\n", "client_token": uuid_token(n), **over}


def post(web, cookie, obj):
    return web.post("/api/submissions", obj, cookie=cookie)


# ---- thành công -----------------------------------------------------------------------------------------------

def test_new_submission_is_201_and_stored_as_queued(web):
    cookie = web.login("alice")
    r = web.submit(cookie, uuid_token(1))
    assert r.status == 201 and r.headers["cache-control"] == "no-store"
    out = r.json()
    assert set(out) == {"submission_id", "state", "received_at"}
    assert out["state"] == "queued" and ISO_MS.fullmatch(out["received_at"])
    row = web.db.get_submission(out["submission_id"])
    assert row["state"] == "queued" and row["user_id"] == 1 and row["problem_id"] == "sum"
    stored = web.db.conn().execute("SELECT client_token, code, language FROM submissions").fetchone()
    assert tuple(stored) == (uuid_token(1), "print(1)\n", "python")


def test_user_id_and_other_client_fields_are_ignored(web):
    cookie = web.login("alice")
    r = post(web, cookie, body(1, user_id=2, role="admin", state="done", verdict="AC", attempt_id=9))
    assert r.status == 201
    row = web.db.conn().execute("SELECT user_id, state, verdict, attempt_id FROM submissions").fetchone()
    assert tuple(row) == (1, "queued", None, 0)


def test_all_three_languages_are_accepted(web):
    cookie = web.login("alice")
    for n, lang in enumerate(("python", "cpp", "java"), 1):
        assert post(web, cookie, body(n, language=lang)).status == 201


def test_submission_reaches_a_worker_without_restarting_the_server(web):
    worker = web.worker()
    cookie = web.login("alice")
    sid = web.submit(cookie, uuid_token(1), code="print(42)\n").json()["submission_id"]
    job = worker.take_job()
    assert job["submission_id"] == sid and job["code"] == "print(42)\n" and job["language"] == "python"
    assert [t["is_sample"] for t in job["tests"]] == [True, True, False, False]
    worker.finish_ac(job)
    detail = web.wait_state(cookie, sid, "judged")
    assert detail["verdict"] == "AC" and detail["time_ms"] == 5 and detail["memory_kib"] == 1024


def test_a_queued_submission_is_taken_by_a_worker_that_connects_later(web):
    cookie = web.login("alice")
    sid = web.submit(cookie, uuid_token(1)).json()["submission_id"]
    assert web.get(f"/api/submissions/{sid}", cookie=cookie).json()["state"] == "queued"
    worker = web.worker()
    assert worker.take_job()["submission_id"] == sid
    assert web.wait_state(cookie, sid, "judging")["verdict"] is None


def test_submissions_are_judged_in_arrival_order(web):
    cookie = web.login("alice")
    ids = [web.submit(cookie, uuid_token(n)).json()["submission_id"] for n in range(1, 5)]
    worker = web.worker()
    seen = []
    for _ in ids:
        job = worker.take_job()
        seen.append(job["submission_id"])
        worker.finish_ac(job)
    assert seen == ids


def test_a_stored_submission_is_judged_after_the_server_restarts(web):
    cookie = web.login("alice")
    sid = web.submit(cookie, uuid_token(1)).json()["submission_id"]
    web.restart()
    worker = web.worker()
    job = worker.take_job()
    assert job["submission_id"] == sid
    worker.finish_ac(job)
    assert web.wait_state(cookie, sid, "judged")["verdict"] == "AC"


def test_the_queued_status_hook_fires_after_the_commit(web, caplog):
    cookie = web.login("alice")
    with caplog.at_level(logging.INFO):
        sid = web.submit(cookie, uuid_token(1)).json()["submission_id"]
        assert wait_until(lambda: f"STATUS submission={sid} user=1 event=queued" in caplog.text)


def test_rows_created_without_a_token_do_not_collide_with_each_other_or_with_rest(web):
    web.db.create_submission(1, "sum", "python", "a")
    web.db.create_submission(1, "sum", "python", "b")
    assert web.submit(web.login("alice"), uuid_token(1)).status == 201


# ---- client_token ---------------------------------------------------------------------------------------------

def test_duplicate_token_returns_the_original_and_ignores_the_new_content(web):
    cookie = web.login("alice")
    first = web.submit(cookie, uuid_token(1), code="print(1)\n")
    second = post(web, cookie, body(1, language="java", code="class X {}", problem_id="secret"))
    assert (first.status, second.status) == (201, 200)
    assert second.json() == first.json()
    assert count_rows(web) == 1
    row = web.db.conn().execute("SELECT language, code FROM submissions").fetchone()
    assert tuple(row) == ("python", "print(1)\n")


def test_duplicate_is_not_queued_twice(web):
    cookie = web.login("alice")
    worker = web.worker()
    web.submit(cookie, uuid_token(1))
    web.submit(cookie, uuid_token(1))
    worker.finish_ac(worker.take_job())
    with pytest.raises(TimeoutError):
        worker.take_job(timeout=0.5)


def test_duplicate_reports_the_current_state_and_the_original_received_at(web):
    cookie = web.login("alice")
    worker = web.worker()
    first = web.submit(cookie, uuid_token(1)).json()
    worker.finish_ac(worker.take_job())
    web.wait_state(cookie, first["submission_id"], "judged")
    again = web.submit(cookie, uuid_token(1))
    assert again.status == 200
    assert again.json() == {**first, "state": "judged"}


def test_token_is_case_insensitive(web):
    cookie = web.login("alice")
    token = uuid_token(0xABCDEF)
    first = web.submit(cookie, token.lower())
    again = web.submit(cookie, token.upper())
    assert (first.status, again.status) == (201, 200) and again.json() == first.json()
    assert web.db.conn().execute("SELECT client_token FROM submissions").fetchone()[0] == token.lower()


def test_each_user_has_their_own_token_space(web):
    a, b = web.login("alice"), web.login("bob")
    ra, rb = web.submit(a, uuid_token(1)), web.submit(b, uuid_token(1))
    assert (ra.status, rb.status) == (201, 201)
    assert ra.json()["submission_id"] != rb.json()["submission_id"]


def test_concurrent_requests_with_one_token_create_exactly_one_submission(web):
    cookie = web.login("alice")
    results = []
    gate = threading.Barrier(10)

    def go():
        gate.wait()
        results.append(web.submit(cookie, uuid_token(7)))

    threads = [threading.Thread(target=go) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(r.status for r in results) == [200] * 9 + [201]
    assert len({r.json()["submission_id"] for r in results}) == 1
    assert count_rows(web) == 1


def test_duplicate_wins_over_a_problem_that_became_hidden(web):
    cookie = web.login("alice")
    first = web.submit(cookie, uuid_token(1))
    with web.db.write() as c:
        c.execute("UPDATE problems SET hidden = 1 WHERE id = 'sum'")
    again = web.submit(cookie, uuid_token(1))
    assert again.status == 200 and again.json() == first.json()
    assert web.submit(cookie, uuid_token(2)).status == 404


@pytest.mark.parametrize("token", [
    "", "x", "6f1c2d3e-4a5b-4c6d-8e7f-0a1b2c3d4e5", "6f1c2d3e-4a5b-4c6d-8e7f-0a1b2c3d4e5f0",
    "6f1c2d3e4a5b4c6d8e7f0a1b2c3d4e5f", "{6f1c2d3e-4a5b-4c6d-8e7f-0a1b2c3d4e5f}", "6f1c2d3e-4a5b-4c6d-8e7f-0a1b2c3d4e5g",
    "6f1c2d3e-4a5b-4c6d-8e7f-0a1b2c3d4e5f\n", " 6f1c2d3e-4a5b-4c6d-8e7f-0a1b2c3d4e5f", "６f1c2d3e-4a5b-4c6d-8e7f-0a1b2c3d4e5f",
])
def test_client_token_must_be_a_uuid(web, token):
    r = post(web, web.login("alice"), body(1, client_token=token))
    assert r.status == 400 and r.error_code == "INVALID_REQUEST"


@pytest.mark.parametrize("token", ["6f1c2d3e-4a5b-4c6d-8e7f-0a1b2c3d4e5f", "6F1C2D3E-4A5B-4C6D-8E7F-0A1B2C3D4E5F",
                                   "00000000-0000-0000-0000-000000000000"])
def test_uuid_forms_are_accepted(web, token):
    assert post(web, web.login("alice"), body(1, client_token=token)).status == 201


# ---- validation -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("field,value", [
    ("problem_id", None), ("problem_id", 5), ("problem_id", ""), ("problem_id", "a" * 65), ("problem_id", "su m"),
    ("problem_id", "sum\n"), ("problem_id", "su/m"), ("problem_id", ["sum"]),
    ("language", None), ("language", "ruby"), ("language", "Python"), ("language", ""), ("language", ["python"]),
    ("language", {"a": 1}), ("language", 3), ("language", "python "),
    ("code", None), ("code", ""), ("code", 5), ("code", ["print(1)"]),
    ("client_token", None), ("client_token", 5), ("client_token", [uuid_token(1)]),
])
def test_invalid_field_is_400(web, field, value):
    r = post(web, web.login("alice"), body(1, **{field: value}))
    assert r.status == 400 and r.error_code == "INVALID_REQUEST" and field in r.json()["error"]["message"]
    assert count_rows(web) == 0


@pytest.mark.parametrize("missing", ["problem_id", "language", "code", "client_token"])
def test_missing_field_is_400(web, missing):
    obj = body(1)
    del obj[missing]
    assert post(web, web.login("alice"), obj).status == 400


def test_whitespace_only_code_is_allowed_but_empty_is_not(web):
    cookie = web.login("alice")
    assert post(web, cookie, body(1, code=" \n")).status == 201
    assert post(web, cookie, body(2, code="")).status == 400


def test_lone_surrogates_are_400_not_500(web):
    cookie = web.login("alice")
    payload = b'{"problem_id": "sum", "language": "python", "code": "x\\ud800y", "client_token": "%s"}'
    r = web.post("/api/submissions", body=payload % uuid_token(1).encode(), cookie=cookie)
    assert r.status == 400 and r.error_code == "INVALID_REQUEST"
    payload = b'{"problem_id": "\\udfff", "language": "python", "code": "x", "client_token": "%s"}'
    assert web.post("/api/submissions", body=payload % uuid_token(1).encode(), cookie=cookie).status == 400
    assert count_rows(web) == 0


def test_valid_surrogate_pairs_and_nul_in_code_roundtrip(web):
    cookie = web.login("alice")
    code = "print('\U0001F600')\n# a\u0000b\n# tiếng Việt\n"
    sid = web.submit(cookie, uuid_token(1), code=code).json()["submission_id"]
    assert web.get(f"/api/submissions/{sid}", cookie=cookie).json()["code"] == code
    worker = web.worker()
    assert worker.take_job()["code"] == code


def test_crlf_and_trailing_whitespace_in_code_are_preserved(web):
    cookie = web.login("alice")
    code = "a = 1\r\nb = 2   \r\n\t\n"
    sid = web.submit(cookie, uuid_token(1), code=code).json()["submission_id"]
    assert web.get(f"/api/submissions/{sid}", cookie=cookie).json()["code"] == code


# ---- đề -------------------------------------------------------------------------------------------------------

def test_unknown_problem_is_404(web):
    r = web.submit(web.login("alice"), uuid_token(1), problem_id="nope")
    assert r.status == 404 and r.error_code == "PROBLEM_NOT_FOUND"
    assert count_rows(web) == 0


def test_hidden_problem_is_404_for_a_user_and_identical_to_unknown(web):
    cookie = web.login("alice")
    hidden = web.submit(cookie, uuid_token(1), problem_id="secret")
    unknown = web.submit(cookie, uuid_token(2), problem_id="nope")
    assert hidden.status == unknown.status == 404 and hidden.body == unknown.body
    assert count_rows(web) == 0


def test_admin_can_submit_to_a_hidden_problem(web):
    r = web.submit(web.login("root"), uuid_token(1), problem_id="secret")
    assert r.status == 201
    assert web.db.conn().execute("SELECT problem_id, user_id FROM submissions").fetchone()[:] == ("secret", 3)


# ---- kích thước mã --------------------------------------------------------------------------------------------

def test_code_limit_is_64_kb_counted_in_utf8_bytes(web):
    cookie = web.login("alice")
    assert web.submit(cookie, uuid_token(1), code="#" * 65536).status == 201
    r = web.submit(cookie, uuid_token(2), code="#" * 65537)
    assert r.status == 413 and r.error_code == "CODE_TOO_LARGE"
    assert web.submit(cookie, uuid_token(3), code="é" * 32768).status == 201  # 65536 byte
    assert web.submit(cookie, uuid_token(4), code="é" * 32769).status == 413  # ít ký tự hơn 64K nhưng 65538 byte
    assert web.submit(cookie, uuid_token(5), code="\U0001F600" * 16385).status == 413  # 4 byte mỗi ký tự


def test_escaped_json_can_exceed_128_kb_while_the_code_is_within_limit(web):
    cookie = web.login("alice")
    code = "\n" * 65536  # mỗi ký tự thành 2 byte trong JSON ("\n"): body khoảng 131 KB, vẫn dưới 512 KB
    r = web.submit(cookie, uuid_token(1), code=code)
    assert r.status == 201


# ---- thứ tự kiểm tra (README 3.2) -----------------------------------------------------------------------------

def test_order_of_checks(make_web):
    clock = FakeClock()
    web = make_web(clock=clock, submit_rate_limit=1)
    populate(web.db)
    web.start()
    cookie = web.login("alice")
    big = "#" * 70_000
    huge = b'{"x": "' + b"p" * 600_000 + b'"}'
    # 415 trước 413 và 400
    assert web.request("POST", "/api/submissions", body=huge, cookie=cookie, content_type="text/plain").status == 415
    # 413 BODY_TOO_LARGE trước 400 (JSON hỏng)
    assert web.post("/api/submissions", body=b"{" + b"p" * 600_000, cookie=cookie).error_code == "BODY_TOO_LARGE"
    # 400 trước 413 CODE_TOO_LARGE
    r = post(web, cookie, body(1, code=big, client_token="bad"))
    assert r.status == 400
    # CODE_TOO_LARGE trước "token trùng"
    assert web.submit(cookie, uuid_token(1)).status == 201
    r = web.submit(cookie, uuid_token(1), code=big)
    assert r.status == 413 and r.error_code == "CODE_TOO_LARGE"
    # token trùng trước 404 và 429
    assert web.submit(cookie, uuid_token(1), problem_id="nope").status == 200
    # 404 trước 429 (đã đủ 1 bài trong cửa sổ)
    assert web.submit(cookie, uuid_token(2), problem_id="nope").status == 404
    assert web.submit(cookie, uuid_token(2)).status == 429


# ---- rate limit nộp bài ---------------------------------------------------------------------------------------

@pytest.fixture
def limited(make_web):
    clock = FakeClock()
    w = make_web(clock=clock, submit_rate_limit=3, submit_rate_window_s=60)
    populate(w.db)
    w.clock = clock
    return w.start()


def test_fourth_submission_in_the_window_is_429_with_retry_after(limited):
    cookie = limited.login("alice")
    for n in range(1, 4):
        assert limited.submit(cookie, uuid_token(n)).status == 201
        limited.clock.advance(5)
    r = limited.submit(cookie, uuid_token(4))
    assert r.status == 429 and r.error_code == "RATE_LIMITED"
    assert r.headers["retry-after"] == "45"  # bài đầu rời cửa sổ sau 60 - 15 giây
    assert count_rows(limited) == 3


def test_the_window_slides_one_submission_at_a_time(limited):
    cookie = limited.login("alice")
    for n in range(1, 4):
        limited.submit(cookie, uuid_token(n))
        limited.clock.advance(10)
    limited.clock.advance(29.9)
    r = limited.submit(cookie, uuid_token(4))
    assert r.status == 429 and r.headers["retry-after"] == "1"
    limited.clock.advance(0.1)
    assert limited.submit(cookie, uuid_token(4)).status == 201
    assert limited.submit(cookie, uuid_token(5)).status == 429  # lại đủ 3 bài trong cửa sổ


def test_rate_limit_is_per_user_and_counts_admins(limited):
    a, b, root = limited.login("alice"), limited.login("bob"), limited.login("root")
    for n in range(1, 4):
        limited.submit(a, uuid_token(n))
    assert limited.submit(a, uuid_token(9)).status == 429
    assert limited.submit(b, uuid_token(9)).status == 201
    for n in range(1, 4):
        assert limited.submit(root, uuid_token(n)).status == 201
    assert limited.submit(root, uuid_token(9)).status == 429


def test_duplicates_are_neither_counted_nor_blocked(limited):
    cookie = limited.login("alice")
    for n in range(1, 4):
        limited.submit(cookie, uuid_token(n))
    again = limited.submit(cookie, uuid_token(2))  # đang bị giới hạn nhưng đây là bản trùng
    assert again.status == 200
    for _ in range(5):
        assert limited.submit(cookie, uuid_token(1)).status == 200
    assert limited.submit(cookie, uuid_token(9)).status == 429


def test_rejected_requests_do_not_count(limited):
    cookie = limited.login("alice")
    for _ in range(5):
        assert post(limited, cookie, body(1, language="ruby")).status == 400
        assert limited.submit(cookie, uuid_token(1), problem_id="nope").status == 404
        assert limited.submit(cookie, uuid_token(1), code="#" * 70_000).status == 413
        assert limited.request("POST", "/api/submissions", body=b"{}", cookie=cookie,
                               content_type="text/plain").status == 415
    for n in range(1, 4):
        assert limited.submit(cookie, uuid_token(n)).status == 201


def test_rate_limit_survives_a_restart(limited):
    cookie = limited.login("alice")
    for n in range(1, 4):
        limited.submit(cookie, uuid_token(n))
    limited.restart()
    cookie = limited.login("alice")
    assert limited.submit(cookie, uuid_token(9)).status == 429


def test_rate_limit_counts_rows_from_other_sources_in_the_window(limited):
    for n in range(3):
        limited.db.create_submission(1, "sum", "python", str(n))
    # create_submission dùng đồng hồ thật, đồng hồ giả của REST bắt đầu từ năm 2027: bài cũ nằm ngoài cửa sổ
    assert limited.submit(limited.login("alice"), uuid_token(1)).status == 201
