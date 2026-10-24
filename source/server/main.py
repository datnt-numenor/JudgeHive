"""FastAPI app. Mọi thứ khởi động trong lifespan (PLAN mục 2.1), không phải lúc import.

Thứ tự: lưu event loop; migrate DB; nạp seed `problems/` (D5); nạp lại hàng đợi từ DB; (PR 7: tính bảng xếp hạng đầu
tiên); rồi mới mở cổng worker và dispatcher. Teardown: dừng cổng worker (đóng listener và socket) rồi dispatcher.
Chạy đúng một process uvicorn (`--workers 1`): hàng đợi và danh sách worker nằm trong bộ nhớ."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from server import config as config_module
from server import problems
from server.db import Database
from server.dispatcher import Dispatcher
from server.hooks import LoopHooks
from server.worker_server import WorkerServer

log = logging.getLogger("judgehive.main")


def _setup_logging():
    logging.basicConfig(level=os.environ.get("JH_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(cfg=None):
    """`cfg`: ServerConfig có sẵn (test); None thì đọc từ biến môi trường lúc lifespan chạy."""

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
        # PR 7: tính bản bảng xếp hạng đầu tiên ở đây (asyncio.to_thread, D12)
        server.start()
        steps.append("worker_port")
        dispatcher.start()
        steps.append("dispatcher")
        app.state.db, app.state.worker_server, app.state.dispatcher = db, server, dispatcher
        try:
            yield
        finally:
            def shutdown():
                server.stop()  # trước: đánh thức dispatcher đang chờ worker rảnh
                dispatcher.stop()

            await asyncio.to_thread(shutdown)
            steps.append("stopped")

    app = FastAPI(lifespan=lifespan)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
