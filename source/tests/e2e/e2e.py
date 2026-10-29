"""Kiểm thử đầu cuối: server + worker thật qua `docker compose`, nộp bài và xem kết quả bằng REST.

Chạy trên máy host (cần Docker và Python 3, chỉ dùng thư viện chuẩn), từ thư mục source/:

    python tests/e2e/e2e.py

Dùng project compose riêng (`judgehive-e2e`) và xóa volume khi xong, nên không đụng DB của stack đang chạy; vẫn cần
cổng 8000 và 9000 trống. Thoát với mã 0 nếu mọi kiểm tra đạt, 1 nếu có kiểm tra hỏng."""

import glob
import http.client
import json
import os
import re
import secrets
import subprocess
import sys
import time
import uuid

from ws_client import HandshakeRejected, WsClient, encode_frame  # client WebSocket tự viết, mask mọi frame

SOURCE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROJECT = "judgehive-e2e"
PASSWORD = "e2e-password"
WORKERS = 3
KILLS = 2  # số lần kill worker giữ bài: bằng số lần giao tối đa (3, D1) trừ một, để lần giao cuối còn chấm xong
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
# Chạy 4 s (giới hạn 1 s x hệ số Python 5 = 5 s): đủ rộng để kill worker giữ bài giữa chừng, đủ hẹp để không bị TLE.
SLOW = ("python", "import sys, time\ntime.sleep(4)\nprint(sum(map(int, sys.stdin.read().split())))\n")

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


def page_call(path, method="GET"):
    """Như `http_call` nhưng trả body nguyên bản (bytes): dành cho trang HTML và file tĩnh, không parse JSON."""
    conn = http.client.HTTPConnection("127.0.0.1", HTTP_PORT, timeout=30)
    try:
        conn.request(method, path, headers={"Connection": "close"})
        resp = conn.getresponse()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
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


def wait_for(predicate, timeout, what, interval=1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise TimeoutError(f"quá {timeout} giây khi chờ: {what}")


def wait_judged(cookie, submission_id, what, timeout=180):
    return wait_for(lambda: (d := detail(cookie, submission_id))["state"] == "judged" and d, timeout, what)


def delivered_to(submission_id, attempt, timeout=30):
    """(worker_id, tên worker) trong dòng log 'submission S attempt A giao cho worker #W (tên)' của server. Tên worker
    là hostname, tức ID ngắn của container, nên dò được container nào đang giữ bài."""
    pattern = re.compile(rf"submission {submission_id} attempt {attempt} giao cho worker #(\d+) \('([^']*)'\)")

    def find():
        m = pattern.search(compose("logs", "--no-color", "--no-log-prefix", "server", check=False).stdout)
        return m and (int(m.group(1)), m.group(2))
    return wait_for(find, timeout, f"log giao bài {submission_id} lần {attempt}", interval=0.2)


def kill_container(container_id):
    """Kill đúng một container (`compose kill worker` kill cả ba cùng lúc nên không dùng được)."""
    proc = subprocess.run(["docker", "kill", container_id], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"docker kill {container_id} -> {proc.returncode}\n{proc.stdout}\n{proc.stderr}")


def container_states(containers):
    """{ID container: (đang chạy, số lần restart, StartedAt)} qua `docker inspect`."""
    out = subprocess.run(["docker", "inspect", "-f", "{{.Id}} {{.State.Running}} {{.RestartCount}} {{.State.StartedAt}}",
                          *containers], capture_output=True, text=True, timeout=60).stdout.splitlines()
    return {f[0]: (f[1] == "true", int(f[2]), f[3]) for f in (line.split() for line in out) if len(f) == 4}


def check(name, ok, detail_text=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail_text}]" if detail_text and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def scoreboard(cookie):
    status, _, body = http_call("GET", "/api/scoreboard", cookie=cookie)
    return body if status == 200 else {"version": 0, "rows": []}


def ws_connect(cookie, origin="same"):
    if origin == "same":
        origin = f"http://127.0.0.1:{HTTP_PORT}"
    return WsClient("127.0.0.1", HTTP_PORT, origin=origin, cookie=cookie, timeout=15)


def ws_read(client, until, timeout=120):
    """Đọc message JSON của `client` tới khi `until(danh sách)` đúng; tự trả PONG cho PING; ghi mọi frame vào `rest_output`."""
    messages, deadline = [], time.monotonic() + timeout
    while time.monotonic() < deadline:
        kind, value = client.recv(timeout=max(0.1, deadline - time.monotonic()))
        if kind == "text":
            rest_output.append(value.encode("utf-8"))
            message = json.loads(value)
            if message.get("type") == "PING":
                client.send_pong_for(message)
            messages.append(message)
            if until(messages):
                break
        elif kind in ("close", "eof"):
            messages.append({"closed": value})
            break
        elif kind == "timeout":
            break
    return messages


def ws_status(messages, sid=None):
    return [m for m in messages if m.get("type") == "STATUS" and (sid is None or m["submission_id"] == sid)]


