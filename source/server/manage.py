"""Lệnh quản trị chạy trong container server.

  python -m server.manage user admin --role admin   # tạo tài khoản, mật khẩu nhập kín
  python -m server.manage problem problems/a.json   # nạp một đề từ file JSON
"""
import argparse
import getpass
import os
import re
import sys

from pydantic import ValidationError

from server.auth import hash_password
from server.db import Database
from server.problems import insert_problem, parse_problem


def open_db():
    return Database(os.environ.get("JH_DB", "/data/judgehive.sqlite3"))


def create_user(parser, args):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", args.username):
        parser.error("Tên tài khoản chỉ gồm chữ, số, _, ., -; tối đa 32 ký tự")
    password = getpass.getpass("Mật khẩu (8–128 byte): ")
    if password != getpass.getpass("Nhập lại: "):
        parser.error("Mật khẩu không khớp")
    try:
        hashed = hash_password(password)
    except ValueError as exc:
        parser.error(str(exc))
    db = open_db()
    try:
        if db.one("SELECT id FROM users WHERE username=?", (args.username,)):
            parser.error("Tài khoản đã tồn tại; đặt lại mật khẩu qua trang admin")
        db.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)", (args.username, hashed, args.role))
        print("Đã tạo tài khoản", args.username)
    finally:
        db.conn.close()


def load_problem(parser, args):
    try:
        with open(args.file, encoding="utf-8") as stream:
            problem = parse_problem(stream.read())
    except (OSError, UnicodeDecodeError) as exc:
        parser.error(f"Không đọc được file đề: {exc}")
    except ValidationError as exc:
        parser.error("File đề không hợp lệ:\n" + str(exc))
    except ValueError as exc:
        parser.error(str(exc))
    db = open_db()
    try:
        pid = insert_problem(db, problem)
        print(f"Đã nạp đề #{pid}: {problem.title} ({len(problem.tests)} test, "
              f"{'ẩn' if problem.hidden else 'hiện'})")
    finally:
        db.conn.close()


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):  # console Windows mặc định không phải UTF-8
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m server.manage")
    commands = parser.add_subparsers(dest="command", required=True)
    user = commands.add_parser("user", help="Tạo tài khoản")
    user.add_argument("username")
    user.add_argument("--role", choices=["user", "admin"], default="admin")
    problem = commands.add_parser("problem", help="Nạp đề từ file JSON")
    problem.add_argument("file")
    args = parser.parse_args(argv)
    (create_user if args.command == "user" else load_problem)(parser, args)


if __name__ == "__main__":
    main()
