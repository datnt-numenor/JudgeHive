"""Bộ bài nộp phá hoại qua stack thật (PLAN mục 8, M5, tầng 2): server + 3 worker bằng `docker compose`.

Chạy trên máy host (cần Docker và Python 3), từ thư mục source/:

    python tests/e2e/destructive.py

Dùng project compose riêng `judgehive-destructive` (xóa volume khi xong), cần cổng 8000 và 9000 trống. Tái dùng hàm của
`e2e.py`. Mỗi ca nộp bằng tài khoản admin (bài admin không vào bảng xếp hạng, D8) và được kiểm: verdict, rồi hạ tầng
(không container nào OOMKilled hay restart, không tiến trình của uid thí sinh còn sót, ba worker vẫn chạy) và một bài AC
"nạn nhân" của người dùng thường vẫn ra AC trong hạn. Tầng 1 (nhóm B-E đầy đủ) ở `tests/worker/test_worker_destructive.py`.
Thoát 0 nếu mọi kiểm tra đạt, 1 nếu có hỏng."""

import json
import os
import secrets
import subprocess
import sys
import time

import e2e
from e2e import PASSWORD, SOURCE, check, compose, detail, http_call, login, manage, submit, wait_judged

e2e.PROJECT = "judgehive-destructive"
ADMINS = [f"root{i}" for i in range(1, 5)]  # xoay vòng: rate limit 10 bài/60 s cho mỗi tài khoản
ADMIN = ADMINS[0]
USERS = [f"u{i}" for i in range(1, 6)]
VICTIM_CODE = "import sys\nprint(sum(map(int, sys.stdin.read().split())))\n"
VICTIM_LIMIT_S = 60  # p90 trong load test là 10 s; 60 s là chỗ chắc chắn có vấn đề

PROC_SNAPSHOT = (  # chạy trong container worker: số tiến trình của uid thí sinh và số fd của tiến trình worker (nếu đọc được)
    "import os\nn = 0\nfd = None\n"
    "for p in os.listdir('/proc'):\n    if not p.isdigit():\n        continue\n    try:\n"
    "        st = open(f'/proc/{p}/status').read()\n"
    "        if any(l.startswith('Uid:') and l.split()[1] == '61000' for l in st.splitlines()):\n            n += 1\n"
    "        if b'worker' in open(f'/proc/{p}/cmdline', 'rb').read() and p != str(os.getpid()):\n"
    "            fd = len(os.listdir(f'/proc/{p}/fd'))\n"
    "    except OSError:\n        pass\n"
    "print(n, fd)\n")


def worker_ids():
    return compose("ps", "-q", "worker").stdout.split()


def inspect_workers(ids):
    """{ID: (đang chạy, cờ OOMKilled, số restart, StartedAt)}. Cờ `OOMKilled` của Docker là cờ dính bật khi OOM killer của
    cgroup giết BẤT KỲ tiến trình nào trong container, kể cả con của thí sinh (đúng thiết kế D28: giết con, worker sống),
    nên nó không dùng để kết luận worker bị giết: dùng Running + RestartCount + StartedAt không đổi."""
    out = subprocess.run(["docker", "inspect", "-f", "{{.Id}} {{.State.Running}} {{.State.OOMKilled}} {{.RestartCount}} {{.State.StartedAt}}",
                          *ids], capture_output=True, text=True, timeout=60).stdout.splitlines()
    return {f[0]: (f[1] == "true", f[2] == "true", int(f[3]), f[4]) for f in (line.split() for line in out) if len(f) == 5}


def proc_snapshot(cid):
    out = subprocess.run(["docker", "exec", cid, "python", "-c", PROC_SNAPSHOT], capture_output=True, text=True, timeout=60).stdout.split()
    return int(out[0]), (None if out[1] == "None" else int(out[1]))


