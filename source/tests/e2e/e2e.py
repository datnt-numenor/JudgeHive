"""Kiểm thử đầu cuối: server + worker thật qua `docker compose`, nộp bài và xem kết quả bằng REST.

Chạy trên máy host (cần Docker và Python 3, chỉ dùng thư viện chuẩn), từ thư mục source/:

    python tests/e2e/e2e.py

Dùng project compose riêng (`judgehive-e2e`) và xóa volume khi xong, nên không đụng DB của stack đang chạy; vẫn cần
cổng 8000 và 9000 trống. Thoát với mã 0 nếu mọi kiểm tra đạt, 1 nếu có kiểm tra hỏng."""

import glob
import http.client
import json
import os
import secrets
import subprocess
import sys
import time
import uuid

SOURCE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROJECT = "judgehive-e2e"
PASSWORD = "e2e-password"
WORKERS = 3
HTTP_PORT = 8000

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
rest_output = []  # mọi body REST đã nhận, để kiểm tra không rò test ẩn


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
    """Cờ nội bộ (attempt_id, delivery_count) mà REST không trả; chỉ để kiểm tra giao lại."""
    out = manage("submissions", "show", str(submission_id)).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def http_call(method, path, obj=None, cookie=None, raw=None, content_type="application/json"):
    conn = http.client.HTTPConnection("127.0.0.1", HTTP_PORT, timeout=30)
    headers = {"Connection": "close"}
    body = raw
    if obj is not None:
        body = json.dumps(obj).encode("utf-8")
    if body is not None and content_type:
        headers["Content-Type"] = content_type
    if cookie:
        headers["Cookie"] = f"jh_session={cookie}"
    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        rest_output.append(data)
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, (json.loads(data) if data else None)
    finally:
        conn.close()


def login(username="alice", password=PASSWORD):
    status, headers, body = http_call("POST", "/api/login", {"username": username, "password": password})
    return status, headers, body


def submit(cookie, language, code, token=None, problem="aplusb"):
    token = token or str(uuid.uuid4())
    status, _, body = http_call("POST", "/api/submissions", {"problem_id": problem, "language": language, "code": code,
                                                              "client_token": token}, cookie=cookie)
    if status not in (200, 201):
        raise RuntimeError(f"nộp bài lỗi {status}: {body}")
    return body["submission_id"], token


def detail(cookie, submission_id):
    status, _, body = http_call("GET", f"/api/submissions/{submission_id}", cookie=cookie)
    return body if status == 200 else {"state": f"http-{status}"}


def wait_for(predicate, timeout, what):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(1)
    raise TimeoutError(f"quá {timeout} giây khi chờ: {what}")


def wait_judged(cookie, submission_id, what, timeout=180):
    return wait_for(lambda: (d := detail(cookie, submission_id))["state"] == "judged" and d, timeout, what)


def check(name, ok, detail_text=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail_text}]" if detail_text and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def scoreboard(cookie):
    status, _, body = http_call("GET", "/api/scoreboard", cookie=cookie)
    return body if status == 200 else {"version": 0, "rows": []}


def restart_server():
    compose("restart", "server")
    compose("up", "-d", "--wait", "--no-recreate")


