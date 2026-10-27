"""Handler `/ws` (README mục 4.1, 4.3). Thứ tự: kiểm Origin (trước `accept`, sai thì HTTP 403), tra phiên, `accept`, kiểm trần,
đăng ký, gửi SCOREBOARD đầu tiên, kiểm lại phiên một lần (thu hẹp cửa sổ race với logout), rồi vòng nhận."""

import asyncio
import logging

from fastapi import WebSocket

from server.rest.sessions import COOKIE_NAME
from server.ws.connection import CODE_TRY_AGAIN_LATER, CODE_UNAUTHENTICATED, Connection
from server.ws.origin import origin_allowed
from server.ws.reader import read_loop

log = logging.getLogger("judgehive.ws")


async def ws_endpoint(websocket: WebSocket):
    hub = websocket.app.state.ws
    cfg = hub.cfg
    if hub.stopped:  # đang tắt server: không nhận kết nối mới (executor ws-db đã đóng)
        await websocket.close(code=CODE_TRY_AGAIN_LATER)
        return
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
        reader = hub.loop.create_task(read_loop(conn, cfg.ws_max_message_bytes, hub.loop))
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
