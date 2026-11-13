"""Chạy: python -m server.manage admin --role admin (mật khẩu nhập kín)."""
import argparse
import getpass
import os
import re
import sys
from server.auth import hash_password
from server.db import Database


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("--role", choices=["user", "admin"], default="admin")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", args.username):
        parser.error("Tên tài khoản chỉ gồm chữ, số, _, ., -; tối đa 32 ký tự")
    password = getpass.getpass("Mật khẩu (8–128 byte): ")
    if password != getpass.getpass("Nhập lại: "):
        parser.error("Mật khẩu không khớp")
    try:
        hashed = hash_password(password)
    except ValueError as exc:
        parser.error(str(exc))
    db = Database(os.environ.get("JH_DB", "/data/judgehive.sqlite3"))
    try:
        if db.one("SELECT id FROM users WHERE username=?", (args.username,)):
            parser.error("Tài khoản đã tồn tại; đặt lại mật khẩu qua trang admin")
        db.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)", (args.username, hashed, args.role))
        print("Đã tạo tài khoản", args.username)
    finally:
        db.conn.close()


if __name__ == "__main__":
    main()
