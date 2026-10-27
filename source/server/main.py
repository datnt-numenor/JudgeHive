"""FastAPI app. Mọi thứ khởi động trong lifespan (PLAN mục 2.1), không phải lúc import.

Thứ tự: lưu event loop; migrate DB; nạp seed `problems/` (D5); nạp lại hàng đợi từ DB; tính bảng xếp hạng đầu tiên; rồi
mới mở cổng worker và dispatcher. Teardown: dừng cổng worker (đóng listener và socket), dispatcher, rồi bảng xếp hạng;
bảng xếp hạng luôn được dừng, kể cả khi khởi động lỗi sau bước tính bản đầu hoặc khi dừng cổng worker/dispatcher lỗi.
Chạy đúng một process uvicorn (`--workers 1`): hàng đợi và danh sách worker nằm trong bộ nhớ."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from server import config as config_module
from server import problems, rest, static
from server.db import Database
from server.dispatcher import Dispatcher
from server.hooks import LoopHooks
from server.rest.state import build_state, system_clock_ms
from server.scoreboard.service import ScoreboardService
from server.worker_server import WorkerServer

log = logging.getLogger("judgehive.main")


def _setup_logging():
    logging.basicConfig(level=os.environ.get("JH_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(cfg=None, clock=None):
    """`cfg`: ServerConfig có sẵn (test); None thì đọc từ biến môi trường lúc lifespan chạy. `clock`: hàm trả thời
    gian hiện tại (ms) cho phiên và rate limit của REST; test truyền đồng hồ giả, mặc định là đồng hồ hệ thống."""

    @asynccontextmanager
    async def lifespan(app):
        _setup_logging()
        config = cfg if cfg is not None else config_module.load_config()
        steps = app.state.startup_steps = []  # thứ tự khởi động thật, để test kiểm chứng
        app.state.cfg = config
        loop = app.state.loop = asyncio.get_running_loop()
        steps.append("loop")

        db = Database(config.db_path)

        async def in_thread(fn, *args):
            def run():
                try:
                    return fn(*args)
                finally:
                    db.close_thread()  # thread của executor sống lâu hơn lời gọi, không giữ kết nối

            return await asyncio.to_thread(run)

        await in_thread(db.migrate)
        steps.append("migrate")
        await in_thread(problems.seed_problems, db, config.problems_dir)
        steps.append("seed")
        hooks = LoopHooks(loop)
        server = WorkerServer(config, db, hooks)
        dispatcher = Dispatcher(config, db, server, hooks)
        server.set_handler(dispatcher)
        await in_thread(dispatcher.load_pending)
        steps.append("reload_queue")
        # Bản bảng xếp hạng đầu tiên (phiên bản 1) tính xong trước khi mở cổng worker (PLAN 2.1, D12); lỗi thì server
        # không khởi động. Truy vấn chạy trong thread riêng của dịch vụ, không trên event loop.
        scoreboard = ScoreboardService(db, loop=loop, clock=clock or system_clock_ms,
                                       min_interval_s=config.scoreboard_min_interval_s)
        try:
            await scoreboard.start()
        except BaseException:
            await scoreboard.stop()
            raise
        hooks.bind_scoreboard(scoreboard)
        app.state.scoreboard = scoreboard
        steps.append("scoreboard")
        try:
            server.start()
            steps.append("worker_port")
            dispatcher.start()
            steps.append("dispatcher")
            app.state.db, app.state.worker_server, app.state.dispatcher = db, server, dispatcher
            app.state.hooks = hooks
            # Băm mật khẩu giả (tốn khoảng chục mili giây) nên chạy trong thread, không chặn event loop.
            app.state.rest = await asyncio.to_thread(build_state, config, db, dispatcher, hooks,
                                                     clock or system_clock_ms)
            steps.append("rest")
            yield
        finally:
            def shutdown():
                # Dừng một thành phần chưa khởi động (khởi động lỗi giữa chừng) là vô hại.
                server.stop()  # trước: đánh thức dispatcher đang chờ worker rảnh
                dispatcher.stop()

            try:
                await asyncio.to_thread(shutdown)
            finally:
                await scoreboard.stop()  # sau cổng worker và dispatcher: hết nguồn gọi mark_dirty
                steps.append("stopped")

    # Không có /docs, /redoc, /openapi.json (công khai, lộ danh sách route) và không tự chuyển hướng dấu "/" cuối
    # (README 3.2: đường dẫn không tồn tại là 404 JSON).
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    rest.install(app)
    # Giao diện web (nếu có thư mục build): `/assets` và fallback SPA trong bộ xử lý 404 của REST; không route nào khớp "/".
    static.install(app, cfg.static_dir if cfg is not None else config_module.static_dir_from_env(os.environ))
    return app


app = create_app()
