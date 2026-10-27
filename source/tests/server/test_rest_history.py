"""REST: lịch sử nộp bài và chi tiết một bài (README 3.3, D8, D11)."""

import pytest

from _rest_support import populate, uuid_token
from _support import insert_submission

LIST_KEYS = {"submission_id", "problem_id", "problem_title", "language", "received_at", "state", "verdict", "time_ms",
             "memory_kib", "failed_test", "failed_is_sample", "re_kind", "rejudged_at"}
DETAIL_KEYS = LIST_KEYS | {"code", "ce_message", "sample_stdout", "sample_input", "sample_expected_output"}


def current_hash(web, problem_id="sum"):
    return web.db.conn().execute("SELECT tests_hash FROM problems WHERE id = ?", (problem_id,)).fetchone()[0]


def done(web, user_id=1, problem_id="sum", **columns):
    """Một bài đã chấm xong; `verdict_test_hash` mặc định là hash hiện tại của đề."""
    columns.setdefault("verdict_test_hash", current_hash(web, problem_id))
    return insert_submission(web.db, user_id, problem_id, state="done", **columns)


def detail(web, cookie, sid):
    r = web.get(f"/api/submissions/{sid}", cookie=cookie)
    assert r.status == 200, r.body
    return r.json()


# ---- danh sách ------------------------------------------------------------------------------------------------

def test_empty_history(web):
    r = web.get("/api/submissions", cookie=web.login("alice"))
    assert r.status == 200 and r.json() == {"submissions": [], "next_before_id": None}


def test_list_has_exactly_the_documented_fields_and_no_secrets(web):
    cookie = web.login("alice")
    done(web, verdict="WA", time_ms=3, memory_kib=4, failed_test=1, failed_is_sample=1, sample_stdout="x",
         ce_message="ce")
    item = web.get("/api/submissions", cookie=cookie).json()["submissions"][0]
    assert set(item) == LIST_KEYS
    assert item["problem_title"] == "Sum" and item["state"] == "judged" and item["verdict"] == "WA"
    assert (item["time_ms"], item["memory_kib"], item["failed_test"], item["failed_is_sample"]) == (3, 4, 1, True)


def test_only_own_submissions_even_for_admin(web):
    a, b, root = (insert_submission(web.db, uid) for uid in (1, 2, 3))
    for name, expected in (("alice", [a]), ("bob", [b]), ("root", [root])):
        ids = [s["submission_id"] for s in web.get("/api/submissions", cookie=web.login(name)).json()["submissions"]]
        assert ids == expected


def test_history_keeps_submissions_of_a_problem_that_is_now_hidden(web):
    sid = insert_submission(web.db, 1, "sum")
    with web.db.write() as c:
        c.execute("UPDATE problems SET hidden = 1 WHERE id = 'sum'")
    items = web.get("/api/submissions", cookie=web.login("alice")).json()["submissions"]
    assert [(s["submission_id"], s["problem_title"]) for s in items] == [(sid, "Sum")]


def test_newest_first_and_default_page_is_50(web):
    ids = [insert_submission(web.db, 1) for _ in range(53)]
    out = web.get("/api/submissions", cookie=web.login("alice")).json()
    assert [s["submission_id"] for s in out["submissions"]] == ids[::-1][:50]
    assert out["next_before_id"] == ids[3]


def test_walking_the_pages_visits_every_submission_once(web):
    ids = [insert_submission(web.db, 1) for _ in range(23)]
    cookie = web.login("alice")
    seen, before = [], None
    while True:
        path = "/api/submissions?limit=5" + ("" if before is None else f"&before_id={before}")
        out = web.get(path, cookie=cookie).json()
        seen += [s["submission_id"] for s in out["submissions"]]
        before = out["next_before_id"]
        if before is None:
            break
    assert seen == ids[::-1]