def ws_close_code(client, timeout=10):
    """Mã của frame đóng đầu tiên, hoặc "eof"/"timeout"."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        kind, value = client.recv(timeout=max(0.1, deadline - time.monotonic()))
        if kind == "close":
            return value
        if kind in ("eof", "timeout"):
            return kind
    return "timeout"


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
        # `/docs` và `/redoc` không có đuôi file nên là đường dẫn của SPA (trả index.html): điều cần chắc là không có tài liệu
        # API của FastAPI (Swagger UI, ReDoc, schema OpenAPI) ở bất kỳ đường nào trong số này.
        doc_pages = [page_call(path) for path in ("/openapi.json", "/docs", "/redoc")]
        check("không lộ tài liệu API",
              doc_pages[0][0] == 404 and all(b"swagger" not in body.lower() and b"redoc" not in body.lower()
                                               and b"openapi" not in body.lower() for _, _, body in doc_pages))
        check("chưa đăng nhập thì 401", http_call("GET", "/api/problems")[0] == 401)

        status, headers, page = page_call("/")
        check("trang chủ là index.html của giao diện, luôn hỏi lại (no-cache)",
              status == 200 and b'id="root"' in page and headers.get("cache-control") == "no-cache", headers.get("cache-control", ""))
        asset = re.search(rb'/assets/[^"<>\s]+\.js', page)
        check("index.html trỏ tới một file JS trong /assets", asset is not None)
        if asset:
            a_status, a_headers, a_body = page_call(asset.group(0).decode())
            check("file trong /assets phục vụ được, cache dài hạn",
                  a_status == 200 and len(a_body) > 0 and "immutable" in a_headers.get("cache-control", ""), a_headers.get("cache-control", ""))
        check("đường dẫn của SPA rơi về index.html", page_call("/problems/aplusb")[2] == page)
        m_status, _, m_body = page_call("/missing.js")
        check("file có đuôi không tồn tại là 404, không phải HTML", m_status == 404 and b'id="root"' not in m_body)
        check("API không tồn tại vẫn là JSON 404", http_call("GET", "/api/nope")[0] == 404 and page_call("/api/nope")[2] != page)
        check("/ws và /assets/ không rơi về trang", page_call("/ws")[0] == 404 and page_call("/assets/missing")[0] == 404)

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
        # Kill đúng worker đang giữ bài, từng cái một (3 lần giao tối đa: kill sau lần 1 và lần 2, lần 3 phải chạy xong).
        # Bài nộp ngay trước vòng này nên không thể chấm xong sớm. Log "giao cho worker" được ghi sau khi DB đã ghi
        # attempt_id, nên dãy attempt 1, 2, 3 trong log là bằng chứng attempt_id đổi sau mỗi lần kill.
        containers = compose("ps", "-q", "worker").stdout.split()
        hostnames = subprocess.run(["docker", "inspect", "-f", "{{.Config.Hostname}}", *containers], capture_output=True,
                                   text=True, timeout=60).stdout.split()
        prefix_ok = len(hostnames) == len(containers) and all(c.startswith(h) for c, h in zip(containers, hostnames))
        check("hostname của worker là tiền tố ID container (e2e dò container giữ bài theo tên worker)", prefix_ok,
              f"hostnames={hostnames} containers={[c[:12] for c in containers]}")
        if not prefix_ok:
            raise RuntimeError("hostname worker không phải tiền tố ID container: không dò được container giữ bài")
        before = container_states(containers)
        killed = set()
        slow_id, _ = submit(cookie, *SLOW)
        for attempt in range(1, KILLS + 1):
            _, name = delivered_to(slow_id, attempt)
            holder = [c for c in containers if c.startswith(name)]
            if len(holder) != 1:
                raise RuntimeError(f"lần giao {attempt}: tên worker {name!r} khớp {len(holder)} container")
            killed.add(holder[0])
            kill_container(holder[0])  # kill ngay, không truy vấn DB giữa hai lần kill: cửa sổ mỗi lần chỉ 2 s
        delivered_to(slow_id, KILLS + 1)  # quá hạn chờ thì ném TimeoutError: bài không được giao lại
        time.sleep(5)  # chờ restart policy (nếu có) tác động trước khi `compose up` dựng lại đủ số worker
        after = container_states(containers)
        untouched = [c for c in containers if c not in killed]
        check("chỉ kill container đang giữ bài: các worker khác không bị khởi động lại",
              all(after.get(c, (False,))[0] and after[c][2] == before[c][2] for c in untouched),
              f"untouched={[c[:12] for c in untouched]} before={before} after={after}")
        for c in killed:  # chỉ quan sát: restart policy của compose (`unless-stopped`) có khởi động lại container bị kill không
            print(f"INFO container {c[:12]} sau docker kill: đang chạy={after[c][0]}, RestartCount={after[c][1]}, "
                  f"khởi động lại={after[c][2] != before[c][2]}", flush=True)
        # Docker cố ý không áp restart policy cho container bị dừng bằng tay (kill/stop), nên phải dựng lại đủ worker ở
        # đây; worker crash thật (không phải do người dùng dừng) vẫn được restart policy dựng lại.
        compose("up", "-d", "--wait", "--no-recreate", "--scale", f"worker={WORKERS}")
        slow = wait_judged(cookie, slow_id, "bài chậm chấm xong sau khi worker chết")
        check("bài giao lại vẫn ra AC", slow["verdict"] == "AC", slow["verdict"])
        internal = show(slow_id)
        check(f"chấm xong ở attempt_id={KILLS + 1}, delivery_count={KILLS + 1}",
              internal["attempt_id"] == str(KILLS + 1) and internal["delivery_count"] == str(KILLS + 1),
              f"attempt={internal['attempt_id']} deliveries={internal['delivery_count']}")

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

        # WebSocket (README mục 4)
        def refused_with_403(**kw):
            try:
                ws_connect(cookie, **kw)
            except HandshakeRejected as e:
                return e.status == 403
            return False

        check("WebSocket: Origin lạ bị từ chối bằng HTTP 403", refused_with_403(origin="http://evil.example"))
        check("WebSocket: thiếu Origin bị từ chối bằng HTTP 403", refused_with_403(origin=None))
        anonymous = ws_connect(None)
        check("WebSocket: không có phiên thì accept rồi đóng 4401", ws_close_code(anonymous) == 4401)

        ws_alice, ws_root = ws_connect(cookie), ws_connect(root_cookie)
        first_alice = ws_alice.recv_json(timeout=15)
        check("WebSocket: nối được và nhận SCOREBOARD ngay",
              isinstance(first_alice, dict) and first_alice["type"] == "SCOREBOARD" and first_alice["rows"]
              and first_alice["rows"][0]["username"] == "alice", str(first_alice)[:150])
        rest_output.append(json.dumps(first_alice).encode("utf-8"))
        ws_root.recv_json(timeout=15)

        ws_sid, _ = submit(root_cookie, *SOLUTIONS["py-ac"][:2])
        frames = ws_read(ws_root, lambda ms: any(m.get("state") == "judged" for m in ws_status(ms, ws_sid)))
        states = [m["state"] for m in ws_status(frames, ws_sid)]
        check("WebSocket: STATUS tới đúng chủ bài theo thứ tự queued, judging, judged",
              states[:1] == ["queued"] and "judging" in states and states[-1:] == ["judged"], str(states))
        check("WebSocket: STATUS judged mang đối tượng bài nộp khớp REST (AC)",
              ws_status(frames, ws_sid)[-1]["submission"] == next(
                  x for x in http_call("GET", "/api/submissions?limit=20", cookie=root_cookie)[2]["submissions"]
                  if x["submission_id"] == ws_sid))
        check("WebSocket: có tiến độ chấm (progress)", any("progress" in m for m in ws_status(frames, ws_sid)))
        alice_frames = ws_read(ws_alice, lambda ms: False, timeout=2.5)
        check("WebSocket: người khác (alice) không nhận STATUS của admin", ws_status(alice_frames) == [])

        mask_probe = ws_connect(cookie)
        mask_probe.recv_json(timeout=15)
        mask_probe.sock.sendall(encode_frame(0x1, b'{"type":"PONG","seq":1}', mask=False))
        check("WebSocket: server từ chối frame client chưa mask bằng 1002 (client e2e luôn mask)",
              ws_close_code(mask_probe) == 1002)
        binary_probe = ws_connect(cookie)
        binary_probe.recv_json(timeout=15)
        binary_probe.send_binary(b"\x00\x01")
        check("WebSocket: frame nhị phân bị đóng 1003", ws_close_code(binary_probe) == 1003)
        big_probe = ws_connect(cookie)
        big_probe.recv_json(timeout=15)
        big_probe.send_text('{"type":"PONG","seq":1,"pad":"' + "x" * 5000 + '"}')
        check("WebSocket: frame quá 4096 byte bị đóng 1009", ws_close_code(big_probe) == 1009)

        extra = [ws_connect(cookie) for _ in range(8)]  # cùng ws_alice là 9
        tenth = ws_connect(cookie)
        eleventh = ws_connect(cookie)
        check("WebSocket: kết nối thứ 10 của một người vẫn được", tenth.recv_json(timeout=15)["type"] == "SCOREBOARD")
        check("WebSocket: kết nối thứ 11 của một người bị đóng 4429", ws_close_code(eleventh) == 4429)
        for c in extra + [tenth]:
            c.close()

        second_login = http_call("POST", "/api/login", {"username": "alice", "password": PASSWORD})[1]["set-cookie"]
        second_cookie = second_login.split(";", 1)[0].split("=", 1)[1]
        ws_second = ws_connect(second_cookie)
        ws_second.recv_json(timeout=15)
        check("WebSocket: logout đóng WebSocket của phiên đó bằng 4401",
              http_call("POST", "/api/logout", {}, cookie=second_cookie)[0] == 204 and ws_close_code(ws_second) == 4401)
        check("WebSocket: logout một phiên không đóng WebSocket của phiên khác",
              ws_status(ws_read(ws_alice, lambda ms: False, timeout=1.5)) == [] and ws_alice.sock.fileno() != -1
              and http_call("GET", "/api/me", cookie=cookie)[0] == 200)
        for c in (ws_alice, ws_root):
            c.close()

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
