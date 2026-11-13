"""WebSocket chỉ báo "có thay đổi" (không kèm dữ liệu); client nhận rồi tự tải lại bằng REST,
nơi đã xét quyền. Chỉ gửi khi dữ liệu thay đổi, không gửi message kiểm tra kết nối."""
import asyncio
import contextlib


class Hub:
    def __init__(self, db):
        self.db = db
        self.clients = {}  # WebSocket -> hash token phiên

    async def publish(self, submission_id=None):
        owner = self.db.one("SELECT user_id FROM submissions WHERE id=?", (submission_id,)) if submission_id else None

        async def deliver(ws, hashed):
            # Kiểm lại token mỗi lần gửi: phiên đã đăng xuất hoặc bị khóa thì đóng socket, không nhận thêm gì.
            user = self.db.user_for_token(hashed)
            try:
                if not user:
                    raise PermissionError("Phiên không còn hợp lệ")
                async with asyncio.timeout(2):
                    if owner and (user["id"] == owner["user_id"] or user["role"] == "admin"):
                        await ws.send_json({"type": "submission", "id": submission_id})
                    await ws.send_json({"type": "scoreboard"})
                    if user["role"] == "admin":
                        await ws.send_json({"type": "workers_changed"})
            except Exception:
                self.clients.pop(ws, None)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(ws.close(code=1008), 0.2)

        # Gửi song song cho mọi trình duyệt. Một trình duyệt chậm không cản các worker khác,
        # nhưng làm lời gọi publish này (và worker đang gọi nó) chờ tối đa 2 giây.
        await asyncio.gather(*(deliver(ws, hashed) for ws, hashed in list(self.clients.items())))
