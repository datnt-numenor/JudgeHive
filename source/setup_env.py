"""Tạo shared secret một lần; chạy từ source/. Không ghi đè file đã có."""
from pathlib import Path
import secrets
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

path = Path(__file__).with_name(".env")
try:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("JH_WORKER_SECRET=" + secrets.token_hex(32) + "\n")
    print("Đã tạo source/.env (không commit file này)")
except FileExistsError:
    print(".env đã tồn tại; giữ nguyên shared secret")
