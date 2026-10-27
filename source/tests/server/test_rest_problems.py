"""REST: danh sách đề và chi tiết đề, đề ẩn (README 3.3, D8, D11)."""

import pytest

from _rest_support import HIDDEN_TESTS, ensure_problem, populate


def test_list_shows_only_visible_problems_to_a_user(web):
    r = web.get("/api/problems", cookie=web.login("alice"))
    assert r.status == 200
    assert r.json() == {"problems": [{"id": "sum", "title": "Sum", "time_limit_ms": 1000, "memory_limit_kib": 262144,
                                      "hidden": False}]}


def test_admin_sees_every_problem_with_the_hidden_flag(web):
    problems = web.get("/api/problems", cookie=web.login("root")).json()["problems"]
    assert [(p["id"], p["hidden"]) for p in problems] == [("secret", True), ("sum", False)]


def test_list_is_sorted_by_id(make_web):
    web = make_web()
    populate(web.db)
    for pid in ("b-2", "a_1", "B", "10", "9"):
        ensure_problem(web.db, pid)
    web.start()
    ids = [p["id"] for p in web.get("/api/problems", cookie=web.login("alice")).json()["problems"]]
    assert ids == sorted(ids) == ["10", "9", "B", "a_1", "b-2", "sum"]


def test_list_is_empty_when_there_are_no_visible_problems(make_web):
    web = make_web()
    web.db.create_user("zed", __import__("_rest_support").password_hash(), "user")
    web.start()
    assert web.get("/api/problems", cookie=web.login("zed")).json() == {"problems": []}


def test_detail_has_statement_and_samples_in_order_but_no_hidden_tests(web):
    r = web.get("/api/problems/sum", cookie=web.login("alice"))
    assert r.status == 200
    body = r.json()
    assert set(body) == {"id", "title", "statement", "time_limit_ms", "memory_limit_kib", "hidden", "samples"}
    assert body["statement"] == "tong hai so" and body["hidden"] is False
    assert body["samples"] == [{"input": "1 2\n", "output": "3\n"}, {"input": "10 20\n", "output": "30\n"}]
    for test in HIDDEN_TESTS:
        assert test.input not in r.body and test.output not in r.body


def test_hidden_and_unknown_problems_look_identical_to_a_user(web):
    cookie = web.login("alice")
    hidden = web.get("/api/problems/secret", cookie=cookie)
    unknown = web.get("/api/problems/does-not-exist", cookie=cookie)
    assert hidden.status == unknown.status == 404
    assert hidden.body == unknown.body and hidden.error_code == "PROBLEM_NOT_FOUND"
    assert {k: v for k, v in hidden.headers.items() if k not in ("date", "content-length")} == \
           {k: v for k, v in unknown.headers.items() if k not in ("date", "content-length")}


def test_admin_can_open_a_hidden_problem(web):
    r = web.get("/api/problems/secret", cookie=web.login("root"))
    assert r.status == 200 and r.json()["hidden"] is True


@pytest.mark.parametrize("pid", ["a" * 65, "has%20space", "a.b", "%00", "..", "%C3%B1", "sum%0A", "sum%0D%0A", "SUM", "+1"])
def test_malformed_or_unknown_ids_are_404_not_400(web, pid):
    r = web.get("/api/problems/" + pid, cookie=web.login("alice"))
    assert r.status == 404 and r.error_code == "PROBLEM_NOT_FOUND"


def test_a_problem_id_of_64_characters_works(make_web):
    web = make_web()
    populate(web.db)
    ensure_problem(web.db, "p" * 64)
    web.start()
    assert web.get("/api/problems/" + "p" * 64, cookie=web.login("alice")).status == 200


def test_samples_with_invalid_utf8_do_not_break_the_response(make_web):
    from server import problems

    web = make_web()
    populate(web.db)
    ensure_problem(web.db, "bin", tests=[problems.TestCase(True, b"\xff\xfe\n", b"ok\n")])
    web.start()
    r = web.get("/api/problems/bin", cookie=web.login("alice"))
    assert r.status == 200 and r.json()["samples"][0]["output"] == "ok\n"


def test_hiding_a_problem_takes_effect_immediately(web):
    cookie = web.login("alice")
    assert web.get("/api/problems/sum", cookie=cookie).status == 200
    with web.db.write() as c:
        c.execute("UPDATE problems SET hidden = 1 WHERE id = 'sum'")
    assert web.get("/api/problems/sum", cookie=cookie).status == 404
    assert web.get("/api/problems", cookie=cookie).json() == {"problems": []}


def test_an_encoded_slash_makes_the_path_not_match_any_route(web):
    r = web.get("/api/problems/a%2Fb", cookie=web.login("alice"))
    assert r.status == 404 and r.error_code == "NOT_FOUND"
