"""Trần tmpfs của container worker (D27). Chạy trong container test với đúng tmpfs của compose:

    --memory=1g --memory-swap=1g --read-only
    --tmpfs /tmp:size=16m,nr_inodes=2048 --tmpfs /dev/shm:size=16m,nr_inodes=2048
    --tmpfs /work:exec,mode=755,size=64m,nr_inodes=2048

Môi trường không đúng như vậy thì test FAIL (không skip): trần không có thì các ca này không chứng minh được gì."""

import os
import re
import shutil
import tempfile

import pytest

from _worker_helpers import case, grade
from worker import sandbox

MiB = 1 << 20
CAPS = {"/tmp": 16 * MiB, "/dev/shm": 16 * MiB, "/work": 64 * MiB}

# Ghi đầy từng thư mục ghi được rồi ghi thêm một lần không bắt lỗi: ENOSPC không bắt thì thoát mã ≠ 0.
FILL_ALL_PY = r'''import os, sys
data = b"x" * (1 << 20)
for d in ("/tmp", "/dev/shm", os.getcwd()):
    i = 0
    try:
        while True:
            with open(os.path.join(d, f"fill{i}"), "wb") as f:
                f.write(data)
            i += 1
            if i > 4096:
                print("NO-LIMIT")
                sys.exit(3)
    except OSError:
        pass
with open("/tmp/one-more", "wb") as f:
    f.write(b"y")  # đóng file thì flush; ENOSPC không bắt nên thoát mã khác 0
print("unreachable")
'''

# Trong test 1 ("FILL") làm đầy thư mục bài rồi in ok; test sau đọc hết stdin và in độ dài.
FILL_THEN_READ_PY = r'''import os, sys
data = sys.stdin.buffer.read()
if data.startswith(b"FILL"):
    block = b"x" * (1 << 20)
    i = hit = 0
    try:
        while i < 4096:
            with open(f"fill{i}", "wb") as f:
                f.write(block)
            i += 1
    except OSError:
        hit = 1
    print("ok" if hit else "notfull")
else:
    print(len(data))
'''

FILL_THEN_READ_CPP = r'''#include <cstdio>
#include <string>
#include <vector>
int main() {
    std::string data; char buf[65536]; size_t n;
    while ((n = fread(buf, 1, sizeof buf, stdin)) > 0) data.append(buf, n);
    if (data.compare(0, 4, "FILL") == 0) {
        std::vector<char> block(1 << 20, 'x');
        bool hit = false;
        for (int i = 0; i < 4096; i++) {
            char name[32]; snprintf(name, sizeof name, "fill%d", i);
            FILE* f = fopen(name, "wb");
            if (!f) { hit = true; break; }
            size_t w = fwrite(block.data(), 1, block.size(), f);
            int rc = fclose(f);
            if (w != block.size() || rc != 0) { hit = true; break; }
        }
        puts(hit ? "ok" : "notfull");
    } else {
        printf("%zu\n", data.size());
    }
}
'''


FILL_THEN_READ_JAVA = r'''import java.io.*;
import java.nio.file.*;
public class Main {
    public static void main(String[] a) throws Exception {
        byte[] data = System.in.readAllBytes();
        if (data.length >= 4 && data[0] == 'F' && data[1] == 'I' && data[2] == 'L' && data[3] == 'L') {
            byte[] block = new byte[1 << 20];
            boolean hit = false;
            try {
                for (int i = 0; i < 4096; i++) {
                    try (FileOutputStream f = new FileOutputStream("fill" + i)) { f.write(block); }
                }
            } catch (IOException e) { hit = true; }
            System.out.println(hit ? "ok" : "notfull");
        } else {
            System.out.println(data.length);
        }
    }
}
'''