def test_next_before_id_is_null_on_the_last_page_even_when_it_is_exactly_full(web):
    ids = [insert_submission(web.db, 1) for _ in range(5)]
    cookie = web.login("alice")
    assert web.get("/api/submissions?limit=5", cookie=cookie).json()["next_before_id"] is None
    assert web.get("/api/submissions?limit=4", cookie=cookie).json()["next_before_id"] == ids[1]


def test_before_id_is_exclusive(web):
    ids = [insert_submission(web.db, 1) for _ in range(3)]
    out = web.get(f"/api/submissions?before_id={ids[2]}", cookie=web.login("alice")).json()
    assert [s["submission_id"] for s in out["submissions"]] == [ids[1], ids[0]]
    assert web.get(f"/api/submissions?before_id={ids[0]}", cookie=web.login("alice")).json()["submissions"] == []


def test_limit_bounds(web):
    for _ in range(3):
        insert_submission(web.db, 1)
    cookie = web.login("alice")
    assert len(web.get("/api/submissions?limit=1", cookie=cookie).json()["submissions"]) == 1
    assert len(web.get("/api/submissions?limit=200", cookie=cookie).json()["submissions"]) == 3
    assert web.get("/api/submissions?before_id=9223372036854775807", cookie=cookie).status == 200


@pytest.mark.parametrize("query", [
    "limit=0", "limit=201", "limit=-1", "limit=abc", "limit=1.5", "limit=%205", "limit=5%20", "limit=%D9%A5", "limit=",
    "limit=%2B5", "limit=5%0A", "limit=1e2", "limit=0x10", "limit=99999999999999999999",
    "before_id=0", "before_id=-1", "before_id=x", "before_id=", "before_id=9223372036854775808",
    "before_id=99999999999999999999999", "before_id=1.0", "before_id=%D9%A5",
    "limit=1&limit=2", "before_id=5&before_id=6", "limit=1&limit=1",
])
def test_bad_query_parameters_are_400(web, query):
    r = web.get("/api/submissions?" + query, cookie=web.login("alice"))
    assert r.status == 400 and r.error_code == "INVALID_REQUEST"


def test_unknown_query_parameters_are_ignored(web):
    insert_submission(web.db, 1)
    r = web.get("/api/submissions?foo=bar&limit=1&x=%FF", cookie=web.login("alice"))
    assert r.status == 200 and len(r.json()["submissions"]) == 1


def test_state_names_and_verdict_before_the_first_result(web):
    queued = insert_submission(web.db, 1, state="queued")
    judging = insert_submission(web.db, 1, state="judging")
    finished = done(web, verdict="AC", time_ms=1, memory_kib=2)
    items = {s["submission_id"]: s for s in web.get("/api/submissions", cookie=web.login("alice")).json()["submissions"]}
    assert (items[queued]["state"], items[queued]["verdict"]) == ("queued", None)
    assert (items[judging]["state"], items[judging]["verdict"]) == ("judging", None)
    assert (items[finished]["state"], items[finished]["verdict"]) == ("judged", "AC")


def test_a_rejudge_in_progress_keeps_the_previous_verdict(web):
    sid = done(web, verdict="WA", time_ms=9, memory_kib=8, failed_test=3, failed_is_sample=0)
    with web.db.write() as c:
        c.execute("UPDATE submissions SET state = 'queued', is_rejudge = 1 WHERE id = ?", (sid,))
    item = web.get("/api/submissions", cookie=web.login("alice")).json()["submissions"][0]
    assert (item["state"], item["verdict"], item["time_ms"], item["failed_test"]) == ("queued", "WA", 9, 3)


def test_timestamps_are_iso_utc_with_milliseconds(web):
    insert_submission(web.db, 1, state="done", verdict="AC", time_ms=1, memory_kib=1,
                      verdict_test_hash="h", rejudged_at=1_800_000_000_123)
    with web.db.write() as c:
        c.execute("UPDATE submissions SET submitted_at = 1700000000007")
    item = web.get("/api/submissions", cookie=web.login("alice")).json()["submissions"][0]
    assert item["received_at"] == "2023-11-14T22:13:20.007Z" and item["rejudged_at"] == "2027-01-15T08:00:00.123Z"


