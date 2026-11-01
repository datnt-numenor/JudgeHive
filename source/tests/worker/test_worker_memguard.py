"""Ba lớp chống bộ nhớ ngoài RSS của thí sinh (D28): oom_score_adj +1000 khóa bởi CAP_SYS_RESOURCE, seccomp, sysctl.

Chạy trong container test với đúng cờ của compose: --memory=1g --memory-swap=1g, --cap-add=SYS_RESOURCE,
--sysctl kernel.shm_rmid_forced=1 --sysctl kernel.shmmax=0. Thiếu thì FAIL (không skip). Các ca tải nặng giữ trong
tiến trình test một khối bộ nhớ 118 MiB (mức RSS của worker khi parse JOB lớn, đã đo) để worker là nạn nhân "to nhất"
nếu thí sinh không bị chọn trước."""

import os
import platform

import pytest

from _worker_helpers import case, grade
from _submissions import SUB
from worker import sandbox, seccomp

MiB = 1 << 20
ARCH = seccomp.host_arch()


@pytest.fixture(scope="module")
def worker_like_rss():
    ballast = bytearray(118 * MiB)
    for i in range(0, len(ballast), 4096):
        ballast[i] = 1
    yield ballast


def _oom_kills():
    with open("/sys/fs/cgroup/memory.events", encoding="utf-8") as f:
        return int(dict(line.split() for line in f)["oom_kill"])


def _sysctl(name):
    with open("/proc/sys/" + name.replace(".", "/"), encoding="utf-8") as f:
        return f.read().strip()


# ------------------------------------------------------------------------------ bộ lọc (thuần, không cần kernel)

def _ret(prog, arch, nr):
    return seccomp.simulate(prog, seccomp.AUDIT_ARCH[arch], nr)


@pytest.mark.parametrize("arch", ["x86_64", "aarch64"])
def test_filter_blocks_memfd_and_shmget_with_eperm_and_allows_the_rest(arch):
    prog = seccomp.build_filter(arch)
    for name, nr in seccomp.BLOCKED[arch]:
        assert _ret(prog, arch, nr) == seccomp.RET_ERRNO_EPERM, name
    others = {"x86_64": (0, 1, 2, 9, 56, 59, 202), "aarch64": (63, 64, 93, 222, 220)}[arch]  # read write mmap clone futex...
    assert all(_ret(prog, arch, nr) == seccomp.RET_ALLOW for nr in others)


def test_filter_kills_x32_abi_on_x86_64_even_for_allowed_numbers():
    prog = seccomp.build_filter("x86_64")
    for nr in (319, 29, 1, 0, 202):
        assert _ret(prog, "x86_64", seccomp.X32_BIT | nr) == seccomp.RET_KILL_PROCESS, nr
    assert _ret(prog, "x86_64", 0xFFFFFFFF) == seccomp.RET_KILL_PROCESS


def test_filter_kills_foreign_architectures_whatever_the_syscall():
    for host, foreign in (("x86_64", 0x40000003), ("x86_64", seccomp.AUDIT_ARCH["aarch64"]),  # i386, aarch64
                          ("aarch64", seccomp.AUDIT_ARCH["x86_64"]), ("aarch64", 0x40000003)):
        prog = seccomp.build_filter(host)
        for nr in (0, 1, 356, 319, 279, 29, 194):  # gồm memfd_create của i386 (356)
            assert seccomp.simulate(prog, foreign, nr) == seccomp.RET_KILL_PROCESS, (host, hex(foreign), nr)


def test_unsupported_host_architecture_refuses_to_build():
    with pytest.raises(seccomp.GuardError):
        seccomp.host_arch("riscv64")


# ---------------------------------------------------------------------------------------- môi trường / khởi động

def test_environment_matches_compose_guard_settings():
    assert _sysctl("kernel.shmmax") == "0" and _sysctl("kernel.shm_rmid_forced") == "1", \
        "chạy thiếu --sysctl kernel.shmmax=0 --sysctl kernel.shm_rmid_forced=1 (xem CLAUDE.md, Lệnh môi trường)"
    assert sandbox._cap_eff() & (1 << sandbox.CAP_SYS_RESOURCE), "chạy thiếu --cap-add=SYS_RESOURCE"


