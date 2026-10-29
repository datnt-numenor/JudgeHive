"""WebSocket `/ws` qua uvicorn thật (README mục 4): handshake, đúng đích, STATUS/SCOREBOARD, mã đóng, trần, PING/PONG.

Client là `tests/e2e/ws_client.py` (thư viện chuẩn, mask mọi frame): server dùng thư viện WebSocket thật nên từ chối frame chưa
mask bằng 1002, vì vậy nếu client mask sai thì các test này không chạy được. Ứng viên khóa được đánh dấu [khóa]; mọi test dùng
khoảng thời gian thật ngắn (ping, idle, hết hạn phiên) là [không khóa]."""

import json
import os
import socket
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "e2e"))

from ws_client import HandshakeRejected, WsClient, encode_frame  # noqa: E402

from _rest_support import PASSWORD, FakeClock, populate, uuid_token  # noqa: E402
from _support import wait_until  # noqa: E402


def open_ws(web, cookie, origin="same", **kw):
    if origin == "same":
        origin = f"http://127.0.0.1:{web.port}"
    return WsClient("127.0.0.1", web.port, origin=origin, cookie=cookie, **kw)


def collect(client, stop_when, timeout=8.0):
    """Đọc message JSON cho tới khi `stop_when(list)` đúng; trả danh sách. Tự trả PONG cho PING."""
    messages, deadline = [], time.monotonic() + timeout
    while time.monotonic() < deadline:
        kind, value = client.recv(timeout=max(0.05, deadline - time.monotonic()))
        if kind == "text":
            import json

            message = json.loads(value)
            if message.get("type") == "PING":
                client.send_pong_for(message)
            messages.append(message)
            if stop_when(messages):
                break
        elif kind in ("close", "eof"):
            messages.append({"closed": value})
            break
    return messages


def statuses(messages, sid=None):
    return [m for m in messages if m.get("type") == "STATUS" and (sid is None or m["submission_id"] == sid)]


def finish_ac(worker):
    job = worker.take_job()
    worker.finish_ac(job)
    return job


@pytest.fixture
def wsweb(make_web):
    w = make_web(scoreboard_min_interval_s=0.05)
    populate(w.db)
    return w.start()