class Harness:
    def __init__(self):
        self.cookies = {}
        self.ids = []
        self.base = {}
        self.fd = {}
        self.turn = 0
        self.admin_turn = 0
        self.last_cookie = None
        self.oom_noted = []

    def admin(self):
        self.admin_turn += 1
        return self.cookies[ADMINS[self.admin_turn % len(ADMINS)]]

    def snapshot(self):
        self.ids = worker_ids()
        self.base = inspect_workers(self.ids)
        self.fd = {c: proc_snapshot(c)[1] for c in self.ids}

    def victim(self, tag):
        """Nộp một bài AC của người dùng thường (xoay vòng u1..u5, mỗi người ≤ trần bài chờ) và trả (verdict, giây)."""
        user = USERS[self.turn % len(USERS)]
        self.turn += 1
        started = time.monotonic()
        sid, _ = submit(self.cookies[user], "python", VICTIM_CODE)
        row = wait_judged(self.cookies[user], sid, f"nạn nhân sau {tag}", timeout=VICTIM_LIMIT_S * 2)
        return row["verdict"], time.monotonic() - started

    def infrastructure(self, tag):
        states = inspect_workers(self.ids)
        check(f"[{tag}] ba worker vẫn chạy, cùng lần khởi động, không restart",
              len(states) == len(self.ids) and all(running and restarts == self.base[c][2] and started == self.base[c][3]
                                                    for c, (running, _, restarts, started) in states.items()), str(states))
        oom_now = sorted(c[:12] for c, st in states.items() if st[1] and not self.base[c][1])
        if oom_now and oom_now != self.oom_noted:
            self.oom_noted = oom_now
            print(f"NOTE [{tag}] OOM killer đã giết tiến trình con trong container {oom_now} (cờ OOMKilled dính; worker vẫn sống, D28)",
                  flush=True)
        leftovers = {}
        for cid in self.ids:
            procs, fds = proc_snapshot(cid)
            leftovers[cid[:12]] = procs
            if fds is not None and self.fd[cid] is not None:
                check(f"[{tag}] worker {cid[:12]} không rò fd", fds <= self.fd[cid], f"{self.fd[cid]} -> {fds}")
        check(f"[{tag}] không tiến trình thí sinh còn sót", all(v == 0 for v in leftovers.values()), str(leftovers))
        verdict, seconds = self.victim(tag)
        check(f"[{tag}] bài AC của người khác sau ca vẫn AC trong {VICTIM_LIMIT_S}s", verdict == "AC" and seconds < VICTIM_LIMIT_S,
              f"{verdict} {seconds:.1f}s")

    def run(self, tag, lang, code, expect, wait=240):
        cookie = self.last_cookie = self.admin()
        sid, _ = submit(cookie, lang, code)
        row = wait_judged(cookie, sid, f"ca {tag}", timeout=wait)
        got = row["verdict"] + (("/" + row["re_kind"]) if row.get("re_kind") else "")
        check(f"[{tag}] verdict {expect}", got in expect.split("|"), f"được {got}")
        self.infrastructure(tag)
        return sid, detail(cookie, sid)


# --------------------------------------------------------------------------------------------------- các nhóm ca

NET_PROBE = (
    "import socket, errno\nr = []\n"
    "for addr in (('server', 9000), ('server', 8000), ('1.1.1.1', 53), ('8.8.8.8', 443)):\n"
    "    try:\n        s = socket.socket()\n        s.settimeout(3)\n        s.connect(addr)\n        r.append('OPEN ' + str(addr))\n"
    "    except OSError as e:\n        r.append('blocked ' + errno.errorcode.get(e.errno, str(e.errno)))\n"
    "try:\n    socket.getaddrinfo('example.com', 80)\n    r.append('DNS-OK')\nexcept OSError:\n    r.append('blocked DNS')\n"
    "import sys\nopen_ = any(x.startswith(('OPEN', 'DNS-OK')) for x in r)\n"
    "print(-1 if open_ else sum(map(int, sys.stdin.read().split())))\n")
