"""Migration 2 (phiên + index phân trang) trên một DB cũ đã có dữ liệu, và REST chạy được trên DB đó."""

import sqlite3

from _rest_support import PASSWORD, Web, password_hash, uuid_token
from server import auth
from server import db as db_module
from server.db import Database


def build_v1_database(path):
    """DB đúng như PR 4 để lại: chỉ có migration 1, kèm một user, một đề và hai bài nộp."""
    conn = sqlite3.connect(path, isolation_level=None)
    for statement in db_module._split_statements(db_module.MIGRATIONS[0]):
        conn.execute(statement)
    conn.execute("PRAGMA user_version=1")
    conn.execute("INSERT INTO users (id, username, password_hash, role, created_at) VALUES (1, 'alice', ?, 'user', 5)",
                 (password_hash(),))
    conn.execute("INSERT INTO problems (id, title, statement, time_limit_ms, memory_limit_kib, hidden, tests_hash, "
                 "created_at, updated_at) VALUES ('sum', 'Sum', 's', 1000, 262144, 0, 'h', 1, 1)")
    conn.execute("INSERT INTO submissions (user_id, problem_id, language, code, client_token, submitted_at, state) "
                 "VALUES (1, 'sum', 'python', 'old-1', NULL, 1000, 'queued')")
    conn.execute("INSERT INTO submissions (user_id, problem_id, language, code, client_token, submitted_at, state) "
                 "VALUES (1, 'sum', 'java', 'old-2', ?, 2000, 'done')", (uuid_token(5),))
    conn.close()


def test_migration_2_keeps_old_data_and_adds_sessions(tmp_path):
    path = str(tmp_path / "old.db")
    build_v1_database(path)
    check = sqlite3.connect(path)
    assert check.execute("PRAGMA user_version").fetchone()[0] == 1
    check.close()

    db = Database(path)
    db.migrate()
    conn = db.conn()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db_module.MIGRATIONS)
    assert [tuple(r) for r in conn.execute("SELECT id, language, code, client_token FROM submissions ORDER BY id")] == \
           [(1, "python", "old-1", None), (2, "java", "old-2", uuid_token(5))]
    assert conn.execute("SELECT username FROM users").fetchone()[0] == "alice"
    assert [r[1] for r in conn.execute("PRAGMA table_info(sessions)")] == \
           ["token_hash", "user_id", "created_at", "expires_at"]
    assert "idx_submissions_user_id" in {r[1] for r in conn.execute("PRAGMA index_list(submissions)")}
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migrating_twice_is_a_noop(tmp_path):
    db = Database(str(tmp_path / "twice.db"))
    db.migrate()
    db.migrate()
    assert db.conn().execute("PRAGMA user_version").fetchone()[0] == len(db_module.MIGRATIONS)


def test_old_password_hashes_still_verify_after_migration(tmp_path):
    build_v1_database(str(tmp_path / "v.db"))
    db = Database(str(tmp_path / "v.db"))
    db.migrate()
    stored = db.conn().execute("SELECT password_hash FROM users").fetchone()[0]
    assert auth.verify_password(PASSWORD, stored)


def test_rest_works_on_a_database_migrated_from_the_old_schema(tmp_path):
    build_v1_database(str(tmp_path / "test.db"))  # cùng đường dẫn mà make_config dùng
    web = Web(tmp_path)
    try:
        web.start()  # server khởi động chạy migration 2
        cookie = web.login("alice")
        items = web.get("/api/submissions", cookie=cookie).json()["submissions"]
        assert [(s["submission_id"], s["state"]) for s in items] == [(2, "judged"), (1, "queued")]
        assert web.submit(cookie, uuid_token(1)).status == 201
        assert web.submit(cookie, uuid_token(5)).status == 200  # token của bài cũ vẫn chống trùng
        assert web.get("/api/submissions/1", cookie=cookie).json()["code"] == "old-1"
    finally:
        web.stop()
