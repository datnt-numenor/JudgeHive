"""State dùng chung của các route REST, dựng trong lifespan (sau khi dispatcher đã chạy) và đặt ở `app.state.rest`."""

import time
from dataclasses import dataclass
from typing import Any, Callable

from server import auth
from server.rest.limits import ConcurrencyGate, SlidingWindow


def system_clock_ms():
    return int(time.time() * 1000)


@dataclass
class RestState:
    cfg: Any
    db: Any
    dispatcher: Any
    hooks: Any
    clock: Callable[[], int]
    login_limiter: SlidingWindow
    login_gate: ConcurrencyGate
    dummy_hash: str  # băm cùng tham số với mật khẩu thật, để username không tồn tại tốn thời gian như username có thật


def build_state(cfg, db, dispatcher, hooks, clock):
    return RestState(cfg=cfg, db=db, dispatcher=dispatcher, hooks=hooks, clock=clock,
                     login_limiter=SlidingWindow(cfg.login_rate_limit, int(cfg.login_rate_window_s * 1000)),
                     login_gate=ConcurrencyGate(cfg.login_max_concurrent),
                     dummy_hash=auth.hash_password("dummy-password-for-timing"))
