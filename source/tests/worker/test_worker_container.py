"""Cấu hình container dự kiến của compose (cap_drop ALL + SETUID, SETGID, KILL, pids_limit) và mục treo:
fork bomb chiếm hết pids_limit có làm worker cùng container không tạo được thread/tiến trình không.

Chạy bằng đúng lệnh test chuẩn (CLAUDE.md, "Lệnh môi trường"), kèm cờ container."""

import subprocess
import threading
import time

import pytest

from _submissions import SUB
from _worker_helpers import case, grade, make_job
from worker import sandbox

UID = sandbox.JUDGE_UID
CAP_BITS = {"CHOWN": 0, "DAC_OVERRIDE": 1, "DAC_READ_SEARCH": 2, "FOWNER": 3, "KILL": 5, "SETGID": 6, "SETUID": 7}
ALLOWED_CAPS = ("KILL", "SETGID", "SETUID")  # cap tối thiểu worker cần (kết quả đo ở PR 3, xem worker/README.md)


def status_field(name):
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith(name + ":"):
                return line.split(":", 1)[1].strip()
    raise KeyError(name)


def pids_current():
    with open("/sys/fs/cgroup/pids.current") as f:
        return int(f.read())


def pids_max():
    with open("/sys/fs/cgroup/pids.max") as f:
        text = f.read().strip()
    assert text != "max", "chạy test với --pids-limit (mặc định lệnh test chuẩn: 512)"
    return int(text)


# ------------------------------------------------------------------------------------------- capabilities

def test_container_has_only_the_expected_capabilities():
    """Nếu test chạy với nhiều cap hơn cần thiết thì chúng có thể che lỗi thiếu cap ở production. Đo ở PR 3: không
    cần CHOWN (thư mục tạm của bài root sở hữu, mode 1777), DAC_OVERRIDE hay FOWNER (file của uid thí sinh được xóa
    bằng một tiến trình chạy dưới chính uid đó); thiếu KILL thì không kill được tiến trình thí sinh khi TLE."""
    eff = int(status_field("CapEff"), 16)
    extra = [name for name, bit in CAP_BITS.items() if eff & (1 << bit) and name not in ALLOWED_CAPS]
    assert extra == [], f"container test có thừa cap: {extra}"
    assert eff & ~sum(1 << b for b in CAP_BITS.values()) == 0, f"có cap lạ trong CapEff={eff:#x}"


def test_worker_can_drop_privileges_read_output_and_clean_up_candidate_files_without_extra_caps(work_root):
    # xóa file của uid thí sinh trong thư mục sticky /tmp, kể cả thư mục con mode 000 (kênh rò chéo giữa bài nộp)
    body, _ = grade(work_root, "python", SUB["tmp_write"]["python"], [case("DATA\n", "ok\n", True)])
    assert body["verdict"] == "AC"
    assert sandbox.uid_entries(UID) == 0
    assert subprocess.run(["ls", "-A", "/tmp/jh-dir"], capture_output=True).returncode != 0


# --------------------------------------------------------------------------------------- pids_limit (mục treo)

def probe_when_saturated():
    """Thử tạo thread và tiến trình từ process worker lúc cgroup đã đầy pids."""
    result = {}
    try:
        t = threading.Thread(target=lambda: None)
        t.start()
        t.join()
        result["thread"] = "ok"
    except RuntimeError as e:
        result["thread"] = f"RuntimeError: {e}"
    try:
        subprocess.run(["true"], check=True)
        result["process"] = "ok"
    except (OSError, subprocess.SubprocessError) as e:
        result["process"] = f"{type(e).__name__}: {e}"
    return result


@pytest.mark.parametrize("lang", ["python", "cpp"])
def test_fork_bomb_saturates_pids_limit_and_everything_recovers(work_root, lang, capsys):
    limit = pids_max()
    baseline = pids_current()
    done = {}

    def run_bomb():
        done["body"], _ = grade(work_root, lang, SUB["fork_bomb"][lang], [case("", "x\n")], time_limit_ms=1000)

    t = threading.Thread(target=run_bomb)
    t.start()
    probes = None
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and t.is_alive():
        if pids_current() >= limit - 1:
            probes = probe_when_saturated()
            break
        time.sleep(0.01)
    t.join(timeout=60)
    assert not t.is_alive()
    with capsys.disabled():
        print(f"\n[pids_limit] {lang}: limit={limit} baseline={baseline} saturated={probes is not None} "
              f"probe={probes}")
    assert probes is not None, "fork bomb không chiếm hết pids_limit"
    assert done["body"]["verdict"] == "TLE"
    # sau khi bị kill: pids về mức nền, không còn tiến trình/zombie của uid thí sinh, bài kế tiếp chấm được
    deadline = time.monotonic() + 5
    while pids_current() > baseline + 8 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert pids_current() <= baseline + 8
    assert sandbox.uid_pids(UID) == []
    body, _ = grade(work_root, "python", SUB["ac"]["python"], [case("1 2\n", "3\n")])
    assert body["verdict"] == "AC"


def test_worker_main_thread_survives_saturation_and_keeps_answering_pings(server, make_worker, work_root):
    limit = pids_max()
    make_worker(None, peer_dead_after_s=60)  # executor thật; server giả không gửi PING định kỳ
    conn = server.accept()
    conn.register_ok()
    conn.send(make_job("cpp", SUB["fork_bomb"]["cpp"], [case("", "x\n")], time_limit_ms=1500))
    assert conn.recv(30)["type"] == "PROGRESS"  # biên dịch xong, bomb sắp chạy
    deadline = time.monotonic() + 8
    while pids_current() < limit - 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pids_current() >= limit - 1, "bomb phải chiếm hết pids_limit"
    conn.send({"type": "PING", "seq": 77})  # thread đọc đã có sẵn: không cần tạo thread mới để trả PONG
    msgs = conn.recv_all("RESULT", 30)
    assert {"type": "PONG", "seq": 77} in msgs
    assert msgs[-1]["verdict"] == "TLE"


def test_connection_lost_while_pids_are_saturated_still_reconnects(server, make_worker):
    limit = pids_max()
    make_worker(None, peer_dead_after_s=60)
    conn = server.accept()
    conn.register_ok()
    conn.send(make_job("cpp", SUB["fork_bomb"]["cpp"], [case("", "x\n")], time_limit_ms=30000))
    assert conn.recv(30)["type"] == "PROGRESS"
    deadline = time.monotonic() + 8
    while pids_current() < limit - 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pids_current() >= limit - 1
    conn.close()  # mất kết nối lúc đầy pids: hủy bài, kill bomb, rồi kết nối lại (cần thread đọc mới)
    new_conn = server.accept(30)
    new_conn.register_ok(worker_id=2)
    new_conn.send(make_job("python", SUB["ac"]["python"], [case("1 2\n", "3\n")], attempt_id=2))
    assert new_conn.recv_all("RESULT", 30)[-1]["verdict"] == "AC"


def test_failure_to_start_reader_thread_is_retried_after_backoff(server, make_worker, monkeypatch):
    original = threading.Thread.start
    calls = {"n": 0}

    def flaky(self):
        if self.name == "reader" and calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("can't start new thread")
        return original(self)

    monkeypatch.setattr(threading.Thread, "start", flaky)
    make_worker(None)
    first = server.accept()
    assert first.expect_closed(5)  # kết nối đầu bị bỏ vì không tạo được thread đọc
    second = server.accept(10)
    assert second.recv()["type"] == "REGISTER"  # worker vẫn sống và đăng ký lại bình thường