NET_FLOOD = (
    "import socket\nok = 0\nfor i in range(1000):\n    try:\n        s = socket.socket()\n        s.settimeout(0.2)\n"
    "        s.connect(('server', 9000))\n        ok += 1\n    except OSError:\n        pass\n"
    "import sys\nprint(-1 if ok else sum(map(int, sys.stdin.read().split())))\n")
FORK_BOMB = "#include <unistd.h>\nint main() { for (;;) fork(); }\n"
TMP_FLOOD = ('#include <cstdio>\n#include <cstring>\nint main() { static char b[1 << 20]; memset(b, 1, sizeof b);\n'
             '  const char* paths[] = {"/tmp/f", "/dev/shm/f", "f"}; for (int k = 0; k < 3; k++) {\n'
             '    FILE* f = fopen(paths[k], "wb"); if (!f) continue; for (int i = 0; i < 200; i++) if (fwrite(b, 1, sizeof b, f) != sizeof b) break;\n'
             '    fclose(f); }\n  puts("done"); }\n')
MANY_CHILDREN = ('#include <unistd.h>\n#include <sys/wait.h>\n#include <cstdlib>\n#include <cstdio>\n'
                 'int main() { for (int c = 0; c < 12; c++) { pid_t p = fork(); if (p < 0) break;\n'
                 '    if (p == 0) { volatile char* m = (char*)malloc(200L << 20); if (!m) _exit(3);\n'
                 '      for (long o = 0; o < (200L << 20); o += 4096) m[o] = 1; sleep(3); _exit(0); }\n    usleep(5000); }\n'
                 '  while (wait(nullptr) > 0) {}\n  puts("done"); }\n')


def group_b(h):
    print("== B: hệ tệp", flush=True)
    h.run("B symlink ra /etc /work", "python",
          "import os\nos.symlink('/etc', 'l1')\nos.symlink('/work', '/tmp/l2')\nprint(0)\n", "WA")
    h.run("B FIFO mở ghi chặn mãi", "python", "import os\nos.mkfifo('f1')\nopen('f1', 'w')\nprint(0)\n", "TLE")
    h.run("B file thưa vượt giới hạn", "python", "f = open('s', 'wb')\nf.seek(1 << 30)\nf.write(b'x')\nf.close()\nprint(0)\n", "RE/EXIT_NONZERO")
    h.run("B C++ ghi vượt FSIZE (không SIGXFSZ)", "cpp",
          '#include <unistd.h>\n#include <fcntl.h>\nint main() { int fd = open("s", O_WRONLY|O_CREAT, 0644); lseek(fd, 1L<<30, SEEK_SET);\n'
          '  return write(fd, "x", 1) < 0; }\n', "RE/EXIT_NONZERO")


def group_c(h):
    print("== C: stdin/stdout", flush=True)
    h.run("C một dòng 100 MB", "python",
          "import sys\nfor i in range(100):\n    sys.stdout.buffer.write(b'a' * (1 << 20))\n", "WA")
    h.run("C đóng stdout", "python", "import os\nos.close(1)\n", "WA")
    h.run("C in rồi segfault", "python", "import ctypes\nprint('x', flush=True)\nctypes.string_at(0)\n", "RE/SIGSEGV")


def group_d(h):
    print("== D: tiến trình / tín hiệu", flush=True)
    h.run("D fork bomb C++", "cpp", FORK_BOMB, "RE|TLE|MLE", wait=300)
    h.run("D 12 con x 200 MiB (OOM killer của cgroup, D28)", "cpp", MANY_CHILDREN, "WA|RE|MLE|TLE", wait=300)
    h.run("D ghi 3 x 200 MiB vào /tmp, /dev/shm, thư mục bài", "cpp", TMP_FLOOD, "WA|RE|MLE|TLE", wait=300)
    h.run("D bỏ qua SIGTERM", "python", "import signal\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nwhile True:\n    pass\n", "TLE")
    h.run("D tự SIGSTOP", "python", "import os, signal\nos.kill(os.getpid(), signal.SIGSTOP)\n", "TLE")
    h.run("D con giữ stdout sau khi cha thoát", "python",
          "import os, time\nif os.fork() == 0:\n    time.sleep(100)\n    os._exit(0)\nprint(0)\n", "WA")


