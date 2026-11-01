"""Input test đi qua memfd (D27): không nằm trên filesystem, không fd nào rò sang chương trình thí sinh.

Chạy trong container test với sandbox thật (chương trình thí sinh hạ xuống uid 61000, worker là root)."""

import fcntl
import os
import re
import subprocess
import sys

import pytest

from _worker_helpers import case, grade
from worker import executor, sandbox

UID = sandbox.JUDGE_UID

# Thử mở mọi /proc/<pid khác>/fd/* (kể cả tiến trình cha là launcher root, tini, worker). Chỉ in kết quả, không đoán.
PROC_PROBE = r'''import os
me = os.getpid()
allowed, denied, other = [], 0, 0
for name in os.listdir("/proc"):
    if not name.isdigit() or int(name) == me:
        continue
    try:
        fds = os.listdir(f"/proc/{name}/fd")
    except PermissionError:
        denied += 1
        fds = [str(n) for n in range(64)]  # không liệt kê được thì thử đoán số fd
    except OSError:
        continue
    for fd in fds:
        try:
            os.close(os.open(f"/proc/{name}/fd/{fd}", os.O_RDONLY | os.O_NONBLOCK))
            allowed.append(f"{name}/{fd}")
        except PermissionError:
            denied += 1
        except OSError:
            other += 1
print("ALLOWED" if allowed else "none-allowed", allowed[:5])
print("denied", denied > 0)
'''

PROC_PROBE_PPID = r'''import os
for name in (str(os.getppid()), "1"):
    for leaf in ("mem", "environ", "cmdline_x"):
        try:
            os.close(os.open(f"/proc/{name}/{leaf}", os.O_RDONLY))
            if leaf != "cmdline_x":
                print("ALLOWED", name, leaf)
        except OSError:
            pass
    for fd in range(64):
        try:
            os.close(os.open(f"/proc/{name}/fd/{fd}", os.O_RDONLY | os.O_NONBLOCK))
            print("ALLOWED", name, fd)
        except OSError:
            pass
print("done")
'''

FD_LIST_PY = r'''import os
fds = []
for n in os.listdir("/proc/self/fd"):
    try:
        target = os.readlink(f"/proc/self/fd/{n}")
    except OSError:
        continue  # fd của chính lần liệt kê
    fds.append((int(n), target))
print(sorted(fds))
'''

FD_LIST_CPP = r'''#include <dirent.h>
#include <unistd.h>
#include <cstdio>
#include <cstdlib>
int main() {
    DIR* d = opendir("/proc/self/fd");
    int dfd = dirfd(d);
    struct dirent* e;
    while ((e = readdir(d))) {
        if (e->d_name[0] == '.') continue;
        int n = atoi(e->d_name);
        if (n != dfd) printf("fd %d\n", n);
    }
    closedir(d);
}
'''


def test_input_is_not_a_file_in_work_root(work_root):
    wd = executor._Workdir(work_root, UID)
    try:
        path = wd.write_input("INPUT-SECRET\n")
        assert re.fullmatch(r"/proc/self/fd/\d+", path), path
        assert os.listdir(work_root) == [os.path.basename(wd.path)]  # chỉ có thư mục bài, không có file input
        assert os.listdir(wd.path) == []
        with open(path, "rb") as f:
            assert f.read() == b"INPUT-SECRET\n"
        with open(path, "rb") as f:  # mở lại cho offset mới: không dùng chung offset của lần trước
            assert f.read() == b"INPUT-SECRET\n"
    finally:
        wd.remove()


def test_input_fd_is_cloexec_and_does_not_survive_exec_even_without_close_fds(work_root):
    wd = executor._Workdir(work_root, UID)
    try:
        fd = int(wd.write_input("x" * 1000).rsplit("/", 1)[1])
        assert fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
        assert not os.get_inheritable(fd)
        out = subprocess.run(["ls", "-l", "/proc/self/fd"], close_fds=False, capture_output=True, text=True, check=True)
        assert "jh-input" not in out.stdout  # close_fds=False: chỉ CLOEXEC mới cứu được (memfd hiện là "memfd:jh-input")
    finally:
        wd.remove()