def expect_close(client, code, timeout=5.0):
    """Đọc (bỏ qua message thường) tới khi gặp frame đóng; kiểm mã."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        kind, value = client.recv(timeout=max(0.05, deadline - time.monotonic()))
        if kind == "close":
            assert value == code, f"mã đóng {value}, mong {code}"
            return
        if kind == "eof":
            pytest.fail("kết nối đóng không có frame đóng")
    pytest.fail(f"không thấy frame đóng trong {timeout} giây")


# ---- handshake ----------------------------------------------------------------------------------------------------

def test_a_valid_connection_gets_the_scoreboard_immediately(wsweb):  # [khóa]
    client = open_ws(wsweb, wsweb.login("alice"))
    message = client.recv_json()
    assert message["type"] == "SCOREBOARD" and message["version"] >= 1 and message["rows"] == []
    assert list(message) == ["type", "version", "generated_at", "rows"]
    client.close()


@pytest.mark.parametrize("origin", [None, "null", "http://evil.example", "http://127.0.0.1:1", "ws://127.0.0.1", ""])
def test_a_missing_or_foreign_origin_is_refused_with_http_403(wsweb, origin):  # [khóa]
    with pytest.raises(HandshakeRejected) as refused:
        open_ws(wsweb, wsweb.login("alice"), origin=origin)
    assert refused.value.status == 403


def test_the_same_host_over_https_is_accepted(wsweb):  # [khóa]
    client = open_ws(wsweb, wsweb.login("alice"), origin=f"https://127.0.0.1:{wsweb.port}")
    assert client.recv_json()["type"] == "SCOREBOARD"
    client.close()


def test_an_origin_in_the_configured_list_is_accepted(make_web):  # [khóa]
    web = make_web(allowed_origins=("http://localhost:5173",))
    populate(web.db)
    web.start()
    client = open_ws(web, web.login("alice"), origin="http://localhost:5173")
    assert client.recv_json()["type"] == "SCOREBOARD"
    client.close()
    with pytest.raises(HandshakeRejected):
        open_ws(web, web.login("alice"), origin="http://localhost:5174")


@pytest.mark.parametrize("cookie", [None, "", "x" * 43, "garbage", "a" * 5000])
def test_no_valid_session_means_accept_then_close_4401(wsweb, cookie):  # [khóa]
    client = open_ws(wsweb, cookie)
    expect_close(client, 4401)


def test_an_expired_session_is_closed_4401(make_web):  # [khóa]
    clock = FakeClock()
    web = make_web(clock=clock)
    populate(web.db)
    web.start()
    cookie = web.login("alice")
    clock.advance(7 * 24 * 3600 + 1)
    expect_close(open_ws(web, cookie), 4401)


def test_a_plain_http_get_of_ws_is_not_an_upgrade_and_not_a_page(wsweb):
    r = wsweb.get("/ws")
    assert r.status != 101 and r.status >= 400 and b"<html" not in r.body.lower()


# ---- STATUS đúng đích ---------------------------------------------------------------------------------------------

def test_status_flows_to_the_owner_in_order_and_ends_with_the_rest_view(wsweb):  # [khóa]
    worker = wsweb.worker()
    cookie = wsweb.login("alice")
    client = open_ws(wsweb, cookie)
    assert client.recv_json()["type"] == "SCOREBOARD"
    sid = wsweb.submit(cookie, uuid_token(1)).json()["submission_id"]
    finish_ac(worker)
    messages = collect(client, lambda ms: any(m.get("state") == "judged" for m in statuses(ms)))
    mine = statuses(messages, sid)
    assert [m["state"] for m in mine][:1] == ["queued"] and mine[-1]["state"] == "judged"
    states = [m["state"] for m in mine]
    assert states.index("judging") < len(states) - 1 and states.count("queued") == 1
    progress = [m["progress"] for m in mine if "progress" in m]
    assert progress and [p["tests_done"] for p in progress] == sorted(p["tests_done"] for p in progress)
    assert all(p["tests_total"] == 4 for p in progress)  # đề `sum`: 2 test mẫu + 2 test ẩn
    final = mine[-1]
    rest_item = next(s for s in wsweb.get("/api/submissions", cookie=cookie).json()["submissions"]
                     if s["submission_id"] == sid)
    assert final["submission"] == rest_item and final["submission"]["verdict"] == "AC"
    client.close()


def test_other_people_never_receive_my_status(wsweb):  # [khóa]
    worker = wsweb.worker()
    alice, bob = wsweb.login("alice"), wsweb.login("bob")
    a1, a2, b = open_ws(wsweb, alice), open_ws(wsweb, alice), open_ws(wsweb, bob)
    for c in (a1, a2, b):
        c.recv_json()
    sid = wsweb.submit(alice, uuid_token(1)).json()["submission_id"]
    finish_ac(worker)
    for tab in (a1, a2):  # mọi tab của alice đều nhận
        assert any(m["state"] == "judged" for m in statuses(
            collect(tab, lambda ms: any(m.get("state") == "judged" for m in statuses(ms))), sid))
    others = collect(b, lambda ms: False, timeout=1.0)
    assert statuses(others) == []  # bob chỉ có thể nhận SCOREBOARD
    for c in (a1, a2, b):
        c.close()


def test_an_admin_does_not_receive_other_peoples_status(wsweb):  # [khóa]
    worker = wsweb.worker()
    alice = wsweb.login("alice")
    root_ws = open_ws(wsweb, wsweb.login("root"))
    root_ws.recv_json()
    wsweb.submit(alice, uuid_token(1))
    finish_ac(worker)
    assert statuses(collect(root_ws, lambda ms: False, timeout=1.0)) == []
    root_ws.close()


def test_status_never_carries_code_or_sample_data_or_hidden_tests(wsweb):  # [khóa]
    worker = wsweb.worker()
    cookie = wsweb.login("alice")
    client = open_ws(wsweb, cookie)
    client.recv_json()
    wsweb.submit(cookie, uuid_token(1), code="print('SECRET-SOURCE')\n")
    job = worker.take_job()
    worker.progress(job, 0)
    worker.result(job, "WA", time_ms=1, memory_kib=1, failed_test=1, failed_is_sample=True, sample_stdout="STDOUT-MARK")
    messages = collect(client, lambda ms: any(m.get("state") == "judged" for m in statuses(ms)))
    import json

    text = json.dumps(messages)
    for forbidden in ("SECRET-SOURCE", "STDOUT-MARK", "HIDDEN-IN", "HIDDEN-OUT", "sample_stdout", "sample_input",
                      "sample_expected_output", "ce_message", '"code"'):
        assert forbidden not in text
    judged = [m for m in statuses(messages) if m["state"] == "judged"][0]["submission"]
    assert judged["verdict"] == "WA" and judged["failed_is_sample"] is True
    client.close()


def test_scoreboard_is_pushed_when_a_result_is_recorded(wsweb):  # [khóa phần phiên bản đơn điệu]
    worker = wsweb.worker()
    cookie = wsweb.login("alice")
    client = open_ws(wsweb, cookie)
    first = client.recv_json()
    wsweb.submit(cookie, uuid_token(1))
    finish_ac(worker)
    boards = [m for m in collect(client, lambda ms: any(m.get("type") == "SCOREBOARD" and m["rows"] for m in ms))
              if m.get("type") == "SCOREBOARD"]
    assert boards[-1]["rows"][0]["username"] == "alice" and boards[-1]["rows"][0]["solved"] == 1
    versions = [first["version"]] + [b["version"] for b in boards]
    assert versions == sorted(set(versions))
    client.close()


# ---- đóng do phiên ------------------------------------------------------------------------------------------------

def test_logout_closes_only_the_websockets_of_that_session(wsweb):  # [khóa]
    worker = wsweb.worker()
    first, second = wsweb.login("alice"), wsweb.login("alice")
    ws1a, ws1b, ws2 = open_ws(wsweb, first), open_ws(wsweb, first), open_ws(wsweb, second)
    for c in (ws1a, ws1b, ws2):
        c.recv_json()
    assert wsweb.post("/api/logout", {}, cookie=first).status == 204
    expect_close(ws1a, 4401)
    expect_close(ws1b, 4401)
    wsweb.submit(second, uuid_token(1))  # phiên còn lại vẫn nhận STATUS
    finish_ac(worker)
    assert any(m["state"] == "judged" for m in statuses(
        collect(ws2, lambda ms: any(m.get("state") == "judged" for m in statuses(ms)))))
    ws2.close()


def test_logging_in_again_with_the_old_cookie_closes_the_replaced_sessions_sockets(wsweb):  # [khóa]
    old = wsweb.login("alice")
    ws_old = open_ws(wsweb, old)
    ws_old.recv_json()
    new = wsweb.post("/api/login", {"username": "alice", "password": PASSWORD}, cookie=old).cookie_value()
    expect_close(ws_old, 4401)
    ws_new = open_ws(wsweb, new)
    assert ws_new.recv_json()["type"] == "SCOREBOARD"
    ws_new.close()


def test_a_session_that_expires_while_connected_is_closed_within_one_heartbeat(make_web):  # [không khóa]
    clock = FakeClock()
    web = make_web(clock=clock, ws_ping_interval_s=0.2, ws_idle_timeout_s=30)
    populate(web.db)
    web.start()
    cookie = web.login("alice")
    client = open_ws(web, cookie)
    client.recv_json()
    clock.advance(7 * 24 * 3600 + 1)
    expect_close(client, 4401, timeout=5)


# ---- trần ---------------------------------------------------------------------------------------------------------

def test_the_per_user_cap_closes_the_extra_connection_with_4429(make_web):  # [khóa mã đóng]
    web = make_web(ws_max_per_user=2)
    populate(web.db)
    web.start()
    cookie = web.login("alice")
    first, second = open_ws(web, cookie), open_ws(web, cookie)
    for c in (first, second):
        assert c.recv_json()["type"] == "SCOREBOARD"
    expect_close(open_ws(web, cookie), 4429)
    other = open_ws(web, web.login("bob"))  # người khác không bị ảnh hưởng
    assert other.recv_json()["type"] == "SCOREBOARD"
    first.close()
    assert wait_until(lambda: len(web.app.state.ws.registry) == 2)
    again = open_ws(web, cookie)  # đóng một tab thì nhường được chỗ
    assert again.recv_json()["type"] == "SCOREBOARD"
    for c in (second, other, again):
        c.close()


def test_the_server_wide_cap_closes_with_1013(make_web):  # [khóa mã đóng]
    web = make_web(ws_max_total=2)
    populate(web.db)
    web.start()
    a, b = open_ws(web, web.login("alice")), open_ws(web, web.login("bob"))
    for c in (a, b):
        assert c.recv_json()["type"] == "SCOREBOARD"
    expect_close(open_ws(web, web.login("root")), 1013)
    for c in (a, b):
        c.close()


def test_websockets_count_against_limit_concurrency_so_http_gets_503(make_web):  # [khóa hành vi hạ tầng]
    web = make_web(ws_max_total=50)
    web.limit_concurrency = 5
    populate(web.db)
    web.start()
    cookie = web.login("alice")
    clients = [open_ws(web, cookie) for _ in range(5)]
    for c in clients:
        assert c.recv_json()["type"] == "SCOREBOARD"
    assert web.request("GET", "/api/health").status == 503
    for c in clients:
        c.close()


# ---- frame của client ---------------------------------------------------------------------------------------------

def test_too_many_frames_in_the_window_close_with_1008(make_web):  # [khóa mã đóng]
    web = make_web(ws_max_frames=5, ws_frame_window_s=30.0)
    populate(web.db)
    web.start()
    client = open_ws(web, web.login("alice"))
    client.recv_json()
    for i in range(6):
        client.send_text(json.dumps({"type": "PONG", "seq": i}))
    expect_close(client, 1008)
    again = open_ws(web, web.login("alice"))  # nối lại (có backoff ở client) thì được nhận lại bình thường
    assert again.recv_json()["type"] == "SCOREBOARD"
    again.close()


def test_a_binary_frame_is_closed_with_1003(wsweb):  # [khóa]
    client = open_ws(wsweb, wsweb.login("alice"))
    client.recv_json()
    client.send_binary(b"\x00\x01")
    expect_close(client, 1003)


def test_a_frame_over_4096_bytes_is_closed_with_1009(wsweb):  # [khóa]
    client = open_ws(wsweb, wsweb.login("alice"))
    client.recv_json()
    client.send_text('{"type":"PONG","seq":1,"pad":"' + "x" * 5000 + '"}')
    expect_close(client, 1009)


def test_unknown_or_malformed_messages_are_ignored_and_the_connection_stays_open(wsweb):  # [khóa]
    worker = wsweb.worker()
    cookie = wsweb.login("alice")
    client = open_ws(wsweb, cookie)
    client.recv_json()
    for junk in ('{"type":"HELLO"}', "not json", "[]", '{"type":"PONG"}', '{"type":"PONG","seq":"1"}', "null"):
        client.send_text(junk)
    client.send_text('{"type":"PONG","seq":1}')
    wsweb.submit(cookie, uuid_token(1))
    finish_ac(worker)
    assert any(m["state"] == "judged" for m in statuses(
        collect(client, lambda ms: any(m.get("state") == "judged" for m in statuses(ms)))))
    client.close()


def test_a_frame_that_is_not_masked_is_refused_by_the_server_with_1002(wsweb):  # đối chứng cho client tự viết
    client = open_ws(wsweb, wsweb.login("alice"))
    client.recv_json()
    client.sock.sendall(encode_frame(0x1, b'{"type":"PONG","seq":1}', mask=False))
    expect_close(client, 1002)


# ---- PING, im lặng, dọn dẹp -----------------------------------------------------------------------------------------

def test_ping_arrives_on_schedule_with_increasing_seq_per_connection(make_web):  # [không khóa số]
    web = make_web(ws_ping_interval_s=0.15, ws_idle_timeout_s=30)
    populate(web.db)
    web.start()
    client = open_ws(web, web.login("alice"))
    pings = [m for m in collect(client, lambda ms: len([m for m in ms if m.get("type") == "PING"]) >= 3)
             if m.get("type") == "PING"]
    assert [p["seq"] for p in pings][:3] == [1, 2, 3]
    client.close()


def test_a_client_that_stays_silent_is_closed_with_4408(make_web):  # [không khóa]
    web = make_web(ws_ping_interval_s=0.1, ws_idle_timeout_s=0.5)
    populate(web.db)
    web.start()
    client = open_ws(web, web.login("alice"))
    deadline = time.monotonic() + 6
    code = None
    while time.monotonic() < deadline:  # không trả PONG: chỉ đọc
        kind, value = client.recv(timeout=0.5)
        if kind == "close":
            code = value
            break
        if kind == "eof":
            break
    assert code == 4408


def test_pong_keeps_the_connection_alive_past_the_idle_timeout(make_web):  # [không khóa]
    web = make_web(ws_ping_interval_s=0.1, ws_idle_timeout_s=0.6)
    populate(web.db)
    web.start()
    client = open_ws(web, web.login("alice"))
    messages = collect(client, lambda ms: False, timeout=1.8)  # tự trả PONG cho mỗi PING
    assert {"closed": 4408} not in messages and not any("closed" in m for m in messages)
    client.close()


def test_a_client_that_vanishes_without_a_close_frame_is_removed(wsweb):
    client = open_ws(wsweb, wsweb.login("alice"))
    client.recv_json()
    registry = wsweb.app.state.ws.registry
    assert wait_until(lambda: len(registry) == 1)
    client.sock.shutdown(socket.SHUT_RDWR)
    client.sock.close()
    assert wait_until(lambda: len(registry) == 0, timeout=5)


def test_many_connections_open_and_close_without_leaking(wsweb):
    cookie = wsweb.login("alice")
    registry = wsweb.app.state.ws.registry
    for _ in range(3):
        clients = [open_ws(wsweb, cookie) for _ in range(5)]
        for c in clients:
            c.recv_json()
        for c in clients:
            c.close()
        assert wait_until(lambda: len(registry) == 0, timeout=5)


def test_server_shutdown_closes_connected_clients_with_a_close_frame(make_web):
    web = make_web()
    populate(web.db)
    web.start()
    client = open_ws(web, web.login("alice"))
    client.recv_json()
    stopper = threading.Thread(target=web.stop)
    stopper.start()
    kind, value = client.recv(timeout=10)
    stopper.join(15)
    assert kind == "close" and value == 1012, (kind, value)  # uvicorn đóng 1012 (service restart) khi tắt; README 4.5
