"""REST: `GET /api/admin/submissions/{id}`, chi tiết bài nộp của bất kỳ ai kèm mã nguồn (README 3.6, D11, D22).

Không khóa. Whitelist theo verdict dùng lại đúng hàm của chủ bài (`views.detail_view`), nên test so hai đường với nhau."""

import json

import pytest

from _support import insert_submission

DETAIL_KEYS = {"submission_id", "problem_id", "problem_title", "language", "received_at", "state", "verdict", "time_ms",
               "memory_kib", "failed_test", "failed_is_sample", "re_kind", "rejudged_at", "code", "ce_message",
               "sample_stdout", "sample_input", "sample_expected_output"}
ADMIN_EXTRA = {"user_id", "username", "attempt_id", "delivery_count", "total_deliveries", "is_rejudge", "rejudge_failed"}
SECRETS = ("HIDDEN-IN", "HIDDEN-OUT", "LEAK-IF-SHOWN", "exit_code")


def admin_cookie(web):
    if not hasattr(web, "_root_cookie"):  # đăng nhập có rate limit, nên đăng nhập root một lần cho mỗi server
        web._root_cookie = web.login("root")
    return web._root_cookie


def user_id(web, name):
    return web.db.conn().execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]


def done(web, who="alice", problem_id="sum", **columns):
    h = web.db.conn().execute("SELECT tests_hash FROM problems WHERE id = ?", (problem_id,)).fetchone()[0]
    columns.setdefault("verdict_test_hash", h)
    return insert_submission(web.db, user_id(web, who), problem_id, state="done", **columns)


def admin_detail(web, sid):
    r = web.get(f"/api/admin/submissions/{sid}", cookie=admin_cookie(web))
    assert r.status == 200, (r.status, r.body)
    return r.json()


# ---- phân quyền và lỗi ------------------------------------------------------------------------------------------

def test_requires_a_session(web):
    sid = done(web, verdict="AC", time_ms=1, memory_kib=1)
    assert web.get(f"/api/admin/submissions/{sid}").status == 401


@pytest.mark.parametrize("make_path", [
    lambda own, other: f"/api/admin/submissions/{own}",       # bài của chính họ
    lambda own, other: f"/api/admin/submissions/{other}",     # bài của người khác
    lambda own, other: "/api/admin/submissions/999999",       # không tồn tại
    lambda own, other: "/api/admin/submissions/abc",          # sai định dạng
    lambda own, other: f"/api/admin/submissions/{own}?x=1",   # query sai: vẫn 403 trước
], ids=["own", "other", "missing", "malformed", "bad-query"])
def test_non_admin_gets_403_before_anything_else(web, make_path):
    own = done(web, "alice", verdict="AC", time_ms=1, memory_kib=1)
    other = done(web, "bob", verdict="AC", time_ms=1, memory_kib=1)
    r = web.get(make_path(own, other), cookie=web.login("alice"))
    assert (r.status, r.error_code) == (403, "FORBIDDEN")


def test_missing_malformed_and_noncanonical_ids_are_the_same_404(web):
    root = admin_cookie(web)
    seen = set()
    for raw in ("999999", "abc", "01", "0", "-1", "1" * 5000, "1.5"):
        r = web.get(f"/api/admin/submissions/{raw}", cookie=root)
        assert (r.status, r.error_code) == (404, "SUBMISSION_NOT_FOUND"), raw[:20]
        seen.add(r.body)
    assert len(seen) == 1


@pytest.mark.parametrize("query", ["?x=1", "?limit=5", "?a=1&a=2"])
def test_any_query_parameter_is_400(web, query):
    sid = done(web, verdict="AC", time_ms=1, memory_kib=1)
    r = web.get(f"/api/admin/submissions/{sid}{query}", cookie=admin_cookie(web))
    assert (r.status, r.error_code) == (400, "INVALID_REQUEST")


def test_other_methods_are_405(web):
    sid = done(web, verdict="AC", time_ms=1, memory_kib=1)
    assert web.post(f"/api/admin/submissions/{sid}", {}, cookie=admin_cookie(web)).status == 405


# ---- nội dung ---------------------------------------------------------------------------------------------------

def test_admin_sees_another_users_submission_with_code_and_operational_fields(web):
    sid = done(web, "alice", code="print('ALICE-CODE')", verdict="WA", time_ms=5, memory_kib=64, failed_test=1,
               failed_is_sample=1, sample_stdout="4\n", attempt_id=2, delivery_count=1, total_deliveries=3,
               is_rejudge=1, rejudge_failed=1)
    d = admin_detail(web, sid)
    assert set(d) == DETAIL_KEYS | ADMIN_EXTRA
    assert (d["code"], d["username"], d["user_id"]) == ("print('ALICE-CODE')", "alice", user_id(web, "alice"))
    assert (d["attempt_id"], d["delivery_count"], d["total_deliveries"], d["is_rejudge"], d["rejudge_failed"]) == (
        2, 1, 3, True, True)
    assert (d["sample_input"], d["sample_expected_output"], d["sample_stdout"]) == ("1 2\n", "3\n", "4\n")


def test_ce_message_only_for_ce(web):
    ce = done(web, verdict="CE", ce_message="Main.cpp:1: error: boom")
    ac = done(web, verdict="AC", time_ms=1, memory_kib=1, ce_message="stale")
    assert admin_detail(web, ce)["ce_message"] == "Main.cpp:1: error: boom"
    assert admin_detail(web, ac)["ce_message"] is None


