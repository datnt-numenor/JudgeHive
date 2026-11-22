"""Kiểm tra giới hạn đăng nhập qua HTTP và vòng đời của tác vụ scrypt."""
import asyncio
from concurrent.futures import CancelledError, ThreadPoolExecutor
from threading import Barrier, Event, Lock

import httpx
import pytest

from server import main
from conftest import system


def attempt(client, username, password="wrong", headers=None):
    return client.post("/api/auth/login", json={"username": username, "password": password}, headers=headers)


def test_valid_login_and_wrong_password_share_generic_error(tmp_path):
    with system(tmp_path) as (client, _):
        wrong = attempt(client, "alice")
        missing = attempt(client, "not-a-user")
        assert wrong.status_code == missing.status_code == 401
        assert wrong.json() == missing.json()
        valid = attempt(client, "alice", "password123")
        assert valid.status_code == 200
        assert client.get("/api/auth/me").json()["username"] == "alice"


def test_ip_limit_ignores_untrusted_proxy_headers(tmp_path):
    with system(tmp_path) as (client, app):
        app.state.login_limiter.clock = lambda: 100.0
        for index in range(main.LOGIN_IP_LIMIT):
            response = attempt(client, f"missing-{index}", headers={"X-Forwarded-For": f"192.0.2.{index + 1}"})
            assert response.status_code == 401
        blocked = attempt(client, "alice", "password123", headers={"X-Forwarded-For": "198.51.100.1"})
        assert blocked.status_code == 429
        assert blocked.headers["Retry-After"] == str(main.LOGIN_WINDOW_SECONDS)
        assert "session" not in blocked.cookies


def test_username_limit_and_window_expiry(tmp_path):
    with system(tmp_path) as (client, app):
        clock = [100.0]
        app.state.login_limiter.clock = lambda: clock[0]
        before = client.portal.call(
            lambda: app.state.db.one("SELECT username,password_hash,role,disabled FROM users WHERE id=2"))
        for _ in range(main.LOGIN_USERNAME_LIMIT):
            assert attempt(client, "alice").status_code == 401
        blocked = attempt(client, "alice", "password123")
        assert blocked.status_code == 429
        assert blocked.headers["Retry-After"] == "60"
        clock[0] += 30.1
        assert attempt(client, "alice", "password123").headers["Retry-After"] == "30"
        after = client.portal.call(
            lambda: app.state.db.one("SELECT username,password_hash,role,disabled FROM users WHERE id=2"))
        sessions = client.portal.call(
            lambda: app.state.db.one("SELECT COUNT(*) AS n FROM sessions WHERE user_id=2")["n"])
        assert after == before and after["disabled"] == 0
        assert sessions == 0
        clock[0] += 30
        assert attempt(client, "alice", "password123").status_code == 200


@pytest.mark.parametrize("mode,username", [
    ("username-existing", "alice"),
    ("username-missing", "not-a-user"),
    ("ip", None),
])
def test_concurrent_attempts_obey_rate_limits(tmp_path, monkeypatch, mode, username):
    with system(tmp_path) as (client, app):
        app.state.login_limiter.clock = lambda: 100.0

        async def reject_password(password, stored, slots):
            return False

        monkeypatch.setattr(main, "limited_verify", reject_password)
        if mode == "ip":
            allowed = main.LOGIN_IP_LIMIT
            usernames = [f"missing-{index}" for index in range(allowed + 4)]
        else:
            allowed = main.LOGIN_USERNAME_LIMIT
            usernames = [username] * (allowed + 4)
        ready = Barrier(len(usernames))

        def concurrent_attempt(target):
            ready.wait(timeout=10)
            return attempt(client, target)

        with ThreadPoolExecutor(max_workers=len(usernames)) as pool:
            responses = list(pool.map(concurrent_attempt, usernames))

        accepted = [response for response in responses if response.status_code == 401]
        blocked = [response for response in responses if response.status_code == 429]
        assert len(accepted) == allowed
        assert len(blocked) == len(usernames) - allowed
        assert {response.json()["detail"] for response in accepted} == {
            "Sai tên đăng nhập hoặc mật khẩu"}
        assert {response.json()["detail"] for response in blocked} == {"Quá nhiều lần đăng nhập"}
        assert {response.headers["Retry-After"] for response in blocked} == {
            str(main.LOGIN_WINDOW_SECONDS)}


