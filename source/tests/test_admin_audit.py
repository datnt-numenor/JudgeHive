import json
import sqlite3

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
