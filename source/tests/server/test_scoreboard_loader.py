"""Truy vấn bảng xếp hạng trên SQLite thật và migration 3 (chỉ mục phủ). Ứng viên khóa: các test lọc dữ liệu (admin, đề ẩn,
chưa có verdict, chấm lại); không khóa: test kế hoạch truy vấn và tên chỉ mục."""

import sqlite3

import pytest

from server import db as db_module
from server.db import Database
from server.scoreboard.loader import BOARD_QUERY, compute_board, load_entries


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "board.db"))
    database.migrate()
    with database.write() as c:
        for name, role in (("alice", "user"), ("bob", "user"), ("root", "admin")):
            c.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?, 'x', ?, 0)", (name, role))
        for pid, hidden in (("sum", 0), ("secret", 1)):
            c.execute("INSERT INTO problems (id, title, statement, time_limit_ms, memory_limit_kib, hidden, tests_hash, "
                      "created_at, updated_at) VALUES (?, 't', 's', 1000, 1000, ?, 'h', 0, 0)", (pid, hidden))
    return database


def submit(db, user_id, problem, verdict, at, *, state="done", **columns):
    cols = {"verdict": verdict, "state": state, **columns}
    names = ["user_id", "problem_id", "language", "code", "submitted_at", *cols]
    values = [user_id, problem, "python", "print(1)", at, *cols.values()]
    with db.write() as c:
        return c.execute(f"INSERT INTO submissions ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                         values).lastrowid


def board(db):
    return [(r.rank, r.username, r.solved, r.wrong, r.last_ac_ms) for r in compute_board(db)]


def test_basic_board(db):
    submit(db, 1, "sum", "WA", 100)
    submit(db, 1, "sum", "AC", 200)
    submit(db, 2, "sum", "AC", 150)
    assert board(db) == [(1, "bob", 1, 0, 150), (2, "alice", 1, 1, 200)]


def test_admin_never_has_a_row(db):
    submit(db, 3, "sum", "AC", 100)
    assert board(db) == []
    submit(db, 1, "sum", "AC", 200)
    assert [r[1] for r in board(db)] == ["alice"]


def test_hidden_problems_are_not_counted_and_unhiding_brings_them_back(db):
    submit(db, 1, "sum", "AC", 100)
    submit(db, 1, "secret", "AC", 200)
    assert board(db) == [(1, "alice", 1, 0, 100)]
    with db.write() as c:
        c.execute("UPDATE problems SET hidden = 0 WHERE id = 'secret'")
    assert board(db) == [(1, "alice", 2, 0, 200)]
    with db.write() as c:
        c.execute("UPDATE problems SET hidden = 1 WHERE id = 'sum'")
    assert board(db) == [(1, "alice", 1, 0, 200)]


def test_a_person_whose_only_submissions_are_on_hidden_problems_has_no_row(db):
    submit(db, 2, "secret", "AC", 100)
    assert board(db) == []


def test_submissions_without_a_verdict_are_ignored(db):
    submit(db, 1, "sum", None, 100, state="queued")
    submit(db, 2, "sum", None, 110, state="judging")
    assert board(db) == []
    submit(db, 1, "sum", "AC", 120)
    assert board(db) == [(1, "alice", 1, 0, 120)]


def test_a_person_with_only_compile_errors_has_a_row_with_zero_solved(db):
    submit(db, 1, "sum", "CE", 100)
    assert board(db) == [(1, "alice", 0, 0, None)]


def test_a_rejudge_in_progress_keeps_counting_the_old_verdict(db):
    sid = submit(db, 1, "sum", "AC", 100)
    with db.write() as c:
        c.execute("UPDATE submissions SET state = 'queued', is_rejudge = 1 WHERE id = ?", (sid,))
    assert board(db) == [(1, "alice", 1, 0, 100)]


def test_a_rejudge_that_changes_the_verdict_changes_the_board(db):
    first = submit(db, 1, "sum", "WA", 100)
    second = submit(db, 1, "sum", "AC", 200)
    assert board(db) == [(1, "alice", 1, 1, 200)]
    with db.write() as c:
        c.execute("UPDATE submissions SET verdict = 'AC' WHERE id = ?", (first,))
        c.execute("UPDATE submissions SET verdict = 'WA' WHERE id = ?", (second,))
    assert board(db) == [(1, "alice", 1, 0, 100)]
    with db.write() as c:
        c.execute("UPDATE submissions SET verdict = 'WA' WHERE id = ?", (first,))
    assert board(db) == [(1, "alice", 0, 0, None)]


def test_received_time_is_the_submission_time_not_the_finish_time(db):
    submit(db, 1, "sum", "AC", 500, finished_at=9_000)
    submit(db, 2, "sum", "AC", 300, finished_at=99_000)
    assert [r[1] for r in board(db)] == ["bob", "alice"]


def test_load_entries_returns_only_the_needed_fields(db):
    submit(db, 1, "sum", "AC", 100)
    entries = load_entries(db.conn())
    assert len(entries) == 1 and entries[0]._fields == ("user_id", "username", "problem_id", "submission_id",
                                                        "received_ms", "verdict")


# ---- migration 3 -----------------------------------------------------------------------------------------------

def index_names(conn):
    return {r[1] for r in conn.execute("PRAGMA index_list(submissions)")}


def test_the_query_uses_the_covering_index(db):
    plan = " | ".join(r[3] for r in db.conn().execute("EXPLAIN QUERY PLAN " + BOARD_QUERY))
    assert "COVERING INDEX idx_submissions_board" in plan, plan


def test_migration_3_on_a_database_at_version_2_with_data(tmp_path):
    path = str(tmp_path / "v2.db")
    conn = sqlite3.connect(path, isolation_level=None)
    for script in db_module.MIGRATIONS[:2]:
        for statement in db_module._split_statements(script):
            conn.execute(statement)
    conn.execute("PRAGMA user_version=2")
    conn.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES ('alice', 'x', 'user', 0)")
    conn.execute("INSERT INTO problems (id, title, statement, time_limit_ms, memory_limit_kib, hidden, tests_hash, "
                 "created_at, updated_at) VALUES ('sum', 't', 's', 1000, 1000, 0, 'h', 0, 0)")
    conn.execute("INSERT INTO submissions (user_id, problem_id, language, code, submitted_at, state, verdict) "
                 "VALUES (1, 'sum', 'python', 'c', 5, 'done', 'AC')")
    assert "idx_submissions_board" not in index_names(conn)
    conn.close()

    database = Database(path)
    database.migrate()
    assert database.conn().execute("PRAGMA user_version").fetchone()[0] == len(db_module.MIGRATIONS) == 3
    assert "idx_submissions_board" in index_names(database.conn())
    assert [(r.username, r.solved) for r in compute_board(database)] == [("alice", 1)]
    database.migrate()  # chạy lại vô hại
    assert database.conn().execute("PRAGMA user_version").fetchone()[0] == 3


def test_the_partial_index_only_holds_rows_with_a_verdict(db):
    submit(db, 1, "sum", None, 100, state="queued")
    submit(db, 1, "sum", "AC", 200)
    count = db.conn().execute("SELECT COUNT(*) FROM submissions INDEXED BY idx_submissions_board "
                              "WHERE verdict IS NOT NULL").fetchone()[0]
    assert count == 1
