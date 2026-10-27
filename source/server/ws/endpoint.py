"""Handler `/ws` (README mục 4.1, 4.3). Thứ tự: kiểm Origin (trước `accept`, sai thì HTTP 403), tra phiên, `accept`, kiểm trần,
đăng ký, gửi SCOREBOARD đầu tiên, kiểm lại phiên một lần (thu hẹp cửa sổ race với logout), rồi vòng nhận."""

import asyncio
import json
import logging

from fastapi import WebSocket

from server.rest.sessions import COOKIE_NAME
from server.ws.connection import (CODE_BINARY, CODE_TOO_BIG, CODE_UNAUTHENTICATED, Connection)
from server.ws.events import parse_client_message
from server.ws.origin import origin_allowed

log = logging.getLogger("judgehive.ws")
MAX_LOGGED_IGNORED = 5  # mỗi kết nối chỉ ghi log vài message bị bỏ qua đầu tiên (client có thể gửi vô hạn)


async def ws_endpoint(websocket: WebSocket):
    hub = websocket.app.state.ws
    cfg = hub.cfg
    if not origin_allowed(websocket.headers.get("origin"), websocket.headers.get("host"), cfg.allowed_origins):
        await websocket.close(code=1008)  # trước accept: uvicorn trả HTTP 403 cho handshake
        return
    user = await hub.lookup_session(websocket.cookies.get(COOKIE_NAME))  # SQLite ở thread riêng, không trên event loop
    await websocket.accept()
    if user is None:
        await websocket.close(code=CODE_UNAUTHENTICATED)  # accept rồi đóng 4401: client phân biệt được "hết phiên"
        return
    refused = hub.registry.check_caps(user.id)
    if refused is not None:
        await websocket.close(code=refused)
        return
    conn = Connection(websocket, user.id, user.token_hash, loop=hub.loop, queue_size=cfg.ws_queue_size,
                      send_timeout_s=cfg.ws_send_timeout_s, close_timeout_s=cfg.ws_close_timeout_s)
    hub.registry.add(conn)  # ngay sau check_caps, không await ở giữa
    reader = None
    try:
        conn.start()
        text = hub.scoreboard_text()
        if text is not None:
            conn.offer_scoreboard(text)
        reader = hub.loop.create_task(_read_loop(conn, cfg.ws_max_message_bytes, hub.loop))
        # Logout có thể xảy ra giữa lúc tra phiên và lúc đăng ký: kiểm lại một lần.
        if user.token_hash not in await hub.live_sessions({user.token_hash}):
            conn.close_soon(CODE_UNAUTHENTICATED)
        await conn.closed.wait()
    finally:
        hub.registry.remove(conn)
        if reader is not None:
            reader.cancel()
            await asyncio.wait({reader})
        await conn.stop()
        if not conn.closed.is_set():
            await conn.close(1001)


async def _read_loop(conn, max_bytes, loop):
    ws = conn.ws
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            conn.last_received = loop.time()  # mọi frame, kể cả frame bị bỏ qua, chứng tỏ kết nối còn sống
            if message.get("bytes") is not None:
                await conn.close(CODE_BINARY)
                return
            text = message.get("text")
            if text is None:
                continue
            if len(text.encode("utf-8")) > max_bytes:
                await conn.close(CODE_TOO_BIG)
                return
            if parse_client_message(text) != "pong":
                conn.ignored_messages += 1
                if conn.ignored_messages <= MAX_LOGGED_IGNORED:
                    log.info("kết nối %s (user %s): bỏ qua message của client", conn.id, conn.user_id)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001  (client đi đột ngột: không phải lỗi của server)
        log.debug("kết nối %s: lỗi khi nhận (%r)", conn.id, e)
    conn.mark_gone()
