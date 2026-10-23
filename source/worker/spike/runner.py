"""Chạy một chương trình con có giới hạn, đọc output có trần, lấy rusage riêng từng lần bằng os.wait4.

Không dùng Popen.wait()/communicate() (cả hai tự reap): vòng lặp selectors đọc pipe, os.wait4(WNOHANG)
reap đúng một lần và trả rusage của chính tiến trình đó. Giới hạn tài nguyên đặt bằng lệnh bọc
`setpriv` + `prlimit` (mode "wrap", không cần preexec_fn nên Python dùng được vfork) hoặc bằng
preexec_fn (mode "preexec", chỉ để so ru_maxrss trong selfcheck).
"""

import json
import os
import resource
import selectors
import signal
import subprocess
import time
from dataclasses import asdict, dataclass

JUDGE_UID = 61000
LAUNCHER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launcher.py")
MiB = 1 << 20
READ_CHUNK = 64 * 1024
GROUP_CLEAR_WAIT_S = 2.0

_PRLIMIT_FLAGS = {"as": "--as", "nproc": "--nproc", "nofile": "--nofile", "fsize": "--fsize", "core": "--core"}
_RLIMIT_NAMES = {"as": "RLIMIT_AS", "nproc": "RLIMIT_NPROC", "nofile": "RLIMIT_NOFILE",
                 "fsize": "RLIMIT_FSIZE", "core": "RLIMIT_CORE"}


@dataclass
class Result:
    argv: list
    spawn_error: str | None = None
    exit_code: int | None = None
    signal: int | None = None
    timed_out: bool = False
    output_capped: bool = False
    wall_ms: float = 0.0
    outer_wall_ms: float = 0.0
    maxrss_kib: int = 0
    utime_ms: float = 0.0
    stime_ms: float = 0.0
    stdout_bytes: int = 0
    stderr_tail: str = ""
    stdout_head: str = ""
    group_procs_at_kill: int | None = None
    group_threads_at_kill: int | None = None
    group_clear_ms: float | None = None
    group_leftover: int | None = None
    stdout: bytes = b""

    def record(self):
        d = asdict(self)
        d.pop("stdout")
        return d

    @property
    def ok(self):
        return self.spawn_error is None and self.exit_code == 0 and not self.timed_out


def group_members(pgid):
    """(số tiến trình không phải zombie, tổng số thread) của nhóm tiến trình `pgid`, đọc từ /proc."""
    procs = threads = 0
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat") as f:
                data = f.read()
        except OSError:
            continue
        rest = data[data.rindex(")") + 2:].split()
        if rest[0] == "Z" or int(rest[2]) != pgid:
            continue
        procs += 1
        threads += int(rest[17])
    return procs, threads


def uid_pids(uid):
    """PID của các tiến trình (không zombie) có real uid = `uid`, thấy được trong PID namespace này."""
    pids = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/status") as f:
                status = f.read()
        except OSError:
            continue
        fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
        if fields.get("State", "").strip().startswith("Z"):
            continue
        if int(fields["Uid"].split()[0]) == uid:
            pids.append(int(name))
    return pids


def kill_uid(uid, rounds=5):
    """SIGKILL mọi tiến trình của `uid` (dọn sau mỗi ca; cũng là phương án chống thoát nhóm bằng setsid)."""
    killed = 0
    for _ in range(rounds):
        pids = uid_pids(uid)
        if not pids:
            break
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
                killed += 1
            except OSError:
                pass
        time.sleep(0.05)
    return killed


def _wrap(argv, limits, uid):
    prefix = []
    if uid is not None:
        prefix += ["setpriv", f"--reuid={uid}", f"--regid={uid}", "--clear-groups"]
    flags = [f"{_PRLIMIT_FLAGS[k]}={v}" for k, v in limits.items() if v is not None]
    if flags:
        prefix += ["prlimit", *flags]
    return prefix + list(argv)


def _preexec(limits, uid):
    def fn():
        if uid is not None:
            os.setgroups([])
            os.setgid(uid)
            os.setuid(uid)
        for key, value in limits.items():
            if value is not None:
                rlim = getattr(resource, _RLIMIT_NAMES[key])
                resource.setrlimit(rlim, (value, value))
    return fn


def _default_env(cwd):
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": cwd, "LANG": "C.UTF-8"}


def _read_available(fd):
    """Đọc một khối từ fd không chặn: bytes, b"" khi chưa có dữ liệu, None khi EOF."""
    try:
        chunk = os.read(fd, READ_CHUNK)
    except BlockingIOError:
        return b""
    return chunk if chunk else None


