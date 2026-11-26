import json
import os
import sqlite3
import subprocess
import sys
import threading

import pytest

from conftest import db_one, login, system
from server.db import Database


def audit_rows(client):
    response = client.get("/api/admin/audit-logs")
    assert response.status_code == 200, response.text
    return response.json()


def test_existing_database_adds_audit_table_without_losing_data(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    legacy = sqlite3.connect(path)
    legacy.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('user','admin')),
            disabled INTEGER NOT NULL DEFAULT 0)
    """)
    legacy.execute(
        "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
        ("legacy-admin", "legacy-hash", "admin"),
    )
    legacy.commit()
    legacy.close()

    upgraded = Database(str(path))
    try:
        assert upgraded.one("SELECT username FROM users WHERE id=1")["username"] == "legacy-admin"
        columns = {row["name"] for row in upgraded.all("PRAGMA table_info(admin_audit_logs)")}
        assert columns == {
            "id", "actor_id", "action", "target_type", "target_id", "created_at", "metadata_json",
        }
    finally:
        upgraded.conn.close()


def test_admin_actions_are_audited_without_sensitive_data(tmp_path):
    with system(tmp_path) as (client, app):
        login(client, "admin")
        password = "new-password-123"
        created = client.post(
            "/api/admin/users",
            json={"username": "carol", "password": password, "role": "user"},
        )
        assert created.status_code == 201
        uid = created.json()["id"]

        assert client.patch(f"/api/admin/users/{uid}", json={"disabled": True}).status_code == 200
        assert client.patch(f"/api/admin/users/{uid}", json={"disabled": False}).status_code == 200
        assert client.patch(f"/api/admin/users/{uid}", json={"password": "changed-password-123"}).status_code == 200
        assert client.patch("/api/admin/problems/1", json={"hidden": True}).status_code == 200
        assert client.patch("/api/admin/problems/1", json={"hidden": False}).status_code == 200

        rows = audit_rows(client)
        assert [row["action"] for row in rows] == [
            "problem.show",
            "problem.hide",
            "user.reset_password",
            "user.enable",
            "user.disable",
            "user.create",
        ]
        assert all(row["actor_id"] == 1 for row in rows)
        assert all(isinstance(json.loads(row["metadata_json"]), dict) for row in rows)
        serialized = json.dumps(rows)
        assert password not in serialized
        assert "changed-password-123" not in serialized
        assert "password_hash" not in serialized
        assert "token_hash" not in serialized
        assert "session" not in serialized


def test_same_state_invalid_and_duplicate_requests_do_not_create_fake_audit(tmp_path):
    with system(tmp_path) as (client, app):
        login(client, "admin")
        initial = len(audit_rows(client))

        # Alice đang được mở; ghi lại cùng trạng thái không phải một hành động enable.
        assert client.patch("/api/admin/users/2", json={"disabled": False}).status_code == 200
        assert client.patch("/api/admin/problems/1", json={"hidden": False}).status_code == 200
        assert len(audit_rows(client)) == initial

        body = {"username": "carol", "password": "password123", "role": "user"}
        assert client.post("/api/admin/users", json=body).status_code == 201
        count_after_create = len(audit_rows(client))
        assert client.post("/api/admin/users", json=body).status_code == 409
        assert client.post("/api/admin/users", json={**body, "username": "bad name"}).status_code == 400
        assert client.patch("/api/admin/users/2", json={"password": "short"}).status_code == 400
        assert len(audit_rows(client)) == count_after_create


def test_reset_and_disable_are_atomic_and_keep_session_revocation(tmp_path):
    with system(tmp_path) as (client, app):
        login(client, "alice")
        alice_cookie = client.cookies.get("session")
        login(client, "admin")

        response = client.patch(
            "/api/admin/users/2",
            json={"password": "replacement-password", "disabled": True},
        )
        assert response.status_code == 200
        rows = audit_rows(client)
        assert {row["action"] for row in rows} == {"user.reset_password", "user.disable"}
        assert client.get(
            "/api/auth/me", headers={"Cookie": f"session={alice_cookie}"}
        ).status_code == 401


def test_audit_insert_failure_rolls_back_business_change_and_session_delete(tmp_path):
    with system(tmp_path) as (client, app):
        login(client, "alice")
        alice_cookie = client.cookies.get("session")
        login(client, "admin")
        client.portal.call(lambda: app.state.db.execute("""
                CREATE TRIGGER fail_admin_audit BEFORE INSERT ON admin_audit_logs
                BEGIN SELECT RAISE(ABORT, 'audit insert failed'); END
            """))

        with pytest.raises(sqlite3.IntegrityError, match="audit insert failed"):
            client.patch("/api/admin/users/2", json={"disabled": True})

        assert db_one(client, app, "SELECT disabled FROM users WHERE id=2")["disabled"] == 0
        assert db_one(client, app, "SELECT COUNT(*) AS n FROM admin_audit_logs")["n"] == 0
        assert client.get(
            "/api/auth/me", headers={"Cookie": f"session={alice_cookie}"}
        ).status_code == 200


def test_audit_api_permissions_cursor_and_persistence(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with system(tmp_path) as (client, app):
        assert client.get("/api/admin/audit-logs").status_code == 401
        login(client, "alice")
        assert client.get("/api/admin/audit-logs").status_code == 403
        login(client, "admin")

        for hidden in (True, False, True, False, True):
            assert client.patch("/api/admin/problems/1", json={"hidden": hidden}).status_code == 200

        first = client.get("/api/admin/audit-logs", params={"limit": 2})
        assert first.status_code == 200
        first_rows = first.json()
        assert len(first_rows) == 2
        assert first_rows[0]["id"] > first_rows[1]["id"]

        second = client.get(
            "/api/admin/audit-logs",
            params={"limit": 2, "before_id": first_rows[-1]["id"]},
        )
        assert second.status_code == 200
        second_rows = second.json()
        assert len(second_rows) == 2
        assert all(row["id"] < first_rows[-1]["id"] for row in second_rows)
        assert client.get("/api/admin/audit-logs", params={"limit": 101}).status_code == 422

    reopened = Database(str(db_path))
    try:
        assert reopened.one("SELECT COUNT(*) AS n FROM admin_audit_logs")["n"] == 5
    finally:
        reopened.conn.close()


@pytest.mark.parametrize("before_id", ["0", "-1", "abc", "9223372036854775808"])
def test_audit_cursor_rejects_invalid_sqlite_integers(tmp_path, before_id):
    with system(tmp_path) as (client, app):
        login(client, "admin")
        assert client.get("/api/admin/audit-logs", params={"before_id": before_id}).status_code == 422
        assert client.get("/api/admin/audit-logs", params={"before_id": "9223372036854775807"}).status_code == 200


def test_concurrent_disable_during_password_hash_gets_enable_audit(tmp_path, monkeypatch):
    import server.main as main

    hashing_started = threading.Event()
    release_hash = threading.Event()
    original_hash = main.hash_password

    def delayed_hash(password):
        if password == "replacement-password":
            hashing_started.set()
            assert release_hash.wait(5)
        return original_hash(password)

    monkeypatch.setattr(main, "hash_password", delayed_hash)
    with system(tmp_path) as (client, app):
        login(client, "admin")
        result = {}

        def reset_password():
            try:
                result["response"] = client.patch(
                    "/api/admin/users/2",
                    json={"password": "replacement-password", "disabled": False},
                )
            except BaseException as exc:
                result["error"] = exc

        request_thread = threading.Thread(target=reset_password)
        request_thread.start()
        try:
            assert hashing_started.wait(5)
            # Connection thứ hai commit thao tác khóa khi request đang chờ scrypt.
            with sqlite3.connect(tmp_path / "test.sqlite3", timeout=5) as other:
                other.execute("UPDATE users SET disabled=1 WHERE id=2")
        finally:
            release_hash.set()
            request_thread.join(10)

        assert not request_thread.is_alive()
        assert "error" not in result, result.get("error")
        assert result["response"].status_code == 200
        assert db_one(client, app, "SELECT disabled FROM users WHERE id=2")["disabled"] == 0
        assert [row["action"] for row in audit_rows(client)] == [
            "user.enable", "user.reset_password",
        ]


def test_create_user_rolls_back_when_audit_insert_fails(tmp_path):
    with system(tmp_path) as (client, app):
        login(client, "admin")
        client.portal.call(lambda: app.state.db.execute("""
            CREATE TRIGGER fail_audit BEFORE INSERT ON admin_audit_logs
            BEGIN SELECT RAISE(ABORT, 'audit insert failed'); END
        """))
        with pytest.raises(sqlite3.IntegrityError, match="audit insert failed"):
            client.post("/api/admin/users", json={
                "username": "carol", "password": "password123", "role": "user",
            })
        assert db_one(client, app, "SELECT id FROM users WHERE username='carol'") is None
        assert db_one(client, app, "SELECT COUNT(*) AS n FROM admin_audit_logs")["n"] == 0


@pytest.mark.parametrize("initial_hidden,new_hidden", [(0, True), (1, False)])
def test_visibility_rolls_back_when_audit_insert_fails(tmp_path, initial_hidden, new_hidden):
    with system(tmp_path) as (client, app):
        login(client, "admin")
        client.portal.call(lambda: app.state.db.execute(
            "UPDATE problems SET hidden=? WHERE id=1", (initial_hidden,)
        ))
        client.portal.call(lambda: app.state.db.execute("""
            CREATE TRIGGER fail_audit BEFORE INSERT ON admin_audit_logs
            BEGIN SELECT RAISE(ABORT, 'audit insert failed'); END
        """))
        with pytest.raises(sqlite3.IntegrityError, match="audit insert failed"):
            client.patch("/api/admin/problems/1", json={"hidden": new_hidden})
        assert db_one(client, app, "SELECT hidden FROM problems WHERE id=1")["hidden"] == initial_hidden
        assert db_one(client, app, "SELECT COUNT(*) AS n FROM admin_audit_logs")["n"] == 0


def test_second_audit_failure_rolls_back_password_disable_and_sessions(tmp_path):
    with system(tmp_path) as (client, app):
        login(client, "alice")
        alice_cookie = client.cookies.get("session")
        login(client, "admin")
        original = db_one(client, app, "SELECT password_hash,disabled FROM users WHERE id=2")
        sessions = db_one(client, app, "SELECT COUNT(*) AS n FROM sessions WHERE user_id=2")["n"]
        client.portal.call(lambda: app.state.db.execute("""
            CREATE TRIGGER fail_second_audit BEFORE INSERT ON admin_audit_logs
            WHEN NEW.action='user.disable'
            BEGIN SELECT RAISE(ABORT, 'second audit insert failed'); END
        """))

        with pytest.raises(sqlite3.IntegrityError, match="second audit insert failed"):
            client.patch("/api/admin/users/2", json={
                "password": "replacement-password", "disabled": True,
            })

        assert db_one(client, app, "SELECT password_hash,disabled FROM users WHERE id=2") == original
        assert db_one(client, app, "SELECT COUNT(*) AS n FROM sessions WHERE user_id=2")["n"] == sessions
        assert db_one(client, app, "SELECT COUNT(*) AS n FROM admin_audit_logs")["n"] == 0
        assert client.get("/api/auth/me", headers={"Cookie": f"session={alice_cookie}"}).status_code == 200


def problem_body():
    return {
        "title": "Đề kiểm tra audit", "statement": "Nội dung chỉ dành cho đề",
        "difficulty": "medium", "hidden": False,
        "tests": [
            {"input": "sample-input\n", "output": "sample-output\n", "is_sample": True},
            {"input": "hidden-input\n", "output": "hidden-output\n", "is_sample": False},
        ],
    }


def test_http_problem_create_update_audit_and_private_tests(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with system(tmp_path) as (client, app):
        body = problem_body()
        assert client.post("/api/admin/problems", json=body).status_code == 401
        assert client.put("/api/admin/problems/1", json=body).status_code == 401
        login(client, "alice")
        assert client.post("/api/admin/problems", json=body).status_code == 403
        assert client.put("/api/admin/problems/1", json=body).status_code == 403
        login(client, "admin")

        created = client.post("/api/admin/problems", json=body)
        assert created.status_code == 201
        pid = created.json()["id"]
        assert client.get(f"/api/admin/problems/{pid}").json()["tests"] == body["tests"]
        row = audit_rows(client)[0]
        assert (row["actor_id"], row["action"], row["target_type"], row["target_id"]) == (
            1, "problem.create", "problem", pid,
        )
        assert json.loads(row["metadata_json"]) == {
            "title": body["title"], "difficulty": "medium", "test_count": 2,
        }

        changed = {**body, "title": "Đề đã sửa", "difficulty": "hard", "hidden": True, "tests": [
            {"input": "new-sample\n", "output": "new-output\n", "is_sample": True},
        ]}
        updated = client.put(f"/api/admin/problems/{pid}", json=changed)
        assert updated.status_code == 200 and updated.json() == {"id": pid}
        detail = client.get(f"/api/admin/problems/{pid}").json()
        assert detail["title"] == changed["title"]
        assert detail["tests"] == changed["tests"]
        assert db_one(client, app, "SELECT COUNT(*) AS n FROM tests WHERE problem_id=?", (pid,))["n"] == 1
        rows = audit_rows(client)
        assert [row["action"] for row in rows] == ["problem.update", "problem.create"]
        assert all(row["actor_id"] == 1 and row["target_id"] == pid for row in rows)
        assert [row["action"] for row in client.get(
            "/api/admin/audit-logs", params={"before_id": rows[0]["id"]}
        ).json()] == ["problem.create"]
        serialized = json.dumps(rows, ensure_ascii=False)
        for sensitive in (body["statement"], "sample-input", "hidden-input", "hidden-output", "new-sample"):
            assert sensitive not in serialized

        login(client, "alice")
        assert client.get(f"/api/problems/{pid}").status_code == 404
        login(client, "admin")
        assert client.patch(f"/api/admin/problems/{pid}", json={"hidden": False}).status_code == 200
        assert [row["action"] for row in audit_rows(client)] == [
            "problem.show", "problem.update", "problem.create",
        ]
        login(client, "alice")
        public = client.get(f"/api/problems/{pid}")
        assert public.status_code == 200
        assert public.json()["samples"] == [{"input": "new-sample\n", "output": "new-output\n"}]
        assert "hidden-input" not in public.text

    reopened = Database(str(db_path))
    try:
        assert [row["action"] for row in reopened.all(
            "SELECT action FROM admin_audit_logs ORDER BY id DESC"
        )] == ["problem.show", "problem.update", "problem.create"]
    finally:
        reopened.conn.close()


def test_invalid_and_locked_problem_requests_do_not_audit(tmp_path):
    from conftest import submit

    with system(tmp_path) as (client, app):
        body = problem_body()
        login(client, "admin")
        assert client.post("/api/admin/problems", json={**body, "tests": []}).status_code == 422
        assert client.put("/api/admin/problems/1", json={**body, "tests": []}).status_code == 422
        assert audit_rows(client) == []

        login(client, "alice")
        submit(client)
        login(client, "admin")
        assert client.put("/api/admin/problems/1", json=body).status_code == 409
        assert audit_rows(client) == []
        assert client.get("/api/admin/problems/1").json()["title"] == "A + B"


def test_problem_create_audit_failure_rolls_back_and_does_not_publish(tmp_path, monkeypatch):
    with system(tmp_path) as (client, app):
        login(client, "admin")
        before_problems = db_one(client, app, "SELECT COUNT(*) AS n FROM problems")["n"]
        before_tests = db_one(client, app, "SELECT COUNT(*) AS n FROM tests")["n"]
        published = []

        async def record_publish(*args):
            published.append(args)

        monkeypatch.setattr(app.state.hub, "publish", record_publish)
        client.portal.call(lambda: app.state.db.execute("""
            CREATE TRIGGER fail_problem_audit BEFORE INSERT ON admin_audit_logs
            BEGIN SELECT RAISE(ABORT, 'audit insert failed'); END
        """))
        with pytest.raises(sqlite3.IntegrityError, match="audit insert failed"):
            client.post("/api/admin/problems", json=problem_body())
        assert db_one(client, app, "SELECT COUNT(*) AS n FROM problems")["n"] == before_problems
        assert db_one(client, app, "SELECT COUNT(*) AS n FROM tests")["n"] == before_tests
        assert db_one(client, app, "SELECT COUNT(*) AS n FROM admin_audit_logs")["n"] == 0
        assert published == []


def test_problem_update_audit_failure_restores_problem_and_testcases(tmp_path, monkeypatch):
    with system(tmp_path) as (client, app):
        login(client, "admin")
        created = client.post("/api/admin/problems", json=problem_body())
        assert created.status_code == 201
        pid = created.json()["id"]
        original = client.get(f"/api/admin/problems/{pid}").json()
        published = []

        async def record_publish(*args):
            published.append(args)

        monkeypatch.setattr(app.state.hub, "publish", record_publish)
        client.portal.call(lambda: app.state.db.execute("""
            CREATE TRIGGER fail_update_audit BEFORE INSERT ON admin_audit_logs
            WHEN NEW.action='problem.update'
            BEGIN SELECT RAISE(ABORT, 'audit insert failed'); END
        """))
        changed = {**problem_body(), "title": "Sửa nhưng phải rollback", "tests": [
            {"input": "replacement", "output": "replacement", "is_sample": True},
        ]}
        with pytest.raises(sqlite3.IntegrityError, match="audit insert failed"):
            client.put(f"/api/admin/problems/{pid}", json=changed)
        assert client.get(f"/api/admin/problems/{pid}").json() == original
        assert db_one(client, app, "SELECT COUNT(*) AS n FROM tests WHERE problem_id=?", (pid,))["n"] == 2
        assert [row["action"] for row in audit_rows(client)] == ["problem.create"]
        assert published == []


def test_cli_problem_import_needs_no_http_actor(tmp_path):
    from pathlib import Path

    problem_file = tmp_path / "problem.json"
    problem_file.write_text(json.dumps(problem_body(), ensure_ascii=False), encoding="utf-8")
    db_path = tmp_path / "cli.sqlite3"
    environment = {**os.environ, "JH_DB": str(db_path)}
    result = subprocess.run(
        [sys.executable, "-m", "server.manage", "problem", str(problem_file)],
        cwd=Path(__file__).resolve().parents[1], env=environment,
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "Đã nạp đề #" in result.stdout
    db = Database(str(db_path))
    try:
        assert db.one("SELECT id FROM problems WHERE title=?", (problem_body()["title"],)) is not None
        assert db.one("SELECT COUNT(*) AS n FROM admin_audit_logs")["n"] == 0
        assert db.one("SELECT COUNT(*) AS n FROM users")["n"] == 0
    finally:
        db.conn.close()
