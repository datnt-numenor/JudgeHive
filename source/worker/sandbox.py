"""Chạy một chương trình với giới hạn tài nguyên và dọn sạch sau đó (PLAN mục 4, D13).

Chương trình luôn chạy qua launcher nhỏ (`launcher.py`: posix_spawn + wait4), không spawn trực tiếp từ process
worker, để `ru_maxrss` không bị cộng đỉnh RSS của worker. Hạ quyền bằng `setpriv`, đặt giới hạn bằng `prlimit`
(không dùng preexec_fn). Không đặt RLIMIT_NPROC: fork bomb bị chặn bằng pids_limit của container, và sau mỗi
test mọi tiến trình của uid thí sinh bị kill (kể cả tiến trình thoát nhóm bằng setsid).

Ba lớp chống bộ nhớ ngoài RSS của thí sinh (D28): (1) `oom_score_adj` +1000 do root có CAP_SYS_RESOURCE đặt trước
`setpriv`, nên thí sinh không hạ lại được và OOM killer luôn chọn thí sinh; (2) seccomp chặn `memfd_create`, `shmget`, `io_uring_*`, mọi `socket()` ngoài `AF_UNIX` (thí sinh không có mạng, D33)
và ABI lạ (`worker/seccomp.py`); (3) sysctl `shm_rmid_forced`, `shmmax` ở compose. Cả biên dịch lẫn chạy đều qua `_wrap`.
"""

import json
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass

from . import seccomp

JUDGE_UID = 61000
LAUNCHER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launcher.py")
READ_CHUNK = 256 * 1024
GROUP_CLEAR_WAIT_S = 0.5
REAP_WAIT_S = 0.5
POLL_S = 0.02  # chu kỳ kiểm tra hủy/hạn chót
SPAWN_RETRIES = 4
TMP_ROOTS = ("/tmp", "/var/tmp", "/dev/shm")

_PRLIMIT_FLAGS = {"as": "--as", "nofile": "--nofile", "fsize": "--fsize", "core": "--core"}


@dataclass
class RunResult:
    spawn_error: str | None = None
    exit_code: int | None = None
    signal: int | None = None
    timed_out: bool = False
    cancelled: bool = False
    output_capped: bool = False
    wall_ms: float = 0.0  # wall time của chính chương trình (đo trong launcher)
    outer_wall_ms: float = 0.0  # từ lúc spawn launcher
    maxrss_kib: int = 0
    stdout: bytes = b""
    stdout_bytes: int = 0
    stderr_head: bytes = b""
    stderr_tail: bytes = b""
    group_procs_at_kill: int | None = None
    group_threads_at_kill: int | None = None
    group_leftover: int = 0

    @property
    def abnormal(self):
        return self.exit_code != 0


def group_members(pgid):
    """(số tiến trình không phải zombie, tổng số thread) của nhóm tiến trình `pgid`, đọc từ /proc."""
    procs = threads = 0
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat") as f:
                data = f.read()
            rest = data[data.rindex(")") + 2:].split()
            if rest[0] == "Z" or int(rest[2]) != pgid:
                continue
            nthreads = int(rest[17])
        except (OSError, ValueError, IndexError):  # tiến trình đang thoát: /proc đọc dở
            continue
        procs += 1
        threads += nthreads
    return procs, threads


def uid_pids(uid):
    """PID (không zombie) có real uid = `uid` thấy được trong PID namespace này."""
    pids = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/status") as f:
                status = f.read()
            fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
            if fields.get("State", "").strip().startswith("Z"):
                continue
            match = int(fields["Uid"].split()[0]) == uid
        except (OSError, KeyError, ValueError, IndexError):  # tiến trình đang thoát: /proc đọc dở
            continue
        if match:
            pids.append(int(name))
    return pids


def kill_uid(uid, timeout_s=2.0):
    """SIGKILL mọi tiến trình của `uid`, lặp đến khi không còn (tiến trình có thể fork giữa các vòng quét) hoặc hết
    `timeout_s`. Trả True nếu sau đó không còn tiến trình nào của uid."""
    deadline = time.monotonic() + timeout_s
    while True:
        pids = uid_pids(uid)
        if not pids:
            return True
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        if time.monotonic() >= deadline:
            return not uid_pids(uid)
        time.sleep(0.02)


