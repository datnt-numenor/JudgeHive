"""Một connection, chỉ dùng ở event loop server. SQL ngắn và không có await giữa transaction."""
import sqlite3
import time
from pathlib import Path


def now():
    return int(time.time() * 1000)


class Database:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('user','admin')),
            disabled INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id), expires_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS problems (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, statement TEXT NOT NULL,
            time_limit_ms INTEGER NOT NULL, memory_limit_kib INTEGER NOT NULL,
            hidden INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS tests (
            id INTEGER PRIMARY KEY, problem_id INTEGER NOT NULL REFERENCES problems(id),
            position INTEGER NOT NULL, input TEXT NOT NULL, output TEXT NOT NULL,
            is_sample INTEGER NOT NULL DEFAULT 0, UNIQUE(problem_id, position));
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            problem_id INTEGER NOT NULL REFERENCES problems(id), language TEXT NOT NULL, code TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'queued', attempt_id INTEGER NOT NULL DEFAULT 0,
            submitted_at INTEGER NOT NULL, finished_at INTEGER, verdict TEXT,
            tests_done INTEGER NOT NULL DEFAULT 0, time_ms INTEGER, message TEXT NOT NULL DEFAULT '');
        CREATE INDEX IF NOT EXISTS submission_user ON submissions(user_id, id);
        """)
        if not self.one("SELECT id FROM problems LIMIT 1"):
            with self.conn:
                cur = self.conn.execute("INSERT INTO problems(title,statement,time_limit_ms,memory_limit_kib) VALUES(?,?,?,?)",
                                        ("A + B", "Đọc hai số nguyên a, b. In tổng a + b. Ví dụ: 2 3 → 5.", 2000, 262144))
                self.conn.executemany("INSERT INTO tests(problem_id,position,input,output,is_sample) VALUES(?,?,?,?,?)",
                                      [(cur.lastrowid, 1, "2 3\n", "5\n", 1),
                                       (cur.lastrowid, 2, "-7 9\n", "2\n", 0)])

    def one(self, sql, args=()):
        row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row is not None else None

    def all(self, sql, args=()):
        return [dict(row) for row in self.conn.execute(sql, args)]

    def execute(self, sql, args=()):
        with self.conn:
            return self.conn.execute(sql, args).lastrowid

    def user_for_token(self, hashed):
        return self.one("SELECT u.id,u.username,u.role FROM sessions s JOIN users u ON u.id=s.user_id "
                        "WHERE s.token_hash=? AND s.expires_at>? AND u.disabled=0", (hashed, now()))

    def scoreboard(self):
        return self.all("""
        WITH solved AS (
          SELECT s.user_id,s.problem_id,MIN(s.submitted_at) AS first_ac
          FROM submissions s JOIN problems p ON p.id=s.problem_id
          WHERE s.verdict='AC' AND p.hidden=0 GROUP BY s.user_id,s.problem_id
        )
        SELECT u.username,COUNT(s.problem_id) AS solved,MAX(s.first_ac) AS last_ac
        FROM users u LEFT JOIN solved s ON s.user_id=u.id WHERE u.role='user' AND u.disabled=0
        GROUP BY u.id ORDER BY solved DESC,last_ac ASC,u.username ASC
        """)
