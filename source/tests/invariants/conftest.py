import os
import shutil
import tempfile
import threading

import pytest

from _worker_contract_helpers import MockServer
from worker.main import Config, Worker


@pytest.fixture
def work_root():
    """Thư mục làm việc như /work ở production: uid thí sinh duyệt vào được và không nằm trong /tmp."""
    path = tempfile.mkdtemp(prefix="jh-test-work-", dir="/srv")
    os.chmod(path, 0o755)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def server():
    s = MockServer()
    yield s
    s.close()


@pytest.fixture
def make_worker(server, work_root):
    """Tạo và chạy một Worker trong thread, trỏ vào mock server. Tự dừng khi test xong.

    Chỉ đặt các field Config trong README worker, mục "Dùng bởi test đã khóa": host, port, secret, name, work_root,
    backoff_base_s, backoff_cap_s, jitter (test ghi đè được qua `overrides`)."""
    started = []

    def make(grade_fn=None, *, sleep=None, **overrides):
        cfg = Config(host="127.0.0.1", port=server.port, secret="test-secret", name="test-worker",
                     work_root=work_root, backoff_base_s=0.05, backoff_cap_s=0.2, jitter=0.0)
        for k, v in overrides.items():
            setattr(cfg, k, v)
        kwargs = {"grade_fn": grade_fn} if grade_fn else {}
        w = Worker(cfg, sleep=sleep, **kwargs)
        t = threading.Thread(target=w.run, name="worker-main", daemon=True)
        t.start()
        started.append((w, t))
        return w

    yield make
    for w, t in started:
        w.stop()
    for w, t in started:
        t.join(timeout=10)
