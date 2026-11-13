"""Chỉ gửi khi dữ liệu thay đổi, không gửi message kiểm tra kết nối."""
import asyncio


class Hub:
    def __init__(self, db):
        self.db = db
        self.clients = {}  # WebSocket -> hash session

    async def publish(self, submission_id=None):
        submission = self.db.one("SELECT id,user_id,state,attempt_id,tests_done,verdict,time_ms,message "
                                 "FROM submissions WHERE id=?", (submission_id,)) if submission_id else None
        board = self.db.scoreboard()

        async def deliver(ws, hashed):
            try:
                user = self.db.user_for_token(hashed)
                if not user:
                    await ws.close(code=1008)
                    self.clients.pop(ws, None)
                    return
                async with asyncio.timeout(2):
                    if submission and (user["id"] == submission["user_id"] or user["role"] == "admin"):
                        await ws.send_json({"type": "submission", "data": submission})
                    await ws.send_json({"type": "scoreboard", "data": board})
                    if user["role"] == "admin":
                        await ws.send_json({"type": "workers_changed"})
            except Exception:
                self.clients.pop(ws, None)
                # Không để một trình duyệt chậm cản worker khác.
                try:
                    await asyncio.wait_for(ws.close(), 0.2)
                except Exception:
                    pass

        await asyncio.gather(*(deliver(ws, hashed) for ws, hashed in list(self.clients.items())))

    async def revoke(self, user_id):
        for ws, hashed in list(self.clients.items()):
            user = self.db.user_for_token(hashed)
            if not user or user["id"] == user_id:
                self.clients.pop(ws, None)
                try:
                    await asyncio.wait_for(ws.close(code=1008), 1)
                except Exception:
                    pass
