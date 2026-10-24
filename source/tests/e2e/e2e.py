"""Kiểm thử đầu cuối PR 4: server + worker thật qua `docker compose`, nộp bài bằng `manage.py` (chưa có REST).

Chạy trên máy host (cần Docker và Python 3, chỉ dùng thư viện chuẩn), từ thư mục source/:

    python tests/e2e/e2e.py

Dùng project compose riêng (`judgehive-e2e`) và xóa volume khi xong, nên không đụng DB của stack đang chạy; vẫn cần
cổng 8000 và 9000 trống. Thoát với mã 0 nếu mọi kiểm tra đạt, 1 nếu có kiểm tra hỏng."""

import os
import secrets
import subprocess
import sys
import time

SOURCE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROJECT = "judgehive-e2e"
PASSWORD = "e2e-password"
WORKERS = 3

SOLUTIONS = {
    "py-ac": ("python", "import sys\nprint(sum(map(int, sys.stdin.read().split())))\n", "AC"),
    "py-wa": ("python", "print(0)\n", "WA"),
    "py-tle": ("python", "while True:\n    pass\n", "TLE"),
    "py-re": ("python", "print(1 // 0)\n", "RE"),
    "cpp-ac": ("cpp", "#include <iostream>\nint main() { long long s = 0, x; while (std::cin >> x) s += x;"
                      " std::cout << s << \"\\n\"; }\n", "AC"),
    "cpp-ce": ("cpp", "int main( { return 0 }\n", "CE"),
    "java-ac": ("java", "import java.util.*;\npublic class Main {\n    public static void main(String[] a) {\n"
                        "        Scanner in = new Scanner(System.in);\n        long s = 0;\n"
                        "        while (in.hasNextLong()) s += in.nextLong();\n        System.out.println(s);\n    }\n}\n",
                "AC"),
}
PROBE = (
    "import os\n"
    "for k, v in sorted(os.environ.items()):\n    print('ENV', k, v)\n"
    "for path in ('/proc/1/environ', '/run/secrets/worker_secret', '/app/worker/main.py'):\n"
    "    try:\n        print(path, 'READ', len(open(path, 'rb').read()))\n"
    "    except Exception as e:\n        print(path, type(e).__name__)\n"
    "print('ENV-DONE')\n"
)
SLOW = ("python", "import sys, time\ntime.sleep(3)\nprint(sum(map(int, sys.stdin.read().split())))\n")

failures = []


def compose(*args, input_text=None, check=True, timeout=600):
    cmd = ["docker", "compose", "-p", PROJECT, *args]
    proc = subprocess.run(cmd, cwd=SOURCE, input=input_text, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} -> {proc.returncode}\n{proc.stdout}\n{proc.stderr}")
    return proc


def manage(*args, input_text=None, check=True):
    return compose("exec", "-T", "server", "python", "-m", "server.manage", *args, input_text=input_text, check=check)


def show(submission_id):
    out = manage("submissions", "show", str(submission_id)).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def wait_for(predicate, timeout, what):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(1)
    raise TimeoutError(f"quá {timeout} giây khi chờ: {what}")


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def add_submission(language, code):
    out = manage("submissions", "add", "--user", "alice", "--problem", "aplusb", "--language", language,
                 "--file", "-", input_text=code).stdout
    return int(out.strip())


def restart_server():
    compose("restart", "server")
    compose("up", "-d", "--wait", "--no-recreate")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # console Windows mặc định không in được tiếng Việt
    secret_file = os.path.join(SOURCE, "secrets", "worker.env")
    os.makedirs(os.path.dirname(secret_file), exist_ok=True)
    if not os.path.exists(secret_file):
        with open(secret_file, "w", encoding="utf-8") as f:
            f.write("JH_WORKER_SECRET=" + secrets.token_hex(32) + "\n")
    compose("down", "-v", "--remove-orphans", check=False)
    try:
        compose("up", "-d", "--build", "--wait", "--scale", f"worker={WORKERS}", timeout=1800)
        check("server khỏe và có đủ worker", len(compose("ps", "-q", "worker").stdout.split()) == WORKERS)

        manage("users", "create", "alice", "--password-stdin", input_text=PASSWORD + "\n")
        ids = {name: add_submission(lang, code) for name, (lang, code, _) in SOLUTIONS.items()}
        slow_id = add_submission(*SLOW)
        restart_server()  # hàng đợi trong RAM: lifespan nạp lại các bài queued từ DB

        for name, (_, _, expected) in SOLUTIONS.items():
            row = wait_for(lambda n=name: (r := show(ids[n]))["state"] == "done" and r, 180, f"bài {name} chấm xong")
            check(f"{name} ra {expected}", row["verdict"] == expected, f"được {row['verdict']}")
        ac = show(ids["py-ac"])
        check("verdict AC ghi hash bộ test", ac["verdict_test_hash"] == ac["delivered_test_hash"] != "None")
        check("cpp-ce không có thời gian", show(ids["cpp-ce"])["time_ms"] == "None")

        # worker chết giữa chừng: bài được giao lại với attempt_id mới
        wait_for(lambda: show(slow_id)["state"] in ("judging", "done"), 120, "bài chậm được giao")
        if show(slow_id)["state"] == "judging":
            compose("kill", "worker")
            compose("up", "-d", "--wait", "--no-recreate", "--scale", f"worker={WORKERS}")
            slow = wait_for(lambda: (r := show(slow_id))["state"] == "done" and r, 180, "bài chậm chấm xong sau khi worker chết")
            check("bài giao lại vẫn ra AC", slow["verdict"] == "AC", slow["verdict"])
            check("bài giao lại có attempt_id mới", int(slow["attempt_id"]) >= 2 and int(slow["delivery_count"]) >= 2,
                  f"attempt={slow['attempt_id']} deliveries={slow['delivery_count']}")
        else:
            check("bài chậm chấm xong trước khi kịp kill worker (bỏ qua kiểm tra giao lại)", True)

        # DB nằm trong volume: restart server không mất verdict
        before = show(ids["py-ac"])
        restart_server()
        check("verdict còn sau khi restart server", show(ids["py-ac"])["verdict"] == before["verdict"] == "AC")

        # secret không lộ cho code thí sinh: chạy một bài nộp thật in môi trường và cố đọc /proc/1/environ
        secret_value = open(secret_file, encoding="utf-8").read().strip().split("=", 1)[1]
        probe_id = add_submission("python", PROBE)
        restart_server()
        probe = wait_for(lambda: (r := show(probe_id))["state"] == "done" and r, 180, "bài dò secret chấm xong")
        out = probe.get("sample_stdout", "")
        check("bài dò secret chạy được (WA ở test mẫu để lấy stdout)", probe["verdict"] == "WA" and "ENV-DONE" in out,
              f"{probe['verdict']} {out[:200]}")
        check("code thí sinh không thấy secret trong env, /proc/1/environ hay file", secret_value not in out)
        check("code thí sinh bị từ chối khi đọc /proc/1/environ", "/proc/1/environ PermissionError" in out, out[:300])
    finally:
        compose("down", "-v", "--remove-orphans", check=False)
    print(f"\n{len(failures)} kiểm tra hỏng" if failures else "\ntất cả kiểm tra đạt")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