def test_fields_that_do_not_apply_to_the_verdict_are_never_returned(web):
    junk = dict(time_ms=11, memory_kib=22, failed_test=2, failed_is_sample=0, re_kind="SIGSEGV", ce_message="ce",
                sample_stdout="out")
    expect = {  # verdict -> các field được phép khác null
        "AC": {"time_ms", "memory_kib"},
        "WA": {"time_ms", "memory_kib", "failed_test", "failed_is_sample"},
        "TLE": {"time_ms", "memory_kib", "failed_test", "failed_is_sample"},
        "MLE": {"time_ms", "memory_kib", "failed_test", "failed_is_sample"},
        "RE": {"failed_test", "failed_is_sample", "re_kind"},
        "CE": set(), "SE": set(),
    }
    cookie = web.login("alice")
    for verdict, allowed in expect.items():
        sid = done(web, verdict=verdict, **junk)
        for item in (web.get("/api/submissions", cookie=cookie).json()["submissions"][0], detail(web, cookie, sid)):
            nonnull = {k for k in ("time_ms", "memory_kib", "failed_test", "failed_is_sample", "re_kind")
                       if item[k] is not None}
            assert nonnull == allowed, verdict
        d = detail(web, cookie, sid)
        assert (d["ce_message"] is not None) == (verdict == "CE"), verdict
        assert d["sample_stdout"] is None, verdict  # failed_is_sample = 0: test sai là test ẩn


def test_unknown_re_kind_is_not_passed_through(web):
    cookie = web.login("alice")
    for kind, shown in (("SIGSEGV", "SIGSEGV"), ("EXIT_NONZERO", "EXIT_NONZERO"), ("OTHER_SIGNAL", "OTHER_SIGNAL"),
                        ("exit(42)", None), ("SIGSEGV; code=139", None)):
        sid = done(web, verdict="RE", failed_test=1, failed_is_sample=0, re_kind=kind)
        assert detail(web, cookie, sid)["re_kind"] == shown


# ---- chi tiết -------------------------------------------------------------------------------------------------

def test_detail_has_the_documented_fields_and_the_submitted_code(web):
    cookie = web.login("alice")
    r = web.submit(cookie, uuid_token(1), code="print('hi')\n")
    d = detail(web, cookie, r.json()["submission_id"])
    assert set(d) == DETAIL_KEYS
    assert d["code"] == "print('hi')\n" and d["state"] == "queued" and d["verdict"] is None
    assert all(d[k] is None for k in ("ce_message", "sample_stdout", "sample_input", "sample_expected_output"))
    assert list(d)[0] == "submission_id"  # đối tượng bài nộp trực tiếp, không bọc


def test_other_peoples_and_missing_submissions_are_the_same_404(web):
    mine = insert_submission(web.db, 1)
    theirs = insert_submission(web.db, 2)
    for name in ("alice", "root"):
        cookie = web.login(name)
        a = web.get(f"/api/submissions/{theirs}", cookie=cookie)
        b = web.get("/api/submissions/99999", cookie=cookie)
        assert a.status == b.status == 404 and a.body == b.body and a.error_code == "SUBMISSION_NOT_FOUND"
    assert web.get(f"/api/submissions/{mine}", cookie=web.login("alice")).status == 200
    assert web.get(f"/api/submissions/{mine}", cookie=web.login("bob")).status == 404


@pytest.mark.parametrize("sid", ["0", "01", "+1", "-1", "1e3", "abc", "1.0", "%201", "1%0A", "%D9%A1",
                                 "9223372036854775808", "99999999999999999999999"])
def test_malformed_submission_ids_are_404(web, sid):
    insert_submission(web.db, 1)
    r = web.get("/api/submissions/" + sid, cookie=web.login("alice"))
    assert r.status == 404 and r.error_code == "SUBMISSION_NOT_FOUND"


