"""CLI của spike executor: chạy các bộ đo và ghi JSON thô vào thư mục kết quả.

Xem README.md trong thư mục này. Chỉ dùng thư viện chuẩn.
"""

import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import time

import report
import suites

# Suite bắt đầu một lần chạy mới (tạo thư mục kết quả mới); các suite còn lại dùng thư mục LATEST.
NEW_RUN = {"main"} | {name for name, _ in suites.MAIN_SUITES}
SINGLE = dict(suites.MAIN_SUITES)


def _first_line(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return ((p.stdout or "") + (p.stderr or "")).strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        return "không đọc được"


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def collect_meta(args):
    cpu = next((ln.split(":", 1)[1].strip() for ln in (_read("/proc/cpuinfo") or "").splitlines()
                if ln.startswith("model name")), "?")
    return {
        "hostname": socket.gethostname(),
        "kernel": platform.release(),
        "cpu_model": cpu,
        "cpu_count": os.cpu_count(),
        "cgroup_cpu_max": _read("/sys/fs/cgroup/cpu.max"),
        "cgroup_memory_max": _read("/sys/fs/cgroup/memory.max"),
        "cgroup_pids_max": _read("/sys/fs/cgroup/pids.max"),
        "python": platform.python_version(),
        "gxx": _first_line(["g++", "--version"]),
        "javac": _first_line(["javac", "-version"]),
        "java": _first_line(["java", "-version"]),
        "os_release": next((ln.split("=", 1)[1].strip('"') for ln in (_read("/etc/os-release") or "").splitlines()
                            if ln.startswith("PRETTY_NAME")), "?"),
        "args": vars(args),
        "limit_kib": suites.LIMIT_KIB,
        "stderr_tail_bytes": suites.STDERR_TAIL,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def run_dir(args):
    latest = os.path.join(args.results, "LATEST")
    if args.suite in NEW_RUN:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + socket.gethostname()
        path = os.path.join(args.results, stamp)
        os.makedirs(path, exist_ok=True)
        with open(latest, "w") as f:
            f.write(stamp)
        return path
    with open(latest) as f:
        return os.path.join(args.results, f.read().strip())


def write_part(path, name, ctx, args):
    with open(os.path.join(path, f"{name}.json"), "w") as f:
        json.dump({"part": name, "meta": collect_meta(args), "runs": ctx.runs}, f, indent=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--suite", required=True,
                    choices=["main", "cold3", "iso-victim", "iso-bomb", "report"] + sorted(SINGLE))
    ap.add_argument("--results", default="/results")
    ap.add_argument("--quick", action="store_true", help="giảm số lần lặp để chạy thử nhanh")
    ap.add_argument("--name", default="s0", help="iso: tên kịch bản")
    ap.add_argument("--uid", type=int, default=suites.JUDGE_UID)
    ap.add_argument("--nproc", type=int, default=0, help="iso: RLIMIT_NPROC của chương trình (0 = không đặt)")
    ap.add_argument("--delay", type=float, default=20.0, help="iso-bomb: giây chờ trước khi nổ")
    ap.add_argument("--duration", type=float, default=45.0, help="iso-victim: giây chạy")
    args = ap.parse_args()

    path = run_dir(args)
    if args.suite == "report":
        report.build(path)
        print(f"đã ghi {os.path.join(path, 'summary.md')}")
        return 0

    ctx = suites.Ctx(quick=args.quick, uid=args.uid)
    if args.suite == "main":
        for name, fn in suites.MAIN_SUITES:
            print(f"== {name}", flush=True)
            fn(ctx)
        part = "main"
    elif args.suite == "cold3":
        suites.suite_cold3(ctx)
        part = "cold3"
    elif args.suite == "iso-victim":
        suites.iso_victim(ctx, args.name, args.nproc, args.duration)
        part = f"iso-{args.name}-victim"
    elif args.suite == "iso-bomb":
        suites.iso_bomb(ctx, args.name, args.nproc, args.delay)
        part = f"iso-{args.name}-bomb"
    else:
        SINGLE[args.suite](ctx)
        part = args.suite
    write_part(path, part, ctx, args)
    print(f"đã ghi {os.path.join(path, part + '.json')}")
    if ctx.failures:
        print("SELFCHECK FAIL:", ", ".join(ctx.failures), file=sys.stderr)
        # suite main vẫn trả 0 để chuỗi compose chạy tiếp; summary.md sẽ ghi FAIL nổi bật
        return 0 if args.suite == "main" else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