# Chạy dưới chính uid thí sinh để xóa file của nó: không cần CAP_DAC_OVERRIDE/CAP_FOWNER (root bị bỏ các cap này
# không xóa được file của uid khác trong thư mục sticky như /tmp, cũng không vào được thư mục 0700 của nó).
_CLEAN_SCRIPT = (
    'for r in "$@"; do [ -d "$r" ] || continue; '
    'find "$r" -mindepth 1 -user "$U" -type d -exec chmod u+rwx {} \\; 2>/dev/null; '
    'find "$r" -mindepth 1 -user "$U" -delete 2>/dev/null; done'
)


def cleanup_uid_files(uid, roots=TMP_ROOTS, timeout_s=5):
    """Xóa file/thư mục do `uid` sở hữu dưới `roots`. Trả True nếu lệnh dọn chạy xong.

    Chạy sau mỗi test để chương trình thí sinh không để lại dữ liệu (kể cả input test ẩn) cho bài nộp sau đọc."""
    argv = ["setpriv", f"--reuid={uid}", f"--regid={uid}", "--clear-groups", "env", f"U={uid}", "sh", "-c",
            _CLEAN_SCRIPT, "sh", *roots]
    try:
        subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=timeout_s, check=True, cwd="/")
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def uid_entries(uid, roots=TMP_ROOTS):
    """Số mục thuộc `uid` còn thấy được dưới `roots` (để kiểm tra; không vào được thư mục không đọc được)."""
    count = 0
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
            for name in dirnames + filenames:
                try:
                    if os.lstat(os.path.join(dirpath, name)).st_uid == uid:
                        count += 1
                except OSError:
                    pass
    return count


def reset_uid(uid, roots=TMP_ROOTS):
    """Dọn trạng thái của uid thí sinh: kill mọi tiến trình rồi xóa file của nó. Trả True nếu dọn sạch cả hai."""
    killed = kill_uid(uid)
    return cleanup_uid_files(uid, roots) and killed


OOM_SCORE_ADJ = 1000
CAP_SYS_RESOURCE = 24
# Chạy dưới root (có CAP_SYS_RESOURCE) TRƯỚC `setpriv`: ghi bởi tiến trình có cap thì kernel đặt thêm sàn
# `oom_score_adj_min`, nên sau khi hạ quyền thí sinh không ghi giá trị thấp hơn được (không cap thì hạ lại được: đã đo).
# Chuỗi -c là HẰNG, không nối đường dẫn/lệnh nào vào đó: lệnh thật đi qua "$@" như các đối số riêng (không bị shell
# diễn giải, nên không có injection qua tên file hay đường dẫn). Không dùng `preexec_fn` để đặt giá trị này: worker nhiều
# thread, mà code chạy giữa fork và exec trong tiến trình nhiều thread có thể kẹt (deadlock) vì lock do thread khác giữ lúc fork.
_OOM_WRAP_SCRIPT = 'echo 1000 > /proc/self/oom_score_adj && exec "$@"'
_OOM_WRAP = ["sh", "-c", _OOM_WRAP_SCRIPT, "sh"]
_filter_lock = threading.Lock()
_filter_path = None


def guard_filter_path():
    """File BPF của thí sinh: tạo một lần (root sở hữu, 0444, nằm trong /tmp sticky nên thí sinh không sửa/xóa được)."""
    global _filter_path
    with _filter_lock:
        if _filter_path is None or not os.path.exists(_filter_path):
            fd, path = tempfile.mkstemp(prefix="jh-seccomp-", suffix=".bpf", dir="/tmp")
            with os.fdopen(fd, "wb") as f:
                f.write(seccomp.build_filter())
            os.chmod(path, 0o444)
            _filter_path = path
        return _filter_path


def _cap_eff():
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("CapEff:"):
                return int(line.split()[1], 16)
    raise seccomp.GuardError("không đọc được CapEff")