def test_input_fd_is_closed_by_drop_and_replaced_per_test(work_root):
    wd = executor._Workdir(work_root, UID)
    try:
        wd.write_input("a")
        n_open = len(os.listdir("/proc/self/fd"))
        for _ in range(5):  # mỗi test thay input: không được tích fd
            wd.write_input("b")
        assert len(os.listdir("/proc/self/fd")) == n_open
        wd.drop_input()
        assert len(os.listdir("/proc/self/fd")) == n_open - 1
    finally:
        wd.remove()


def test_candidate_cannot_open_fds_of_worker_or_other_processes(work_root):
    body, _ = grade(work_root, "python", PROC_PROBE, [case("x" * 4096, "none-allowed []\ndenied True\n", True)])
    assert body["verdict"] == "AC", body
    body, _ = grade(work_root, "python", PROC_PROBE_PPID, [case("", "done\n", True)])
    assert body["verdict"] == "AC", body  # AC nghĩa là không dòng "ALLOWED" nào


@pytest.mark.parametrize("lang,code", [("python", FD_LIST_PY), ("cpp", FD_LIST_CPP)])
def test_candidate_inherits_no_fd_beyond_stdio_so_expected_output_is_unreachable(work_root, lang, code):
    expected = "EXPECTED-" + "Z" * 32 + "\n"
    body, _ = grade(work_root, lang, code, [case("in\n", expected, True)])
    assert body["verdict"] == "WA", body  # chương trình không in expected; ta chỉ cần xem nó thấy fd nào
    out = body["sample_stdout"]
    assert "EXPECTED-" not in out
    if lang == "python":
        nums = sorted({n for n, _ in eval(out.splitlines()[0])})
    else:
        nums = sorted(int(m) for m in re.findall(r"fd (\d+)", out))
    assert nums == [0, 1, 2], out


def test_expected_output_is_not_in_stdin_or_files_candidate_can_read(work_root):
    secret = "EXPECTED-" + "Q" * 40
    code = ('import os, sys\ndata = sys.stdin.buffer.read()\nhits = 0\n'
            'needle = b"EXPE" + b"CTED-" + b"Q" * 40\n'  # dựng lúc chạy: mã nguồn bài không chứa sẵn chuỗi này
            'hits += needle in data\n'
            'for root in ("/work", "/srv", "/proc/self"):\n'
            '    for d, _, fs in os.walk(root, onerror=lambda e: None):\n'
            '        for f in fs:\n'
            '            try:\n'
            '                hits += needle in open(os.path.join(d, f), "rb").read(1 << 20)\n'
            '            except OSError:\n'
            '                pass\n'
            'print("hits", hits)\n')
    body, _ = grade(work_root, "python", code, [case("input only\n", secret + "\n", True)])
    assert body["verdict"] == "WA" and body["sample_stdout"] == "hits 0\n", body


def test_large_input_is_delivered_intact_through_memfd(work_root):
    big = ("0123456789abcdef" * 4096 + "\n") * 40  # ~2,6 MiB
    code = "import sys\nd = sys.stdin.buffer.read()\nprint(len(d))\n"
    body, _ = grade(work_root, "python", code, [case(big, f"{len(big.encode())}\n")])
    assert body["verdict"] == "AC", body


def test_sys_has_memfd():
    assert sys.platform == "linux" and hasattr(os, "memfd_create")


def test_candidate_cannot_reopen_its_stdin_to_overwrite_or_reread_the_input(work_root):
    code = ("import os\n"
            "for flags in (os.O_RDWR, os.O_WRONLY, os.O_RDONLY):\n"
            "    try:\n"
            "        os.close(os.open('/proc/self/fd/0', flags))\n"
            "        print('ALLOWED', flags)\n"
            "    except PermissionError:\n"
            "        pass\n"
            "print('done')\n")
    body, _ = grade(work_root, "python", code, [case("INPUT\n", "done\n", True)])
    assert body["verdict"] == "AC", body


def test_write_input_failure_leaks_no_fd_and_leaves_workdir_usable(work_root, monkeypatch):
    wd = executor._Workdir(work_root, UID)
    try:
        before = len(os.listdir("/proc/self/fd"))

        def boom(*a, **k):
            raise OSError(24, "Too many open files")

        monkeypatch.setattr(os, "memfd_create", boom)
        with pytest.raises(OSError):
            wd.write_input("x")
        monkeypatch.undo()
        assert len(os.listdir("/proc/self/fd")) == before
        with open(wd.write_input("ok"), "rb") as f:
            assert f.read() == b"ok"
    finally:
        wd.remove()