def run(argv, *, cwd, timeout_s, limits=None, uid=JUDGE_UID, stdin_path=None, stdout_cap=16 * MiB,
        stderr_tail=2048, keep_stdout=False, mode="wrap"):
    """Chạy `argv`; trả Result. `limits`: khóa as/nproc/nofile/fsize/core, giá trị None = không đặt."""
    limits = {"core": 0, "fsize": 256 * MiB, **(limits or {})}
    res = Result(argv=list(argv))
    launcher = mode == "launcher"
    full = _wrap(argv, limits, uid) if mode in ("wrap", "launcher") else list(argv)
    preexec = _preexec(limits, uid) if mode == "preexec" else None
    rfd = wfd = None
    if launcher:
        rfd, wfd = os.pipe()
        full = ["python3", "-S", "-I", LAUNCHER, str(wfd), *full]
    stdin = open(stdin_path, "rb") if stdin_path else subprocess.DEVNULL
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(full, cwd=cwd, env=_default_env(cwd), stdin=stdin, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True, preexec_fn=preexec,
                                pass_fds=(wfd,) if launcher else ())
    except OSError as e:
        res.spawn_error = repr(e)
        if launcher:
            os.close(rfd)
        return res
    finally:
        if stdin_path:
            stdin.close()
        if launcher:
            os.close(wfd)

    pid = proc.pid
    pgid = pid  # start_new_session=True: tiến trình là trưởng nhóm
    sel = selectors.DefaultSelector()
    for stream, tag in ((proc.stdout, "out"), (proc.stderr, "err")):
        os.set_blocking(stream.fileno(), False)
        sel.register(stream.fileno(), selectors.EVENT_READ, tag)
    if launcher:
        os.set_blocking(rfd, False)
        sel.register(rfd, selectors.EVENT_READ, "res")

    out = bytearray()
    head = bytearray()
    res_buf = bytearray()
    state = {"out_n": 0, "tail": b""}

    def consume(fd, tag):
        chunk = _read_available(fd)
        if chunk is None:
            sel.unregister(fd)
            return None
        if tag == "res":
            res_buf.extend(chunk)
        elif tag == "out":
            state["out_n"] += len(chunk)
            if len(head) < 256:
                head.extend(chunk[: 256 - len(head)])
            if keep_stdout and len(out) < stdout_cap:
                out.extend(chunk[: stdout_cap - len(out)])
        else:
            state["tail"] = (state["tail"] + chunk)[-stderr_tail:]
        return chunk

    deadline = t0 + timeout_s
    status = rusage = None
    t_end = None
    while True:
        if sel.get_map():
            for key, _ in sel.select(timeout=0.02):
                consume(key.fd, key.data)
        else:
            time.sleep(0.0005)
        reaped, st, ru = os.wait4(pid, os.WNOHANG)
        if reaped:
            t_end, status, rusage = time.monotonic(), st, ru
            break
        if state["out_n"] > stdout_cap:
            res.output_capped = True
            break
        if time.monotonic() > deadline:
            res.timed_out = True
            break

    if status is None:
        t_end = time.monotonic()
        res.group_procs_at_kill, res.group_threads_at_kill = group_members(pgid)
        if launcher:
            os.kill(pid, signal.SIGUSR1)  # launcher kill con rồi reap để lấy rusage; killpg dọn sau
        else:
            _killpg(pgid)
        end = time.monotonic() + 5.0
        while time.monotonic() < end:
            reaped, st, ru = os.wait4(pid, os.WNOHANG)
            if reaped:
                status, rusage = st, ru
                break
            time.sleep(0.001)
    else:
        for key in list(sel.get_map().values()):  # đọc nốt phần còn trong pipe (không chờ con cháu giữ pipe)
            while consume(key.fd, key.data):
                pass

    res.wall_ms = res.outer_wall_ms = (t_end - t0) * 1000
    info = None
    if launcher and status is not None:
        for key in list(sel.get_map().values()):
            while key.data == "res" and consume(key.fd, key.data):
                pass
        try:
            info = json.loads(bytes(res_buf))
        except ValueError:
            res.spawn_error = f"launcher không trả kết quả (exit {os.waitstatus_to_exitcode(status)})"
    if launcher:
        if info:
            code = os.waitstatus_to_exitcode(info["status"])
            usage = (info["maxrss"], info["utime"], info["stime"])
            if not res.timed_out and not res.output_capped:
                res.wall_ms = info["wall_ms"]
    elif status is not None:
        code = os.waitstatus_to_exitcode(status)
        usage = (rusage.ru_maxrss, rusage.ru_utime, rusage.ru_stime)
    else:
        code = usage = None
        res.spawn_error = "không reap được tiến trình sau SIGKILL"
    if code is not None:
        res.exit_code, res.signal = (code, None) if code >= 0 else (None, -code)
        res.maxrss_kib, res.utime_ms, res.stime_ms = usage[0], usage[1] * 1000, usage[2] * 1000
    res.stdout_bytes = state["out_n"]
    res.stderr_tail = state["tail"].decode("utf-8", errors="replace")
    res.stdout = bytes(out)
    res.stdout_head = bytes(head).decode("utf-8", errors="replace")

    t_clear = time.monotonic()
    _killpg(pgid)  # dọn con cháu còn sót, kể cả khi tiến trình chính đã thoát bình thường
    res.group_leftover = group_members(pgid)[0]
    while res.group_leftover and time.monotonic() - t_clear < GROUP_CLEAR_WAIT_S:
        time.sleep(0.002)
        res.group_leftover = group_members(pgid)[0]
    res.group_clear_ms = (time.monotonic() - t_clear) * 1000

    proc.returncode = os.waitstatus_to_exitcode(status) if status is not None else -signal.SIGKILL
    proc.stdout.close()
    proc.stderr.close()
    if launcher:
        os.close(rfd)
    return res


def _killpg(pgid):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