def verify_guard():
    """Kiểm điều kiện của D28 lúc worker khởi động; không đủ thì GuardError (worker không chạy code thí sinh).
    Trả đường dẫn file BPF."""
    if not _cap_eff() & (1 << CAP_SYS_RESOURCE):
        raise seccomp.GuardError("thiếu CAP_SYS_RESOURCE (compose: cap_add SYS_RESOURCE): oom_score_adj của thí sinh "
                                 "sẽ hạ lại được")
    path = guard_filter_path()
    out = subprocess.run(_OOM_WRAP + ["cat", "/proc/self/oom_score_adj"], capture_output=True, text=True, timeout=10)
    if out.returncode != 0 or out.stdout.strip() != str(OOM_SCORE_ADJ):
        raise seccomp.GuardError("không đặt được oom_score_adj cho tiến trình con")
    for name, want in (("kernel/shmmax", "0"), ("kernel/shm_rmid_forced", "1")):
        got = _read_sysctl(name)
        if got != want:
            raise seccomp.GuardError(f"sysctl {name.replace('/', '.')} = {got!r}, cần {want!r} (compose: sysctls)")
    # Nạp thử bộ lọc thật bằng đúng chuỗi lệnh của thí sinh: util-linux/kernel không hỗ trợ seccomp thì lộ ngay lúc khởi động.
    probe = subprocess.run(_wrap(["true"], {"nofile": 32}, JUDGE_UID), capture_output=True, timeout=10)
    if probe.returncode != 0:
        raise seccomp.GuardError("không chạy được lệnh mẫu dưới setpriv + seccomp-filter: "
                                 + probe.stderr.decode(errors="replace")[:200])
    # Hiệu lực thật, đo bằng hành vi dưới uid thí sinh: (a) không hạ lại được oom_score_adj, (b) memfd_create bị chặn.
    # (close tường minh: lỗi ghi chỉ lộ ra khi flush, nếu không thì thoát mã 0 dù bị từ chối)
    for what, code in (("hạ oom_score_adj", "f = open('/proc/self/oom_score_adj', 'w'); f.write('0'); f.close()"),
                       ("memfd_create", "import os; os.memfd_create('x')")):
        effective = subprocess.run(_wrap(["python3", "-I", "-S", "-c", code], {"nofile": 32}, JUDGE_UID),
                                   capture_output=True, timeout=10)
        if effective.returncode == 0:
            raise seccomp.GuardError(f"thí sinh vẫn làm được: {what} (bảo vệ D28 không có hiệu lực)")
    # D33: mạng. Khác hai ca trên, ca này in dấu hiệu rõ ràng và có đối chứng dương (socket Unix phải tạo được), để
    # "python không chạy được" hay "chặn cả mọi socket" không bị nhầm là bộ lọc đúng.
    net = subprocess.run(_wrap(["python3", "-I", "-S", "-c", _NET_PROBE], {"nofile": 32}, JUDGE_UID),
                         capture_output=True, text=True, timeout=10)
    got = net.stdout.split()
    if "INET_OPEN" in got:
        raise seccomp.GuardError("thí sinh vẫn tạo được socket AF_INET (bảo vệ D33 không có hiệu lực)")
    if got != ["INET_EPERM", "UNIX_OK"]:
        raise seccomp.GuardError("không đo được chặn mạng của thí sinh (cần socket AF_INET bị EPERM và AF_UNIX tạo được): "
                                 f"stdout={net.stdout[:100]!r} stderr={net.stderr[:100]!r}")
    # Ngược chiều hai ca trên: ở đây phải THÀNH CÔNG. no_new_privs là điều kiện để nạp seccomp không cần cap và để
    # thí sinh không lấy lại quyền qua setuid/file capability; đọc từ chính tiến trình thí sinh.
    if not _candidate_no_new_privs():
        raise seccomp.GuardError("tiến trình thí sinh không có NoNewPrivs: 1 (setpriv --no-new-privs không có hiệu lực)")
    return path


_NET_PROBE = ("import socket, errno\n"
              "try:\n    socket.socket(socket.AF_INET)\n    print('INET_OPEN')\n"
              "except OSError as e:\n    print('INET_EPERM' if e.errno == errno.EPERM else 'INET_OTHER')\n"
              "socket.socket(socket.AF_UNIX)\nprint('UNIX_OK')\n")
_NNP_PROBE = ("import sys; sys.exit(0 if any(l.split()[:2] == ['NoNewPrivs:', '1'] "
              "for l in open('/proc/self/status')) else 1)")