def test_scrypt_slots_reject_without_waiting(tmp_path, monkeypatch):
    with system(tmp_path) as (client, app):
        original = main.verify_password
        release = Event()
        all_started = Event()
        lock = Lock()
        started = 0

        def paused(password, stored):
            nonlocal started
            with lock:
                started += 1
                if started == main.LOGIN_SCRYPT_SLOTS:
                    all_started.set()
            assert release.wait(10), "Tác vụ scrypt không được giải phóng"
            return original(password, stored)

        monkeypatch.setattr(main, "verify_password", paused)
        with ThreadPoolExecutor(max_workers=main.LOGIN_SCRYPT_SLOTS) as pool:
            pending = [pool.submit(attempt, client, f"missing-{index}")
                       for index in range(main.LOGIN_SCRYPT_SLOTS)]
            try:
                assert all_started.wait(10)
                blocked = attempt(client, "alice", "password123")
                assert blocked.status_code == 429
                assert blocked.headers["Retry-After"] == "1"
                assert all(not future.done() for future in pending)
            finally:
                release.set()
            assert all(future.result(timeout=10).status_code == 401 for future in pending)
        assert attempt(client, "alice", "password123").status_code == 200


def test_scrypt_exception_releases_slot(tmp_path, monkeypatch):
    with system(tmp_path) as (client, app):
        original = main.verify_password

        def broken(password, stored):
            raise RuntimeError("scrypt failed")

        monkeypatch.setattr(main, "verify_password", broken)
        with pytest.raises(RuntimeError, match="scrypt failed"):
            attempt(client, "alice", "password123")
        assert client.portal.call(lambda: app.state.login_slots._value) == main.LOGIN_SCRYPT_SLOTS
        monkeypatch.setattr(main, "verify_password", original)
        assert attempt(client, "alice", "password123").status_code == 200


def test_cancelled_http_request_keeps_slot_until_scrypt_finishes(tmp_path, monkeypatch):
    with system(tmp_path) as (client, app):
        original = main.verify_password
        entered = Event()
        release = Event()

        def paused(password, stored):
            if password == "cancel-password":
                entered.set()
                assert release.wait(10), "Tác vụ scrypt không được giải phóng"
            return original(password, stored)

        monkeypatch.setattr(main, "verify_password", paused)

        async def request_login():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http:
                await http.post("/api/auth/login", json={"username": "alice", "password": "cancel-password"})

        pending = client.portal.start_task_soon(request_login)
        try:
            assert entered.wait(10)
            assert pending.cancel()
            with pytest.raises(CancelledError):
                pending.result(timeout=10)
            # Request đã hủy nhưng thread vẫn chạy, nên slot vẫn phải được giữ.
            assert client.portal.call(lambda: app.state.login_slots._value) == main.LOGIN_SCRYPT_SLOTS - 1
        finally:
            release.set()

        async def wait_for_release():
            async def released():
                while app.state.login_slots._value != main.LOGIN_SCRYPT_SLOTS:
                    await asyncio.sleep(0)

            await asyncio.wait_for(released(), 5)

        client.portal.call(wait_for_release)
        monkeypatch.setattr(main, "verify_password", original)
        assert attempt(client, "alice", "password123").status_code == 200


def test_four_cancelled_requests_keep_all_slots_until_threads_finish(tmp_path, monkeypatch):
    with system(tmp_path) as (client, app):
        original = main.verify_password
        release = Event()
        all_started = Event()
        lock = Lock()
        started = 0

        def paused(password, stored):
            nonlocal started
            with lock:
                started += 1
                if started == main.LOGIN_SCRYPT_SLOTS:
                    all_started.set()
            assert release.wait(10), "Tác vụ scrypt không được giải phóng"
            return original(password, stored)

        monkeypatch.setattr(main, "verify_password", paused)

        async def request_login(index):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://testserver") as http:
                await http.post("/api/auth/login",
                                json={"username": f"missing-{index}", "password": "cancel-password"})

        pending = [client.portal.start_task_soon(request_login, index)
                   for index in range(main.LOGIN_SCRYPT_SLOTS)]
        try:
            assert all_started.wait(10)
            for future in pending:
                assert future.cancel()
                with pytest.raises(CancelledError):
                    future.result(timeout=10)
            assert client.portal.call(lambda: app.state.login_slots._value) == 0
            blocked = attempt(client, "alice", "password123")
            assert blocked.status_code == 429
            assert blocked.headers["Retry-After"] == "1"
        finally:
            release.set()

        async def wait_for_all_slots():
            async def released():
                while app.state.login_slots._value != main.LOGIN_SCRYPT_SLOTS:
                    await asyncio.sleep(0)

            await asyncio.wait_for(released(), 5)

        client.portal.call(wait_for_all_slots)
        monkeypatch.setattr(main, "verify_password", original)
        assert attempt(client, "alice", "password123").status_code == 200
