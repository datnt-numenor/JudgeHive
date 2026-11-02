"""Ranh giới MLE / RE ở cả ba ngôn ngữ (M4, D13, D28, D29), qua executor thật trong container test.

Số đo gốc (2/11, giới hạn 256 MiB, `--cpus=2`): Python AC tới 240 MiB, MLE từ 245; C++ AC tới 245, MLE từ 250; Java mảng đơn
AC tới ~150-160 MiB (baseline `-Xmx` + SerialGC), MLE từ 180; hello world AC ở 64 và 512 MiB cả ba. Các giá trị trong file
này chọn có biên an toàn quanh các ngưỡng đó. Hai đường hết bộ nhớ: (A) vượt `RLIMIT_AS`/`-Xmx` của chính tiến trình → MLE;
(B) cgroup cạn do nhiều tiến trình → OOM killer → RE/SIGKILL khi RSS đỉnh dưới 90% giới hạn."""

import pytest

from _submissions import SUB
from _worker_helpers import case, grade

LANGS = ["python", "cpp", "java"]
MiB = 1024
LIMIT = 256 * MiB  # KiB

PY_TOUCH = "n = int(input())\nb = bytearray(n << 20)\nfor i in range(0, len(b), 4096):\n    b[i] = 1\nprint('ok')\n"
CPP_TOUCH = r'''#include <cstdio>
int main() { long n; scanf("%ld", &n); volatile char* p = new char[(size_t)n << 20];
  for (size_t i = 0; i < ((size_t)n << 20); i += 4096) p[i] = 1; puts("ok"); }
'''
JAVA_TOUCH = r'''import java.util.*;
public class Main { public static void main(String[] a) { Scanner s = new Scanner(System.in); int n = s.nextInt();
  byte[] b = new byte[n << 20]; for (int i = 0; i < b.length; i += 4096) b[i] = 1; System.out.println("ok"); } }
'''
TOUCH = {"python": PY_TOUCH, "cpp": CPP_TOUCH, "java": JAVA_TOUCH}
HELLO = {"python": "print('ok')\n", "cpp": '#include <cstdio>\nint main() { puts("ok"); }\n',
         "java": 'public class Main { public static void main(String[] a) { System.out.println("ok"); } }\n'}
# dùng được trong giới hạn 256 MiB: Python/C++ tới giới hạn − ~14/~8 MiB (RLIMIT_AS tính cả runtime);
# Java: mảng đơn chỉ ~60% (heap cũ = 2/3 `-Xmx` với SerialGC; D29 sẽ chốt cờ JVM, đổi số này khi đó)
SAFE_MIB = {"python": 230, "cpp": 235, "java": 100}


def run(work_root, lang, code, inp="", out="ok\n", limit_kib=LIMIT, time_ms=5000):
    return grade(work_root, lang, code, [case(inp, out, True)], memory_limit_kib=limit_kib, time_limit_ms=time_ms)[0]


# ------------------------------------------------------------------------------- đường A: vượt giới hạn của chính nó

@pytest.mark.parametrize("lang", LANGS)
def test_allocating_clearly_inside_the_limit_is_ac(work_root, lang):
    body = run(work_root, lang, TOUCH[lang], f"{SAFE_MIB[lang]}\n")
    assert body["verdict"] == "AC", body
    assert body["memory_kib"] >= SAFE_MIB[lang] * MiB * 0.9  # báo RSS thật, không bị cắt về giới hạn


@pytest.mark.parametrize("lang", LANGS)
def test_allocating_a_little_over_the_limit_is_mle(work_root, lang):
    body = run(work_root, lang, TOUCH[lang], f"{256 + 8}\n")
    assert body["verdict"] == "MLE" and body["failed_test"] == 1, body


@pytest.mark.parametrize("limit_mib", [64, 512])
@pytest.mark.parametrize("lang", LANGS)
def test_hello_world_fits_the_smallest_and_largest_limits_and_over_by_8_is_mle(work_root, lang, limit_mib):
    """Sàn/trần của giới hạn đề (D23 mục 9): Java hello phải khởi động được dưới `-Xmx64m` (RSS ~37 MiB)."""
    ok = run(work_root, lang, HELLO[lang], limit_kib=limit_mib * MiB)
    assert ok["verdict"] == "AC", ok
    over = run(work_root, lang, TOUCH[lang], f"{limit_mib + 8}\n", limit_kib=limit_mib * MiB)
    assert over["verdict"] == "MLE", over


