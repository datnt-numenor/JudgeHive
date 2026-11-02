"""Load test JudgeHive (D25): giả lập nhiều thí sinh qua HTTP + WebSocket, như một client bên ngoài.

Kiểm từ ngoài (không import server/worker, không đọc DB): 50 bài nộp đồng thời không mất, không chấm trùng, verdict
đúng, mỗi người chỉ nhận bài của mình; đo thời gian chờ. Xem tools/README.md để biết cách tạo tài khoản và chạy.

Phần ĐẦU file là hàm thuần (kế hoạch bài nộp, kiểm luồng STATUS, percentile...), test được không cần server và không
cần `websockets` (chỉ import trong `_listen`). Phần I/O ở cuối."""

import argparse
import asyncio
import http.client
import json
import os
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urlsplit

PASSWORD_ENV = "JH_LOADTEST_PASSWORD"
STATE_RANK = {"queued": 0, "judging": 1, "judged": 2}
# README mục 4.2/3.3: STATUS và danh sách bài nộp không bao giờ mang các field này.
FORBIDDEN_KEYS = frozenset({"code", "ce_message", "sample_stdout", "sample_input", "sample_expected_output"})
LOGIN_CONCURRENCY = 4  # server cho 8 đăng nhập đồng thời (README 3.2)
PENDING_CAP = 5  # N_USER (D25)
GLOBAL_QUEUE_CAP = 200  # N_GLOBAL (D25)

_SUM_PY = "import sys\nprint(sum(map(int, sys.stdin.read().split())))\n"
_SUM_CPP = ("#include <iostream>\nint main() { long long s = 0, x; while (std::cin >> x) s += x;"
            " std::cout << s << \"\\n\"; }\n")
_SUM_JAVA = ("import java.util.*;\npublic class Main {\n    public static void main(String[] a) {\n"
             "        Scanner in = new Scanner(System.in);\n        long s = 0;\n"
             "        while (in.hasNextLong()) s += in.nextLong();\n        System.out.println(s);\n    }\n}\n")
# Chậm có chủ đích để bài còn ở trạng thái chờ khi các bài sau được nộp (kịch bản trần). Đúng hay sai không quan trọng.
_SLOW_PY = "import sys, time\ntime.sleep(3)\nprint(sum(map(int, sys.stdin.read().split())))\n"

# kind -> (ngôn ngữ, mã, verdict mong đợi), cho đề seed `aplusb` (tổng các số nguyên ở stdin)
KINDS = {
    "py-ac": ("python", _SUM_PY, "AC"),
    "cpp-ac": ("cpp", _SUM_CPP, "AC"),
    "java-ac": ("java", _SUM_JAVA, "AC"),
    "py-wa": ("python", "print(0)\n", "WA"),
    "py-ce": ("python", "def f(:\n    pass\n", "CE"),  # lỗi cú pháp Python là CE (D24)
}
DEFAULT_MIX = (("py-ac", 50), ("cpp-ac", 20), ("java-ac", 10), ("py-wa", 10), ("py-ce", 10))


# ================================================================================================ phần thuần

@dataclass
class Planned:
    index: int
    user: int  # chỉ số người dùng 0-based
    kind: str
    language: str
    code: str
    expected: str
    token: str


def parse_mix(text):
    """'py-ac=50,cpp-ac=20,...' -> ((kind, weight), ...). Từ chối kind lạ, trọng số không dương hoặc tổng bằng 0."""
    mix = []
    for part in text.split(","):
        kind, _, weight = part.strip().partition("=")
        if kind not in KINDS:
            raise ValueError(f"kind lạ {kind!r}; hợp lệ: {', '.join(KINDS)}")
        if not weight.isdigit() or int(weight) <= 0:
            raise ValueError(f"trọng số của {kind} phải là số nguyên dương")
        mix.append((kind, int(weight)))
    return tuple(mix)


def allocate(total, mix):
    """Chia `total` bài theo trọng số, phần dư theo phần thập phân lớn nhất (tổng luôn đúng `total`)."""
    weight_sum = sum(w for _, w in mix)
    exact = [total * w / weight_sum for _, w in mix]
    counts = [int(x) for x in exact]
    order = sorted(range(len(mix)), key=lambda i: (exact[i] - counts[i]), reverse=True)
    for i in order[: total - sum(counts)]:
        counts[i] += 1
    return {kind: c for (kind, _), c in zip(mix, counts)}


