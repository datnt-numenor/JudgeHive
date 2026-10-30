"""Cấu hình server: mặc định lấy từ `common.protocol`, đọc từ biến môi trường `JH_*`; test ghi đè từng field."""

import os
from dataclasses import dataclass

from common import protocol

DEFAULT_DB_PATH = "/data/judgehive.db"
DEFAULT_PROBLEMS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "problems")
DEFAULT_STATIC_DIR = "/app/static"  # nơi Dockerfile đặt giao diện web đã build; không có thư mục thì chỉ chạy API


class ConfigError(Exception):
    pass


@dataclass
class ServerConfig:
    bind_host: str = "0.0.0.0"
    worker_port: int = protocol.DEFAULT_WORKER_PORT
    secret: str = ""
    db_path: str = DEFAULT_DB_PATH
    problems_dir: str = DEFAULT_PROBLEMS_DIR
    static_dir: str = DEFAULT_STATIC_DIR
    ping_interval_s: float = protocol.PING_INTERVAL_S
    peer_dead_after_s: float = protocol.PEER_DEAD_AFTER_S
    send_timeout_s: float = protocol.SEND_TIMEOUT_S
    register_timeout_s: float = protocol.REGISTER_TIMEOUT_S
    max_unauthenticated: int = protocol.MAX_UNAUTHENTICATED
    compile_timeout_max_s: float = protocol.COMPILE_TIMEOUT_MAX_S
    slack_s: float = protocol.SLACK_S
    max_time_factor: float = protocol.MAX_TIME_FACTOR
    per_test_overhead_s: float = protocol.PER_TEST_OVERHEAD_S
    monitor_tick_s: float = 0.5  # [ĐỀ XUẤT] độ phân giải của quét last_seen, watchdog và lịch PING
    max_deliveries: int = 3  # D1, D22: số lần giao đã xác nhận bị cắt ngang
    max_total_deliveries: int = 6  # D22 [ĐỀ XUẤT]: số lần giao tính cả chưa xác nhận
    # REST (README mục 3.2); test thu nhỏ để chạy nhanh
    session_ttl_s: int = 7 * 24 * 3600
    submit_rate_limit: int = 10
    submit_rate_window_s: float = 60.0
    login_rate_limit: int = 10
    login_rate_window_s: float = 60.0
    login_max_concurrent: int = 8
    scoreboard_min_interval_s: float = 1.0  # bảng xếp hạng tính lại tối đa một lần mỗi khoảng này (README 3.3)
    # WebSocket (README mục 4); test thu nhỏ các khoảng thời gian
    allowed_origins: tuple = ()  # origin được phép ngoài "cùng host với header Host" (JH_ALLOWED_ORIGINS)
    ws_max_per_user: int = 10  # vượt thì đóng 4429
    ws_max_total: int = 400  # vượt thì đóng 1013
    ws_queue_size: int = 32  # hàng đợi gửi của mỗi kết nối; đầy thì đóng 1013
    ws_send_timeout_s: float = 5.0  # một lần gửi không xong trong khoảng này thì đóng 1013
    ws_close_timeout_s: float = 2.0  # chờ tối đa khi đóng một kết nối (kết nối chết có thể không trả lời)
    ws_ping_interval_s: float = 30.0  # chu kỳ PING ứng dụng và kiểm tra phiên
    ws_idle_timeout_s: float = 60.0  # im lặng quá khoảng này thì đóng 4408
    ws_max_message_bytes: int = 4096  # frame lớn hơn thì đóng 1009
    ws_max_frames: int = 20  # quá số frame này trong ws_frame_window_s giây thì đóng 1008
    ws_frame_window_s: float = 10.0
    ws_event_queue_size: int = 10000  # hàng đợi sự kiện STATUS từ các thread; đầy thì bỏ sự kiện và ghi log


def db_path_from_env(env):
    return env.get("JH_DB_PATH", DEFAULT_DB_PATH)


def problems_dir_from_env(env):
    return env.get("JH_PROBLEMS_DIR", DEFAULT_PROBLEMS_DIR)


def static_dir_from_env(env):
    return env.get("JH_STATIC_DIR", DEFAULT_STATIC_DIR)


def allowed_origins_from_env(env):
    raw = env.get("JH_ALLOWED_ORIGINS", "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def read_secret(env):
    """Secret ưu tiên `JH_WORKER_SECRET_FILE`, sau đó `JH_WORKER_SECRET`. Không bao giờ ghi giá trị vào log."""
    path = env.get("JH_WORKER_SECRET_FILE")
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                secret = f.read().strip()
        except OSError as e:
            raise ConfigError(f"không đọc được JH_WORKER_SECRET_FILE: {e.strerror}") from e
    else:
        secret = env.get("JH_WORKER_SECRET", "")
    if not secret:
        raise ConfigError("thiếu secret: đặt JH_WORKER_SECRET_FILE (ưu tiên) hoặc JH_WORKER_SECRET")
    return secret


def load_config(env=None):
    env = os.environ if env is None else env
    cfg = ServerConfig(secret=read_secret(env), db_path=db_path_from_env(env),
                       problems_dir=problems_dir_from_env(env), static_dir=static_dir_from_env(env),
                       allowed_origins=allowed_origins_from_env(env))
    try:
        cfg.worker_port = int(env.get("JH_WORKER_PORT", cfg.worker_port))
    except ValueError as e:
        raise ConfigError(f"JH_WORKER_PORT không phải số nguyên: {e}") from e
    cfg.bind_host = env.get("JH_BIND_HOST", cfg.bind_host)
    return cfg
