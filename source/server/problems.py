"""Đề bài: đọc thư mục seed, hash bộ test, kiểm giới hạn D3, nạp vào DB (D5) và dựng message `JOB`.

Thư mục một đề (`server/problems/<id>/`, [ĐỀ XUẤT], D18):
    problem.json   {"title": str, "time_limit_ms": int, "memory_limit_kib": int}
    statement.txt  đề bài, chữ thường
    samples/NN.in, NN.out   test mẫu (chạy trước, công khai)
    tests/NN.in,   NN.out   test ẩn
Thứ tự test = sắp tên file tăng dần; test mẫu trước test ẩn."""

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass

from common import protocol

log = logging.getLogger("judgehive.problems")

SLUG = re.compile(r"^[a-z0-9_-]{1,64}$")  # [ĐỀ XUẤT]
MAX_INT = 2**31 - 1


class ProblemError(Exception):
    """Thư mục đề hoặc bộ test không hợp lệ."""


@dataclass(frozen=True)
class TestCase:
    __test__ = False  # không phải test của pytest

    is_sample: bool
    input: bytes
    output: bytes


@dataclass(frozen=True)
class ProblemData:
    id: str
    title: str
    statement: str
    time_limit_ms: int
    memory_limit_kib: int
    tests: tuple


def compute_tests_hash(tests):
    """sha256 hex của dãy test theo thứ tự, mỗi phần có cờ mẫu/ẩn và tiền tố độ dài (không mơ hồ khi nối)."""
    h = hashlib.sha256()
    for t in tests:
        h.update(b"S" if t.is_sample else b"H")
        for blob in (t.input, t.output):
            h.update(len(blob).to_bytes(8, "big"))
            h.update(blob)
    return h.hexdigest()


def make_job_message(submission_id, attempt_id, language, code, time_limit_ms, memory_limit_kib, tests):
    """`tests`: dãy (is_sample, input, output) với input/output là bytes UTF-8, đã sắp mẫu trước."""
    return {
        "type": "JOB", "submission_id": submission_id, "attempt_id": attempt_id, "language": language,
        "code": code, "time_limit_ms": time_limit_ms, "memory_limit_kib": memory_limit_kib,
        "tests": [{"input": bytes(i).decode("utf-8"), "output": bytes(o).decode("utf-8"), "is_sample": bool(s)}
                  for s, i, o in tests],
    }


def check_limits(tests):
    """D3: tổng test tối đa 8 MB (kích thước thô) và dòng `JOB` worst-case (code 64 KB toàn ký tự điều khiển, mỗi ký
    tự thành 6 byte sau escape) không vượt 16 MB."""
    total = sum(len(t.input) + len(t.output) for t in tests)
    if total > protocol.MAX_TESTS_TOTAL_BYTES:
        raise ProblemError(f"tổng test {total} byte vượt {protocol.MAX_TESTS_TOTAL_BYTES}")
    worst = make_job_message(MAX_INT, MAX_INT, "python", "\x01" * protocol.MAX_CODE_BYTES, MAX_INT, MAX_INT,
                             [(t.is_sample, t.input, t.output) for t in tests])
    try:
        protocol.encode(worst, max_line_bytes=protocol.MAX_LINE_SERVER_TO_WORKER)
    except protocol.EncodeError as e:
        raise ProblemError(f"dòng JOB trường hợp xấu nhất vượt giới hạn: {e}") from e


def load_problem_dir(path):
    problem_id = os.path.basename(os.path.normpath(path))
    if not SLUG.match(problem_id):
        raise ProblemError(f"tên thư mục đề không hợp lệ: {problem_id!r}")
    try:
        with open(os.path.join(path, "problem.json"), encoding="utf-8") as f:
            meta = json.load(f)
        with open(os.path.join(path, "statement.txt"), encoding="utf-8") as f:
            statement = f.read()
    except (OSError, ValueError) as e:
        raise ProblemError(f"{problem_id}: không đọc được problem.json hoặc statement.txt: {e}") from e
    if not isinstance(meta, dict) or not isinstance(meta.get("title"), str) or not meta["title"].strip():
        raise ProblemError(f"{problem_id}: problem.json thiếu title")
    limits = {}
    for key in ("time_limit_ms", "memory_limit_kib"):
        value = meta.get(key)
        if type(value) is not int or not 0 < value <= MAX_INT:
            raise ProblemError(f"{problem_id}: {key} phải là số nguyên dương")
        limits[key] = value
    tests = [TestCase(True, i, o) for i, o in _read_pairs(os.path.join(path, "samples"))]
    tests += [TestCase(False, i, o) for i, o in _read_pairs(os.path.join(path, "tests"))]
    if not tests:
        raise ProblemError(f"{problem_id}: không có test nào")
    check_limits(tests)
    return ProblemData(problem_id, meta["title"], statement, limits["time_limit_ms"], limits["memory_limit_kib"],
                       tuple(tests))


