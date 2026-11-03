"""Bộ bài phá hoại tầng worker (PLAN mục 8, M5, nhóm B-E và SIGXFSZ), qua executor thật trong container test.

Mỗi ca chạy một chương trình thí sinh có chủ ý gây hại, rồi kiểm ba điều ngoài verdict của chính nó:
(1) không còn tiến trình hay entry của uid thí sinh (`/tmp`, `/dev/shm`, thư mục làm việc), (2) không rò fd của worker,
(3) một bài AC "nạn nhân" chấm ngay sau đó vẫn ra AC. Verdict kỳ vọng lấy từ số đo 3/11 (image test, cờ chuẩn,
`--cpus=2`); ca nào chấp nhận hai verdict thì ghi lý do ngay tại ca.

Hai ca chủ ý KHÔNG nằm ở đây: `#include "/etc/passwd"` làm `ce_message` hiện nội dung file đọc được của uid thí sinh
(đã ghi README phần hạn chế) và nhóm mạng (đã có ở `test_worker_netguard.py`)."""

import json
import os
import time

import pytest

from _worker_helpers import case, grade
from worker import sandbox

UID = sandbox.JUDGE_UID
VICTIM = "print('ok')\n"


def judge(work_root, lang, code, inp="", out="ok\n", time_ms=2000, limit_kib=262144, sample=True):
    return grade(work_root, lang, code, [case(inp, out, sample)], time_limit_ms=time_ms, memory_limit_kib=limit_kib)[0]


def verdict_of(body):
    return body["verdict"] + ("/" + body["re_kind"] if body.get("re_kind") else "")


def assert_infrastructure_unharmed(work_root, fd_before):
    assert sandbox.uid_pids(UID) == [], "còn tiến trình của uid thí sinh"
    assert sandbox.uid_entries(UID, ("/tmp", "/dev/shm", work_root)) == 0, "còn file của uid thí sinh"
    assert len(os.listdir("/proc/self/fd")) <= fd_before, "worker rò fd"
    started = time.monotonic()
    victim = judge(work_root, "python", VICTIM)
    assert victim["verdict"] == "AC", victim
    assert time.monotonic() - started < 5, "bài AC sau ca phá hoại chậm bất thường"


def run_case(work_root, lang, code, expect, **kw):
    fd_before = len(os.listdir("/proc/self/fd"))
    body = judge(work_root, lang, code, **kw)
    assert verdict_of(body) in expect.split("|"), body
    assert_infrastructure_unharmed(work_root, fd_before)
    return body


# ------------------------------------------------------------------------------------ B. hệ tệp

PY_FS = [
    ("symlink_ra_etc_work_srv", "AC",
     "import os\nos.symlink('/etc', 'l1')\nos.symlink('/work', '/tmp/l2')\nos.symlink('/srv', 'l3')\nprint('ok')\n"),
    ("hardlink_toi_etc_passwd", "AC",  # không tạo được hardlink tới file của root (protected_hardlinks hoặc EPERM): bắt OSError
     "import os\ntry:\n    os.link('/etc/passwd', 'hl')\n    print('LINKED')\nexcept OSError:\n    print('ok')\n"),
    ("fifo_khong_mo", "AC",
     "import os\nos.mkfifo('f1')\nos.mkfifo('/tmp/f2')\nprint('ok')\n"),
    ("fifo_mo_ghi_chan_mai", "TLE",  # open() chặn tới khi hết giờ; dọn dẹp không được treo theo
     "import os\nos.mkfifo('f1')\nopen('f1', 'w')\nprint('ok')\n"),
    ("thu_muc_long_sau_3000_cap", "AC|RE/EXIT_NONZERO",  # đến giới hạn độ dài đường dẫn thì Python ném lỗi: cả hai đều hợp lệ
     "import os\nfor i in range(3000):\n    os.mkdir('d')\n    os.chdir('d')\nprint('ok')\n"),
    ("file_thua_64MiB_tru_1", "AC",
     "f = open('s', 'wb')\nf.seek(64 * 1024 * 1024 - 1)\nf.write(b'x')\nf.close()\nprint('ok')\n"),
    ("file_thua_vuot_FSIZE", "RE/EXIT_NONZERO",  # SIGXFSZ bị bỏ qua (kế thừa SIG_IGN), write ra EFBIG
     "f = open('s', 'wb')\nf.seek(1 << 30)\nf.write(b'x')\nf.close()\nprint('ok')\n"),
    ("ten_file_la", "AC",
     "import os\nopen('-rf', 'w').close()\nopen('a\\nb', 'w').close()\nopen('x' * 255, 'w').close()\n"
     "open('/tmp/-rf', 'w').close()\nos.mkdir('/tmp/d d')\nprint('ok')\n"),
    ("chmod_000_thu_muc", "AC",
     "import os\nos.mkdir('d')\nopen('d/f', 'w').close()\nos.chmod('d', 0)\nprint('ok')\n"),
]