@pytest.mark.parametrize("lang", LANGS)
def test_memory_hogs_are_mle_with_the_seed_time_limit(work_root, lang):
    """Đề seed `aplusb` (1000 ms; hệ số Python 5 / Java 2 / C++ 1): không chương trình nào ra TLE trước MLE."""
    for name in ("big_block", "grow"):
        body = run(work_root, lang, SUB[name][lang], out="x\n", time_ms=1000)
        assert body["verdict"] == "MLE", (name, body)


@pytest.mark.parametrize("lang,code", [
    ("python", "import sys\nn = int(input())\ntry:\n    b = bytearray(n << 20)\n    print('ok')\nexcept MemoryError:\n    sys.exit(1)\n"),
    ("cpp", '#include <cstdio>\n#include <new>\nint main() { long n; scanf("%ld", &n);\n'
            '  try { volatile char* p = new char[(size_t)n << 20]; p[0] = 1; puts("ok"); } catch (std::bad_alloc&) { return 1; } }\n'),
    ("java", 'import java.util.*;\npublic class Main { public static void main(String[] a) { int n = new Scanner(System.in).nextInt();\n'
             '  try { byte[] b = new byte[n << 20]; b[0] = 1; System.out.println("ok"); } catch (OutOfMemoryError e) { System.exit(1); } } }\n'),
])
def test_caught_oom_then_exit_1_is_re_not_mle(work_root, lang, code):
    """Chương trình tự bắt lỗi cấp phát rồi thoát mã 1 không in gì: không còn chữ ký, RSS thấp: RE (chọn của chương trình)."""
    body = run(work_root, lang, code, "264\n")
    assert body["verdict"] == "RE" and body["re_kind"] == "EXIT_NONZERO", body


def test_java_oom_in_a_side_thread_with_main_exiting_0_is_not_a_memory_verdict(work_root):
    """Giới hạn thiết kế (README): OOM ở thread phụ mà main vẫn thoát mã 0 thì không có thoát bất thường, nên ra AC."""
    code = ('public class Main { public static void main(String[] a) throws Exception {\n'
            '  Thread t = new Thread(() -> { long[] x = new long[1 << 28]; x[0] = 1; }); t.start(); t.join();\n'
            '  System.out.println("ok"); } }\n')
    assert run(work_root, "java", code)["verdict"] == "AC"


def test_java_thread_exhaustion_is_mle_by_its_oom_signature(work_root):
    code = ('public class Main { public static void main(String[] a) throws Exception {\n'
            '  for (;;) { Thread t = new Thread(() -> { try { Thread.sleep(100000); } catch (Exception e) {} });\n'
            '    t.setDaemon(true); t.start(); } } }\n')
    assert run(work_root, "java", code, out="x\n")["verdict"] == "MLE"


@pytest.mark.parametrize("lang,kind", [("python", "EXIT_NONZERO"), ("cpp", "SIGSEGV"), ("java", "EXIT_NONZERO")])
def test_unbounded_recursion_is_re_not_mle(work_root, lang, kind):
    codes = {
        "python": "def f(n):\n    return f(n + 1)\nf(0)\n",
        "cpp": '#include <cstdio>\nint f(int n) { volatile char b[1024]; b[0] = 1; return f(n + 1) + b[0]; }\nint main() { return f(0); }\n',
        "java": "public class Main { static int f(int n) { return f(n + 1) + 1; } public static void main(String[] a) { f(0); } }\n",
    }
    body = run(work_root, lang, codes[lang], out="x\n")
    assert body["verdict"] == "RE" and body["re_kind"] == kind, body  # C++ stack overflow: ảnh nạp nhỏ nên không bị xếp MLE


# ------------------------------------------------------- C++: mảng toàn cục / ảnh nạp lớn hơn giới hạn (D29 mục 1)

def bss(mib, tail):
    return ('#include <cstdio>\nstatic volatile char a[%dULL << 20];\nint main() { a[0] = 1; a[(%dULL << 20) - 1] = 2; %s }\n'
            % (mib, mib, tail))


PRINT3 = 'printf("%d\\n", a[0] + a[(@ULL << 20) - 1]);'


def bss_ok(mib):
    return bss(mib, PRINT3.replace("@", str(mib)))


def test_cpp_global_array_over_the_limit_is_mle_not_sigsegv(work_root):
    for mib in (300, 1024):
        body = run(work_root, "cpp", bss_ok(mib), out="3\n")
        assert body["verdict"] == "MLE", (mib, body)


def test_cpp_global_array_inside_the_limit_is_ac(work_root):
    body = run(work_root, "cpp", bss_ok(200), out="3\n")
    assert body["verdict"] == "AC", body