def test_ce_message_is_returned_only_for_ce(web):
    cookie = web.login("alice")
    ce = done(web, verdict="CE", ce_message="Main.cpp:1: error: boom")
    assert detail(web, cookie, ce)["ce_message"] == "Main.cpp:1: error: boom"
    assert detail(web, cookie, done(web, verdict="AC", time_ms=1, memory_kib=1, ce_message="stale"))["ce_message"] is None


def test_failed_sample_returns_input_expected_and_the_programs_stdout(web):
    cookie = web.login("alice")
    first = done(web, verdict="WA", time_ms=1, memory_kib=1, failed_test=1, failed_is_sample=1, sample_stdout="4\n")
    d = detail(web, cookie, first)
    assert (d["sample_input"], d["sample_expected_output"], d["sample_stdout"]) == ("1 2\n", "3\n", "4\n")
    second = done(web, verdict="WA", time_ms=1, memory_kib=1, failed_test=2, failed_is_sample=1, sample_stdout="31\n")
    d = detail(web, cookie, second)
    assert (d["sample_input"], d["sample_expected_output"], d["sample_stdout"]) == ("10 20\n", "30\n", "31\n")


def test_failed_hidden_test_exposes_nothing_about_the_test(web):
    cookie = web.login("alice")
    sid = done(web, verdict="WA", time_ms=1, memory_kib=1, failed_test=3, failed_is_sample=0,
               sample_stdout="LEAK-IF-SHOWN")
    r = web.get(f"/api/submissions/{sid}", cookie=cookie)
    d = r.json()
    assert (d["failed_test"], d["failed_is_sample"]) == (3, False)
    assert d["sample_stdout"] is d["sample_input"] is d["sample_expected_output"] is None
    assert b"LEAK-IF-SHOWN" not in r.body and b"HIDDEN-" not in r.body


def test_sample_input_is_withheld_when_the_tests_changed_after_the_verdict(web):
    cookie = web.login("alice")
    sid = done(web, verdict="WA", time_ms=1, memory_kib=1, failed_test=1, failed_is_sample=1, sample_stdout="4\n",
               verdict_test_hash="an-older-hash")
    d = detail(web, cookie, sid)
    assert d["sample_input"] is None and d["sample_expected_output"] is None and d["sample_stdout"] == "4\n"


def test_sample_input_is_withheld_for_a_hidden_problem_unless_admin(web):
    user_sid = done(web, user_id=1, problem_id="secret", verdict="WA", time_ms=1, memory_kib=1, failed_test=1,
                    failed_is_sample=1, sample_stdout="4\n")
    admin_sid = done(web, user_id=3, problem_id="secret", verdict="WA", time_ms=1, memory_kib=1, failed_test=1,
                     failed_is_sample=1, sample_stdout="4\n")
    d = detail(web, web.login("alice"), user_sid)
    assert d["sample_input"] is None and d["sample_expected_output"] is None and d["sample_stdout"] == "4\n"
    assert d["problem_title"] == "Sum"
    d = detail(web, web.login("root"), admin_sid)
    assert (d["sample_input"], d["sample_expected_output"]) == ("1 2\n", "3\n")


def test_failed_test_number_outside_the_samples_gives_no_sample_data(web):
    cookie = web.login("alice")
    sid = done(web, verdict="WA", time_ms=1, memory_kib=1, failed_test=7, failed_is_sample=1, sample_stdout="4\n")
    d = detail(web, cookie, sid)
    assert d["sample_input"] is None and d["sample_expected_output"] is None