@pytest.mark.parametrize("name,expect,code", PY_FS, ids=[c[0] for c in PY_FS])
def test_filesystem_abuse_is_contained_and_cleaned_up(work_root, name, expect, code):
    run_case(work_root, "python", code, expect, time_ms=200 if expect == "TLE" else 2000)


def test_symlink_to_etc_does_not_touch_the_target(work_root):
    before = os.stat("/etc/passwd").st_mtime_ns
    run_case(work_root, "python", PY_FS[0][2], "AC")
    assert os.stat("/etc/passwd").st_mtime_ns == before
    assert os.path.isdir("/etc")


# ---------------------------------------------------------------------------- B'. SIGXFSZ ở C++/Java

CPP_FSIZE_RAW = ('#include <unistd.h>\n#include <fcntl.h>\n#include <cstdio>\nint main() {\n'
                 '  int fd = open("s", O_WRONLY | O_CREAT, 0644); lseek(fd, 1L << 30, SEEK_SET);\n'
                 '  ssize_t r = write(fd, "x", 1); printf("r=%zd\\n", r); }\n')
CPP_FSIZE_STDIO = ('#include <cstdio>\nint main() { FILE* f = fopen("s", "wb"); fseek(f, 1L << 30, SEEK_SET); fputc(1, f);\n'
                   '  fflush(f); puts("ok"); fclose(f); }\n')
JAVA_FSIZE = ('import java.io.*;\npublic class Main { public static void main(String[] a) throws Exception {\n'
              '  RandomAccessFile f = new RandomAccessFile("s", "rw"); f.seek(1L << 30); f.write(1); System.out.println("ok"); } }\n')


def test_cpp_write_past_fsize_gets_efbig_not_a_signal(work_root):
    """Đo 3/11: SIGXFSZ không tới tiến trình thí sinh (disposition SIG_IGN được kế thừa qua exec), `write` trả -1/EFBIG.
    Chương trình in ra đúng điều đó nên ra WA (đáp án mong 'x'), không phải RE/SIGXFSZ và không để lại file 1 GiB."""
    body = run_case(work_root, "cpp", CPP_FSIZE_RAW, "WA", out="x\n")
    assert body["sample_stdout"] == "r=-1\n"


def test_cpp_stdio_write_past_fsize_is_silent_and_harmless(work_root):
    run_case(work_root, "cpp", CPP_FSIZE_STDIO, "AC")


def test_java_write_past_fsize_is_an_exception_hence_re(work_root):
    run_case(work_root, "java", JAVA_FSIZE, "RE/EXIT_NONZERO")


# ----------------------------------------------------------------------------- C. stdin / stdout

