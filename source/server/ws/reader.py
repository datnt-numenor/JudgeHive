"""Vòng nhận frame của client (README mục 4.3), tách khỏi `endpoint.py` (cần FastAPI) để test được bằng websocket giả.

Quy tắc: chỉ `PONG` hợp lệ có nghĩa; mọi frame (kể cả frame bị bỏ qua) làm mới mốc im lặng; frame nhị phân đóng 1003; frame văn bản
dài quá `max_bytes` đóng 1009 (phòng thủ thứ hai: uvicorn đã chặn bằng `--ws-max-size`); message khác bị bỏ qua và ghi log vài lần đầu."""

import asyncio
import logging

from server.ws.connection import CODE_BINARY, CODE_TOO_BIG
from server.ws.events import parse_client_message

log = logging.getLogger("judgehive.ws")
MAX_LOGGED_IGNORED = 5  # mỗi kết nối chỉ ghi log vài message bị bỏ qua đầu tiên (client có thể gửi vô hạn)


async def read_loop(conn, max_bytes, loop):
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
