"""Script quản lý, chạy trong container server (DB nằm trong named volume):

    docker compose exec server python -m server.manage users create NAME [--role admin] [--password-stdin]
    docker compose exec server python -m server.manage problems reimport ID
    docker compose exec server python -m server.manage submissions show ID

Nộp bài qua REST (`POST /api/submissions`); `submissions show` chỉ để xem một dòng khi gỡ lỗi. Mật khẩu không bao giờ
qua argv: nhắc nhập (getpass) hoặc `--password-stdin`."""

import argparse
import getpass
import os
import re
import sqlite3
import sys

from server import auth, problems
from server.config import db_path_from_env, problems_dir_from_env
from server.db import Database

USERNAME = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")  # [ĐỀ XUẤT]
SHOW_FIELDS = ("id", "user_id", "problem_id", "language", "state", "is_rejudge", "attempt_id", "delivery_count",
               "verdict", "time_ms", "memory_kib", "failed_test", "failed_is_sample", "re_kind", "rejudge_failed",
               "delivered_test_hash", "verdict_test_hash")


class CommandError(Exception):
    pass


def build_parser():
    parser = argparse.ArgumentParser(prog="manage")
    groups = parser.add_subparsers(dest="group", required=True)

    users = groups.add_parser("users").add_subparsers(dest="command", required=True)
    create = users.add_parser("create")
    create.add_argument("username")
    create.add_argument("--role", choices=("user", "admin"), default="user")
    create.add_argument("--password-stdin", action="store_true", help="đọc mật khẩu từ dòng đầu của stdin")

    probs = groups.add_parser("problems").add_subparsers(dest="command", required=True)
    probs.add_parser("reimport").add_argument("problem_id")

    subs = groups.add_parser("submissions").add_subparsers(dest="command", required=True)
    subs.add_parser("show").add_argument("submission_id", type=int)
    return parser


def cmd_users_create(db, args, stdin):
    if not USERNAME.match(args.username):
        raise CommandError("username chỉ gồm chữ, số, '_', '.', '-' và dài 1 đến 32 ký tự")
    if args.password_stdin:
        password = stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("Mật khẩu: ")
        if password != getpass.getpass("Nhập lại: "):
            raise CommandError("hai lần nhập mật khẩu không khớp")
    try:
        stored = auth.hash_password(password)
    except auth.PasswordError as e:
        raise CommandError(str(e)) from e
    try:
        db.create_user(args.username, stored, args.role)
    except sqlite3.IntegrityError as e:
        raise CommandError(f"username {args.username!r} đã tồn tại") from e
    print(f"đã tạo {args.role} {args.username}")


def cmd_problems_reimport(db, args, env):
    try:
        data = problems.reimport_problem(db, problems_dir_from_env(env), args.problem_id)
    except problems.ProblemError as e:
        raise CommandError(str(e)) from e
    print(f"đã nạp lại {data.id}: {len(data.tests)} test, hash {problems.compute_tests_hash(data.tests)}")


def cmd_submissions_show(db, args):
    row = db.conn().execute("SELECT * FROM submissions WHERE id = ?", (args.submission_id,)).fetchone()
    if row is None:
        raise CommandError(f"không có bài nộp {args.submission_id}")
    for key in SHOW_FIELDS:
        print(f"{key}={row[key]}")
    for key in ("ce_message", "sample_stdout"):
        if row[key]:
            print(f"{key}=" + row[key].replace("\n", "\\n")[:2000])


def main(argv=None, env=None, stdin=None):
    env = os.environ if env is None else env
    stdin = sys.stdin if stdin is None else stdin
    args = build_parser().parse_args(argv)
    db = Database(db_path_from_env(env))
    try:
        db.migrate()
        if args.group == "users":
            cmd_users_create(db, args, stdin)
        elif args.group == "problems":
            cmd_problems_reimport(db, args, env)
        else:
            cmd_submissions_show(db, args)
    except CommandError as e:
        print(f"lỗi: {e}", file=sys.stderr)
        return 1
    finally:
        db.close_thread()
    return 0


if __name__ == "__main__":
    sys.exit(main())