def test_verify_guard_passes_with_compose_settings_and_fails_closed_without_the_cap(monkeypatch):
    assert os.path.exists(sandbox.verify_guard())
    monkeypatch.setattr(sandbox, "_cap_eff", lambda: 0)
    with pytest.raises(seccomp.GuardError, match="SYS_RESOURCE"):
        sandbox.verify_guard()


def test_verify_guard_fails_closed_when_the_sysctls_are_not_applied(monkeypatch):
    real = sandbox._read_sysctl
    for name, bad in (("kernel/shmmax", "18446744073692774399"), ("kernel/shm_rmid_forced", "0")):
        monkeypatch.setattr(sandbox, "_read_sysctl", lambda n, name=name, bad=bad: bad if n == name else real(n))
        with pytest.raises(seccomp.GuardError, match="sysctl"):
            sandbox.verify_guard()


def test_verify_guard_fails_closed_when_the_seccomp_filter_cannot_be_loaded(monkeypatch):
    monkeypatch.setattr(sandbox, "guard_filter_path", lambda: "/nonexistent/none.bpf")
    with pytest.raises(seccomp.GuardError, match="setpriv"):
        sandbox.verify_guard()


def test_verify_guard_detects_a_guard_that_does_not_actually_protect(monkeypatch):
    """Kiểm bằng hành vi: nếu bọc thiếu (không khóa oom_score_adj, không seccomp) thì khởi động phải bị từ chối."""
    def unguarded(argv, limits, uid):
        return ["setpriv", f"--reuid={uid}", f"--regid={uid}", "--clear-groups", *argv]
    monkeypatch.setattr(sandbox, "_wrap", unguarded)
    with pytest.raises(seccomp.GuardError, match="thí sinh vẫn làm được"):
        sandbox.verify_guard()


def test_filter_file_is_root_owned_readonly_and_candidate_cannot_replace_it(work_root):
    path = sandbox.guard_filter_path()
    st = os.stat(path)
    assert st.st_uid == 0 and st.st_mode & 0o777 == 0o444
    code = (f"import os\nfor f in (os.remove, lambda p: open(p, 'wb')):\n    try:\n        f({path!r}); print('ALLOWED')\n"
            "    except OSError:\n        print('denied')\n")
    body, _ = grade(work_root, "python", code, [case("", "x\n", True)])
    assert body["sample_stdout"].splitlines() == ["denied", "denied"], body
    assert os.path.exists(path)


# ------------------------------------------------------------------------------------------- oom_score_adj

def test_candidate_has_oom_adj_1000_cannot_lower_it_and_has_no_capabilities(work_root):
    code = ("import os\n"
            "def rd(p): return open(p).read().strip()\n"
            "before = rd('/proc/self/oom_score_adj')\n"
            "try:\n    f = open('/proc/self/oom_score_adj', 'w'); f.write('0'); f.close(); res = 'lowered'\n"
            "except OSError as e:\n    res = 'refused'\n"
            "caps = [l.split()[1] for l in open('/proc/self/status') if l.startswith(('CapEff', 'CapPrm'))]\n"
            "print(before, res, rd('/proc/self/oom_score_adj'), *caps)\n")
    body, _ = grade(work_root, "python", code, [case("", "x\n", True)])
    assert body["sample_stdout"].split() == ["1000", "refused", "1000", "0000000000000000", "0000000000000000"], body


def test_compile_step_is_also_wrapped_with_adj_and_seccomp(work_root):
    """g++ và javac chạy qua cùng _wrap (oom_score_adj + seccomp): biên dịch vẫn thành công, bài AC."""
    for lang in ("cpp", "java"):
        calls = []
        orig = sandbox._wrap
        sandbox._wrap = lambda argv, limits, uid, orig=orig: calls.append(argv) or orig(argv, limits, uid)
        try:
            body, _ = grade(work_root, lang, SUB["ac"][lang], [case("1 2\n", "3\n", True)])
        finally:
            sandbox._wrap = orig
        assert body["verdict"] == "AC", (lang, body)
        assert len(calls) >= 2 and any(("g++" in c or "javac" in c) for c in calls), (lang, calls)


@pytest.mark.parametrize("lang", ["python", "cpp", "java"])
def test_all_three_languages_still_accept_a_correct_solution_under_the_guard(work_root, lang):
    body, _ = grade(work_root, lang, SUB["ac"][lang], [case("1 2\n", "3\n", True), case("10 20\n", "30\n")])
    assert body["verdict"] == "AC", body


