"""Phần thuần của tools/load_test.py: không cần server, Docker hay `websockets`."""

import importlib
import json
import sys
import uuid

import pytest

from tools import load_test as lt


# ---------------------------------------------------------------------------------------------- kế hoạch bài nộp

def test_parse_mix_accepts_valid_and_rejects_bad_input():
    assert lt.parse_mix("py-ac=50, cpp-ac=20") == (("py-ac", 50), ("cpp-ac", 20))
    for bad in ("nope=10", "py-ac=0", "py-ac=-1", "py-ac=x", "py-ac"):
        with pytest.raises(ValueError):
            lt.parse_mix(bad)


def test_default_mix_has_java_about_ten_percent_and_covers_all_verdicts():
    counts = lt.allocate(50, lt.DEFAULT_MIX)
    assert counts == {"py-ac": 25, "cpp-ac": 10, "java-ac": 5, "py-wa": 5, "py-ce": 5}
    assert {lt.KINDS[k][2] for k in counts} == {"AC", "WA", "CE"}


@pytest.mark.parametrize("total", [0, 1, 7, 49, 50, 51, 123])
def test_allocate_always_sums_to_total(total):
    assert sum(lt.allocate(total, lt.DEFAULT_MIX).values()) == total


def test_plan_has_users_times_per_user_bài_each_user_gets_per_user_and_unique_tokens():
    plan = lt.make_plan(10, 5)
    assert len(plan) == 50
    for u in range(10):
        mine = [p for p in plan if p.user == u]
        assert len(mine) == 5 and len({p.kind for p in mine}) >= 3  # rải vòng tròn: mỗi người có nhiều loại
    tokens = [p.token for p in plan]
    assert len(set(tokens)) == 50 and all(str(uuid.UUID(t)) == t for t in tokens)
    assert {p.kind: sum(1 for q in plan if q.kind == p.kind) for p in plan}["java-ac"] == 5


def test_plan_is_parameterised():
    plan = lt.make_plan(3, 2, (("py-ac", 1),))
    assert len(plan) == 6 and {p.kind for p in plan} == {"py-ac"} and {p.user for p in plan} == {0, 1, 2}


# ----------------------------------------------------------------------------------------------- check_stream

def st(state, done=None, total=4):
    e = {"type": "STATUS", "submission_id": 1, "state": state}
    if done is not None:
        e["progress"] = {"tests_done": done, "tests_total": total}
    return e


def test_a_normal_stream_has_no_problem():
    res = lt.check_stream([st("queued"), st("judging"), st("judging", 1), st("judging", 2), st("judged")])
    assert res == {"problems": [], "judged": 1, "redeliveries": 0}


def test_two_judged_are_reported_as_duplicates():
    res = lt.check_stream([st("queued"), st("judged"), st("judged")])
    assert res["judged"] == 2 and any("sau judged" in p for p in res["problems"])


def test_anything_after_judged_is_a_problem():
    res = lt.check_stream([st("queued"), st("judged"), st("judging", 1)])
    assert any("sau judged" in p for p in res["problems"])


def test_progress_must_not_decrease_or_exceed_total_or_have_wrong_type():
    assert any("giảm" in p for p in lt.check_stream([st("judging", 3), st("judging", 1)])["problems"])
    assert any("tests_total" in p for p in lt.check_stream([st("judging", 5, 4)])["problems"])
    bad = st("judging")
    bad["progress"] = {"tests_done": True, "tests_total": 4}
    assert any("sai kiểu" in p for p in lt.check_stream([bad])["problems"])
    bad["progress"] = {"tests_done": "1", "tests_total": 4}
    assert any("sai kiểu" in p for p in lt.check_stream([bad])["problems"])


def test_queued_after_judging_is_a_redelivery_not_a_problem_and_resets_progress():
    res = lt.check_stream([st("queued"), st("judging", 2), st("queued"), st("judging", 1), st("judged")])
    assert res["problems"] == [] and res["redeliveries"] == 1 and res["judged"] == 1