def _candidate_no_new_privs():
    """True nếu tiến trình chạy qua `_wrap` (dưới uid thí sinh) thấy `NoNewPrivs: 1` trong /proc/self/status."""
    probe = subprocess.run(_wrap(["python3", "-I", "-S", "-c", _NNP_PROBE], {"nofile": 32}, JUDGE_UID),
                           capture_output=True, timeout=10)
    return probe.returncode == 0


def _read_sysctl(name):
    with open("/proc/sys/" + name, encoding="utf-8") as f:
        return f.read().strip()


def _wrap(argv, limits, uid):
    prefix = _OOM_WRAP + ["setpriv", f"--reuid={uid}", f"--regid={uid}", "--clear-groups", "--no-new-privs",
                          "--seccomp-filter", guard_filter_path()]
    flags = [f"{_PRLIMIT_FLAGS[k]}={v}" for k, v in limits.items() if v is not None]
    if flags:
        prefix += ["prlimit", *flags]
    # SCHED_IDLE (không cần cap): chương trình thí sinh chỉ chiếm CPU mà worker không cần. Nếu không, fork bomb với
    # vài trăm tiến trình tranh CPU làm chính worker đói CPU (đo ở PR 3: TLE mất 33 giây dưới --cpus=2).
    return prefix + ["chrt", "--idle", "0"] + list(argv)


def _env(cwd):
    # Môi trường tối giản: không kế thừa env của worker (secret nếu có nằm ở env của worker).
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": cwd, "TMPDIR": cwd, "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1"}


