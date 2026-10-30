"""SQLite theo PLAN mục 2.1: WAL, busy_timeout, mỗi thread một kết nối, `isolation_level=None`, và transaction đọc rồi
ghi mở bằng `BEGIN IMMEDIATE`. `manage.py` dùng chung module này để có cùng cấu hình."""

import contextlib
import logging
import os
import sqlite3
import threading
import time

log = logging.getLogger("judgehive.db")

BUSY_TIMEOUT_MS = 5000  # [ĐỀ XUẤT]

# Mỗi phần tử là một migration; `PRAGMA user_version` = số migration đã chạy. PR sau thêm phần tử mới ở cuối.
MIGRATIONS = [
    """
    CREATE TABLE users (
        id            INTEGER PRIMARY KEY,
        username      TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role          TEXT NOT NULL CHECK (role IN ('user', 'admin')),
        created_at    INTEGER NOT NULL
    );

    CREATE TABLE problems (
        id               TEXT PRIMARY KEY,
        title            TEXT NOT NULL,
        statement        TEXT NOT NULL,
        time_limit_ms    INTEGER NOT NULL CHECK (time_limit_ms > 0),
        memory_limit_kib INTEGER NOT NULL CHECK (memory_limit_kib > 0),
        hidden           INTEGER NOT NULL DEFAULT 0 CHECK (hidden IN (0, 1)),
        tests_hash       TEXT NOT NULL,
        created_at       INTEGER NOT NULL,
        updated_at       INTEGER NOT NULL
    );

    CREATE TABLE tests (
        id         INTEGER PRIMARY KEY,
        problem_id TEXT NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
        position   INTEGER NOT NULL,
        is_sample  INTEGER NOT NULL CHECK (is_sample IN (0, 1)),
        input      BLOB NOT NULL,
        output     BLOB NOT NULL,
        UNIQUE (problem_id, position)
    );

    CREATE TABLE submissions (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id             INTEGER NOT NULL REFERENCES users(id),
        problem_id          TEXT NOT NULL REFERENCES problems(id),
        language            TEXT NOT NULL CHECK (language IN ('python', 'cpp', 'java')),
        code                TEXT NOT NULL,
        client_token        TEXT,
        submitted_at        INTEGER NOT NULL,
        state               TEXT NOT NULL CHECK (state IN ('queued', 'judging', 'done')),
        is_rejudge          INTEGER NOT NULL DEFAULT 0 CHECK (is_rejudge IN (0, 1)),
        attempt_id          INTEGER NOT NULL DEFAULT 0,
        delivery_count      INTEGER NOT NULL DEFAULT 0,
        delivered_test_hash TEXT,
        verdict_test_hash   TEXT,
        rejudge_requested   INTEGER NOT NULL DEFAULT 0 CHECK (rejudge_requested IN (0, 1)),
        rejudge_failed      INTEGER NOT NULL DEFAULT 0 CHECK (rejudge_failed IN (0, 1)),
        rejudged_at         INTEGER,
        verdict             TEXT CHECK (verdict IN ('AC', 'WA', 'TLE', 'MLE', 'RE', 'CE', 'SE')),
        time_ms             INTEGER,
        memory_kib          INTEGER,
        failed_test         INTEGER,
        failed_is_sample    INTEGER CHECK (failed_is_sample IN (0, 1)),
        re_kind             TEXT,
        ce_message          TEXT,
        sample_stdout       TEXT,
        finished_at         INTEGER,
        UNIQUE (user_id, client_token)
    );

    CREATE INDEX idx_submissions_state ON submissions(state);
    CREATE INDEX idx_submissions_problem ON submissions(problem_id);
    CREATE INDEX idx_submissions_user ON submissions(user_id, submitted_at);
    """,
    # PR 5 (REST): phiên đăng nhập (chỉ lưu hash SHA-256 của token) và index cho phân trang lịch sử nộp bài.
    """
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL
    );

    CREATE INDEX idx_sessions_user ON sessions(user_id);
    CREATE INDEX idx_sessions_expires ON sessions(expires_at);
    CREATE INDEX idx_submissions_user_id ON submissions(user_id, id);
    """,
    # PR 7 (B1): chỉ mục phủ một phần cho truy vấn bảng xếp hạng (`server/scoreboard/loader.py`). Cột `code` (tới 64 KB)
    # nằm trước `verdict` trong bảng nên quét bảng phải đi qua các trang tràn; chỉ mục này trả đủ cột mà không chạm bảng.
    """
    CREATE INDEX idx_submissions_board ON submissions(problem_id, user_id, submitted_at, id, verdict)
        WHERE verdict IS NOT NULL;
    """,
    # D22: `delivery_count` đổi nghĩa thành số lần giao ĐÃ XÁC NHẬN bị cắt ngang; `total_deliveries` đếm mọi lần giao.
    # Mọi lần mất trước đây coi là đã xác nhận, nên backfill `total_deliveries` = `delivery_count` cũ; bài đang `judging`
    # có lần giao dở chưa mất nên bớt 1 ở cả hai cột (khớp việc `load_pending` hoàn lần giao dở).
    """
    ALTER TABLE submissions ADD COLUMN total_deliveries INTEGER NOT NULL DEFAULT 0;
    UPDATE submissions SET total_deliveries = delivery_count;
    UPDATE submissions SET delivery_count = MAX(delivery_count - 1, 0) WHERE state = 'judging';
    """,
]