def hidden_test_files():
    """Nội dung các test ẩn của đề seed `aplusb`, để chắc REST không bao giờ trả chúng."""
    found = []
    for path in glob.glob(os.path.join(SOURCE, "server", "problems", "aplusb", "tests", "*.in")):
        with open(path, "rb") as f:
            data = f.read().strip()
        if len(data) >= 9:  # bỏ input quá ngắn (ví dụ "10 20") vì có thể khớp nhầm với văn bản khác
            found.append(data)
    return found


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

        status, headers, body = http_call("GET", "/api/health")
        check("health công khai và tối thiểu", status == 200 and body == {"status": "ok"})
        check("không lộ header server (cờ --no-server-header)", "server" not in headers, headers.get("server", ""))
        check("không lộ tài liệu API", http_call("GET", "/openapi.json")[0] == 404 and http_call("GET", "/docs")[0] == 404)
        check("chưa đăng nhập thì 401", http_call("GET", "/api/problems")[0] == 401)

        manage("users", "create", "alice", "--password-stdin", input_text=PASSWORD + "\n")
        manage("users", "create", "root", "--role", "admin", "--password-stdin", input_text=PASSWORD + "\n")
        check("sai mật khẩu thì 401", login(password="sai")[0] == 401)
        status, headers, body = login()
        cookie = headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
        check("đăng nhập đặt cookie HttpOnly SameSite=Lax", status == 200 and "HttpOnly" in headers["set-cookie"]
              and "SameSite=Lax" in headers["set-cookie"], headers.get("set-cookie", ""))
        check("GET /api/me trả đúng người dùng", http_call("GET", "/api/me", cookie=cookie)[2]["user"]["username"] == "alice")
        problems = http_call("GET", "/api/problems", cookie=cookie)[2]["problems"]
        check("danh sách đề có aplusb", [p["id"] for p in problems] == ["aplusb"])
        problem = http_call("GET", "/api/problems/aplusb", cookie=cookie)[2]
        check("chi tiết đề có test mẫu", len(problem["samples"]) >= 1 and problem["statement"])
        check("số 5000 chữ số trong query là 400, không phải 500",
              http_call("GET", "/api/submissions?limit=" + "1" * 5000, cookie=cookie)[0] == 400)
        check("id 5000 chữ số trên đường dẫn là 404",
              http_call("GET", "/api/submissions/" + "1" * 5000, cookie=cookie)[0] == 404)

        ids = {}
        tokens = {}
        for name, (lang, code, _) in SOLUTIONS.items():
            ids[name], tokens[name] = submit(cookie, lang, code)
        slow_id, _ = submit(cookie, *SLOW)
        again_id, _ = submit(cookie, SOLUTIONS["py-ac"][0], "print('khác')\n", token=tokens["py-ac"])
        check("nộp trùng client_token trả bài gốc", again_id == ids["py-ac"])
        check("nộp trùng trả 200", http_call("POST", "/api/submissions", {"problem_id": "aplusb", "language": "python",
              "code": "x", "client_token": tokens["py-ac"]}, cookie=cookie)[0] == 200)

        for name, (_, _, expected) in SOLUTIONS.items():
            row = wait_judged(cookie, ids[name], f"bài {name} chấm xong")
            check(f"{name} ra {expected}", row["verdict"] == expected, f"được {row['verdict']}")
        check("cpp-ce có thông báo biên dịch rút gọn và không có thời gian",
              detail(cookie, ids["cpp-ce"])["ce_message"] and detail(cookie, ids["cpp-ce"])["time_ms"] is None)
        check("py-re báo loại lỗi, không báo mã thoát", detail(cookie, ids["py-re"])["re_kind"] is not None
              and "exit_code" not in detail(cookie, ids["py-re"]))
        history = http_call("GET", "/api/submissions?limit=5", cookie=cookie)[2]
        check("lịch sử phân trang, mới nhất trước",
              [s["submission_id"] for s in history["submissions"]] == sorted(
                  (s["submission_id"] for s in history["submissions"]), reverse=True)
              and history["next_before_id"] is not None)

        # bảng xếp hạng: cần phiên; alice giải đề aplusb bằng bài py-ac nộp đầu tiên (các bài sau AC không thành lần sai)
        check("bảng xếp hạng cần phiên", http_call("GET", "/api/scoreboard")[0] == 401)
        board = wait_for(lambda: (b := scoreboard(cookie))["rows"] and b["rows"][0]["solved"] == 1 and b, 30,
                         "bảng xếp hạng có alice với 1 đề đã giải")
        received = detail(cookie, ids["py-ac"])["received_at"]
        check("bảng xếp hạng: alice hạng 1, giải 1 đề, 0 lần sai, mốc AC là lúc nhận bài py-ac",
              board["rows"] == [{"rank": 1, "username": "alice", "solved": 1, "wrong": 0, "last_ac_at": received}],
              str(board["rows"]))
        root_login = http_call("POST", "/api/login", {"username": "root", "password": PASSWORD})
        root_cookie = root_login[1]["set-cookie"].split(";", 1)[0].split("=", 1)[1]
        root_id, _ = submit(root_cookie, *SOLUTIONS["py-ac"][:2])
        wait_judged(root_cookie, root_id, "bài AC của admin chấm xong")
        time.sleep(3)  # chờ bảng được tính lại sau kết quả của admin
        check("bảng xếp hạng: admin không bao giờ có hàng",
              [r["username"] for r in scoreboard(cookie)["rows"]] == ["alice"])
        version_before = scoreboard(cookie)["version"]

        # worker chết giữa chừng: bài được giao lại với attempt_id mới
        wait_for(lambda: detail(cookie, slow_id)["state"] in ("judging", "judged"), 120, "bài chậm được giao")
        if detail(cookie, slow_id)["state"] == "judging":
            compose("kill", "worker")
            compose("up", "-d", "--wait", "--no-recreate", "--scale", f"worker={WORKERS}")
            slow = wait_judged(cookie, slow_id, "bài chậm chấm xong sau khi worker chết")
            check("bài giao lại vẫn ra AC", slow["verdict"] == "AC", slow["verdict"])
            internal = show(slow_id)
            check("bài giao lại có attempt_id mới", int(internal["attempt_id"]) >= 2 and int(internal["delivery_count"]) >= 2,
                  f"attempt={internal['attempt_id']} deliveries={internal['delivery_count']}")
        else:
            check("bài chậm chấm xong trước khi kịp kill worker (bỏ qua kiểm tra giao lại)", True)

        # DB nằm trong volume: restart server không mất verdict lẫn phiên đăng nhập
        restart_server()
        check("verdict còn sau khi restart server", detail(cookie, ids["py-ac"])["verdict"] == "AC")
        check("phiên còn sau khi restart server", http_call("GET", "/api/me", cookie=cookie)[0] == 200)
        after_restart = scoreboard(cookie)
        check("restart: bảng dựng lại từ DB, phiên bản bắt đầu lại",
              [r["username"] for r in after_restart["rows"]] == ["alice"] and after_restart["version"] <= 3,
              f"version={after_restart['version']}")

        # secret không lộ cho code thí sinh: chạy một bài nộp thật in môi trường và cố đọc /proc/1/environ
        secret_value = open(secret_file, encoding="utf-8").read().strip().split("=", 1)[1]
        probe_id, _ = submit(cookie, "python", PROBE)
        probe = wait_judged(cookie, probe_id, "bài dò secret chấm xong")
        out = probe.get("sample_stdout") or ""
        check("bài dò secret chạy được (WA ở test mẫu để lấy stdout)", probe["verdict"] == "WA" and "ENV-DONE" in out,
              f"{probe['verdict']} {out[:200]}")
        check("code thí sinh không thấy secret trong env, /proc/1/environ hay file", secret_value not in out)
        check("code thí sinh bị từ chối khi đọc /proc/1/environ", "/proc/1/environ PermissionError" in out, out[:300])

        wait_for(lambda: scoreboard(cookie)["version"] > after_restart["version"], 30,
                 "bảng xếp hạng được tính lại sau kết quả của bài dò secret")
        check("bảng xếp hạng được tính lại khi có kết quả mới (phiên bản tăng)",
              scoreboard(cookie)["version"] > after_restart["version"])
        check("version trước restart là số dương", version_before >= 1)

        # không endpoint nào của REST trả test ẩn hay secret
        blob = b"".join(rest_output)
        leaked = [t for t in hidden_test_files() if t in blob]
        check("REST không trả nội dung test ẩn", not leaked, f"{len(leaked)} test ẩn xuất hiện")
        check("REST không trả secret của worker", secret_value.encode() not in blob)

        # đăng xuất hủy phiên ở server
        check("đăng xuất trả 204", http_call("POST", "/api/logout", {}, cookie=cookie)[0] == 204)
        check("dùng lại cookie sau đăng xuất thì 401", http_call("GET", "/api/me", cookie=cookie)[0] == 401)
    finally:
        compose("down", "-v", "--remove-orphans", check=False)
    print(f"\n{len(failures)} kiểm tra hỏng" if failures else "\ntất cả kiểm tra đạt")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