def _mount_options(path):
    with open("/proc/self/mountinfo", encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if parts[4] == path:
                fs_type = parts[parts.index("-") + 1]
                super_opts = parts[parts.index("-") + 3]
                return fs_type, parts[5] + "," + super_opts
    return None, ""


def _size_bytes(options):
    m = re.search(r"(?:^|,)size=(\d+)k", options)
    return int(m.group(1)) * 1024 if m else None


@pytest.fixture(scope="module")
def tmpfs_env():
    """Phải đang chạy với các tmpfs có trần như compose; nếu không thì fail với thông báo rõ."""
    problems = []
    for path, cap in CAPS.items():
        fs_type, opts = _mount_options(path)
        size = _size_bytes(opts)
        if fs_type != "tmpfs":
            problems.append(f"{path} không phải tmpfs riêng ({fs_type})")
        elif size is None or size > cap:
            problems.append(f"{path} không có trần size≤{cap // MiB}m (options: {opts})")
        elif "nr_inodes=" not in opts:
            problems.append(f"{path} không có nr_inodes (options: {opts})")
    assert not problems, "môi trường test không khớp compose (xem CLAUDE.md, Lệnh môi trường): " + "; ".join(problems)


@pytest.fixture
def tmpfs_work_root(tmpfs_env):
    path = tempfile.mkdtemp(prefix="jh-quota-", dir="/work")
    os.chmod(path, 0o755)
    yield path
    sandbox.reset_uid(sandbox.JUDGE_UID)
    shutil.rmtree(path, ignore_errors=True)


def _oom_kills():
    """Số lần OOM killer đã giết trong cgroup của container (cgroup v2). Không đọc được thì test fail."""
    with open("/sys/fs/cgroup/memory.events", encoding="utf-8") as f:
        return int(dict(line.split() for line in f)["oom_kill"])


@pytest.mark.parametrize("lang,code", [("python", FILL_THEN_READ_PY), ("cpp", FILL_THEN_READ_CPP),
                                   ("java", FILL_THEN_READ_JAVA)])
def test_full_work_does_not_break_the_next_test_even_with_a_large_input(tmpfs_work_root, lang, code):
    big = "0123456789abcdef" * (2 * MiB // 16)  # 2 MiB: bản cũ ghi input vào /work ra SE (OSError 28)
    tests = [case("FILL\n", "ok\n", True), case(big, f"{len(big)}\n"), case("FILL\n", "ok\n")]
    body, progress = grade(tmpfs_work_root, lang, code, tests, time_limit_ms=5000)
    assert body["verdict"] == "AC", body
    assert progress == [0, 1, 2, 3]  # chương trình in "ok" (không phải "notfull") nên /work đã thật sự đầy lúc chấm


def test_continuous_writing_keeps_worker_alive_and_python_gets_re(tmpfs_work_root):
    kills_before = _oom_kills()
    body, _ = grade(tmpfs_work_root, "python", FILL_ALL_PY, [case("", "unreachable\n", True)], time_limit_ms=5000)
    assert body["verdict"] == "RE" and body["re_kind"] == "EXIT_NONZERO", body  # README: ghi đầy → RE (Python)
    assert "NO-LIMIT" not in body.get("sample_stdout", "")
    assert _oom_kills() == kills_before  # không ai bị OOM killer giết, kể cả worker (chính tiến trình này)
    assert sandbox.uid_pids(sandbox.JUDGE_UID) == []
    assert sandbox.uid_entries(sandbox.JUDGE_UID, ("/tmp", "/dev/shm")) == 0  # đã dọn
    body, _ = grade(tmpfs_work_root, "python", "print(1)\n", [case("", "1\n")])
    assert body["verdict"] == "AC", body  # worker chấm tiếp được


def test_flooding_inodes_is_stopped_by_nr_inodes_and_cleanup_stays_within_budget(tmpfs_work_root):
    code = ('import os\nn = 0\ntry:\n    while n < 100000:\n        open(f"/tmp/f{n}", "w").close()\n'
            '        n += 1\nexcept OSError:\n    pass\nprint("created", n)\n')
    body, _ = grade(tmpfs_work_root, "python", code, [case("", "created 0\n", True)])
    created = int(re.search(r"created (\d+)", body["sample_stdout"]).group(1))
    assert 0 < created <= 2048, body
    assert sandbox.uid_entries(sandbox.JUDGE_UID, ("/tmp",)) == 0