def _read_pairs(directory):
    if not os.path.isdir(directory):
        return []
    names = os.listdir(directory)
    stems = sorted({n[:-3] for n in names if n.endswith(".in")} | {n[:-4] for n in names if n.endswith(".out")})
    pairs = []
    for stem in stems:
        blobs = []
        for suffix in (".in", ".out"):
            try:
                with open(os.path.join(directory, stem + suffix), "rb") as f:
                    data = f.read()
                data.decode("utf-8")
            except (OSError, UnicodeDecodeError) as e:
                raise ProblemError(f"{directory}: thiếu hoặc không phải UTF-8: {stem}{suffix} ({type(e).__name__})") from e
            blobs.append(data)
        pairs.append(tuple(blobs))
    return pairs


def _now_ms():
    return int(time.time() * 1000)


def store_problem(conn, data, *, replace):
    """Ghi đề và bộ test (gọi trong `db.write()`). `replace=False`: chỉ thêm đề chưa có, trả False nếu đã có.
    `replace=True`: ghi đè nội dung đề và test, giữ nguyên cờ ẩn. Trả True nếu đã ghi."""
    exists = conn.execute("SELECT 1 FROM problems WHERE id = ?", (data.id,)).fetchone() is not None
    if exists and not replace:
        return False
    now = _now_ms()
    digest = compute_tests_hash(data.tests)
    if exists:
        conn.execute("UPDATE problems SET title=?, statement=?, time_limit_ms=?, memory_limit_kib=?, tests_hash=?, "
                     "updated_at=? WHERE id=?",
                     (data.title, data.statement, data.time_limit_ms, data.memory_limit_kib, digest, now, data.id))
        conn.execute("DELETE FROM tests WHERE problem_id = ?", (data.id,))
    else:
        conn.execute("INSERT INTO problems (id, title, statement, time_limit_ms, memory_limit_kib, hidden, "
                     "tests_hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)",
                     (data.id, data.title, data.statement, data.time_limit_ms, data.memory_limit_kib, digest, now, now))
    conn.executemany("INSERT INTO tests (problem_id, position, is_sample, input, output) VALUES (?, ?, ?, ?, ?)",
                     [(data.id, pos, int(t.is_sample), t.input, t.output) for pos, t in enumerate(data.tests, 1)])
    return True


def seed_problems(db, directory):
    """D5: nạp các đề trong `directory` mà DB chưa có; không bao giờ ghi đè. Đề hỏng bị bỏ qua và ghi log (không chặn
    server khởi động). Trả danh sách id đã nạp."""
    if not os.path.isdir(directory):
        log.warning("không có thư mục đề seed: %s", directory)
        return []
    inserted = []
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if not os.path.isdir(path) or name.startswith((".", "_")):
            continue
        if db.conn().execute("SELECT 1 FROM problems WHERE id = ?", (name,)).fetchone() is not None:
            continue
        try:
            data = load_problem_dir(path)
            with db.write() as conn:
                if store_problem(conn, data, replace=False):
                    inserted.append(data.id)
        except ProblemError as e:
            log.error("bỏ qua đề seed hỏng: %s", e)
    if inserted:
        log.info("đã nạp đề seed: %s", ", ".join(inserted))
    return inserted


def reimport_problem(db, directory, problem_id):
    """D5: nạp lại cưỡng bức một đề từ thư mục (đổi hash test; không tự rejudge, không đổi cờ ẩn)."""
    if not SLUG.match(problem_id):
        raise ProblemError(f"id đề không hợp lệ: {problem_id!r}")
    path = os.path.join(directory, problem_id)
    if not os.path.isdir(path):
        raise ProblemError(f"không có thư mục đề: {path}")
    data = load_problem_dir(path)
    with db.write() as conn:
        store_problem(conn, data, replace=True)
    return data
