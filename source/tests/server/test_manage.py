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


@pytest.mark.parametrize("password", ["s3cret-pass\x00", "s3cret\x00-pass", "\x00s3cret-pass"])
def test_users_create_rejects_passwords_with_nul(env, password, capsys):
    # scrypt của OpenSSL coi "pw" và "pw\x00" là một mật khẩu, nên NUL bị từ chối ở chỗ băm duy nhất (auth.py)
    assert call(env, "users", "create", "alice", "--password-stdin", stdin=password + "\n") == 1
    assert "NUL" in capsys.readouterr().err
    assert db_of(env).conn().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


@pytest.mark.parametrize("name", ["", "has space", "a" * 33, "bad/slash"])
def test_users_create_rejects_bad_usernames(env, name):
    assert call(env, "users", "create", name, "--password-stdin", stdin="s3cret-pass\n") == 1


def stored_hash(env, username):
    return db_of(env).conn().execute("SELECT password_hash FROM users WHERE username = ?", (username,)).fetchone()[0]


def test_users_create_drops_a_leading_bom_from_the_stdin_password(env):
    # Windows PowerShell 5.1 đặt U+FEFF ở đầu dữ liệu pipe; mật khẩu lưu ra không được chứa nó
    assert call(env, "users", "create", "alice", "--password-stdin", stdin="﻿s3cret-pass\r\n") == 0
    stored = stored_hash(env, "alice")
    assert auth.verify_password("s3cret-pass", stored)
    assert not auth.verify_password("﻿s3cret-pass", stored)


def test_users_create_keeps_a_bom_that_is_not_at_the_start(env):
    assert call(env, "users", "create", "alice", "--password-stdin", stdin="s3cret﻿-pass\n") == 0
    assert auth.verify_password("s3cret﻿-pass", stored_hash(env, "alice"))


@pytest.mark.parametrize("raw,password", [
    (b"\xef\xbb\xbfs3cret-pass\r\n", "s3cret-pass"),  # có BOM, kết thúc CRLF
    (b"s3cret-pass\n", "s3cret-pass"),  # không BOM
    ("mật-khẩu-9\n".encode("utf-8"), "mật-khẩu-9"),  # không ASCII, không BOM
    (b"\xef\xbb\xbf" + "mật-khẩu-9\r\n".encode("utf-8"), "mật-khẩu-9"),  # không ASCII, có BOM
])
def test_real_stdin_bytes_are_read_as_utf8_without_bom(env, raw, password):
    proc = subprocess.run([sys.executable, "-m", "server.manage", "users", "create", "carol", "--password-stdin"],
                          input=raw, capture_output=True, timeout=30,
                          env={"PATH": os.environ["PATH"], "PYTHONPATH": os.getcwd(), **env})
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    stored = stored_hash(env, "carol")
    assert auth.verify_password(password, stored)
    assert not auth.verify_password("﻿" + password, stored)


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


def test_users_create_rejects_a_username_with_a_trailing_newline(env):
    assert call(env, "users", "create", "alice\n", "--password-stdin", stdin="s3cret-pass\n") == 1
    assert db_of(env).conn().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


# ---- users set-password (D30) -------------------------------------------------------------------------------------

def make_user(env, name="alice", password="old-password"):
    assert call(env, "users", "create", name, "--password-stdin", stdin=password + "\n") == 0


def add_sessions(env, name, n):
    from server.rest import sessions

    db = db_of(env)
    uid = db.conn().execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]
    for _ in range(n):
        sessions.create_session(db, uid, 1_800_000_000_000, 3600)


def sessions_of(env, name):
    return db_of(env).conn().execute(
        "SELECT COUNT(*) FROM sessions s JOIN users u ON u.id = s.user_id WHERE u.username = ?", (name,)).fetchone()[0]


def test_set_password_changes_the_hash_and_prints_no_password(env, capsys):
    make_user(env)
    capsys.readouterr()
    assert call(env, "users", "set-password", "alice", "--password-stdin", stdin="new-password-1\n") == 0
    stored = stored_hash(env, "alice")
    assert auth.verify_password("new-password-1", stored) and not auth.verify_password("old-password", stored)
    out = capsys.readouterr()
    assert "new-password-1" not in out.out + out.err and "old-password" not in out.out + out.err


def test_set_password_deletes_all_sessions_of_that_user_and_only_theirs(env, capsys):
    make_user(env, "alice")
    make_user(env, "bob")
    add_sessions(env, "alice", 2)
    add_sessions(env, "bob", 1)
    capsys.readouterr()
    assert call(env, "users", "set-password", "alice", "--password-stdin", stdin="new-password-1\n") == 0
    assert sessions_of(env, "alice") == 0 and sessions_of(env, "bob") == 1
    assert "2 phiên" in capsys.readouterr().out


def test_set_password_leaves_role_and_disabled_alone(env):
    assert call(env, "users", "create", "root", "--role", "admin", "--password-stdin", stdin="old-password\n") == 0
    with db_of(env).write() as c:
        c.execute("UPDATE users SET disabled = 1 WHERE username = 'root'")
    assert call(env, "users", "set-password", "root", "--password-stdin", stdin="new-password-1\n") == 0
    row = db_of(env).conn().execute("SELECT role, disabled FROM users WHERE username = 'root'").fetchone()
    assert tuple(row) == ("admin", 1)


def test_set_password_for_an_unknown_user_fails_before_reading_any_password(env, capsys):
    class Boom(io.StringIO):
        def readline(self, *a):
            raise AssertionError("không được đọc mật khẩu khi user không tồn tại")

    assert manage.main(["users", "set-password", "ghost", "--password-stdin"], env=env, stdin=Boom()) == 1
    assert "không có user" in capsys.readouterr().err


@pytest.mark.parametrize("password", ["short", "", "x" * 200, "s3cret-pass\x00"])
def test_set_password_rejects_bad_passwords_and_changes_nothing(env, password):
    make_user(env)
    add_sessions(env, "alice", 1)
    before = stored_hash(env, "alice")
    assert call(env, "users", "set-password", "alice", "--password-stdin", stdin=password + "\n") == 1
    assert stored_hash(env, "alice") == before and sessions_of(env, "alice") == 1  # hỏng thì không hủy phiên


def test_set_password_drops_a_leading_bom(env):
    make_user(env)
    assert call(env, "users", "set-password", "alice", "--password-stdin", stdin="\ufeffnew-password-1\r\n") == 0
    assert auth.verify_password("new-password-1", stored_hash(env, "alice"))


def test_set_password_never_takes_the_password_as_an_argument(env):
    make_user(env)
    with pytest.raises(SystemExit):
        call(env, "users", "set-password", "alice", "--password", "new-password-1")
    with pytest.raises(SystemExit):
        call(env, "users", "set-password", "alice", "new-password-1")


def test_set_password_works_as_python_dash_m(env):
    make_user(env)
    proc = subprocess.run([sys.executable, "-m", "server.manage", "users", "set-password", "alice", "--password-stdin"],
                          input=b"new-password-1\n", capture_output=True, timeout=60,
                          env={"PATH": os.environ["PATH"], "PYTHONPATH": os.getcwd(), **env})
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert auth.verify_password("new-password-1", stored_hash(env, "alice"))