PY_IO = [
    ("khong_doc_stdin_2MiB", "AC", "print('ok')\n", "1 " * (1 << 20) + "\n", "ok\n"),
    ("dong_stdout_roi_chay_tiep", "WA", "import os, sys\nos.close(1)\nsys.exit(0)\n", "", "ok\n"),
    ("in_NUL_va_utf8_hong", "WA", "import sys\nsys.stdout.buffer.write(b'\\x00\\xff\\xfe ok\\n')\n", "", "x\n"),
    ("mot_dong_100MB", "WA",
     "import sys\nw = sys.stdout.buffer\nfor i in range(100):\n    w.write(b'a' * (1 << 20))\n", "", "ok\n"),
    ("khong_in_gi", "WA", "pass\n", "", "ok\n"),
    ("in_roi_segfault", "RE/SIGSEGV",
     "import ctypes\nprint('partial', flush=True)\nctypes.string_at(0)\n", "", "ok\n"),
    ("stderr_100MB_stdout_dung", "AC",  # stderr không được chấm và bị chặn trần
     "import sys\nfor i in range(100):\n    sys.stderr.write('e' * (1 << 20))\nprint('ok')\n", "", "ok\n"),
    ("doc_stdin_qua_EOF", "AC",
     "import sys\nsys.stdin.read()\nsys.stdin.read()\nprint('ok' if sys.stdin.read() == '' else 'bad')\n", "", "ok\n"),
]


@pytest.mark.parametrize("name,expect,code,inp,out", PY_IO, ids=[c[0] for c in PY_IO])
def test_stdio_abuse_gets_a_clean_verdict(work_root, name, expect, code, inp, out):
    body = run_case(work_root, "python", code, expect, inp=inp, out=out)
    # dữ liệu thí sinh in ra không bao giờ làm hỏng JSON/UTF-8 của kết quả
    assert json.loads(json.dumps(body, ensure_ascii=False).encode("utf-8").decode("utf-8")) == body
    assert len(body.get("sample_stdout") or "") <= 4096


# -------------------------------------------------------------------------- D. tiến trình / tín hiệu

PY_PROC = [
    ("bo_qua_SIGTERM", "TLE", "import signal\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nwhile True:\n    pass\n"),
    ("tu_SIGSTOP", "TLE", "import os, signal\nos.kill(os.getpid(), signal.SIGSTOP)\nprint('ok')\n"),
    ("python_thread_bomb", "AC|MLE|RE/EXIT_NONZERO|TLE",  # trần pids/RLIMIT_AS dừng vòng lặp; cách dừng phụ thuộc máy
     "import threading, time\ntry:\n    while True:\n        threading.Thread(target=time.sleep, args=(60,), daemon=True).start()\n"
     "except Exception:\n    print('ok')\n"),
    ("zombie_flood", "AC|TLE|RE/EXIT_NONZERO",
     "import os, time\ntry:\n    for i in range(2000):\n        if os.fork() == 0:\n            os._exit(0)\nexcept OSError:\n    pass\n"
     "print('ok')\ntime.sleep(0.2)\n"),
    ("con_giu_stdout_sau_khi_cha_thoat", "AC",
     "import os, time\nif os.fork() == 0:\n    time.sleep(100)\n    os._exit(0)\nprint('ok')\n"),
    ("kill_tat_ca_-1", "AC|RE/SIGKILL", "import os\nos.kill(-1, 9)\nprint('ok')\n"),
    ("nang_RLIMIT_AS_roi_cap_600MiB", "MLE",
     "import resource\ntry:\n    resource.setrlimit(resource.RLIMIT_AS, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))\nexcept Exception:\n    pass\n"
     "b = bytearray(600 << 20)\nprint('ok')\n"),
    ("ptrace_tini", "WA",
     "import ctypes\nlibc = ctypes.CDLL(None, use_errno=True)\nr = libc.ptrace(16, 1, 0, 0)\nprint('ptrace', r, ctypes.get_errno())\n"),
]


@pytest.mark.parametrize("name,expect,code", PY_PROC, ids=[c[0] for c in PY_PROC])
def test_process_and_signal_abuse_leaves_no_trace(work_root, name, expect, code):
    run_case(work_root, "python", code, expect, out="x\n" if expect == "WA" else "ok\n",
             time_ms=200 if expect == "TLE" else 2000)


