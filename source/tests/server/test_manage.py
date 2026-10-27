"""manage.py: users create, problems reimport, submissions show (D18; `submissions add` đã bỏ ở PR 5, có REST)."""

import io
import os
import subprocess
import sys

import pytest

from _support import add_problem, add_user
from server import auth, manage
from server.db import Database
from test_problems import make_dir


@pytest.fixture
def env(tmp_path):
    problems_dir = tmp_path / "problems"
    problems_dir.mkdir()
    return {"JH_DB_PATH": str(tmp_path / "manage.db"), "JH_PROBLEMS_DIR": str(problems_dir)}


def db_of(env):
    return Database(env["JH_DB_PATH"])


def call(env, *argv, stdin=""):
    return manage.main(list(argv), env=env, stdin=io.StringIO(stdin))


def test_users_create_stores_a_verifiable_hash_and_the_role(env, capsys):
    assert call(env, "users", "create", "alice", "--password-stdin", stdin="s3cret-pass\n") == 0
    row = db_of(env).conn().execute("SELECT * FROM users WHERE username = 'alice'").fetchone()
    assert row["role"] == "user" and "s3cret-pass" not in row["password_hash"]
    assert auth.verify_password("s3cret-pass", row["password_hash"])
    assert "s3cret-pass" not in capsys.readouterr().out


def test_users_create_admin(env):
    assert call(env, "users", "create", "root", "--role", "admin", "--password-stdin", stdin="s3cret-pass\n") == 0
    assert db_of(env).conn().execute("SELECT role FROM users WHERE username = 'root'").fetchone()[0] == "admin"


def test_users_create_rejects_duplicates(env, capsys):
    call(env, "users", "create", "alice", "--password-stdin", stdin="s3cret-pass\n")
    assert call(env, "users", "create", "alice", "--password-stdin", stdin="another-pass\n") == 1
    assert "đã tồn tại" in capsys.readouterr().err
    assert db_of(env).conn().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


@pytest.mark.parametrize("password", ["short", "", "x" * 200])
def test_users_create_rejects_bad_passwords(env, password):
    assert call(env, "users", "create", "alice", "--password-stdin", stdin=password + "\n") == 1
    assert db_of(env).conn().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


@pytest.mark.parametrize("name", ["", "has space", "a" * 33, "bad/slash"])
def test_users_create_rejects_bad_usernames(env, name):
    assert call(env, "users", "create", name, "--password-stdin", stdin="s3cret-pass\n") == 1


def test_password_is_never_an_argument(env):
    with pytest.raises(SystemExit):  # không có tùy chọn --password: mật khẩu không vào lịch sử shell hay danh sách process
        call(env, "users", "create", "alice", "--password", "s3cret-pass")


def test_problems_reimport(env, capsys):
    make_dir(env["JH_PROBLEMS_DIR"], "p1")
    assert call(env, "problems", "reimport", "p1") == 0
    assert "2 test" in capsys.readouterr().out
    assert db_of(env).conn().execute("SELECT COUNT(*) FROM tests WHERE problem_id = 'p1'").fetchone()[0] == 2


def test_problems_reimport_unknown_problem(env, capsys):
    assert call(env, "problems", "reimport", "nope") == 1
    assert "không có thư mục đề" in capsys.readouterr().err


@pytest.fixture
def ready(env, tmp_path):
    database = db_of(env)
    database.migrate()
    add_user(database)
    add_problem(database)
    code = tmp_path / "sol.py"
    code.write_text("print(3)\n", encoding="utf-8")
    return env, str(code)


def test_submissions_add_no_longer_exists(ready, capsys):
    env, code = ready
    with pytest.raises(SystemExit):
        call(env, "submissions", "add", "--user", "alice", "--problem", "sum", "--language", "python", "--file", code)
    assert db_of(env).conn().execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 0


def test_submissions_show_prints_state_and_verdict(ready, capsys):
    env, _ = ready
    sid = db_of(env).create_submission(1, "sum", "python", "print(3)\n")
    with db_of(env).write() as c:
        c.execute("UPDATE submissions SET state = 'done', verdict = 'CE', ce_message = 'a\nb' WHERE id = ?", (sid,))
    assert call(env, "submissions", "show", str(sid)) == 0
    out = capsys.readouterr().out.splitlines()
    assert "state=done" in out and "verdict=CE" in out and "ce_message=a\\nb" in out


def test_submissions_show_unknown_id(ready):
    env, _ = ready
    assert call(env, "submissions", "show", "999") == 1


def test_module_entry_point_works_as_python_dash_m(env, tmp_path):
    proc = subprocess.run([sys.executable, "-m", "server.manage", "users", "create", "bob", "--password-stdin"],
                          input="s3cret-pass\n", capture_output=True, text=True, timeout=30,
                          env={"PATH": os.environ["PATH"], "PYTHONPATH": os.getcwd(), **env})
    assert proc.returncode == 0, proc.stderr
    assert db_of(env).conn().execute("SELECT username FROM users").fetchone()[0] == "bob"