def make_plan(users, per_user, mix=DEFAULT_MIX, token_factory=lambda: str(uuid.uuid4())):
    """Kế hoạch bài nộp: `users * per_user` bài, kind theo `mix`, rải vòng tròn để mỗi người có đủ loại; mỗi bài một
    `client_token` riêng. Mỗi người đúng `per_user` bài."""
    kinds = [k for k, c in allocate(users * per_user, mix).items() for _ in range(c)]
    plan = []
    for i, kind in enumerate(kinds):
        language, code, expected = KINDS[kind]
        plan.append(Planned(i, i % users, kind, language, code, expected, token_factory()))
    return plan


def check_stream(events):
    """Kiểm luồng STATUS của MỘT bài (list dict theo thứ tự đến). Trả {'problems': [...], 'judged': n,
    'redeliveries': n}. Luật: sau `judged` không còn STATUS nào; `tests_done` không giảm trong một lần giao và không
    vượt `tests_total`; `queued` sau `judging` là giao lại do worker mất (README 4.2) nên hợp lệ, chỉ đếm."""
    problems, judged, redeliveries = [], 0, 0
    seen_judging, last_done = False, -1
    for e in events:
        state = e.get("state")
        if state not in STATE_RANK:
            problems.append(f"state lạ {state!r}")
            continue
        if judged:
            problems.append(f"STATUS {state} đến sau judged")
        if state == "queued":
            if seen_judging:
                redeliveries += 1
            last_done = -1
        elif state == "judging":
            seen_judging = True
            progress = e.get("progress")
            if progress is not None:
                done, total = progress.get("tests_done"), progress.get("tests_total")
                if not isinstance(done, int) or not isinstance(total, int) or isinstance(done, bool):
                    problems.append("progress sai kiểu")
                elif done < last_done:
                    problems.append(f"tests_done giảm từ {last_done} xuống {done}")
                elif done > total:
                    problems.append(f"tests_done {done} > tests_total {total}")
                else:
                    last_done = done
        else:
            judged += 1
    return {"problems": problems, "judged": judged, "redeliveries": redeliveries}


def check_isolation(received_ids, own_ids):
    """`received_ids`: {user: tập submission_id nhận qua WS}; `own_ids`: {user: tập id đã nộp}. Trả danh sách lỗi."""
    problems = []
    for user, got in received_ids.items():
        foreign = got - own_ids.get(user, set())
        if foreign:
            problems.append(f"người dùng {user} nhận STATUS của bài không phải của mình: {sorted(foreign)}")
    return problems


def check_history(ids, baseline, own):
    """Lịch sử REST của một người sau lần chạy: `ids` (theo thứ tự trả về) phải là đúng `baseline` (bài có từ lần chạy
    trước trên cùng tài khoản) cộng `own` (bài vừa nộp), không trùng id, không thiếu, không thừa."""
    problems = []
    if len(ids) != len(set(ids)):
        problems.append("id trùng trong lịch sử")
    want = set(baseline) | set(own)
    if set(ids) != want:
        problems.append(f"thiếu {sorted(want - set(ids))}, thừa {sorted(set(ids) - want)}")
    return problems


def find_forbidden_keys(obj, path=""):
    """Đường dẫn của mọi key cấm (FORBIDDEN_KEYS) ở bất kỳ độ sâu nào trong obj."""
    found = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            here = f"{path}.{key}" if path else str(key)
            if key in FORBIDDEN_KEYS:
                found.append(here)
            found.extend(find_forbidden_keys(value, here))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            found.extend(find_forbidden_keys(value, f"{path}[{i}]"))
    return found