def test_cpp_global_vector_that_throws_bad_alloc_is_mle(work_root):
    code = '#include <cstdio>\n#include <vector>\nstd::vector<int> v(1 << 28);\nint main() { v[0] = 1; puts("ok"); }\n'
    assert run(work_root, "cpp", code)["verdict"] == "MLE"


NULL_DEREF = "volatile int* p = 0; *p = 1; puts(\"unreachable\");"


def test_real_segfault_with_a_small_image_stays_re(work_root):
    body = run(work_root, "cpp", bss(100, NULL_DEREF), out="x\n")
    assert body["verdict"] == "RE" and body["re_kind"] == "SIGSEGV", body


def test_boundary_case_real_segfault_with_an_image_in_the_slack_band_is_classified_mle(work_root):
    """Ca biên đã ghi trong README: ảnh nạp nằm trong [giới hạn − 12 MiB, giới hạn] mà segfault thật (con trỏ sai) cũng bị
    xếp MLE vì không phân biệt được với `.bss` không map nổi. Test minh họa hành vi này, không phải điều mong muốn."""
    body = run(work_root, "cpp", bss(250, NULL_DEREF), out="x\n")  # 250 MiB > 256 − 12 MiB = 244 MiB
    assert body["verdict"] == "MLE", body


def test_malloc_result_used_without_a_null_check_is_a_plain_segfault_re(work_root):
    """Không phải ảnh nạp: `malloc` trả NULL rồi chương trình ghi vào đó là lỗi của chương trình (RE/SIGSEGV)."""
    code = '#include <cstdio>\n#include <cstdlib>\nint main() { volatile char* p = (char*)malloc(1ULL << 30); p[0] = 1; puts("ok"); }\n'
    body = run(work_root, "cpp", code)
    assert body["verdict"] == "RE" and body["re_kind"] == "SIGSEGV", body


# ----------------------------------------------- đường B: nhiều tiến trình, cgroup cạn (D28), worker vẫn sống

PY_FORK = r'''import os, time
N, MIB = @N@, @MIB@
pids = []
for c in range(N):
    pid = os.fork()
    if pid == 0:
        b = bytearray(MIB << 20)
        for i in range(0, len(b), 4096):
            b[i] = 1
        time.sleep(3)
        os._exit(0)
    pids.append(pid)
    time.sleep(0.005)
bad = 0
for p in pids:
    _, st = os.waitpid(p, 0)
    if st != 0:
        bad += 1
if bad:
    os.kill(os.getpid(), 9)
print("survived")
'''
CPP_FORK = r'''#include <unistd.h>
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
            sleep(3); _exit(0);
        }
        usleep(5000);
    }
    int bad = 0, st;
    while (wait(&st) > 0) if (!WIFEXITED(st) || WEXITSTATUS(st)) bad++;
    if (bad) kill(getpid(), SIGKILL);
    puts("survived");
}
'''
FORK = {"python": PY_FORK, "cpp": CPP_FORK}


def oom_kills():
    with open("/sys/fs/cgroup/memory.events", encoding="utf-8") as f:
        return int(dict(line.split() for line in f)["oom_kill"])


@pytest.mark.parametrize("lang", ["python", "cpp"])
def test_many_children_over_the_cgroup_budget_is_re_sigkill_and_the_worker_survives(work_root, lang):
    """Mỗi con 200 MiB (< RLIMIT_AS 256 MiB), 10 con = 2 GB > cgroup 1 GiB: OOM killer giết thí sinh. RSS đỉnh đo được
    (~201-206 MiB) dưới 90% giới hạn (230 MiB) nên ra RE/SIGKILL (D28 mục 5), không phải MLE."""
    before = oom_kills()
    body = run(work_root, lang, FORK[lang].replace("@N@", "10").replace("@MIB@", "200"), out="survived\n", time_ms=10000)
    assert body["verdict"] == "RE" and body["re_kind"] == "SIGKILL", body
    assert oom_kills() > before  # chính OOM killer của cgroup đã chạy
    assert run(work_root, lang, HELLO[lang], out="ok\n")["verdict"] == "AC"  # worker/sandbox còn dùng được


@pytest.mark.parametrize("lang", ["python", "cpp"])
def test_many_children_inside_the_cgroup_budget_are_not_killed(work_root, lang):
    before = oom_kills()
    body = run(work_root, lang, FORK[lang].replace("@N@", "3").replace("@MIB@", "150"), out="survived\n", time_ms=10000)
    assert body["verdict"] == "AC", body
    assert oom_kills() == before
