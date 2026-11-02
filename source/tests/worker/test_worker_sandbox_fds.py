"""`sandbox.run` không rò fd khi spawn lỗi: pipe kết quả (rfd/wfd) và file stdin phải được đóng ở mọi nhánh lỗi.

Đếm /proc/self/fd trước và sau nhiều lần gọi (worker chạy lâu, mỗi fd rò mỗi lần lỗi cộng dồn tới EMFILE)."""

import os

import pytest

from worker import sandbox

N = 20


def open_fds():
    return len(os.listdir("/proc/self/fd"))


def run_ok(work_root, **kw):
    return sandbox.run(["true"], cwd=work_root, timeout_s=10, **kw)


@pytest.fixture
def warm(work_root):
    """Chạy một lần trước để file BPF và các cache khác đã được tạo; sau đó số fd phải đứng yên."""
    assert run_ok(work_root).exit_code == 0
    return work_root


def test_missing_stdin_file_is_a_spawn_error_and_leaks_no_fd(warm):
    before = open_fds()
    for _ in range(N):
        res = run_ok(warm, stdin_path="/nonexistent/input")
        assert res.spawn_error and "FileNotFoundError" in res.spawn_error
    assert open_fds() == before


def test_popen_oserror_is_a_spawn_error_and_leaks_no_fd(warm, monkeypatch):
    def boom(*a, **k):
        raise PermissionError("không spawn được")
    monkeypatch.setattr(sandbox.subprocess, "Popen", boom)
    before = open_fds()
    for kw in ({}, {"stdin_path": os.devnull}):  # có và không có file stdin phải đóng
        for _ in range(N):
            res = run_ok(warm, **kw)
            assert res.spawn_error and "PermissionError" in res.spawn_error
    assert open_fds() == before


def test_unexpected_exception_propagates_and_leaks_no_fd(warm, monkeypatch):
    def boom(*a, **k):
        raise ValueError("lỗi lập trình")
    monkeypatch.setattr(sandbox.subprocess, "Popen", boom)
    before = open_fds()
    for kw in ({}, {"stdin_path": os.devnull}):
        for _ in range(N):
            with pytest.raises(ValueError):
                run_ok(warm, **kw)
    assert open_fds() == before


def test_stdin_file_is_closed_explicitly_not_left_to_the_garbage_collector(warm, monkeypatch):
    """CPython tự đóng file khi hết tham chiếu nên đếm fd không thấy việc thiếu `close()`; giữ tham chiếu để kiểm."""
    opened = []
    real_open = open

    def recording_open(*a, **k):
        f = real_open(*a, **k)
        opened.append(f)
        return f
    monkeypatch.setattr(sandbox, "open", recording_open, raising=False)
    assert run_ok(warm, stdin_path=os.devnull).exit_code == 0
    assert len(opened) == 1 and opened[0].closed

    def boom(*a, **k):
        raise PermissionError("không spawn được")
    monkeypatch.setattr(sandbox.subprocess, "Popen", boom)
    opened.clear()
    assert run_ok(warm, stdin_path=os.devnull).spawn_error
    assert len(opened) == 1 and opened[0].closed


def test_successful_runs_leak_no_fd(warm):
    before = open_fds()
    for kw in ({}, {"stdin_path": os.devnull}):
        for _ in range(N):
            assert run_ok(warm, **kw).exit_code == 0
    assert open_fds() == before
