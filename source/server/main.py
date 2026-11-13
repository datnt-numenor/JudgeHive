import asyncio
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from common.protocol import MAX_LINE
from server.auth import hash_password, verify_password, token_hash
from server.db import Database, now
from server.dispatch import Engine
from server.realtime import Hub


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Login(Model):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(max_length=128)


class NewUser(Login):
    role: str = "user"


class EditUser(Model):
    disabled: bool | None = None
    password: str | None = Field(default=None, max_length=128)


class Case(Model):
    input: str
    output: str
    is_sample: bool = False


class Problem(Model):
    title: str = Field(min_length=1, max_length=160)
    statement: str = Field(min_length=1, max_length=20000)
    time_limit_ms: int = Field(default=2000, ge=100, le=10000)
    memory_limit_kib: int = Field(default=262144, ge=65536, le=524288)
    tests: list[Case] = Field(min_length=1, max_length=20)


class Visibility(Model):
    hidden: bool


class Submission(Model):
    problem_id: int
    language: str
    code: str = Field(min_length=1, max_length=65536)


async def current_user(request: Request):
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
        listener = await asyncio.start_server(engine.handle, os.environ.get("JH_TCP_HOST", "0.0.0.0"),
                                              worker_port if worker_port is not None else int(os.environ.get("JH_WORKER_PORT", "9000")),
                                              limit=MAX_LINE)
        app.state.worker_port = listener.sockets[0].getsockname()[1]
        try:
            yield
        finally:
            listener.close()
            await listener.wait_closed()
            await engine.stop()
            for ws in list(hub.clients):
                await ws.close(code=1001)
            db.conn.close()

    app = FastAPI(lifespan=lifespan)

    @app.middleware("http")
    async def body_and_origin(request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
                return JSONResponse({"detail": "Origin không hợp lệ"}, status_code=403)
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > 8 * 1024 * 1024:
                    return JSONResponse({"detail": "Request quá lớn"}, status_code=413)
            request._body = bytes(data)
        return await call_next(request)

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    @app.post("/api/auth/login")
    async def login(body: Login, request: Request, response: Response):
        db = app.state.db
        user = db.one("SELECT * FROM users WHERE username=?", (body.username,))
        if not user or not await asyncio.to_thread(verify_password, body.password, user["password_hash"]):
            raise HTTPException(401, "Sai tên đăng nhập hoặc mật khẩu")
        # Trong lúc băm mật khẩu, admin có thể khóa/đổi mật khẩu tài khoản.
        fresh = db.one("SELECT * FROM users WHERE id=?", (user["id"],))
        if fresh["disabled"] or fresh["password_hash"] != user["password_hash"]:
            raise HTTPException(401, "Tài khoản không khả dụng")
        token = secrets.token_urlsafe(32)
        db.execute("DELETE FROM sessions WHERE expires_at<=?", (now(),))
        db.execute("INSERT INTO sessions VALUES(?,?,?)", (token_hash(token), user["id"], now() + 86400000))
        response.set_cookie("session", token, httponly=True, samesite="strict", max_age=86400,
                            secure=request.url.scheme == "https")
        return {k: user[k] for k in ("id", "username", "role")}

    @app.post("/api/auth/logout")
    async def logout(request: Request, response: Response):
        hashed = token_hash(request.cookies.get("session", ""))
        app.state.db.execute("DELETE FROM sessions WHERE token_hash=?", (hashed,))
        response.delete_cookie("session")
        await app.state.hub.revoke(-1)
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
        return app.state.db.all("SELECT * FROM problems" + ("" if user["role"] == "admin" else " WHERE hidden=0") + " ORDER BY id")

    @app.get("/api/problems/{pid}")
    async def problem(pid: int, user=Depends(current_user)):
        row = get_problem(pid, user)
        row["samples"] = app.state.db.all("SELECT input,output FROM tests WHERE problem_id=? AND is_sample=1 ORDER BY position", (pid,))
        return row

    @app.post("/api/submissions", status_code=201)
    async def submit(body: Submission, user=Depends(current_user)):
        get_problem(body.problem_id, user)
        if body.language not in {"python", "cpp", "java"} or len(body.code.encode()) > 65536:
            raise HTTPException(400, "Ngôn ngữ không hợp lệ hoặc code quá 64 KiB")
        sid = app.state.db.execute("INSERT INTO submissions(user_id,problem_id,language,code,submitted_at) VALUES(?,?,?,?,?)",
                                   (user["id"], body.problem_id, body.language, body.code, now()))
        app.state.engine.queue.put_nowait(sid)
        await app.state.hub.publish(sid)
        return {"id": sid}

    @app.get("/api/submissions")
    async def submissions(user=Depends(current_user), before: int = 9223372036854775807):
        return app.state.db.all("SELECT id,problem_id,language,state,verdict,submitted_at FROM submissions "
                                "WHERE user_id=? AND id<? ORDER BY id DESC LIMIT 100", (user["id"], before))

    @app.get("/api/submissions/{sid}")
    async def submission(sid: int, user=Depends(current_user)):
        row = app.state.db.one("SELECT * FROM submissions WHERE id=?", (sid,))
        if not row or (row["user_id"] != user["id"] and user["role"] != "admin"):
            raise HTTPException(404, "Không tìm thấy bài nộp")
        return row

    @app.get("/api/scoreboard")
    async def scoreboard(user=Depends(current_user)):
        return app.state.db.scoreboard()

    def validate_problem(body):
        size = sum(len(t.input.encode()) + len(t.output.encode()) for t in body.tests)
        if size > 1024 * 1024:
            raise HTTPException(400, "Tổng dữ liệu test quá 1 MiB")

    def save_problem(body, pid=None):
        db = app.state.db
        validate_problem(body)
        with db.conn:
            values = (body.title, body.statement, body.time_limit_ms, body.memory_limit_kib)
            if pid is None:
                pid = db.conn.execute("INSERT INTO problems(title,statement,time_limit_ms,memory_limit_kib) VALUES(?,?,?,?)", values).lastrowid
            else:
                db.conn.execute("UPDATE problems SET title=?,statement=?,time_limit_ms=?,memory_limit_kib=? WHERE id=?", (*values, pid))
                db.conn.execute("DELETE FROM tests WHERE problem_id=?", (pid,))
            db.conn.executemany("INSERT INTO tests(problem_id,position,input,output,is_sample) VALUES(?,?,?,?,?)",
                                [(pid, i, t.input, t.output, t.is_sample) for i, t in enumerate(body.tests)])
        return {"id": pid}

    @app.post("/api/admin/problems", status_code=201)
    async def add_problem(body: Problem, user=Depends(admin_user)):
        return save_problem(body)

    @app.get("/api/admin/problems/{pid}")
    async def admin_problem(pid: int, user=Depends(admin_user)):
        row = get_problem(pid, user)
        row["tests"] = app.state.db.all("SELECT input,output,is_sample FROM tests WHERE problem_id=? ORDER BY position", (pid,))
        for case in row["tests"]:
            case["is_sample"] = bool(case["is_sample"])
        row["locked"] = bool(app.state.db.one("SELECT id FROM submissions WHERE problem_id=? LIMIT 1", (pid,)))
        return row

    @app.put("/api/admin/problems/{pid}")
    async def edit_problem(pid: int, body: Problem, user=Depends(admin_user)):
        get_problem(pid, user)
        if app.state.db.one("SELECT id FROM submissions WHERE problem_id=? LIMIT 1", (pid,)):
            raise HTTPException(409, "Đề đã có bài nộp; hãy tạo đề mới")
        return save_problem(body, pid)

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
        if app.state.db.one("SELECT id FROM users WHERE username=?", (body.username,)):
            raise HTTPException(409, "Tên tài khoản đã tồn tại")
        try:
            hashed = await asyncio.to_thread(hash_password, body.password)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        # Kiểm tra lại sau await, tránh hai request tạo cùng tên.
        if app.state.db.one("SELECT id FROM users WHERE username=?", (body.username,)):
            raise HTTPException(409, "Tên tài khoản đã tồn tại")
        uid = app.state.db.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)", (body.username, hashed, body.role))
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
        if hashed or body.disabled:
            await app.state.hub.revoke(uid)
        await app.state.hub.publish()
        return {"ok": True}

    @app.get("/api/admin/workers")
    async def workers(user=Depends(admin_user)):
        return {"workers": app.state.engine.view(), "queue": app.state.engine.queue.qsize()}

    @app.get("/api/admin/submissions")
    async def admin_submissions(user=Depends(admin_user), before: int = 9223372036854775807):
        return app.state.db.all("SELECT s.id,s.user_id,u.username,s.problem_id,s.language,s.state,s.verdict,s.submitted_at "
                                "FROM submissions s JOIN users u ON u.id=s.user_id WHERE s.id<? ORDER BY s.id DESC LIMIT 100", (before,))

    @app.websocket("/ws")
    async def websocket(ws: WebSocket):
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
        if path.startswith(("api/", "ws", "assets/")) or not (static / "index.html").is_file():
            raise HTTPException(404, "Không tìm thấy")
        return FileResponse(static / "index.html")

    return app


if __name__ == "__main__":
    import uvicorn
    # websockets backend; None tắt ping tự động của thư viện.
    uvicorn.run(create_app(), host="0.0.0.0", port=8000, workers=1,
                ws="websockets", ws_ping_interval=None, ws_ping_timeout=None)
