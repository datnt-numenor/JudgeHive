"""Các bộ đo của spike. Mỗi suite nhận Ctx, ghi bản ghi thô vào ctx.runs (báo cáo tính sau, ở report.py)."""

import concurrent.futures
import itertools
import os
import random
import resource
import time

import programs
import runner
from programs import LANGS
from runner import JUDGE_UID, MiB

WORK_ROOT = "/tmp/spike"
LIMIT_KIB = 262144  # ví dụ memory_limit_kib trong README
LIMIT_BYTES = LIMIT_KIB * 1024
JAVA_XMX = "-Xmx256m"
JAVA_TUNED = [JAVA_XMX, "-XX:+UseSerialGC", "-XX:TieredStopAtLevel=1", "-Xshare:auto"]
STDERR_TAIL = 2048
OUTPUT_CAP = 16 * MiB
SOURCE_NAME = {"python": "main.py", "cpp": "main.cpp", "java": "Main.java"}
COMPILE_CMD = {"cpp": ["g++", "-O2", "-std=c++17", "-o", "main", "main.cpp"], "java": ["javac", "Main.java"]}


class Ctx:
    def __init__(self, quick=False, uid=JUDGE_UID):
        self.runs = []
        self.quick = quick
        self.uid = uid
        self.failures = []
        self._n = 0
        self._builds = {}
        os.makedirs(WORK_ROOT, exist_ok=True)
        os.chmod(WORK_ROOT, 0o755)

    def reps(self, n):
        return max(1, n // 4) if self.quick else n

    def add(self, suite, res, **ctx):
        rec = res.record() if hasattr(res, "record") else dict(res)
        rec.update(suite=suite, t_epoch=time.time(), **ctx)
        self.runs.append(rec)
        return rec

    def workdir(self, label):
        self._n += 1
        path = os.path.join(WORK_ROOT, f"{self._n:04d}-{label}")
        os.makedirs(path)
        os.chown(path, self.uid, self.uid)
        os.chmod(path, 0o755)
        return path

    def input_file(self, name, data):
        path = os.path.join(WORK_ROOT, name)
        with open(path, "wb") as f:
            f.write(data)
        os.chmod(path, 0o644)
        return path

    def check(self, name, passed, **detail):
        self.add("selfcheck", {"case": "check", "name": name, "passed": passed, **detail})
        if not passed:
            self.failures.append(name)


class Build:
    def __init__(self, lang, cwd, compile_res=None):
        self.lang, self.cwd, self.compile_res = lang, cwd, compile_res

    def argv(self, jvm=()):
        if self.lang == "python":
            return ["python3", "main.py"]
        if self.lang == "cpp":
            return ["./main"]
        return ["java", *jvm, "Main"]


def compile_fresh(ctx, lang, prog, suite=None, **tags):
    """Thư mục mới, ghi mã nguồn, biên dịch (nếu cần). Trả Build; ghi bản ghi biên dịch khi có `suite`."""
    cwd = ctx.workdir(f"{lang}-{prog}")
    src = os.path.join(cwd, SOURCE_NAME[lang])
    with open(src, "w") as f:
        f.write(programs.SOURCES[prog][lang])
    os.chown(src, ctx.uid, ctx.uid)
    res = None
    if lang in COMPILE_CMD:
        res = runner.run(COMPILE_CMD[lang], cwd=cwd, timeout_s=120, uid=ctx.uid, stderr_tail=STDERR_TAIL)
        if suite:
            ctx.add(suite, res, case=prog, lang=lang, **tags)
        if not res.ok:
            raise RuntimeError(f"biên dịch {lang}/{prog} lỗi: {res.stderr_tail}")
    return Build(lang, cwd, res)


def get_build(ctx, lang, prog):
    key = (lang, prog, ctx.uid)
    if key not in ctx._builds:
        ctx._builds[key] = compile_fresh(ctx, lang, prog)
    return ctx._builds[key]


def lang_limits(lang):
    """Giới hạn bộ nhớ như PLAN mục 4: RLIMIT_AS cho Python/C++, -Xmx cho Java."""
    return {"as": LIMIT_BYTES} if lang != "java" else {}


def run_prog(ctx, suite, build, *, case, variant="", limits=None, jvm=(), stdin_path=None, timeout_s=20,
             keep_stdout=False, mode="launcher", **extra):
    res = runner.run(build.argv(jvm), cwd=build.cwd, timeout_s=timeout_s, limits=limits, uid=ctx.uid,
                     stdin_path=stdin_path, stdout_cap=OUTPUT_CAP, stderr_tail=STDERR_TAIL,
                     keep_stdout=keep_stdout, mode=mode)
    rec = ctx.add(suite, res, case=case, lang=build.lang, variant=variant, limits=dict(limits or {}),
                  jvm=list(jvm), **extra)
    return res, rec


def normalize(data):
    """Chuẩn hóa output như PLAN mục 4: \\r\\n -> \\n, bỏ khoảng trắng cuối dòng, bỏ dòng trống cuối."""
    lines = [ln.rstrip(b" \t") for ln in data.replace(b"\r\n", b"\n").split(b"\n")]
    while lines and not lines[-1]:
        lines.pop()
    return b"\n".join(lines)


def proc_status_kib(field):
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith(field + ":"):
                return int(line.split()[1])
    return -1


# --------------------------------------------------------------------------------------------- selfcheck

def suite_selfcheck(ctx):
    """Chứng minh rusage riêng từng lần, dọn nhóm sau killpg, và đo độ phình ru_maxrss theo cách spawn."""
    a = compile_fresh(ctx, "python", "alloc200")
    b = get_build(ctx, "cpp", "hello")
    ra, _ = run_prog(ctx, "selfcheck", a, case="alloc200", timeout_s=30)
    rb, _ = run_prog(ctx, "selfcheck", b, case="hello_after_alloc200", timeout_s=30)
    children_max = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    ctx.check("rusage_separation", ra.ok and rb.ok and rb.maxrss_kib < ra.maxrss_kib / 2
              and children_max >= ra.maxrss_kib * 0.9,
              a_maxrss_kib=ra.maxrss_kib, b_maxrss_kib=rb.maxrss_kib, children_maxrss_kib=children_max)

    sleeper = get_build(ctx, "python", "spawn_sleep")
    rs, _ = run_prog(ctx, "selfcheck", sleeper, case="killpg_cleanup", timeout_s=1.0,
                     limits={"nproc": 256})
    ctx.check("killpg_cleanup", rs.timed_out and rs.group_leftover == 0,
              procs_at_kill=rs.group_procs_at_kill, leftover=rs.group_leftover, clear_ms=rs.group_clear_ms)
    runner.kill_uid(ctx.uid)

    ctx.add("selfcheck", {"case": "orchestrator_rss", "vmrss_kib": proc_status_kib("VmRSS"),
                          "vmhwm_kib": proc_status_kib("VmHWM")})
    for lang in LANGS:
        hello = get_build(ctx, lang, "hello")
        for mode in ("wrap", "preexec", "launcher"):
            for _ in range(ctx.reps(3)):
                run_prog(ctx, "selfcheck", hello, case="hello_rss", variant=mode, mode=mode,
                         limits=lang_limits(lang), jvm=[JAVA_XMX] if lang == "java" else ())


# ------------------------------------------------------------------------------------------------ compile

def _compile_parallel(ctx, suite, lang, prog, label, n=3):
    with concurrent.futures.ThreadPoolExecutor(n) as pool:
        list(pool.map(lambda i: compile_fresh(ctx, lang, prog, suite, variant=label, index=i), range(n)))


def suite_cold3(ctx):
    """Chạy trong container mới: lần biên dịch đầu của mỗi toolchain là 3 lần song song (tệ nhất)."""
    for lang in ("java", "cpp"):
        _compile_parallel(ctx, "compile", lang, "hello", "cold_parallel3")


def suite_compile(ctx):
    for lang in ("cpp", "java"):  # lần đầu trong container, một mình
        compile_fresh(ctx, lang, "hello", "compile", variant="cold_solo", index=0)
    for prog in ("hello", "medium"):
        for lang in ("cpp", "java"):
            for i in range(ctx.reps(10)):
                compile_fresh(ctx, lang, prog, "compile", variant="warm_solo", index=i)
    for lang in ("cpp", "java"):
        for r in range(ctx.reps(5)):
            _compile_parallel(ctx, "compile", lang, "medium", "warm_parallel3")


# ---------------------------------------------------------------------------------------------------- d13

D13_CASES = ("big_block", "grow", "div0", "fake_oom", "fake_oom_flood")


def d13_variants(lang):
    if lang != "java":
        return [("as256", lang_limits(lang), ())]
    return [("xmx256", {}, [JAVA_XMX]),
            ("xmx256_exitoom", {}, [JAVA_XMX, "-XX:+ExitOnOutOfMemoryError"]),
            ("as256_xmx256", {"as": LIMIT_BYTES}, [JAVA_XMX])]


def suite_d13(ctx):
    zero = ctx.input_file("div0.txt", b"1 0\n")
    for lang in LANGS:
        for case in D13_CASES:
            build = get_build(ctx, lang, case)
            for variant, limits, jvm in d13_variants(lang):
                for i in range(ctx.reps(5)):
                    run_prog(ctx, "d13", build, case=case, variant=variant, limits=limits, jvm=jvm,
                             stdin_path=zero, timeout_s=30, limit_kib=LIMIT_KIB, index=i)
                    runner.kill_uid(ctx.uid)


# ----------------------------------------------------------------------------------------------- overhead

def suite_overhead(ctx):
    expected = normalize(b"0123456789abcde\n" * 1048576)
    for lang in LANGS:
        hello = get_build(ctx, lang, "hello")
        variants = [("default", [JAVA_XMX])] + [("tuned", JAVA_TUNED)] if lang == "java" else [("default", ())]
        for variant, jvm in variants:
            for i in range(ctx.reps(20)):
                run_prog(ctx, "overhead", hello, case="startup", variant=variant, limits=lang_limits(lang),
                         jvm=jvm, index=i)
    for lang in LANGS:
        printer = get_build(ctx, lang, "printer16")
        for i in range(ctx.reps(5)):
            res, rec = run_prog(ctx, "overhead", printer, case="print16", limits=lang_limits(lang),
                                jvm=[JAVA_XMX] if lang == "java" else (), keep_stdout=True, timeout_s=60,
                                index=i)
            t0 = time.perf_counter()
            matched = normalize(res.stdout) == expected
            rec["compare_ms"] = (time.perf_counter() - t0) * 1000
            rec["matched"] = matched


# --------------------------------------------------------------------------------------------------- bench

def suite_bench(ctx):
    rng = random.Random(20260101)
    nums = [rng.randint(1, 10 ** 9) for _ in range(10 ** 6)]
    data = f"{len(nums)}\n{' '.join(map(str, nums))}\n".encode()
    sum_input = ctx.input_file("sum_io.txt", data)
    expected = {"sum_io": str(sum(nums))}
    for prog in ("sum_io", "cpu_loop"):
        for lang in ("cpp", "java", "python"):  # cpp trước để lấy đáp án cpu_loop
            build = get_build(ctx, lang, prog)
            for i in range(ctx.reps(10)):
                res, rec = run_prog(ctx, "bench", build, case=prog, limits=lang_limits(lang),
                                    jvm=[JAVA_XMX] if lang == "java" else (), keep_stdout=True,
                                    stdin_path=sum_input if prog == "sum_io" else None, timeout_s=120,
                                    index=i)
                answer = res.stdout.decode(errors="replace").strip()
                expected.setdefault(prog, answer)
                rec["correct"] = res.ok and answer == expected[prog]


# ---------------------------------------------------------------------------------------------------- kill

def suite_kill(ctx):
    uid = ctx.uid
    for lang in LANGS:
        jvm = [JAVA_XMX] if lang == "java" else ()
        sleeper = get_build(ctx, lang, "spawn_sleep")
        for i in range(ctx.reps(3)):
            runner.kill_uid(uid)
            run_prog(ctx, "kill", sleeper, case="spawn_sleep", limits={"nproc": 256}, jvm=jvm, timeout_s=2,
                     index=i)
        bomb = get_build(ctx, lang, "fork_bomb")
        for nproc in (32, 64, 128):
            for i in range(ctx.reps(3)):
                runner.kill_uid(uid)
                res, rec = run_prog(ctx, "kill", bomb, case="fork_bomb", variant=f"nproc{nproc}",
                                    limits={"nproc": nproc}, jvm=jvm, timeout_s=3, index=i)
                rec["eagain_seen"] = "EAGAIN" in res.stderr_tail
                rec["uid_procs_after_killpg"] = len(runner.uid_pids(uid))
        runner.kill_uid(uid)
        escaper = get_build(ctx, lang, "setsid_escape")
        res, rec = run_prog(ctx, "kill", escaper, case="setsid_escape", limits={"nproc": 256}, jvm=jvm,
                            timeout_s=2, index=0)
        rec["escaped_alive_after_killpg"] = len(runner.uid_pids(uid))
        rec["uid_kill_count"] = runner.kill_uid(uid)
        rec["uid_procs_after_uid_kill"] = len(runner.uid_pids(uid))


# --------------------------------------------------------------------------------------------------- floors

def suite_floors(ctx):
    steps = {"nproc": [8, 16, 32, 64, 128, 256], "nofile": [8, 16, 32, 64, 128, 256, 1024]}
    for kind, values in steps.items():
        for lang in LANGS:
            hello = get_build(ctx, lang, "hello")
            for value in values:
                for i in range(ctx.reps(2)):
                    res, rec = run_prog(ctx, "floors", hello, case=f"floor_{kind}", variant=str(value),
                                        limits={kind: value}, jvm=[JAVA_XMX] if lang == "java" else (),
                                        timeout_s=20, keep_stdout=True, value=value, index=i)
                    rec["passed"] = res.ok and res.stdout.strip() == b"hello"
                    runner.kill_uid(ctx.uid)


# ------------------------------------------------------------------------------------------- isolation

def iso_victim(ctx, name, nproc, duration):
    """Chạy bài bình thường (Java, Python, C++ xen kẽ) liên tục; ghi từng lần thành/bại kèm mốc epoch."""
    builds = {lang: get_build(ctx, lang, "hello") for lang in LANGS}
    ctx.add("iso", {"case": "victim_start", "name": name, "uid": ctx.uid, "nproc": nproc})
    limits = {"nproc": nproc or None}
    stop = time.time() + duration
    for lang in itertools.cycle(("java", "python", "cpp")):
        if time.time() > stop:
            break
        res, rec = run_prog(ctx, "iso", builds[lang], case="victim_run", limits=limits,
                            jvm=[JAVA_XMX] if lang == "java" else (), keep_stdout=True, timeout_s=20,
                            name=name, role="victim")
        rec["ok"] = res.ok and res.stdout.strip() == b"hello"
        time.sleep(0.05)


def iso_bomb(ctx, name, nproc, delay):
    """Đợi `delay` giây (tính từ lúc process khởi động) rồi chạy fork bomb C++ trong 12 giây."""
    t_begin = time.time()
    bomb = get_build(ctx, "cpp", "fork_bomb")
    time.sleep(max(0.0, delay - (time.time() - t_begin)))
    t_start = time.time()
    res, rec = run_prog(ctx, "iso", bomb, case="bomb_window", limits={"nproc": nproc or None}, timeout_s=12,
                        name=name, role="bomb")
    rec.update(t_start=t_start, t_end=time.time(), eagain_seen="EAGAIN" in res.stderr_tail)
    runner.kill_uid(ctx.uid)


MAIN_SUITES = [("compile", suite_compile), ("selfcheck", suite_selfcheck), ("d13", suite_d13),
               ("overhead", suite_overhead), ("bench", suite_bench), ("kill", suite_kill),
               ("floors", suite_floors)]