def test_failed_hidden_test_exposes_nothing_about_the_test(web):
    sid = done(web, verdict="WA", time_ms=1, memory_kib=1, failed_test=3, failed_is_sample=0,
               sample_stdout="LEAK-IF-SHOWN")
    r = web.get(f"/api/admin/submissions/{sid}", cookie=admin_cookie(web))
    d = r.json()
    assert (d["failed_test"], d["failed_is_sample"]) == (3, False)
    assert d["sample_stdout"] is d["sample_input"] is d["sample_expected_output"] is None
    for secret in SECRETS:
        assert secret.encode() not in r.body, secret


def test_hidden_tests_never_appear_whatever_the_row_contains(web):
    # `sample_stdout` chỉ được hiện khi test sai là test mẫu (README 3.3), nên marker rò rỉ chỉ gắn vào dòng test ẩn
    ids = [done(web, verdict=v, time_ms=1, memory_kib=1, failed_test=t, failed_is_sample=0, sample_stdout="LEAK-IF-SHOWN",
                re_kind="EXIT_NONZERO")
           for v in ("WA", "RE", "TLE", "MLE", "AC", "SE") for t in (3, 4, 99)]
    for sid in ids:
        text = json.dumps(admin_detail(web, sid))
        for secret in SECRETS:
            assert secret not in text, (sid, secret)


def test_stale_columns_for_the_verdict_stay_hidden(web):
    sid = done(web, verdict="AC", time_ms=3, memory_kib=8, failed_test=1, failed_is_sample=1, re_kind="SIGSEGV",
               sample_stdout="LEAK-IF-SHOWN")
    d = admin_detail(web, sid)
    assert (d["failed_test"], d["failed_is_sample"], d["re_kind"]) == (None, None, None)
    assert d["sample_stdout"] is d["sample_input"] is d["sample_expected_output"] is None


def test_se_carries_only_the_verdict(web):
    sid = done(web, verdict="SE", ce_message="internal detail", sample_stdout="LEAK-IF-SHOWN", time_ms=9)
    d = admin_detail(web, sid)
    assert d["verdict"] == "SE" and d["ce_message"] is None and d["time_ms"] is None
    assert d["sample_stdout"] is None


def test_sample_input_withheld_when_the_tests_changed_after_the_verdict(web):
    sid = done(web, verdict="WA", time_ms=1, memory_kib=1, failed_test=1, failed_is_sample=1, sample_stdout="4\n",
               verdict_test_hash="an-older-hash")
    d = admin_detail(web, sid)
    assert d["sample_input"] is None and d["sample_expected_output"] is None and d["sample_stdout"] == "4\n"


def test_hidden_problem_still_shows_samples_to_admin_and_keeps_the_title(web):
    sid = done(web, "alice", problem_id="secret", verdict="WA", time_ms=1, memory_kib=1, failed_test=2,
               failed_is_sample=1, sample_stdout="31\n")
    d = admin_detail(web, sid)
    assert (d["sample_input"], d["sample_expected_output"], d["problem_title"]) == ("10 20\n", "30\n", "Sum")


def test_same_whitelist_as_the_owners_view_for_the_admins_own_submission(web):
    """Cùng hàm lọc: với bài của chính admin, `GET /api/submissions/{id}` và route admin trả cùng nội dung."""
    for kwargs in (dict(verdict="WA", time_ms=1, memory_kib=1, failed_test=1, failed_is_sample=1, sample_stdout="4\n"),
                   dict(verdict="WA", time_ms=1, memory_kib=1, failed_test=3, failed_is_sample=0, sample_stdout="x"),
                   dict(verdict="CE", ce_message="boom"),
                   dict(verdict="RE", failed_test=2, failed_is_sample=1, re_kind="SIGSEGV", sample_stdout="y")):
        sid = done(web, "root", **kwargs)
        mine = web.get(f"/api/submissions/{sid}", cookie=admin_cookie(web)).json()
        theirs = admin_detail(web, sid)
        assert {k: v for k, v in theirs.items() if k not in ADMIN_EXTRA} == mine


def test_a_queued_submission_with_no_result_yet(web):
    sid = insert_submission(web.db, user_id(web, "alice"), "sum", code="print(0)")
    d = admin_detail(web, sid)
    assert (d["state"], d["verdict"], d["code"], d["attempt_id"]) == ("queued", None, "print(0)", 0)


# ---- log --------------------------------------------------------------------------------------------------------

def test_each_view_logs_admin_and_submission_but_never_the_code(web, caplog):
    caplog.set_level("INFO", logger="judgehive.rest")
    sid = done(web, "alice", code="print('VERY-SECRET-CODE')", verdict="AC", time_ms=1, memory_kib=1)
    admin_detail(web, sid)
    root = user_id(web, "root")
    assert f"ADMIN admin={root} view submission={sid}" in [r.getMessage() for r in caplog.records]
    assert "VERY-SECRET-CODE" not in caplog.text


def test_a_refused_or_missing_view_is_not_logged_as_a_view(web, caplog):
    caplog.set_level("INFO", logger="judgehive.rest")
    sid = done(web, "alice", verdict="AC", time_ms=1, memory_kib=1)
    web.get(f"/api/admin/submissions/{sid}", cookie=web.login("bob"))
    web.get("/api/admin/submissions/999999", cookie=admin_cookie(web))
    assert "view submission" not in caplog.text
