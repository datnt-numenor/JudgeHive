"""Đo truy vấn bảng xếp hạng có và không có chỉ mục phủ `idx_submissions_board` (migration 3). Chạy tay, KHÔNG nằm trong bộ
test thường (tên file không bắt đầu bằng `test_`, nên pytest không thu thập).

Chạy từ thư mục source/, chỉ cần thư viện chuẩn:

    python tests/perf/measure_scoreboard.py --rows 5000 --code-kib 64
    python tests/perf/measure_scoreboard.py --rows 50000 --code-kib 4

Cảnh báo dung lượng: 5000 x 64 KiB khoảng 330 MB, 50000 x 4 KiB khoảng 210 MB trong thư mục tạm; 50000 x 64 KiB (3,2 GB)
là quá lớn, không dùng. Kết quả phụ thuộc đĩa và hệ điều hành: ghi lại nơi chạy cùng số đo (README server hoặc STATE).
Script tự xóa thư mục tạm khi xong."""

import argparse
import os
import platform
import random
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from server.db import Database  # noqa: E402
from server.scoreboard.loader import BOARD_QUERY, load_entries  # noqa: E402
from server.scoreboard.ranking import compute_rows  # noqa: E402

VERDICTS = ["AC"] * 40 + ["WA"] * 30 + ["TLE"] * 8 + ["RE"] * 6 + ["MLE"] * 3 + ["CE"] * 8 + ["SE"] * 5


def populate(db, rows, code_kib, users, problems, batch=500):
    rnd = random.Random(2026)
    code = "x" * (code_kib * 1024)
    with db.write() as c:
        for u in range(users):
            c.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?, 'x', 'user', 0)",
                      (f"user{u:05d}",))
        c.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES ('root', 'x', 'admin', 0)")
        for p in range(problems):
            c.execute("INSERT INTO problems (id, title, statement, time_limit_ms, memory_limit_kib, hidden, tests_hash, "
                      "created_at, updated_at) VALUES (?, 't', 's', 1000, 1000, ?, 'h', 0, 0)",
                      (f"p{p:02d}", 1 if p == problems - 1 else 0))
    done = 0
    while done < rows:
        n = min(batch, rows - done)
        with db.write() as c:
            c.executemany(
                "INSERT INTO submissions (user_id, problem_id, language, code, submitted_at, state, verdict) "
                "VALUES (?, ?, 'python', ?, ?, ?, ?)",
                [(rnd.randint(1, users), f"p{rnd.randrange(problems):02d}", code, 1_700_000_000_000 + done + i,
                  *(("done", rnd.choice(VERDICTS)) if rnd.random() < 0.85 else ("queued", None))) for i in range(n)])
        done += n


def time_query(path, repeat):
    """Mỗi lần mở kết nối mới (không giữ cache trang của SQLite); cache của hệ điều hành thì vẫn ấm."""
    samples, split = [], []
    for _ in range(repeat):
        db = Database(path)
        start = time.perf_counter()
        entries = load_entries(db.conn())
        mid = time.perf_counter()
        rows = compute_rows(entries)
        end = time.perf_counter()
        samples.append((end - start) * 1000)
        split.append(((mid - start) * 1000, (end - mid) * 1000))
        db.close_thread()
    return samples, len(rows), split


def plan(path):
    conn = sqlite3.connect(path)
    try:
        return " | ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + BOARD_QUERY))
    finally:
        conn.close()


def insert_cost(path, code_kib, n=300):
    db = Database(path)
    code = "x" * (code_kib * 1024)
    start = time.perf_counter()
    for i in range(n):
        with db.write() as c:
            c.execute("INSERT INTO submissions (user_id, problem_id, language, code, submitted_at, state, verdict) "
                      "VALUES (1, 'p00', 'python', ?, ?, 'done', 'WA')", (code, 1_800_000_000_000 + i))
    elapsed = (time.perf_counter() - start) * 1000 / n
    db.close_thread()
    return elapsed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=5000)
    ap.add_argument("--code-kib", type=int, default=64)
    ap.add_argument("--users", type=int, default=300)
    ap.add_argument("--problems", type=int, default=10)
    ap.add_argument("--repeat", type=int, default=7)
    ap.add_argument("--dir", default=None, help="thư mục chứa file tạm (mặc định: thư mục tạm của hệ điều hành)")
    args = ap.parse_args()
    if args.rows * args.code_kib > 400_000:
        sys.exit("quá lớn (hơn khoảng 400 MB); dùng 5000 x 64 hoặc 50000 x 4")

    workdir = tempfile.mkdtemp(prefix="jh-board-", dir=args.dir)
    path = os.path.join(workdir, "board.db")
    try:
        print(f"máy: {platform.platform()} | Python {platform.python_version()} | SQLite {sqlite3.sqlite_version}")
        print(f"dữ liệu: {args.rows} bài x {args.code_kib} KiB code, {args.users} người, {args.problems} đề (1 đề ẩn)")
        db = Database(path)
        db.migrate()
        start = time.perf_counter()
        populate(db, args.rows, args.code_kib, args.users, args.problems)
        db.conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.close_thread()
        print(f"nạp dữ liệu: {time.perf_counter() - start:.1f} s, file {os.path.getsize(path) / 2**20:.0f} MiB")

        with_index, n_rows, split_with = time_query(path, args.repeat)
        print(f"[có chỉ mục]    plan: {plan(path)}")
        cost_with = insert_cost(path, args.code_kib)

        conn = sqlite3.connect(path)
        conn.execute("DROP INDEX idx_submissions_board")
        conn.commit()
        conn.close()
        without_index, _, split_without = time_query(path, args.repeat)
        print(f"[không chỉ mục] plan: {plan(path)}")
        cost_without = insert_cost(path, args.code_kib)

        def line(label, s):
            return f"{label:<15} lần đầu {s[0]:8.1f} ms | trung vị {statistics.median(s):8.1f} ms | min {min(s):8.1f} ms"

        print(f"\nkết quả truy vấn bảng ({n_rows} hàng xếp hạng), {args.repeat} lần mỗi bên:")
        print(line("có chỉ mục", with_index))
        print(line("không chỉ mục", without_index))
        ratio = statistics.median(without_index) / max(statistics.median(with_index), 1e-9)
        print(f"không chỉ mục / có chỉ mục (trung vị): {ratio:.1f} lần")
        for label, sp in (("có chỉ mục", split_with), ("không chỉ mục", split_without)):
            print(f"  tách {label}: đọc SQL trung vị {statistics.median(a for a, _ in sp):.1f} ms, "
                  f"xếp hạng bằng Python trung vị {statistics.median(b for _, b in sp):.1f} ms")
        print(f"chi phí ghi một bài (trung bình 300 lần INSERT): có chỉ mục {cost_with:.2f} ms, "
              f"không chỉ mục {cost_without:.2f} ms")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
