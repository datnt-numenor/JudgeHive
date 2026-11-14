"""Độ khó của đề: cột trong DB, nâng cấp DB cũ, file đề, API."""
import json
from pathlib import Path
import sqlite3

import pytest

from server.db import Database
from server.problems import insert_problem, parse_problem
from conftest import login, system

OLD_PROBLEMS_SCHEMA = """
CREATE TABLE problems (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, statement TEXT NOT NULL,
    time_limit_ms INTEGER NOT NULL, memory_limit_kib INTEGER NOT NULL,
    hidden INTEGER NOT NULL DEFAULT 0);
INSERT INTO problems(title,statement,time_limit_ms,memory_limit_kib) VALUES('Cũ','S',1000,65536);
"""


def problem(**changes):
    return json.dumps({"title": "T", "statement": "S", "tests": [{"input": "1", "output": "1"}], **changes})


def test_new_database_has_difficulty_with_default_and_seed_is_easy(tmp_path):
    db = Database(str(tmp_path / "new.sqlite3"))
    assert db.one("SELECT difficulty FROM problems WHERE id=1")["difficulty"] == "easy"
    db.execute("INSERT INTO problems(title,statement,time_limit_ms,memory_limit_kib) VALUES('X','S',1000,65536)")
    assert db.one("SELECT difficulty FROM problems WHERE title='X'")["difficulty"] == "medium"
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO problems(title,statement,time_limit_ms,memory_limit_kib,difficulty) "
                   "VALUES('Y','S',1000,65536,'impossible')")
    db.conn.close()


def test_old_database_without_column_is_upgraded_on_start(tmp_path):
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_PROBLEMS_SCHEMA)
    conn.commit()
    conn.close()
    db = Database(str(path))  # mở lần đầu: thêm cột
    assert db.all("SELECT title,difficulty FROM problems") == [{"title": "Cũ", "difficulty": "medium"}]
    db.conn.close()
    db = Database(str(path))  # mở lần hai: không thêm lại, không lỗi
    assert [r["name"] for r in db.conn.execute("PRAGMA table_info(problems)")].count("difficulty") == 1
    db.conn.close()


def test_problem_file_difficulty(tmp_path):
    assert parse_problem(problem()).difficulty == "medium"
    assert parse_problem(problem(difficulty="hard")).difficulty == "hard"
    for bad in ("Hard", "expert", "", 1, None):
        with pytest.raises(ValueError):
            parse_problem(problem(difficulty=bad))
    db = Database(str(tmp_path / "p.sqlite3"))
    files = {path.name: parse_problem(path.read_text(encoding="utf-8"))
             for path in (Path(__file__).parents[1] / "problems").glob("*.json")}
    assert {p.difficulty for p in files.values()} <= {"easy", "medium", "hard"}
    pid = insert_problem(db, files["a-cong-b-ram-64.json"])
    assert db.one("SELECT difficulty FROM problems WHERE id=?", (pid,))["difficulty"] == files["a-cong-b-ram-64.json"].difficulty
    db.conn.close()


def test_api_returns_difficulty(tmp_path):
    with system(tmp_path) as (client, app):
        pid = client.portal.call(lambda: insert_problem(app.state.db, parse_problem(problem(difficulty="hard"))))
        login(client)
        listed = {p["id"]: p["difficulty"] for p in client.get("/api/problems").json()}
        assert listed == {1: "easy", pid: "hard"}
        assert client.get(f"/api/problems/{pid}").json()["difficulty"] == "hard"
