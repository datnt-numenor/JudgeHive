"""db.py: cấu hình SQLite theo PLAN mục 2.1, migration, schema, và các điều PLAN ghi là "chưa kiểm chứng"."""

import sqlite3
import threading
import time

import pytest

from _support import add_problem, add_user, insert_submission
from server import db as db_module
from server.db import BUSY_TIMEOUT_MS, MIGRATIONS, Database


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "t.db"))
    database.migrate()
    return database


def test_pragmas_follow_the_plan(db):
    c = db.conn()
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert c.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c.isolation_level is None  # tự quản lý transaction


def test_each_thread_gets_its_own_connection(db):
    mine = db.conn()
    assert db.conn() is mine
    other = []
    t = threading.Thread(target=lambda: other.append(db.conn()))
    t.start()
    t.join()
    assert other[0] is not mine


def test_close_thread_drops_the_connection(db):
    first = db.conn()
    db.close_thread()
    assert db.conn() is not first


def test_migrate_is_idempotent_and_sets_user_version(db):
    db.migrate()
    db.migrate()
    assert db.conn().execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    tables = {r[0] for r in db.conn().execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"users", "problems", "tests", "submissions"} <= tables


def test_concurrent_migrations_do_not_clash(tmp_path):
    path = str(tmp_path / "race.db")
    errors = []

    def run():
        try:
            Database(path).migrate()
        except Exception as e:  # noqa: BLE001 - test thu mọi lỗi
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert Database(path).conn().execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_write_commits_and_rolls_back_on_exception(db):
    with db.write() as c:
        c.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES ('a', 'x', 'user', 0)")
    with pytest.raises(RuntimeError):
        with db.write() as c:
            c.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES ('b', 'x', 'user', 0)")
            raise RuntimeError("giữa chừng")
    names = [r[0] for r in db.conn().execute("SELECT username FROM users")]
    assert names == ["a"]
    assert not db.conn().in_transaction  # rollback xong, kết nối dùng lại được


def test_begin_immediate_waits_for_the_other_writer_instead_of_failing(db):
    holding, release = threading.Event(), threading.Event()

    def hold():
        with db.write():
            holding.set()
            release.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    assert holding.wait(5)
    threading.Timer(0.5, release.set).start()
    started = time.monotonic()
    with db.write() as c:  # phải chờ writer kia, không báo "database is locked" ngay
        c.execute("SELECT 1")
    waited = time.monotonic() - started
    t.join()
    assert 0.3 < waited < BUSY_TIMEOUT_MS / 1000


def test_deferred_read_then_write_fails_fast_in_wal_which_is_why_the_plan_uses_begin_immediate(db):
    """PLAN mục 2.1 ghi điều này là "chưa chạy thử": transaction đọc rồi nâng lên ghi sau khi người khác đã commit báo
    busy ngay, không chờ busy_timeout."""
    add_user(db)
    reader = sqlite3.connect(db.path, isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM users").fetchone()
    add_user(db, "bob")  # người khác commit sau khi reader đã đọc
    started = time.monotonic()
    with pytest.raises(sqlite3.OperationalError):
        reader.execute("UPDATE users SET role = 'admin'")
    assert time.monotonic() - started < BUSY_TIMEOUT_MS / 1000 / 2
    reader.close()


def test_foreign_keys_are_enforced(db):
    with pytest.raises(sqlite3.IntegrityError):
        insert_submission(db, user_id=999)


def test_client_token_is_unique_per_user_but_null_is_free(db):
    user, other = add_user(db), add_user(db, "bob")
    add_problem(db)
    insert_submission(db, user, client_token="t1")
    insert_submission(db, other, client_token="t1")
    insert_submission(db, user)
    insert_submission(db, user)
    with pytest.raises(sqlite3.IntegrityError):
        insert_submission(db, user, client_token="t1")


@pytest.mark.parametrize("column,value", [("state", "weird"), ("verdict", "XX"), ("language", "ruby"),
                                          ("rejudge_requested", 2)])
def test_check_constraints(db, column, value):
    user = add_user(db)
    add_problem(db)
    with pytest.raises(sqlite3.IntegrityError):
        insert_submission(db, user, **{column: value})


def test_deleting_a_problem_removes_its_tests(db):
    add_problem(db)
    with db.write() as c:
        c.execute("DELETE FROM problems WHERE id = 'sum'")
    assert db.conn().execute("SELECT COUNT(*) FROM tests").fetchone()[0] == 0


def test_module_exposes_migrations_as_a_list_of_scripts():
    assert isinstance(db_module.MIGRATIONS, list) and all(isinstance(m, str) for m in db_module.MIGRATIONS)