# Các key của dict `Database.get_submission` (giao diện ổn định cho PR sau và cho test; không phải toàn bộ cột).
SUBMISSION_VIEW = ("id", "user_id", "problem_id", "language", "state", "attempt_id", "delivered_test_hash", "verdict",
                   "verdict_test_hash", "time_ms", "memory_kib", "failed_test", "failed_is_sample", "re_kind",
                   "ce_message", "sample_stdout")


class Database:
    def __init__(self, path):
        self.path = path
        self._local = threading.local()

    def conn(self):
        """Kết nối của thread hiện tại (tạo lần đầu gọi)."""
        c = getattr(self._local, "conn", None)
        if c is None:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            c = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
            c.row_factory = sqlite3.Row
            c.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            _enable_wal(c)
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA foreign_keys=ON")
            self._local.conn = c
        return c

    def close_thread(self):
        """Đóng kết nối của thread hiện tại; thread sống ngắn (một kết nối worker) gọi khi thoát."""
        c = getattr(self._local, "conn", None)
        if c is not None:
            self._local.conn = None
            with contextlib.suppress(sqlite3.Error):
                c.close()

    @contextlib.contextmanager
    def write(self):
        """Transaction đọc rồi ghi: `BEGIN IMMEDIATE`, commit khi khối kết thúc, rollback khi có exception."""
        c = self.conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
            c.execute("COMMIT")
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                c.execute("ROLLBACK")
            raise

    def create_user(self, username, password_hash, role="user"):
        """Thêm user, trả id. `sqlite3.IntegrityError` nếu username đã có."""
        with self.write() as c:
            return c.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                             (username, password_hash, role, _now_ms())).lastrowid

    def create_submission(self, user_id, problem_id, language, code):
        """Ghi một bài nộp ở trạng thái `queued`, trả id. Không tự đưa vào hàng đợi trong RAM của dispatcher."""
        with self.write() as c:
            return c.execute("INSERT INTO submissions (user_id, problem_id, language, code, submitted_at, state) "
                             "VALUES (?, ?, ?, ?, ?, 'queued')",
                             (user_id, problem_id, language, code, _now_ms())).lastrowid

    def get_submission(self, submission_id):
        """dict với các key trong `SUBMISSION_VIEW` (`failed_is_sample` là bool hoặc None), hoặc None nếu không có."""
        row = self.conn().execute(f"SELECT {', '.join(SUBMISSION_VIEW)} FROM submissions WHERE id = ?",
                                  (submission_id,)).fetchone()
        if row is None:
            return None
        view = dict(row)
        if view["failed_is_sample"] is not None:
            view["failed_is_sample"] = bool(view["failed_is_sample"])
        return view

    def migrate(self):
        """Chạy các migration còn thiếu. An toàn khi server và `manage.py` chạy cùng lúc (mỗi bước trong một
        transaction IMMEDIATE và đọc lại `user_version` bên trong)."""
        for index, script in enumerate(MIGRATIONS):
            with self.write() as c:
                version = c.execute("PRAGMA user_version").fetchone()[0]
                if version > index:
                    continue
                for statement in _split_statements(script):
                    c.execute(statement)
                c.execute(f"PRAGMA user_version={index + 1}")
            log.info("đã chạy migration %d", index + 1)


def _enable_wal(conn):
    """Bật WAL. Đổi journal_mode cần khóa độc quyền và SQLite báo "database is locked" ngay (không chờ busy_timeout)
    khi một kết nối khác đang tạo DB mới cùng lúc (server và manage.py khởi động đồng thời), nên thử lại có giới hạn."""
    deadline = time.monotonic() + BUSY_TIMEOUT_MS / 1000
    while True:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or time.monotonic() > deadline:
                raise
            time.sleep(0.01)


def _now_ms():
    return int(time.time() * 1000)


def _split_statements(script):
    return [s.strip() for s in script.split(";") if s.strip()]