def test_se_shows_only_the_verdict(web):
    cookie = web.login("alice")
    sid = done(web, verdict="SE", time_ms=1, memory_kib=1, failed_test=1, failed_is_sample=1, re_kind="SIGSEGV",
               ce_message="detail", sample_stdout="detail")
    d = detail(web, cookie, sid)
    assert d["verdict"] == "SE"
    assert all(d[k] is None for k in DETAIL_KEYS - {"submission_id", "problem_id", "problem_title", "language",
                                                    "received_at", "state", "verdict", "code"} - {"rejudged_at"})


# ---- luồng thật qua worker giả: không rò test ẩn --------------------------------------------------------------

def all_rest_output(web, cookie, sid):
    out = b""
    for path in ("/api/submissions", f"/api/submissions/{sid}", "/api/problems", "/api/problems/sum", "/api/me"):
        out += web.get(path, cookie=cookie).body
    return out


def test_wrong_answer_on_a_hidden_test_never_leaks_through_any_endpoint(web):
    cookie = web.login("alice")
    worker = web.worker()
    sid = web.submit(cookie, uuid_token(1)).json()["submission_id"]
    job = worker.take_job()
    hidden_index = next(i for i, t in enumerate(job["tests"], 1) if not t["is_sample"])
    for done_count in range(hidden_index):
        worker.progress(job, done_count)
    worker.result(job, "WA", time_ms=7, memory_kib=2048, failed_test=hidden_index, failed_is_sample=False)
    d = web.wait_state(cookie, sid, "judged")
    assert (d["verdict"], d["failed_test"], d["failed_is_sample"], d["time_ms"]) == ("WA", hidden_index, False, 7)
    out = all_rest_output(web, cookie, sid)
    assert b"HIDDEN-" not in out and b'"sample_stdout":null' in out


def test_wrong_answer_on_a_sample_shows_the_sample_to_its_owner(web):
    cookie = web.login("alice")
    worker = web.worker()
    sid = web.submit(cookie, uuid_token(1)).json()["submission_id"]
    job = worker.take_job()
    worker.progress(job, 0)
    worker.result(job, "WA", time_ms=7, memory_kib=2048, failed_test=1, failed_is_sample=True, sample_stdout="4\n")
    d = web.wait_state(cookie, sid, "judged")
    assert (d["sample_input"], d["sample_expected_output"], d["sample_stdout"]) == ("1 2\n", "3\n", "4\n")
    assert b"HIDDEN-" not in all_rest_output(web, cookie, sid)


def test_runtime_error_exposes_the_kind_but_no_exit_code(web):
    cookie = web.login("alice")
    worker = web.worker()
    sid = web.submit(cookie, uuid_token(1)).json()["submission_id"]
    job = worker.take_job()
    worker.progress(job, 0)
    worker.result(job, "RE", failed_test=3, failed_is_sample=False, re_kind="EXIT_NONZERO")
    d = web.wait_state(cookie, sid, "judged")
    assert d["verdict"] == "RE" and d["re_kind"] == "EXIT_NONZERO"
    assert not any("exit" in k and k != "re_kind" for k in d)


def test_compile_error_flow(web):
    cookie = web.login("alice")
    worker = web.worker()
    sid = web.submit(cookie, uuid_token(1), language="cpp", code="int main(").json()["submission_id"]
    job = worker.take_job()
    worker.result(job, "CE", ce_message="Main.cpp:1: error: expected")
    d = web.wait_state(cookie, sid, "judged")
    assert d["verdict"] == "CE" and d["ce_message"] == "Main.cpp:1: error: expected" and d["time_ms"] is None


def test_each_users_history_is_isolated_in_a_live_flow(web):
    a, b = web.login("alice"), web.login("bob")
    sa = web.submit(a, uuid_token(1)).json()["submission_id"]
    sb = web.submit(b, uuid_token(1)).json()["submission_id"]
    assert [s["submission_id"] for s in web.get("/api/submissions", cookie=a).json()["submissions"]] == [sa]
    assert [s["submission_id"] for s in web.get("/api/submissions", cookie=b).json()["submissions"]] == [sb]
    assert web.get(f"/api/submissions/{sb}", cookie=a).status == 404