def group_e(h):
    print("== E: biên dịch", flush=True)
    macro = ("#define A0 x\n" + "".join(f"#define A{i+1} " + " ".join([f"A{i}"] * 10) + "\n" for i in range(12)) + "int A12;\nint main() {}\n")
    h.run("E C++ macro nổ", "cpp", macro, "CE")
    h.run("E C++ template đệ quy sâu", "cpp",
          "template<int N> struct F { static const int v = F<N - 1>::v + 1; };\ntemplate<> struct F<0> { static const int v = 0; };\n"
          "int main() { return F<100000>::v; }\n", "CE")
    h.run("E Java package x;", "java",
          'package x;\npublic class Main { public static void main(String[] a) { System.out.println(0); } }\n', "CE")


def group_f(h):
    print("== F: mạng thí sinh bị chặn (D33)", flush=True)
    for tag, code in (("F thăm dò server:9000/8000, Internet, DNS", NET_PROBE), ("F 1000 kết nối tới cổng worker", NET_FLOOD)):
        # chương trình giải đúng A+B nếu mọi kết nối đều bị chặn, in -1 (WA) nếu có bất kỳ kết nối/DNS nào thành công
        h.run(tag, "python", code, "AC")
    log = compose("logs", "--no-color", "--no-log-prefix", "server", check=False).stdout
    check("[F] không có kết nối chưa xác thực nào từ thí sinh chạm cổng worker", "quá 16 kết nối chưa xác thực" not in log)
    check("[F] không có REGISTER lạ được chấp nhận (số worker không đổi)", len(worker_ids()) == len(h.ids))


def group_g(h):
    print("== G: XSS / hiển thị", flush=True)
    xss = '<script>alert(1)</script>"><img src=x onerror=alert(2)>'
    # CE Python mang nguyên dòng nguồn có HTML trong ce_message
    _, d = h.run("G CE chứa HTML", "python", f"def f(:  # {xss}\n    pass\n", "CE")
    msg = d.get("ce_message") or ""
    check("[G] ce_message chứa nguyên văn thẻ HTML (client lo hiển thị như chữ thường)", "<script>" in msg or "&lt;" not in msg, msg[:80])
    status, headers, _ = http_call("GET", f"/api/submissions/{d['submission_id']}", cookie=h.last_cookie)
    check("[G] REST trả application/json", status == 200 and headers.get("content-type", "").startswith("application/json"),
          headers.get("content-type", ""))
    # username không nhận ký tự HTML: manage từ chối
    proc = manage("users", "create", "<script>alert(1)</script>", "--password-stdin", input_text=PASSWORD + "\n", check=False)
    check("[G] không tạo được username chứa HTML", proc.returncode != 0)
    # code chứa HTML nhưng hợp lệ: lịch sử/chi tiết trả lại đúng nguyên văn trong JSON
    code = f"# {xss}\nimport sys\nprint(sum(map(int, sys.stdin.read().split())))\n"
    sid, _ = submit(h.cookies["u1"], "python", code)
    wait_judged(h.cookies["u1"], sid, "bài AC chứa HTML")
    got = detail(h.cookies["u1"], sid).get("code")
    check("[G] mã nguồn chứa HTML được trả lại nguyên văn trong JSON", got == code)
    # control chars
    ctl = "# \x01\x02\x1b[31m\u202e\nimport sys\nprint(sum(map(int, sys.stdin.read().split())))\n"
    sid, _ = submit(h.cookies["u1"], "python", ctl)
    row = wait_judged(h.cookies["u1"], sid, "bài có ký tự điều khiển")
    check("[G] ký tự điều khiển trong mã nguồn không làm hỏng chấm hay JSON", row["verdict"] == "AC", row["verdict"])