def _killpg(pgid):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run(argv, *, cwd, timeout_s, limits=None, uid=JUDGE_UID, stdin_path=None, stdout_cap=16 * 1024 * 1024,
        stdout_keep=0, stderr_tail=2048, stderr_head=0, cancel=None):
    """Chạy `argv` dưới uid `uid` với `limits` (khóa as/nofile/fsize/core).

    `timeout_s` tính từ lúc spawn launcher (gồm cả khởi động launcher). stdout vượt `stdout_cap` byte thì kill
    (`output_capped`); `stdout_keep` byte đầu của stdout được giữ lại. `cancel` là threading.Event: đặt cờ thì
    kill như khi quá hạn (`cancelled`)."""
    # RLIMIT_CORE = 1 chứ không phải 0: khi core_pattern là một pipe (WSL2, apport, systemd-coredump), giá trị 0 không
    # ngăn kernel đẩy core dump qua pipe (đo ở PR 3: abort() với 200 MB RSS mất 4,5 giây), còn 1 thì kernel bỏ qua.
    limits = {"core": 1, **(limits or {})}
    res = RunResult()
    guard_filter_path()  # lỗi tạo file BPF (đầy /tmp...) phải nổ TRƯỚC khi mở pipe, để không rò rfd/wfd
    rfd, wfd = os.pipe()
    stdin = None
    t0 = time.monotonic()
    proc = None
    try:
        # Mọi thứ có thể ném lỗi sau khi mở pipe nằm trong try: lỗi mở `stdin_path` (OSError) thành `spawn_error`,
        # lỗi khác thì đóng `rfd` rồi ném tiếp; `wfd` luôn đóng ở finally.
        full = [sys.executable, "-S", "-I", LAUNCHER, str(wfd), *_wrap(argv, limits, uid)]
        stdin = open(stdin_path, "rb") if stdin_path else subprocess.DEVNULL
        for attempt in range(SPAWN_RETRIES):
            try:
                proc = subprocess.Popen(full, cwd=cwd, env=_env(cwd), stdin=stdin, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, start_new_session=True, pass_fds=(wfd,))
                break
            except BlockingIOError:  # EAGAIN: pids_limit chưa kịp nhả sau fork bomb (zombie chưa được reap)
                if attempt == SPAWN_RETRIES - 1:
                    raise
                time.sleep(0.1 * (attempt + 1))
    except OSError as e:
        res.spawn_error = repr(e)
        os.close(rfd)
        return res
    except BaseException:
        os.close(rfd)
        raise
    finally:
        if stdin is not None and stdin_path:
            stdin.close()
        os.close(wfd)

    pid = pgid = proc.pid  # start_new_session: launcher là trưởng nhóm
    sel = selectors.DefaultSelector()
    for stream, tag in ((proc.stdout, "out"), (proc.stderr, "err")):
        os.set_blocking(stream.fileno(), False)
        sel.register(stream.fileno(), selectors.EVENT_READ, tag)
    os.set_blocking(rfd, False)
    sel.register(rfd, selectors.EVENT_READ, "res")

    out, res_buf = bytearray(), bytearray()
    head = bytearray()
    tail = b""
    out_n = 0

    def consume(fd, tag):
        nonlocal out_n, tail
        try:
            chunk = os.read(fd, READ_CHUNK)
        except BlockingIOError:
            return b""
        if not chunk:
            sel.unregister(fd)
            return None
        if tag == "res":
            res_buf.extend(chunk)
        elif tag == "out":
            out_n += len(chunk)
            if len(out) < stdout_keep:
                out.extend(chunk[: stdout_keep - len(out)])
        else:
            if len(head) < stderr_head:
                head.extend(chunk[: stderr_head - len(head)])
            tail = (tail + chunk)[-stderr_tail:]
        return chunk

    deadline = t0 + timeout_s
    reaped = False
    status = None
    while True:
        if sel.get_map():
            for key, _ in sel.select(timeout=POLL_S):
                consume(key.fd, key.data)
        else:
            time.sleep(0.0005)
        done, st = os.waitpid(pid, os.WNOHANG)
        if done:
            reaped, status = True, st
            break
        if out_n > stdout_cap:
            res.output_capped = True
            break
        if cancel is not None and cancel.is_set():
            res.cancelled = True
            break
        if time.monotonic() > deadline:
            res.timed_out = True
            break
    res.outer_wall_ms = (time.monotonic() - t0) * 1000

    if not reaped:
        res.group_procs_at_kill, res.group_threads_at_kill = group_members(pgid)
        try:
            os.kill(pid, signal.SIGUSR1)  # launcher kill con rồi vẫn reap để lấy rusage; killpg dọn phần còn lại
        except ProcessLookupError:
            pass
        end = time.monotonic() + REAP_WAIT_S
        while time.monotonic() < end:
            done, st = os.waitpid(pid, os.WNOHANG)
            if done:
                reaped, status = True, st
                break
            time.sleep(0.001)
        if not reaped:
            _killpg(pgid)
            try:
                _, status = os.waitpid(pid, 0)
                reaped = True
            except ChildProcessError:
                pass

    for key in list(sel.get_map().values()):  # đọc nốt phần còn trong pipe (không chờ con cháu giữ pipe mở)
        while consume(key.fd, key.data):
            pass
    _killpg(pgid)  # dọn con cháu còn sót, kể cả khi chương trình đã thoát bình thường
    t_clear = time.monotonic()
    res.group_leftover = group_members(pgid)[0]
    while res.group_leftover and time.monotonic() - t_clear < GROUP_CLEAR_WAIT_S:
        time.sleep(0.002)
        res.group_leftover = group_members(pgid)[0]

    res.stdout, res.stdout_bytes = bytes(out), out_n
    res.stderr_head, res.stderr_tail = bytes(head), tail
    proc.returncode = 0
    sel.close()  # fd epoll: không đóng thì chỉ được thu khi GC chạy, mỗi lần chạy rò một fd (đo: +1 mỗi lần)
    proc.stdout.close()
    proc.stderr.close()
    os.close(rfd)

    try:
        info = json.loads(bytes(res_buf))
    except ValueError:
        info = None
    killed_by_us = res.timed_out or res.cancelled or res.output_capped
    if info is None and killed_by_us:
        # SIGUSR1 đến khi launcher chưa kịp cài handler (hạn rất ngắn): launcher chết, coi như bị kill
        res.signal, res.wall_ms = signal.SIGKILL, res.outer_wall_ms
    elif info is None:
        res.spawn_error = "launcher không trả kết quả"
    elif "spawn_error" in info:
        res.spawn_error = info["spawn_error"]
    else:
        code = os.waitstatus_to_exitcode(info["status"])
        res.exit_code, res.signal = (code, None) if code >= 0 else (None, -code)
        res.maxrss_kib = info["maxrss"]
        res.wall_ms = info["wall_ms"]
    return res