def percentile(values, p):
    """Phân vị p (0-100) nội suy tuyến tính; None nếu rỗng."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * p / 100
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def describe(values):
    return {"n": len(values), "p50": percentile(values, 50), "p90": percentile(values, 90),
            "p99": percentile(values, 99), "max": max(values) if values else None}


def parse_cookie(headers):
    """Giá trị cookie `jh_session` từ header Set-Cookie (dict chữ thường), hoặc None."""
    m = re.search(r"(?:^|[;,]\s*)jh_session=([^;,\s]+)", headers.get("set-cookie", ""))
    return m.group(1) if m else None


_DELIVERY = re.compile(r"submission (\d+) attempt (\d+) giao cho worker")


def check_delivery_log(text, ids, redeliveries):
    """Đối chiếu log server (`docker compose logs server`, dòng 'submission S attempt A giao cho worker ...') với các
    bài đã nộp: mỗi bài được giao đúng 1 + số lần giao lại quan sát được (`redeliveries`: {id: n}), attempt tăng đều."""
    attempts = {}
    for m in _DELIVERY.finditer(text):
        attempts.setdefault(int(m.group(1)), []).append(int(m.group(2)))
    problems = []
    for sid in ids:
        got = attempts.get(sid, [])
        want = 1 + redeliveries.get(sid, 0)
        if len(got) != want or got != list(range(1, want + 1)):
            problems.append(f"bài {sid}: log giao {got}, mong đợi attempt 1..{want}")
    return problems


def split_retry_after(headers):
    value = headers.get("retry-after", "")
    return int(value) if value.isdigit() else None


# =================================================================================================== phần I/O

@dataclass
class Config:
    base: str
    users: int = 10
    per_user: int = 5
    prefix: str = "loadtest"
    problem: str = "aplusb"
    mix: tuple = DEFAULT_MIX
    deadline_s: float = 120.0
    resubmit: int = 5
    with_s2: bool = False
    queue_full: bool = False
    queue_full_users: int = 45
    workers_expected: int = 3
    out: str | None = None
    delivery_log: str | None = None
    password: str = field(default="", repr=False)  # không bao giờ vào repr/log

    @property
    def split(self):
        return urlsplit(self.base)

    @property
    def host(self):
        return self.split.hostname

    @property
    def port(self):
        return self.split.port or (443 if self.split.scheme == "https" else 80)

    @property
    def netloc(self):
        """host[:cổng] đúng như thư viện HTTP/WS gửi trong header Host (cổng mặc định được bỏ)."""
        default = 443 if self.split.scheme == "https" else 80
        return self.host if self.port == default else f"{self.host}:{self.port}"

    @property
    def origin(self):
        """Origin của WS: cùng scheme/host/cổng với Host nên qua được kiểm Origin của server (README 4.1)."""
        return f"{self.split.scheme}://{self.netloc}"

    @property
    def ws_url(self):
        return f"{'wss' if self.split.scheme == 'https' else 'ws'}://{self.netloc}/ws"

    def username(self, index):
        return f"{self.prefix}-{index + 1:02d}"


class Http:
    """HTTP/1.1 bằng thư viện chuẩn, một kết nối mỗi request (an toàn khi gọi từ nhiều thread)."""

    def __init__(self, cfg):
        self.cfg = cfg

    def call(self, method, path, body=None, cookie=None, timeout=30):
        conn = http.client.HTTPConnection(self.cfg.host, self.cfg.port, timeout=timeout)
        headers = {"Connection": "close"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = f"jh_session={cookie}"
        try:
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            try:
                parsed = json.loads(raw) if raw else None
            except ValueError:
                parsed = None
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, parsed
        finally:
            conn.close()


class Report:
    def __init__(self):
        self.lines, self.failures = [], []

    def check(self, name, ok, detail=""):
        self.lines.append(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
        print(self.lines[-1], flush=True)
        if not ok:
            self.failures.append(name)

    def info(self, text):
        print("INFO " + text, flush=True)


@dataclass
class Rec:
    plan: Planned
    t_post: float = 0.0
    t_ack: float = 0.0
    status: int = 0
    sid: int | None = None
    error: str | None = None
    events: list = field(default_factory=list)  # [(t_nhận, message STATUS)]
    final: dict | None = None  # đối tượng bài nộp cuối (từ judged hoặc REST)
    final_via: str | None = None
    retry_after: int | None = None


def login_all(cfg, http_, indexes, report):
    """Đăng nhập mỗi tài khoản đúng một lần (giới hạn 10 lần/60 s theo username), tối đa LOGIN_CONCURRENCY song song."""
    def one(i):
        status, headers, body = http_.call("POST", "/api/login", {"username": cfg.username(i), "password": cfg.password})
        return i, status, parse_cookie(headers), body

    cookies = {}
    with ThreadPoolExecutor(max_workers=LOGIN_CONCURRENCY) as pool:
        for i, status, cookie, body in pool.map(one, indexes):
            if status == 200 and cookie:
                cookies[i] = cookie
            else:
                code = (body or {}).get("error", {}).get("code") if isinstance(body, dict) else None
                report.check(f"đăng nhập {cfg.username(i)}", False, f"HTTP {status} {code or ''}".strip())
    return cookies


async def _listen(cfg, cookie, sink, ready, closed):
    from websockets.asyncio.client import connect  # chỉ import khi chạy thật

    try:
        async with connect(cfg.ws_url, origin=cfg.origin, additional_headers={"Cookie": f"jh_session={cookie}"},
                           ping_interval=None, max_size=2 ** 20) as ws:
            async for raw in ws:
                t = time.monotonic()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                kind = msg.get("type") if isinstance(msg, dict) else None
                if kind == "PING":
                    await ws.send(json.dumps({"type": "PONG", "seq": msg.get("seq")}))
                elif kind == "STATUS":
                    sink.append((t, msg))
                elif kind == "SCOREBOARD":
                    ready.set()
            closed.append(ws.close_code)
    except Exception as e:  # noqa: BLE001 - ghi lại rồi để kiểm tra báo cáo
        closed.append(f"{type(e).__name__}: {e}")
    finally:
        ready.set()


def submit_one(http_, cfg, cookie, planned, rec, barrier=None):
    body = {"problem_id": cfg.problem, "language": planned.language, "code": planned.code,
            "client_token": planned.token}
    if barrier is not None:
        barrier.wait(timeout=60)
    rec.t_post = time.monotonic()
    try:
        status, headers, parsed = http_.call("POST", "/api/submissions", body, cookie)
    except OSError as e:
        rec.status, rec.error = 0, f"{type(e).__name__}: {e}"
        rec.t_ack = time.monotonic()
        return
    rec.t_ack = time.monotonic()
    rec.status = status
    if status in (200, 201) and isinstance(parsed, dict):
        rec.sid = parsed.get("submission_id")
    else:
        code = parsed.get("error", {}).get("code") if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict) else None
        rec.error = f"HTTP {status} {code or ''}".strip()
        rec.retry_after = split_retry_after(headers)


def fetch_history(http_, cookie):
    """Toàn bộ lịch sử `GET /api/submissions` của một người (phân trang theo next_before_id)."""
    items, before, pages = [], None, 0
    while pages < 100:
        path = "/api/submissions?limit=20" + (f"&before_id={before}" if before else "")
        status, _, body = http_.call("GET", path, cookie=cookie)
        if status != 200 or not isinstance(body, dict):
            raise RuntimeError(f"GET {path} -> {status}")
        items.extend(body.get("submissions", []))
        before = body.get("next_before_id")
        pages += 1
        if not before:
            return items
    raise RuntimeError("lịch sử phân trang quá 100 trang")


async def scenario_main(cfg, http_, report, cookies, result):
    plan = make_plan(cfg.users, cfg.per_user, cfg.mix)
    loop = asyncio.get_running_loop()
    recs = [Rec(p) for p in plan]
    sinks = {u: [] for u in cookies}
    readies = {u: asyncio.Event() for u in cookies}
    closed = {u: [] for u in cookies}
    tasks = [asyncio.create_task(_listen(cfg, cookies[u], sinks[u], readies[u], closed[u])) for u in cookies]
    try:
        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in readies.values())), timeout=30)
    except asyncio.TimeoutError:
        pass
    not_ready = [cfg.username(u) for u, c in closed.items() if c]
    report.check("mọi WebSocket mở được và nhận SCOREBOARD đầu", not not_ready, f"đóng sớm: {not_ready} {[closed[u] for u in cookies if closed[u]]}")

    # Lịch sử có sẵn của các tài khoản (từ lần chạy trước) để C5 chỉ xét phần thay đổi do lần chạy này.
    baseline = {}
    for u in cookies:
        try:
            baseline[u] = {it.get("submission_id") for it in fetch_history(http_, cookies[u])}
        except RuntimeError as e:
            report.check(f"đọc lịch sử trước khi nộp của {cfg.username(u)}", False, str(e))
            baseline[u] = set()
    report.info("READY-TO-SUBMIT sẵn sàng nộp bài (barrier)")  # mốc để tác nhân bên ngoài (thử phá có chủ đích) canh thời điểm
    barrier = threading.Barrier(len(recs))
    with ThreadPoolExecutor(max_workers=len(recs)) as pool:
        t_release = time.monotonic()
        futures = [loop.run_in_executor(pool, submit_one, http_, cfg, cookies[r.plan.user], r.plan, r, barrier)
                   for r in recs if r.plan.user in cookies]
        await asyncio.gather(*futures)
    accepted = [r for r in recs if r.status in (200, 201) and r.sid]
    rejected = [r for r in recs if r not in accepted]
    report.check(f"cả {len(recs)} bài nộp được chấp nhận (201)", not rejected,
                 f"{len(rejected)} bài bị từ chối: {sorted({r.error or r.status for r in rejected})}")

    by_sid = {r.sid: r for r in accepted}
    deadline = t_release + cfg.deadline_s
    judged_sids = set()
    while time.monotonic() < deadline:
        for u, sink in sinks.items():
            for _, msg in sink:
                if msg.get("state") == "judged":
                    judged_sids.add(msg.get("submission_id"))
        if all(r.sid in judged_sids for r in accepted):
            break
        await asyncio.sleep(0.5)
    for u, sink in sinks.items():
        for t, msg in sink:
            rec = by_sid.get(msg.get("submission_id"))
            if rec is not None:
                rec.events.append((t, msg))
    for r in accepted:
        for _, msg in r.events:
            if msg.get("state") == "judged" and isinstance(msg.get("submission"), dict):
                r.final, r.final_via = msg["submission"], "ws"

    # --- C1: không mất. Quá hạn thì KHÔNG nâng hạn ngầm: báo như phát hiện, kèm ngôn ngữ.
    late = [r for r in accepted if r.final is None]
    for r in late:
        status, _, body = http_.call("GET", f"/api/submissions/{r.sid}", cookie=cookies[r.plan.user])
        if status == 200 and isinstance(body, dict) and body.get("state") == "judged":
            r.final, r.final_via = body, "rest-after-deadline"
    not_done = [r for r in late if r.final is None]
    by_lang = {}
    for r in late:
        by_lang.setdefault(r.plan.language, []).append(r.sid)
    report.check(f"C1 mọi bài đã nhận ra judged trong {cfg.deadline_s:g} s (theo STATUS qua WS)", not late,
                 f"{len(late)} bài quá hạn hoặc mất push, theo ngôn ngữ {by_lang}; "
                 f"{len(not_done)} bài vẫn chưa judged khi hỏi lại bằng REST")

    # --- C2: luồng STATUS hợp lệ, đúng một judged
    stream_problems, dup, redeliv = [], [], {}
    for r in accepted:
        res = check_stream([m for _, m in r.events])
        redeliv[r.sid] = res["redeliveries"]
        if res["judged"] > 1:
            dup.append((r.sid, res["judged"]))
        stream_problems += [f"bài {r.sid}: {p}" for p in res["problems"]]
    report.check("C2 không bài nào có hơn một STATUS judged (không chấm trùng)", not dup, str(dup))
    report.check("C2 luồng STATUS hợp lệ (thứ tự, tests_done)", not stream_problems, "; ".join(stream_problems[:5]))
    if any(redeliv.values()):
        report.info(f"giao lại quan sát được: {{{', '.join(f'{k}: {v}' for k, v in redeliv.items() if v)}}}")

    # --- C3: verdict đúng và WS khớp REST
    wrong, mismatch = [], []
    for r in accepted:
        if r.final is None:
            continue
        got = r.final.get("verdict")
        if got != r.plan.expected:
            wrong.append((r.sid, r.plan.kind, r.plan.expected, got))
        status, _, body = http_.call("GET", f"/api/submissions/{r.sid}", cookie=cookies[r.plan.user])
        if status != 200 or not isinstance(body, dict) or body.get("verdict") != got:
            mismatch.append((r.sid, got, body.get("verdict") if isinstance(body, dict) else status))
    report.check("C3 verdict đúng như mong đợi cho từng bài", not wrong, f"(id, kind, mong đợi, nhận): {wrong[:5]}")
    report.check("C3 verdict trong STATUS khớp GET /api/submissions/{id}", not mismatch, str(mismatch[:5]))

    # --- C4: cô lập
    own = {u: {r.sid for r in accepted if r.plan.user == u} for u in cookies}
    got_ids = {u: {m.get("submission_id") for _, m in sinks[u]} for u in cookies}
    report.check("C4 mỗi người chỉ nhận STATUS của bài mình", not check_isolation(got_ids, own),
                 "; ".join(check_isolation(got_ids, own)[:3]))

    # --- C6: nộp lại cùng client_token
    dup_resubmit = []
    for r in accepted[: cfg.resubmit]:
        status, _, body = http_.call("POST", "/api/submissions", {
            "problem_id": cfg.problem, "language": r.plan.language, "code": r.plan.code,
            "client_token": r.plan.token}, cookies[r.plan.user])
        if status != 200 or not isinstance(body, dict) or body.get("submission_id") != r.sid:
            dup_resubmit.append((r.sid, status, body.get("submission_id") if isinstance(body, dict) else None))
    report.check(f"C6 nộp lại cùng client_token ({cfg.resubmit} bài) trả 200 và cùng submission_id", not dup_resubmit,
                 str(dup_resubmit[:3]))

    # --- C5: lịch sử đủ, không trùng, không bài lạ; C7: không lộ field cấm
    hist_problems, leaks = [], []
    for u in cookies:
        try:
            items = fetch_history(http_, cookies[u])
        except RuntimeError as e:
            hist_problems.append(f"{cfg.username(u)}: {e}")
            continue
        ids = [it.get("submission_id") for it in items]
        hist_problems += [f"{cfg.username(u)}: {p}" for p in check_history(ids, baseline[u], own[u])]
        leaks += find_forbidden_keys(items, f"history[{cfg.username(u)}]")
    for u in cookies:
        for _, m in sinks[u]:
            leaks += find_forbidden_keys(m, f"STATUS[{cfg.username(u)}]")
    report.check("C5 lịch sử REST của mỗi người đúng các bài đã nộp, mỗi bài một lần", not hist_problems,
                 "; ".join(hist_problems[:3]))
    report.check("C7 STATUS và danh sách bài nộp không mang code/ce_message/sample_*", not leaks, str(leaks[:5]))

    if cfg.delivery_log:
        with open(cfg.delivery_log, encoding="utf-8", errors="replace") as f:
            problems = check_delivery_log(f.read(), [r.sid for r in accepted], redeliv)
        report.check("log server: mỗi bài được giao đúng số lần quan sát được", not problems, "; ".join(problems[:3]))

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    result["metrics"] = metrics(recs, t_release)
    result["recs"] = [rec_to_json(r) for r in recs]
    return recs


def metrics(recs, t_release):
    rows = {"ack": [], "wait": [], "judge": [], "total": []}
    per_lang = {}
    last = None
    for r in recs:
        if r.sid is None:
            continue
        rows["ack"].append(r.t_ack - r.t_post)
        times = {m.get("state"): t for t, m in r.events if m.get("state") in ("judging", "judged")}
        first_judging = next((t for t, m in r.events if m.get("state") == "judging"), None)
        judged = times.get("judged")
        if first_judging is not None:
            rows["wait"].append(first_judging - r.t_ack)
        if judged is not None:
            rows["total"].append(judged - r.t_post)
            per_lang.setdefault(r.plan.language, []).append(judged - r.t_post)
            if first_judging is not None:
                rows["judge"].append(judged - first_judging)
            last = judged if last is None else max(last, judged)
    span = (last - t_release) if last is not None else None
    done = len(rows["total"])
    return {"seconds": {k: describe(v) for k, v in rows.items()},
            "total_by_language": {k: describe(v) for k, v in per_lang.items()},
            "judged": done, "span_s": span, "throughput_per_s": (done / span) if span else None}


def rec_to_json(r):
    """Số liệu thô một bài: KHÔNG có code, cookie hay token."""
    return {"index": r.plan.index, "user": r.plan.user, "kind": r.plan.kind, "language": r.plan.language,
            "expected": r.plan.expected, "status": r.status, "submission_id": r.sid, "error": r.error,
            "ack_s": r.t_ack - r.t_post, "verdict": (r.final or {}).get("verdict"), "final_via": r.final_via,
            "events": [{"t": t - r.t_post, "state": m.get("state")} for t, m in r.events]}


def print_metrics(report, m):
    def fmt(d):
        return "-" if not d or d["n"] == 0 else (f"n={d['n']} p50={d['p50']:.2f}s p90={d['p90']:.2f}s "
                                                 f"p99={d['p99']:.2f}s max={d['max']:.2f}s")
    report.info("thời gian (giây) từ POST tới:")
    for name, label in (("ack", "201 (ack)"), ("wait", "STATUS judging đầu, tính từ ack (chờ hàng đợi)"),
                        ("judge", "judged, tính từ judging đầu (chấm)"), ("total", "judged, tính từ POST (tổng)")):
        report.info(f"  {label}: {fmt(m['seconds'][name])}")
    for lang, d in sorted(m["total_by_language"].items()):
        report.info(f"  tổng theo {lang}: {fmt(d)}")
    if m["throughput_per_s"]:
        report.info(f"thông lượng: {m['judged']} bài trong {m['span_s']:.1f} s = {m['throughput_per_s']:.2f} bài/s")


def scenario_pending_cap(cfg, http_, report, cookie):
    """S2: một người nộp 8 bài chậm cùng lúc: đúng PENDING_CAP bài được nhận, còn lại 429 TOO_MANY_PENDING."""
    n = PENDING_CAP + 3
    recs = [Rec(Planned(i, 0, "py-slow", "python", _SLOW_PY, "AC", str(uuid.uuid4()))) for i in range(n)]
    barrier = threading.Barrier(n)
    with ThreadPoolExecutor(max_workers=n) as pool:
        list(pool.map(lambda r: submit_one(http_, cfg, cookie, r.plan, r, barrier), recs))
    ok = [r for r in recs if r.status in (200, 201)]
    rejected = [r for r in recs if r.status == 429]
    report.check(f"S2 đúng {PENDING_CAP} bài được nhận khi nộp {n} bài chậm cùng lúc", len(ok) == PENDING_CAP,
                 f"nhận {len(ok)}, 429: {len(rejected)}, khác: {[r.error for r in recs if r not in ok and r not in rejected]}")
    report.check("S2 các bài còn lại bị 429 TOO_MANY_PENDING kèm Retry-After 5",
                 len(rejected) == n - PENDING_CAP and all((r.error or "").endswith("TOO_MANY_PENDING") and
                                                           r.retry_after == 5 for r in rejected),
                 str([(r.error, r.retry_after) for r in rejected[:3]]))
    try:
        ids = [it.get("submission_id") for it in fetch_history(http_, cookie)]
    except RuntimeError as e:
        ids = []
        report.check("S2 đọc được lịch sử", False, str(e))
    report.check("S2 bài bị từ chối không có trong lịch sử", set(ids) == {r.sid for r in ok}, f"lịch sử {len(ids)} bài")
    _wait_all_judged(http_, cookie, [r.sid for r in ok], timeout=cfg.deadline_s)


def _wait_all_judged(http_, cookie, sids, timeout):
    end = time.monotonic() + timeout
    pending = set(sids)
    while pending and time.monotonic() < end:
        for sid in list(pending):
            status, _, body = http_.call("GET", f"/api/submissions/{sid}", cookie=cookie)
            if status == 200 and isinstance(body, dict) and body.get("state") == "judged":
                pending.discard(sid)
        if pending:
            time.sleep(1)
    return not pending


def scenario_queue_full(cfg, http_, report, cookies):
    """S3 (cờ --queue-full): nhiều người, mỗi người PENDING_CAP bài chậm; hàng đợi toàn hệ thống đầy thì 503 QUEUE_FULL."""
    n = len(cookies) * PENDING_CAP
    recs = [Rec(Planned(i, i % len(cookies), "py-slow", "python", _SLOW_PY, "AC", str(uuid.uuid4()))) for i in range(n)]
    users = sorted(cookies)
    barrier = threading.Barrier(n)
    with ThreadPoolExecutor(max_workers=n) as pool:
        list(pool.map(lambda r: submit_one(http_, cfg, cookies[users[r.plan.user]], r.plan, r, barrier), recs))
    ok = [r for r in recs if r.status in (200, 201)]
    full = [r for r in recs if (r.error or "").endswith("QUEUE_FULL")]
    other = [r.error for r in recs if r not in ok and r not in full]
    report.check(f"S3 có bài bị 503 QUEUE_FULL khi nộp {n} bài chậm (cần > {GLOBAL_QUEUE_CAP} bài chờ)", bool(full),
                 f"nhận {len(ok)}, QUEUE_FULL {len(full)}, khác {other[:3]}")
    report.check(f"S3 số bài được nhận ≤ {GLOBAL_QUEUE_CAP} + số worker ({cfg.workers_expected})",
                 len(ok) <= GLOBAL_QUEUE_CAP + cfg.workers_expected, f"nhận {len(ok)}")
    report.check("S3 QUEUE_FULL kèm Retry-After 10", all(r.retry_after == 10 for r in full) and bool(full),
                 str([r.retry_after for r in full[:3]]))
    for u in users:  # chờ chấm xong để stack sạch cho lần sau
        _wait_all_judged(http_, cookies[u], [r.sid for r in ok if users[r.plan.user] == u], timeout=600)


def build_parser():
    p = argparse.ArgumentParser(description="Load test JudgeHive (D25). Mật khẩu lấy từ biến môi trường "
                                f"{PASSWORD_ENV} (không nhận qua dòng lệnh).")
    p.add_argument("--base", required=True, help="URL gốc, ví dụ http://host.docker.internal:8000 hoặc http://server:8000; "
                   "Origin của WebSocket được suy đúng từ đây")
    p.add_argument("--users", type=int, default=10)
    p.add_argument("--per-user", type=int, default=PENDING_CAP, help="bài mỗi người, tối đa trần bài đang chờ")
    p.add_argument("--prefix", default="loadtest", help="tài khoản là <prefix>-01, <prefix>-02, ...")
    p.add_argument("--problem", default="aplusb")
    p.add_argument("--mix", default=",".join(f"{k}={w}" for k, w in DEFAULT_MIX))
    p.add_argument("--deadline", type=float, default=120.0, help="giây chờ mọi bài ra judged; KHÔNG tự nâng")
    p.add_argument("--resubmit", type=int, default=5, help="số bài nộp lại cùng client_token (C6)")
    p.add_argument("--with-s2", action="store_true", help="S2: trần bài đang chờ (cần thêm tài khoản thứ users+1)")
    p.add_argument("--queue-full", action="store_true", help="S3: làm đầy hàng đợi (cần nhiều tài khoản)")
    p.add_argument("--queue-full-users", type=int, default=45)
    p.add_argument("--workers-expected", type=int, default=3)
    p.add_argument("--out", help="ghi số liệu thô JSON (không có code, cookie, token)")
    p.add_argument("--delivery-log", help="file log server (docker compose logs server) để đối chiếu số lần giao")
    return p


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # báo cáo tiếng Việt: không phụ thuộc locale của máy/container
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    password = os.environ.get(PASSWORD_ENV, "")
    if not password:
        print(f"thiếu biến môi trường {PASSWORD_ENV}", file=sys.stderr)
        return 2
    try:
        mix = parse_mix(args.mix)
    except ValueError as e:
        print(f"--mix: {e}", file=sys.stderr)
        return 2
    cfg = Config(base=args.base, users=args.users, per_user=args.per_user, prefix=args.prefix, problem=args.problem,
                 mix=mix, deadline_s=args.deadline, resubmit=args.resubmit, with_s2=args.with_s2,
                 queue_full=args.queue_full, queue_full_users=args.queue_full_users,
                 workers_expected=args.workers_expected, out=args.out, delivery_log=args.delivery_log, password=password)
    if cfg.per_user > PENDING_CAP:
        print(f"--per-user {cfg.per_user} vượt trần {PENDING_CAP} bài đang chờ của mỗi người", file=sys.stderr)
        return 2
    http_, report = Http(cfg), Report()
    report.info(f"cấu hình: {cfg}  Origin WS: {cfg.origin}  ws: {cfg.ws_url}")
    status, _, body = http_.call("GET", "/api/health")
    report.check("server trả health 200", status == 200, f"HTTP {status}")
    if status != 200:
        return 1

    result = {"config": {k: v for k, v in vars(cfg).items() if k != "password"}}
    main_users = list(range(cfg.users))
    cookies = login_all(cfg, http_, main_users, report)
    if len(cookies) == len(main_users):
        asyncio.run(scenario_main(cfg, http_, report, cookies, result))
        if result.get("metrics"):
            print_metrics(report, result["metrics"])
    if cfg.with_s2:
        c = login_all(cfg, http_, [cfg.users], report)
        if c:
            scenario_pending_cap(cfg, http_, report, c[cfg.users])
    if cfg.queue_full:
        c = login_all(cfg, http_, list(range(cfg.users + 1, cfg.users + 1 + cfg.queue_full_users)), report)
        if len(c) == cfg.queue_full_users:
            scenario_queue_full(cfg, http_, report, c)
    if cfg.out:
        with open(cfg.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1, default=str)
    print(f"\n{len(report.lines) - len(report.failures)} PASS, {len(report.failures)} FAIL", flush=True)
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