def test_reading_pid1_memory_files_is_denied(work_root):
    code = ("out = []\nfor p in ('/proc/1/environ', '/proc/1/mem', '/proc/1/cmdline'):\n    try:\n"
            "        out.append(p + ' ' + str(len(open(p, 'rb').read(64))))\n    except Exception as e:\n"
            "        out.append(p + ' ' + type(e).__name__)\nprint(out)\n")
    body = run_case(work_root, "python", code, "WA", out="x\n")
    shown = body["sample_stdout"]
    assert "PermissionError" in shown or "OSError" in shown or "FileNotFoundError" in shown, shown
    assert not any(f"{p} " + d in shown for p in ("/proc/1/environ", "/proc/1/mem") for d in "123456789")


# ------------------------------------------------------------------------------------ E. biên dịch

CPP_TEMPLATE = ("template<int N> struct F { static const int v = F<N - 1>::v + 1; };\n"
                "template<> struct F<0> { static const int v = 0; };\nint main() { return F<100000>::v; }\n")
CPP_CONSTEXPR = ("constexpr long f() { long s = 0; for (long i = 0; i < (1L << 40); i++) s += i; return s; }\n"
                 "static_assert(f() > 0, \"x\");\nint main() {}\n")
CPP_MACRO = ("#define A0 x\n" + "".join(f"#define A{i+1} A{i} A{i} A{i} A{i} A{i} A{i} A{i} A{i} A{i} A{i}\n" for i in range(12))
             + "int A12;\nint main() {}\n")

CPP_BOMBS = [("template_de_quy", CPP_TEMPLATE), ("constexpr_lap_2_40", CPP_CONSTEXPR),
             ("include_chinh_no", '#include __FILE__\nint main() {}\n'), ("macro_no", CPP_MACRO)]


@pytest.mark.parametrize("name,code", CPP_BOMBS, ids=[c[0] for c in CPP_BOMBS])
def test_cpp_compile_bombs_are_ce_in_bounded_time(work_root, name, code):
    started = time.monotonic()
    body = run_case(work_root, "cpp", code, "CE")
    assert time.monotonic() - started < 15
    assert len(body["ce_message"]) <= 4096


@pytest.mark.parametrize("path", ["/app/worker/main.py", "/proc/1/environ", "/run/secrets/x"])
def test_cpp_include_of_files_the_candidate_cannot_read_is_ce_without_their_content(work_root, path):
    body = run_case(work_root, "cpp", f'#include "{path}"\nint main() {{}}\n', "CE")
    for marker in ("JUDGE_UID", "REGISTER", "WORKER_SECRET", "PATH="):
        assert marker not in body["ce_message"], body["ce_message"]


JAVA_CASES = [
    ("package_x", "CE", 'package x;\npublic class Main { public static void main(String[] a) { System.out.println("ok"); } }\n'),
    ("public_class_foo", "CE", 'public class Foo { public static void main(String[] a) { System.out.println("ok"); } }\n'),
    ("class_foo_khong_public", "CE", 'class Foo { public static void main(String[] a) { System.out.println("ok"); } }\n'),
    ("hai_lop_A_va_Main", "AC",
     'class A { static String s() { return "ok"; } }\npublic class Main { public static void main(String[] a) { System.out.println(A.s()); } }\n'),
    ("class_Main_khong_public", "AC", 'class Main { public static void main(String[] a) { System.out.println("ok"); } }\n'),
    ("NUL_trong_nguon", "CE", 'public class Main { public static void main(String[] a) { System.out.println("ok"); }\x00 }\n'),
]


@pytest.mark.parametrize("name,expect,code", JAVA_CASES, ids=[c[0] for c in JAVA_CASES])
def test_java_source_layout_abuse_gets_ce_or_ac_never_re_or_se(work_root, name, expect, code):
    run_case(work_root, "java", code, expect)