def group_h(h):
    print("== H: đồng thời", flush=True)
    heavy = [("cpp", FORK_BOMB), ("cpp", TMP_FLOOD), ("cpp", MANY_CHILDREN)]
    jobs = []
    for lang, code in heavy:
        cookie = h.admin()
        jobs.append((cookie, submit(cookie, lang, code)[0]))
    ids = [sid for _, sid in jobs]
    started = time.monotonic()
    victims = []
    for i in range(10):
        user = USERS[i % len(USERS)]
        victims.append((user, submit(h.cookies[user], "python", VICTIM_CODE)[0]))
        if i % len(USERS) == len(USERS) - 1:  # tôn trọng trần 5 bài chờ mỗi người
            for u, sid in victims[-len(USERS):]:
                wait_judged(h.cookies[u], sid, "nạn nhân khi 3 ca nặng chạy", timeout=240)
    rows = [wait_judged(h.cookies[u], sid, "nạn nhân", timeout=240) for u, sid in victims]
    elapsed = time.monotonic() - started
    check("[H] 10 bài AC của người khác đều AC khi 3 ca nặng chạy cùng lúc", all(r["verdict"] == "AC" for r in rows),
          str([r["verdict"] for r in rows]))
    check(f"[H] 10 bài AC xong trong {2 * VICTIM_LIMIT_S}s", elapsed < 2 * VICTIM_LIMIT_S, f"{elapsed:.0f}s")
    for cookie, sid in jobs:
        wait_judged(cookie, sid, "ca nặng", timeout=300)
    redelivered = [s for s in [e2e.show(sid) for sid in ids] if s.get("total_deliveries") != "1"]
    check("[H] không bài nào bị giao lại ngoài ý muốn", not redelivered, str(redelivered))
    h.infrastructure("H")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    secret_file = os.path.join(SOURCE, "secrets", "worker.env")
    os.makedirs(os.path.dirname(secret_file), exist_ok=True)
    if not os.path.exists(secret_file):
        with open(secret_file, "w", encoding="utf-8") as f:
            f.write("JH_WORKER_SECRET=" + secrets.token_hex(32) + "\n")
    compose("down", "-v", "--remove-orphans", check=False)
    started = time.monotonic()
    try:
        compose("up", "-d", "--build", "--wait", "--scale", f"worker={e2e.WORKERS}", timeout=1800)
        for a in ADMINS:
            manage("users", "create", a, "--role", "admin", "--password-stdin", input_text=PASSWORD + "\n")
        for u in USERS:
            manage("users", "create", u, "--password-stdin", input_text=PASSWORD + "\n")
        h = Harness()
        for name in [*ADMINS, *USERS]:
            status, headers, body = login(name)
            if status != 200:
                raise RuntimeError(f"đăng nhập {name}: {status} {body}")
            h.cookies[name] = headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
        h.snapshot()
        check("có đủ ba worker trước khi bắt đầu", len(h.ids) == e2e.WORKERS)
        for group in (group_b, group_c, group_d, group_e, group_f, group_g, group_h):
            group(h)
        check("không có dữ liệu test ẩn trong mọi body REST/WS đã nhận",
              not any(t in body for t in e2e.hidden_test_files() for body in e2e.rest_output))
        logs = compose("logs", "--no-color", "--no-log-prefix", check=False).stdout
        check("không có secret worker trong log", open(secret_file, encoding="utf-8").read().split("=", 1)[1].strip() not in logs)
    finally:
        print(f"tổng thời gian {time.monotonic() - started:.0f}s", flush=True)
        compose("down", "-v", "--remove-orphans", check=False)
    print(f"{len(e2e.failures)} kiểm tra hỏng", flush=True)
    return 1 if e2e.failures else 0


if __name__ == "__main__":
    sys.exit(main())