# ---------------------------------------------------------------------------------------------------- seccomp

CPP_SYSCALLS = r'''#include <sys/syscall.h>
#include <unistd.h>
#include <sys/ipc.h>
#include <sys/shm.h>
#include <cerrno>
#include <cstdio>
int main() {
    errno = 0; long a = syscall(SYS_memfd_create, "m", 0); int ea = errno;
    errno = 0; int b = shmget(IPC_PRIVATE, 4096, IPC_CREAT | 0600); int eb = errno;
    printf("memfd=%ld/%d shmget=%d/%d\n", a, ea, b, eb);
}
'''
CPP_INT80 = r'''#include <cstdio>
int main() {
    puts("start"); fflush(stdout);
    long r;
    asm volatile("int $0x80" : "=a"(r) : "a"(356), "b"("m"), "c"(0) : "memory");   // memfd_create của ABI i386
    printf("survived %ld\n", r);
}
'''
CPP_X32 = r'''#include <sys/syscall.h>
#include <unistd.h>
#include <cstdio>
int main() {
    puts("start"); fflush(stdout);
    long r = syscall(0x40000000L | SYS_memfd_create, "m", 0);   // __X32_SYSCALL_BIT | nr
    printf("survived %ld\n", r);
}
'''


def test_native_memfd_create_and_shmget_get_eperm_in_the_candidate(work_root):
    body, _ = grade(work_root, "cpp", CPP_SYSCALLS, [case("", "x\n", True)])
    assert body["sample_stdout"] == "memfd=-1/1 shmget=-1/1\n", body  # errno 1 = EPERM


@pytest.mark.parametrize("name,code", [("int0x80", CPP_INT80), ("x32", CPP_X32)])
def test_memfd_create_through_foreign_abis_kills_the_candidate(work_root, name, code):
    assert platform.machine() == "x86_64", "hai ca ABI i386/x32 chỉ có nghĩa trên x86_64; ca aarch64 do bộ lọc thuần kiểm"
    body, _ = grade(work_root, "cpp", code, [case("", "x\n", True)])
    assert body["verdict"] == "RE" and body["re_kind"] == "OTHER_SIGNAL", body  # SIGSYS
    assert body["sample_stdout"] == "start\n", body  # không in "survived"


# --------------------------------------------------------------------- OOM: thí sinh là nạn nhân, worker sống

# Cha chờ các con; nếu có con chết bất thường thì tự SIGKILL mình (bài "mất"). Con sống hết thì in "survived".
CPP_PARENT_TAIL = r'''
    int bad = 0, st;
    while (wait(&st) > 0) if (!WIFEXITED(st) || WEXITSTATUS(st)) bad++;
    if (bad) kill(getpid(), SIGKILL);
    puts("survived");
}
'''
CPP_MANY = r'''#include <unistd.h>
#include <signal.h>
#include <sys/wait.h>
#include <cstdio>
#include <cstdlib>
int main() {
    for (int c = 0; c < @N@; c++) {
        pid_t pid = fork();
        if (pid < 0) break;
        if (pid == 0) {
            volatile char* p = (char*)malloc(@MIB@L << 20);
            if (!p) _exit(3);
            for (long o = 0; o < (@MIB@L << 20); o += 4096) p[o] = 1;
            sleep(8); _exit(0);
        }
        usleep(5000);
    }
''' + CPP_PARENT_TAIL
CPP_SOCK = r'''#include <sys/socket.h>
#include <fcntl.h>
#include <unistd.h>
#include <signal.h>
#include <sys/wait.h>
#include <cstdio>
#include <vector>
int main() {
    std::vector<char> buf(65536, 'x');
    for (int c = 0; c < 300; c++) {
        pid_t pid = fork();
        if (pid < 0) break;
        if (pid == 0) {
            for (int k = 0; k < 14; k++) {
                int sv[2]; if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv)) break;
                int big = 4 << 20;
                setsockopt(sv[0], SOL_SOCKET, SO_SNDBUF, &big, sizeof big);
                setsockopt(sv[1], SOL_SOCKET, SO_SNDBUF, &big, sizeof big);
                fcntl(sv[0], F_SETFL, O_NONBLOCK); fcntl(sv[1], F_SETFL, O_NONBLOCK);
                for (int d = 0; d < 2; d++)
                    while (true) { ssize_t w = write(sv[d], buf.data(), buf.size()); if (w <= 0) break; }
            }
            sleep(8); _exit(0);
        }
        usleep(2000);
    }
''' + CPP_PARENT_TAIL
# Bộ nhớ chia sẻ ẩn danh qua mmap (seccomp không chặn): kiểm lớp OOM độc lập với seccomp.
CPP_SHARED = r'''#include <sys/mman.h>
#include <unistd.h>
#include <signal.h>
#include <sys/wait.h>
#include <cstdio>
int main() {
    for (int c = 0; c < 12; c++) {
        pid_t pid = fork();
        if (pid < 0) break;
        if (pid == 0) {
            volatile char* p = (char*)mmap(0, 200L << 20, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANONYMOUS, -1, 0);
            if (p == (char*)MAP_FAILED) _exit(3);
            for (long o = 0; o < (200L << 20); o += 4096) p[o] = 1;
            sleep(8); _exit(0);
        }
        usleep(5000);
    }
''' + CPP_PARENT_TAIL
PY_MEMFD_PRIVATE = r'''import os, sys
try:
    os.memfd_create("m")
    print("created")
except OSError as e:
    print("blocked", e.errno)
'''


