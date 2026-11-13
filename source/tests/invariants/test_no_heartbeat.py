"""Khóa: không có message định kỳ nào (không PING/PONG, không keepalive) trên TCP worker và WebSocket."""
import asyncio
import inspect
import socket

import server.main
from common.protocol import TYPES
from _support import register, silent_for, system


def test_protocol_has_no_heartbeat_messages():
    assert TYPES == {"REGISTER", "REGISTER_OK", "JOB", "PROGRESS", "RESULT", "ERROR"}


def test_idle_worker_connection_stays_silent_and_open(tmp_path):
    with system(tmp_path) as (client, app):
        async def scenario():
            reader, writer, ack = await register(app)
            assert ack["type"] == "REGISTER_OK"
            assert await silent_for(reader, 1.5) is True
            assert app.state.engine.view()[0]["state"] == "idle"
            writer.close()
        client.portal.call(scenario)


def test_server_does_not_enable_tcp_keepalive(tmp_path):
    with system(tmp_path) as (client, app):
        async def scenario():
            reader, writer, _ = await register(app)
            await asyncio.sleep(0.1)
            sockets = [w["writer"].get_extra_info("socket") for w in app.state.engine.workers.values()]
            assert sockets and all(s.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 0 for s in sockets)
            writer.close()
        client.portal.call(scenario)


def test_uvicorn_websocket_ping_disabled():
    source = inspect.getsource(server.main)
    assert "ws_ping_interval=None" in source and "ws_ping_timeout=None" in source