def test_unknown_state_is_a_problem_and_an_empty_stream_has_no_judged():
    assert lt.check_stream([{"state": "weird"}])["problems"]
    assert lt.check_stream([]) == {"problems": [], "judged": 0, "redeliveries": 0}


# --------------------------------------------------------------------------------- cô lập, key cấm, phân vị, cookie

def test_isolation_detects_foreign_submissions():
    assert lt.check_isolation({0: {1, 2}, 1: {3}}, {0: {1, 2}, 1: {3}}) == []
    problems = lt.check_isolation({0: {1, 2, 9}}, {0: {1, 2}})
    assert len(problems) == 1 and "9" in problems[0]


def test_history_check_allows_earlier_runs_but_catches_missing_extra_and_duplicate_ids():
    assert lt.check_history([9, 8, 3, 2, 1], {1, 2, 3}, {8, 9}) == []  # có bài của lần chạy trước (baseline)
    assert lt.check_history([8, 9], set(), {8, 9}) == []
    assert any("thiếu [9]" in p for p in lt.check_history([8], set(), {8, 9}))
    assert any("thừa [7]" in p for p in lt.check_history([7, 8, 9], set(), {8, 9}))
    assert any("trùng" in p for p in lt.check_history([8, 8, 9], set(), {8, 9}))
    assert any("thiếu [1]" in p for p in lt.check_history([8, 9], {1}, {8, 9}))  # bài của lần trước biến mất


def test_forbidden_keys_are_found_at_any_depth_and_clean_data_passes():
    assert lt.find_forbidden_keys({"a": [{"b": {"code": "x"}}], "ce_message": "m"}) == ["a[0].b.code", "ce_message"]
    assert lt.find_forbidden_keys({"submission": {"verdict": "AC", "time_ms": 3}}) == []


def test_percentile_edge_cases_and_interpolation():
    assert lt.percentile([], 50) is None
    assert lt.percentile([7], 99) == 7
    assert lt.percentile([1, 2, 3, 4], 50) == 2.5
    assert lt.percentile([4, 1, 3, 2], 0) == 1 and lt.percentile([4, 1, 3, 2], 100) == 4
    assert lt.percentile(list(range(101)), 90) == 90
    assert lt.describe([]) == {"n": 0, "p50": None, "p90": None, "p99": None, "max": None}
    assert lt.describe([1, 3])["max"] == 3


def test_parse_cookie():
    assert lt.parse_cookie({"set-cookie": "jh_session=abc123; Path=/; HttpOnly; SameSite=Lax"}) == "abc123"
    assert lt.parse_cookie({}) is None and lt.parse_cookie({"set-cookie": "other=1"}) is None


# --------------------------------------------------------------------------------------------- log giao bài

def test_delivery_log_check():
    log = ("submission 5 attempt 1 giao cho worker #1 ('a')\nsubmission 6 attempt 1 giao cho worker #2 ('b')\n"
           "submission 6 attempt 2 giao cho worker #1 ('a')\n")
    assert lt.check_delivery_log(log, [5], {}) == []
    assert lt.check_delivery_log(log, [5, 6], {6: 1}) == []
    assert lt.check_delivery_log(log, [6], {})  # giao hai lần mà không quan sát được giao lại
    assert lt.check_delivery_log(log, [7], {})  # không có dòng nào
    assert lt.check_delivery_log("submission 5 attempt 2 giao cho worker #1 ('a')", [5], {})  # thiếu attempt 1


# ------------------------------------------------------------------------------------------------- cấu hình

def make_cfg(base, **kw):
    return lt.Config(base=base, password="s3cret-pw", **kw)


def test_config_repr_never_contains_the_password():
    cfg = make_cfg("http://host.docker.internal:8000")
    assert "s3cret-pw" not in repr(cfg) and "s3cret-pw" not in str(cfg)