@pytest.mark.parametrize("name,code", [
    ("12 con x 200 MiB", CPP_MANY.replace("@N@", "12").replace("@MIB@", "200")),
    ("40 con x 60 MiB (mỗi con nhỏ hơn RSS worker)", CPP_MANY.replace("@N@", "40").replace("@MIB@", "60")),
    ("socketpair 300 con", CPP_SOCK),
    ("mmap MAP_SHARED|MAP_ANONYMOUS 12 con", CPP_SHARED),
])
def test_memory_attacks_outside_the_candidates_own_rss_kill_the_candidate_not_the_worker(worker_like_rss, work_root,
                                                                                        name, code):
    kills = _oom_kills()
    body, _ = grade(work_root, "cpp", code, [case("", "survived\n", True)], time_limit_ms=20000, memory_limit_kib=262144)
    assert body["verdict"] == "RE" and body["re_kind"] == "SIGKILL", (name, body)
    assert _oom_kills() > kills, name  # OOM killer thật sự chạy và chọn thí sinh
    assert sandbox.uid_pids(sandbox.JUDGE_UID) == []
    body, _ = grade(work_root, "python", SUB["ac"]["python"], [case("1 2\n", "3\n", True)])
    assert body["verdict"] == "AC", body  # worker (tiến trình này) còn sống và chấm tiếp được


def test_python_memfd_is_blocked_by_seccomp_not_by_oom(work_root):
    body, _ = grade(work_root, "python", PY_MEMFD_PRIVATE, [case("", "x\n", True)])
    assert body["sample_stdout"] == "blocked 1\n", body


def test_sysv_shm_cannot_be_created_and_leaves_nothing(work_root):
    before = len([l for l in open("/proc/sysvipc/shm").read().splitlines()[1:]])
    body, _ = grade(work_root, "cpp", CPP_SYSCALLS, [case("", "x\n", True)])
    assert "shmget=-1/1" in body["sample_stdout"], body
    assert len(open("/proc/sysvipc/shm").read().splitlines()[1:]) == before


# ------------------------------------------------------------------------------- dạng argv của _wrap (không injection)

def test_wrap_passes_the_real_command_as_separate_args_and_never_builds_the_shell_string(work_root):
    nasty = ["./main; touch /tmp/pwned", "$(id)", "`id`", "a b", work_root + "/Main.cpp", "it's"]
    argv = sandbox._wrap(nasty, {"as": 1024, "nofile": 32, "core": 1}, 61000)
    assert argv[:4] == ["sh", "-c", sandbox._OOM_WRAP_SCRIPT, "sh"]
    script = argv[2]
    assert script == 'echo 1000 > /proc/self/oom_score_adj && exec "$@"'  # hằng, đúng một dạng
    assert work_root not in script and "Main" not in script and "main" not in script and "pwned" not in script
    assert argv[4] == "setpriv" and argv[-len(nasty):] == nasty  # lệnh thật nguyên vẹn, từng đối số riêng
    assert argv.count("-c") == 1  # không có sh -c thứ hai
