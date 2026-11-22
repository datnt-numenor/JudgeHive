"""Authentication must not issue a session from credentials changed during verification."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Event

import pytest

from server.auth import hash_password
from conftest import login, system


@pytest.mark.parametrize("change", ["reset", "disable", "external_reset"])
def test_login_rechecks_credentials_after_verification(tmp_path, monkeypatch, change):
    with system(tmp_path) as (client, app):
        login(client)
        previous_session = client.cookies.get("session")
        login(client, "admin")

        from server import main

        original_verify = main.verify_password
        entered = Event()
        release = Event()

        def paused_verify(password, stored):
            if password == "password123":
                entered.set()
                assert release.wait(10), "Login verification was not released"
            return original_verify(password, stored)

        monkeypatch.setattr(main, "verify_password", paused_verify)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, "/api/auth/login",
                                  json={"username": "alice", "password": "password123"})
            try:
                assert entered.wait(10), "Login did not reach password verification"
                if change == "reset":
                    changed = client.patch("/api/admin/users/2", json={"password": "newpassword123"})
                    assert changed.status_code == 200
                elif change == "disable":
                    changed = client.patch("/api/admin/users/2", json={"disabled": True})
                    assert changed.status_code == 200
                else:
                    # A second connection can commit while scrypt runs in a thread.
                    with sqlite3.connect(tmp_path / "test.sqlite3") as other:
                        other.execute("UPDATE users SET password_hash=? WHERE id=2",
                                      (hash_password("newpassword123"),))
                        other.execute("DELETE FROM sessions WHERE user_id=2")
            finally:
                release.set()
            response = pending.result(timeout=10)

        assert response.status_code == 401
        assert "session" not in response.cookies
        assert client.get("/api/auth/me", headers={"Cookie": f"session={previous_session}"}).status_code == 401

        if change != "disable":
            fresh = client.post("/api/auth/login",
                                json={"username": "alice", "password": "newpassword123"})
            assert fresh.status_code == 200
            assert client.get("/api/auth/me").json()["username"] == "alice"
            assert client.get("/api/admin/users").status_code == 403
        else:
            assert client.post("/api/auth/login", json={"username": "alice",
                                                         "password": "password123"}).status_code == 401