@pytest.mark.parametrize("base,origin,ws,netloc", [
    ("http://host.docker.internal:8000", "http://host.docker.internal:8000", "ws://host.docker.internal:8000/ws",
     "host.docker.internal:8000"),
    ("http://server:8000", "http://server:8000", "ws://server:8000/ws", "server:8000"),
    ("http://localhost", "http://localhost", "ws://localhost/ws", "localhost"),
    ("https://judge.example.org", "https://judge.example.org", "wss://judge.example.org/ws", "judge.example.org"),
    ("https://judge.example.org:8443", "https://judge.example.org:8443", "wss://judge.example.org:8443/ws",
     "judge.example.org:8443"),
])
def test_origin_and_ws_url_follow_the_host_header_exactly(base, origin, ws, netloc):
    cfg = make_cfg(base)
    assert (cfg.origin, cfg.ws_url, cfg.netloc) == (origin, ws, netloc)


def test_usernames_are_prefix_and_two_digit_index():
    cfg = make_cfg("http://x:1", prefix="lt")
    assert [cfg.username(i) for i in (0, 9, 10)] == ["lt-01", "lt-10", "lt-11"]


def test_main_refuses_without_password_or_with_bad_arguments(monkeypatch, capsys):
    monkeypatch.delenv(lt.PASSWORD_ENV, raising=False)
    assert lt.main(["--base", "http://127.0.0.1:1"]) == 2
    monkeypatch.setenv(lt.PASSWORD_ENV, "pw")
    assert lt.main(["--base", "http://127.0.0.1:1", "--mix", "nope=1"]) == 2
    assert lt.main(["--base", "http://127.0.0.1:1", "--per-user", str(lt.PENDING_CAP + 1)]) == 2
    err = capsys.readouterr().err
    assert "pw" not in err.replace("password", "")  # không in mật khẩu


def test_module_imports_without_websockets(monkeypatch):
    monkeypatch.setitem(sys.modules, "websockets", None)  # import websockets sẽ ném ImportError
    monkeypatch.delitem(sys.modules, "tools.load_test", raising=False)
    mod = importlib.import_module("tools.load_test")
    assert mod.make_plan(2, 2)


# --------------------------------------------------------------------------------------- số đo, số liệu thô

def rec(plan, post, ack, events):
    r = lt.Rec(plan)
    r.t_post, r.t_ack, r.status, r.sid, r.events = post, ack, 201, plan.index + 1, [(t, {"state": s}) for t, s in events]
    return r


def test_metrics_compute_ack_wait_judge_total_and_throughput():
    plan = lt.make_plan(2, 1, (("py-ac", 1),))
    recs = [rec(plan[0], 10.0, 10.5, [(11.0, "judging"), (12.0, "judged")]),
            rec(plan[1], 10.0, 10.5, [(13.0, "judging"), (15.0, "judged")])]
    m = lt.metrics(recs, 10.0)
    assert m["seconds"]["ack"]["max"] == 0.5
    assert m["seconds"]["wait"]["p50"] == pytest.approx(1.5)  # 0,5 và 2,5
    assert m["seconds"]["judge"]["max"] == 2.0 and m["seconds"]["total"]["max"] == 5.0
    assert m["judged"] == 2 and m["span_s"] == 5.0 and m["throughput_per_s"] == pytest.approx(0.4)
    assert set(m["total_by_language"]) == {"python"}


def test_metrics_with_nothing_judged_does_not_divide_by_zero():
    plan = lt.make_plan(1, 1, (("py-ac", 1),))
    m = lt.metrics([rec(plan[0], 1.0, 1.2, [(1.3, "queued")])], 1.0)
    assert m["judged"] == 0 and m["throughput_per_s"] is None


def test_raw_record_json_has_no_code_token_or_cookie():
    plan = lt.make_plan(1, 1, (("py-ac", 1),))
    r = rec(plan[0], 1.0, 1.2, [(1.3, "judged")])
    r.final, r.final_via = {"verdict": "AC"}, "ws"
    dumped = json.dumps(lt.rec_to_json(r))
    assert plan[0].token not in dumped and plan[0].code not in dumped and "jh_session" not in dumped
    assert json.loads(dumped)["verdict"] == "AC"
