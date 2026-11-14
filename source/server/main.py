"""App FastAPI: REST, WebSocket /ws, mở cổng TCP cho worker trong lifespan, phục vụ client đã build."""
import asyncio
import os
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from common.protocol import MAX_LINE_TO_SERVER
from server.auth import hash_password, verify_password, token_hash
from server.db import Database, now
from server.dispatch import Engine
from server.problems import Model
from server.realtime import Hub

MAX_PENDING_PER_USER = 3


class Login(Model):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(max_length=128)


class NewUser(Login):
    role: str = "user"


class EditUser(Model):
    disabled: bool | None = None
    password: str | None = Field(default=None, max_length=128)


class Visibility(Model):
    hidden: bool


class Submission(Model):
    problem_id: int
    language: str
    code: str = Field(min_length=1, max_length=65536)


async def current_user(request: Request):
    # Quyền lấy từ DB theo token phiên, không tin role/user_id do client gửi.
    hashed = token_hash(request.cookies.get("session", ""))
    user = request.app.state.db.user_for_token(hashed)
    if not user:
        raise HTTPException(401, "Cần đăng nhập")
    return user


async def admin_user(user=Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Cần quyền admin")
    return user


def create_app(db_path=None, secret=None, worker_port=None):
    @asynccontextmanager
    async def lifespan(app):
        shared = secret if secret is not None else os.environ.get("JH_WORKER_SECRET", "")
        if len(shared) < 16:
            raise RuntimeError("JH_WORKER_SECRET cần ít nhất 16 ký tự")
        db = Database(db_path or os.environ.get("JH_DB", "/data/judgehive.sqlite3"))
        hub = Hub(db)
        engine = Engine(db, hub, shared)
        engine.recover()
        app.state.db, app.state.hub, app.state.engine = db, hub, engine
        # Cổng worker chạy chung event loop với web. limit = giới hạn dòng chiều vào server,
        # nên buffer của mỗi kết nối không vượt 64 KiB kể cả trước khi worker xác thực.
        listener = await asyncio.start_server(engine.handle, os.environ.get("JH_TCP_HOST", "0.0.0.0"),
                                              worker_port if worker_port is not None else int(os.environ.get("JH_WORKER_PORT", "9000")),
                                              limit=MAX_LINE_TO_SERVER)
        app.state.worker_port = listener.sockets[0].getsockname()[1]
        try:
            yield
        finally:
            listener.close()
            # Dừng các kết nối worker trước: từ Python 3.12.1 wait_closed chờ mọi kết nối đóng.
            await engine.stop()
            await listener.wait_closed()
            for ws in list(hub.clients):
                await ws.close(code=1001)
            db.conn.close()

    app = FastAPI(lifespan=lifespan)

    @app.middleware("http")
    async def same_origin_writes(request, call_next):
        # Request ghi từ trang web khác (CSRF) bị chặn; cookie SameSite=strict là lớp thứ hai.
        origin = request.headers.get("origin")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin and origin != f"{request.url.scheme}://{request.url.netloc}":
            return JSONResponse({"detail": "Origin không hợp lệ"}, status_code=403)
        return await call_next(request)

    @app.post("/api/auth/login")
    async def login(body: Login, request: Request, response: Response):
        db = app.state.db
        user = db.one("SELECT * FROM users WHERE username=?", (body.username,))
        # scrypt tốn vài chục ms CPU: chạy ở thread để không chặn event loop (và cổng worker).
        if not user or user["disabled"] or not await asyncio.to_thread(verify_password, body.password, user["password_hash"]):
            raise HTTPException(401, "Sai tên đăng nhập hoặc mật khẩu")
        token = secrets.token_urlsafe(32)
        db.execute("DELETE FROM sessions WHERE expires_at<=?", (now(),))
        # DB chỉ lưu SHA-256 của token: lộ DB cũng không dùng được phiên.
        db.execute("INSERT INTO sessions VALUES(?,?,?)", (token_hash(token), user["id"], now() + 86400000))
        response.set_cookie("session", token, httponly=True, samesite="strict", max_age=86400,
                            secure=request.url.scheme == "https")
        return {k: user[k] for k in ("id", "username", "role")}

    @app.post("/api/auth/logout")
    async def logout(request: Request, response: Response):
        hashed = token_hash(request.cookies.get("session", ""))
        app.state.db.execute("DELETE FROM sessions WHERE token_hash=?", (hashed,))
        response.delete_cookie("session")
        return {"ok": True}

    @app.get("/api/auth/me")
    async def me(user=Depends(current_user)):
        return user

    def get_problem(pid, user):
        row = app.state.db.one("SELECT * FROM problems WHERE id=?", (pid,))
        if not row or (row["hidden"] and user["role"] != "admin"):
            raise HTTPException(404, "Không tìm thấy đề")
        return row

    @app.get("/api/problems")
    async def problems(user=Depends(current_user)):
        # Một câu SQL cho cả danh sách: status chỉ tính bài của chính người đang đăng nhập;
        # ac_rate = số bài AC / số bài đã chấm xong của đề (mọi người), chưa có bài thì NULL.
        return app.state.db.all(
            "SELECT p.*,"
            " CASE WHEN SUM(s.user_id=:uid AND s.verdict='AC') > 0 THEN 'solved'"
            "      WHEN SUM(s.user_id=:uid) > 0 THEN 'attempted' END AS status,"
            " CAST(SUM(s.state='done' AND s.verdict='AC') AS REAL) / NULLIF(SUM(s.state='done'), 0) AS ac_rate"
            " FROM problems p LEFT JOIN submissions s ON s.problem_id=p.id"
            + ("" if user["role"] == "admin" else " WHERE p.hidden=0")
            + " GROUP BY p.id ORDER BY p.id",
            {"uid": user["id"]})

    @app.get("/api/problems/{pid}")
    async def problem(pid: int, user=Depends(current_user)):
        row = get_problem(pid, user)
        # Chỉ test mẫu; test ẩn không bao giờ rời server ngoài JOB gửi worker.
        row["samples"] = app.state.db.all("SELECT input,output FROM tests WHERE problem_id=? AND is_sample=1 ORDER BY position", (pid,))
        return row

    @app.post("/api/submissions", status_code=201)
    async def submit(body: Submission, user=Depends(current_user)):
        get_problem(body.problem_id, user)
        if body.language not in {"python", "cpp", "java"} or len(body.code.encode()) > 65536:
            raise HTTPException(400, "Ngôn ngữ không hợp lệ hoặc code quá 64 KiB")
        pending = app.state.db.one("SELECT COUNT(*) AS n FROM submissions WHERE user_id=? AND state IN ('queued','judging')",
                                   (user["id"],))["n"]
        if pending >= MAX_PENDING_PER_USER:
            raise HTTPException(429, f"Bạn đang có {pending} bài chờ chấm; hãy đợi có kết quả rồi nộp tiếp")
        sid = app.state.db.execute("INSERT INTO submissions(user_id,problem_id,language,code,submitted_at) VALUES(?,?,?,?,?)",
                                   (user["id"], body.problem_id, body.language, body.code, now()))
        app.state.engine.queue.put_nowait(sid)
        await app.state.hub.publish(sid)
        return {"id": sid}

    @app.get("/api/submissions")
    async def submissions(user=Depends(current_user)):
        return app.state.db.all("SELECT id,problem_id,language,state,verdict,submitted_at FROM submissions "
                                "WHERE user_id=? ORDER BY id DESC LIMIT 100", (user["id"],))

    @app.get("/api/submissions/{sid}")
    async def submission(sid: int, user=Depends(current_user)):
        row = app.state.db.one("SELECT * FROM submissions WHERE id=?", (sid,))
        if not row or (row["user_id"] != user["id"] and user["role"] != "admin"):
            raise HTTPException(404, "Không tìm thấy bài nộp")
        return row

    @app.get("/api/scoreboard")
    async def scoreboard(user=Depends(current_user)):
        return app.state.db.scoreboard()

    @app.patch("/api/admin/problems/{pid}")
    async def visibility(pid: int, body: Visibility, user=Depends(admin_user)):
        get_problem(pid, user)
        app.state.db.execute("UPDATE problems SET hidden=? WHERE id=?", (body.hidden, pid))
        await app.state.hub.publish()
        return {"ok": True}

    @app.get("/api/admin/users")
    async def users(user=Depends(admin_user)):
        return app.state.db.all("SELECT id,username,role,disabled FROM users ORDER BY id")

    @app.post("/api/admin/users", status_code=201)
    async def add_user(body: NewUser, user=Depends(admin_user)):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", body.username) or body.role not in {"user", "admin"}:
            raise HTTPException(400, "Tên tài khoản hoặc vai trò không hợp lệ")
        try:
            hashed = await asyncio.to_thread(hash_password, body.password)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        try:
            # Cột username là UNIQUE: hai request cùng tên thì request sau nhận 409.
            uid = app.state.db.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)", (body.username, hashed, body.role))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Tên tài khoản đã tồn tại")
        return {"id": uid}

    @app.patch("/api/admin/users/{uid}")
    async def edit_user(uid: int, body: EditUser, user=Depends(admin_user)):
        if not app.state.db.one("SELECT id FROM users WHERE id=?", (uid,)):
            raise HTTPException(404, "Không tìm thấy tài khoản")
        if body.disabled and uid == user["id"]:
            raise HTTPException(400, "Không tự khóa tài khoản đang dùng")
        hashed = None
        if body.password is not None:
            try:
                hashed = await asyncio.to_thread(hash_password, body.password)
            except ValueError as exc:
                raise HTTPException(400, str(exc))
        with app.state.db.conn:
            if hashed:
                app.state.db.conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hashed, uid))
            if body.disabled is not None:
                app.state.db.conn.execute("UPDATE users SET disabled=? WHERE id=?", (body.disabled, uid))
            if hashed or body.disabled:
                app.state.db.conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        # publish kiểm lại token của mọi WebSocket nên phiên vừa bị hủy cũng bị đóng tại đây.
        await app.state.hub.publish()
        return {"ok": True}

    @app.get("/api/admin/workers")
    async def workers(user=Depends(admin_user)):
        return {"workers": app.state.engine.view(), "queue": app.state.engine.queue.qsize()}

    @app.get("/api/admin/submissions")
    async def admin_submissions(user=Depends(admin_user)):
        return app.state.db.all("SELECT s.id,s.user_id,u.username,s.problem_id,s.language,s.state,s.verdict,s.submitted_at "
                                "FROM submissions s JOIN users u ON u.id=s.user_id ORDER BY s.id DESC LIMIT 100")

    @app.websocket("/ws")
    async def websocket(ws: WebSocket):
        # Trình duyệt luôn gửi Origin khi mở WebSocket; trang web khác không được mở bằng cookie của người dùng.
        origin = ws.headers.get("origin")
        expected = ("https" if ws.url.scheme == "wss" else "http") + "://" + ws.url.netloc
        hashed = token_hash(ws.cookies.get("session", ""))
        if (origin and origin != expected) or not app.state.db.user_for_token(hashed):
            await ws.close(code=1008)
            return
        await ws.accept()
        app.state.hub.clients[ws] = hashed
        try:
            # Chỉ chờ EOF. Client không cần gửi bất kỳ message định kỳ nào.
            while True:
                await ws.receive_text()
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            app.state.hub.clients.pop(ws, None)

    static = Path(os.environ.get("JH_STATIC", str(Path(__file__).parents[1] / "client" / "dist")))
    if (static / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/{path:path}")
    async def frontend(path: str):
        # React Router xử lý đường dẫn ở trình duyệt: F5 ở /submissions/3 vẫn phải trả index.html.
        if path.startswith(("api/", "ws", "assets/")) or not (static / "index.html").is_file():
            raise HTTPException(404, "Không tìm thấy")
        return FileResponse(static / "index.html")

    return app


if __name__ == "__main__":
    import uvicorn
    # Một process uvicorn (hàng đợi và kết nối worker nằm trong bộ nhớ). websockets backend;
    # None tắt ping tự động của thư viện: hệ thống không có heartbeat.
    uvicorn.run(create_app(), host="0.0.0.0", port=8000, workers=1,
                ws="websockets", ws_ping_interval=None, ws_ping_timeout=None)
