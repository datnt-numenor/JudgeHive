"""Danh sách đề: trạng thái của chính người xem và tỉ lệ AC của đề."""
from server.db import now
from server.problems import insert_problem, parse_problem
from conftest import login, system


def add_submission(app, username, problem_id, state, verdict=None):
    db = app.state.db
    uid = db.one("SELECT id FROM users WHERE username=?", (username,))["id"]
    db.execute("INSERT INTO submissions(user_id,problem_id,language,code,submitted_at,state,verdict) "
               "VALUES(?,?,'python','pass',?,?,?)", (uid, problem_id, now(), state, verdict))


def problem_list(client, username):
    login(client, username)
    return {p["id"]: p for p in client.get("/api/problems").json()}


def test_status_is_per_user_and_ac_rate_counts_finished_submissions(tmp_path):
    with system(tmp_path) as (client, app):
        def seed():
            hidden = insert_problem(app.state.db, parse_problem(
                '{"title": "Ẩn", "statement": "S", "hidden": true, "tests": [{"input": "1", "output": "1"}]}'))
            add_submission(app, "alice", 1, "done", "WA")
            add_submission(app, "alice", 1, "done", "AC")
            add_submission(app, "bob", 1, "done", "WA")
            add_submission(app, "bob", 1, "done", "TLE")
            add_submission(app, "bob", 1, "queued")  # chưa chấm xong: không tính vào tỉ lệ
            return hidden
        hidden = client.portal.call(seed)

        alice, bob, admin = (problem_list(client, name) for name in ("alice", "bob", "admin"))
        assert alice[1]["status"] == "solved"
        assert bob[1]["status"] == "attempted", "Bob chưa AC dù Alice đã AC"
        assert admin[1]["status"] is None, "Trạng thái không lộ bài của người khác"
        assert alice[1]["ac_rate"] == bob[1]["ac_rate"] == admin[1]["ac_rate"] == 1 / 4
        assert hidden not in alice and hidden in admin
        assert admin[hidden]["hidden"] == 1 and admin[hidden]["status"] is None and admin[hidden]["ac_rate"] is None


def test_problem_without_finished_submissions_has_no_rate(tmp_path):
    with system(tmp_path) as (client, app):
        client.portal.call(lambda: add_submission(app, "alice", 1, "judging"))
        row = problem_list(client, "alice")[1]
        assert row["status"] == "attempted" and row["ac_rate"] is None
        row = problem_list(client, "bob")[1]
        assert row["status"] is None and row["ac_rate"] is None
