"""Tạo/sửa đề và xem siêu dữ liệu bộ test của admin dưới `/api/admin/problems` (README 3.6, D35, D23(6)(7)(9)).

Cùng quy ước với `admin_routes.py`: `require_admin` đứng trước mọi dependency khác (403 trước mọi lỗi của body). Hai
endpoint upload (`POST`, `PUT`) có thêm cổng đồng thời `upload_gate` (một lượt tại một lúc, 429 `RATE_LIMITED`): nó nằm
TRƯỚC khi đọc body, để server 512 MB không phải giữ hai body 17 MiB cùng lúc. Việc ghi đề và bộ test là một `db.write()`.

D11: không endpoint nào ở đây trả `input`/`output` của test ẩn; log chỉ ghi admin, hành động, mã đề và số test."""

import threading

from fastapi import APIRouter, Depends, Request

from server import problems
from server.rest import sessions
from server.rest.admin_routes import _strict_query
from server.rest.admin_user_routes import _require_active_admin
from server.rest.body import BODY_LIMIT_PROBLEM, json_object_dependency, utf8_size
from server.rest.errors import ApiJSONResponse, invalid, log, problem_id_taken, problem_not_found, rate_limited
from server.rest.problem_routes import PROBLEM_COLUMNS, PROBLEM_ID, _text, problem_detail

router = APIRouter()

CONTENT_FIELDS = ("title", "statement", "time_limit_ms", "memory_limit_kib")
TEST_KEYS = {"input", "output", "is_sample"}
ROOT_STRING_FIELDS = ("id", "title", "statement")  # các field gốc có giá trị chuỗi (còn lại là số hoặc `tests`)
TEST_STRING_FIELDS = ("input", "output")


def max_body_tokens(tests=problems.WEB_MAX_TESTS):
    """Số token cấu trúc JSON tối đa của một body hợp lệ (README.md:957, bảng `POST /api/admin/problems`): mỗi chuỗi,
    và mỗi ký tự `{ } [ ] , :` ngoài nội dung chuỗi, là một token. Body hợp lệ lớn nhất gồm object gốc (`id`, bốn field của
    `CONTENT_FIELDS`, `tests`) và mảng `tests` có `WEB_MAX_TESTS` phần tử, mỗi phần tử là object ba key. Vượt con số này
    thì chắc chắn sai contract nên bị từ chối trước `json.loads` (chặn khuếch đại bộ nhớ: `{},` dài 3 byte thành dict
    ~64 byte). Độ sâu không cần chặn riêng: số ngoặc đã bị chặn, nên độ sâu cũng nhỏ và validator từ chối cấu trúc sai."""
    root_fields = len(CONTENT_FIELDS) + 2  # + `id`, `tests`
    root = 2 + (root_fields - 1) + root_fields + root_fields + len(ROOT_STRING_FIELDS)  # {} , : khóa, giá trị chuỗi
    array = 2 + (tests - 1)  # [] và dấu phẩy giữa các phần tử
    keys = len(TEST_KEYS)
    per_test = 2 + (keys - 1) + keys + keys + len(TEST_STRING_FIELDS)
    return root + array + tests * per_test


body_problem = json_object_dependency(BODY_LIMIT_PROBLEM, "UPLOAD_READ_TIMEOUT_S", max_tokens=max_body_tokens())


class UploadSlot:
    """Một lượt giữ cổng upload; `release` idempotent."""

    def __init__(self, gate):
        self._gate = gate
        self._held = True
        self._lock = threading.Lock()

    def release(self):
        with self._lock:
            held, self._held = self._held, False
        if held:
            self._gate.leave()


async def upload_slot(request: Request):
    """Cổng đồng thời của hai endpoint upload: giữ từ trước khi đọc body tới khi handler xong (kể cả khi lỗi).

    Handler tự `release` trong `finally` của nó, TRƯỚC khi response được gửi: phần thoát của dependency chạy sau khi
    response đã rời server, nên chỉ dựa vào nó thì client gửi lượt kế tiếp ngay sẽ nhận 429 oan. Phần `finally` ở đây là
    lưới an toàn cho các đường lỗi trước khi handler chạy (body hỏng, quá hạn đọc, 413)."""
    gate = request.app.state.rest.upload_gate
    if not gate.try_enter():
        raise rate_limited(1)
    slot = UploadSlot(gate)
    try:
        yield slot
    finally:
        slot.release()


def _int_in(body, field, bounds):
    value = body.get(field)
    if type(value) is not int or not bounds[0] <= value <= bounds[1]:
        raise invalid(field)
    return value


def _parse_tests(raw):
    """Kiểm `tests`; trả bộ test đã sắp mẫu trước (giữ thứ tự tương đối) hoặc ném 400."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= problems.WEB_MAX_TESTS:
        raise invalid("tests")
    parsed = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != TEST_KEYS:
            raise invalid("tests")
        text_in, text_out, sample = item["input"], item["output"], item["is_sample"]
        if not isinstance(text_in, str) or not isinstance(text_out, str) or type(sample) is not bool:
            raise invalid("tests")
        utf8_size(text_in, "tests")  # surrogate lẻ không mã hóa được: 400, không phải 500
        utf8_size(text_out, "tests")
        parsed.append(problems.TestCase(sample, text_in.encode("utf-8"), text_out.encode("utf-8")))
    ordered = [t for t in parsed if t.is_sample] + [t for t in parsed if not t.is_sample]
    try:
        problems.check_limits(ordered)  # D3: tổng 8 MiB thô và dòng JOB xấu nhất ≤ 16 MiB
    except problems.ProblemError:
        raise invalid("tests") from None
    return tuple(ordered)


def _parse_content(body):
    title = body.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > problems.WEB_MAX_TITLE_CHARS:
        raise invalid("title")
    utf8_size(title, "title")
    statement = body.get("statement")
    if not isinstance(statement, str) or not 1 <= utf8_size(statement, "statement") <= problems.WEB_MAX_STATEMENT_BYTES:
        raise invalid("statement")
    return {"title": title, "statement": statement,
            "time_limit_ms": _int_in(body, "time_limit_ms", problems.WEB_TIME_LIMIT_MS),
            "memory_limit_kib": _int_in(body, "memory_limit_kib", problems.WEB_MEMORY_LIMIT_KIB)}


def _detail(conn, problem_id):
    row = conn.execute(f"SELECT {PROBLEM_COLUMNS}, statement FROM problems WHERE id = ?", (problem_id,)).fetchone()
    return problem_detail(conn, row, True)


@router.post("/api/admin/problems")
def create_problem(request: Request, admin: sessions.SessionUser = Depends(sessions.require_admin),
                   slot: UploadSlot = Depends(upload_slot), body: dict = Depends(body_problem)):
    try:
        return _create(request, admin, body)
    finally:
        slot.release()


def _create(request, admin, body):
    state = request.app.state.rest
    if set(body) != {"id", *CONTENT_FIELDS, "tests"}:
        raise invalid()
    problem_id = body["id"]
    if not isinstance(problem_id, str) or not problems.SLUG.fullmatch(problem_id):  # chữ thường, chặt hơn mục 3.2
        raise invalid("id")
    content = _parse_content(body)
    tests = _parse_tests(body["tests"])
    data = problems.ProblemData(problem_id, content["title"], content["statement"], content["time_limit_ms"],
                                content["memory_limit_kib"], tests)
    with state.db.write() as c:
        _require_active_admin(c, admin.id)
        if not problems.store_problem(c, data, replace=False, hidden=1, source="web"):
            raise problem_id_taken()
        result = _detail(c, problem_id)
    log.info("ADMIN admin=%s create problem=%s tests=%d", admin.id, problem_id, len(tests))
    return ApiJSONResponse(result, status_code=201)


@router.put("/api/admin/problems/{problem_id}")
def replace_problem(problem_id: str, request: Request, admin: sessions.SessionUser = Depends(sessions.require_admin),
                    slot: UploadSlot = Depends(upload_slot), body: dict = Depends(body_problem)):
    try:
        return _replace(problem_id, request, admin, body)
    finally:
        slot.release()


def _replace(problem_id, request, admin, body):
    state = request.app.state.rest
    if not {*CONTENT_FIELDS} <= set(body) <= {*CONTENT_FIELDS, "tests"}:
        raise invalid()
    content = _parse_content(body)
    tests = _parse_tests(body["tests"]) if "tests" in body else None
    if not PROBLEM_ID.fullmatch(problem_id):
        raise problem_not_found()
    with state.db.write() as c:
        _require_active_admin(c, admin.id)
        if not problems.update_problem(c, problem_id, tests=tests, **content):
            raise problem_not_found()
        result = _detail(c, problem_id)
    log.info("ADMIN admin=%s replace problem=%s tests=%s", admin.id, problem_id, "kept" if tests is None else len(tests))
    return ApiJSONResponse(result)


@router.get("/api/admin/problems/{problem_id}/tests")
def problem_tests(problem_id: str, request: Request, admin: sessions.SessionUser = Depends(sessions.require_admin)):
    _strict_query(request, ())
    if not PROBLEM_ID.fullmatch(problem_id):
        raise problem_not_found()
    conn = request.app.state.rest.db.conn()
    row = conn.execute("SELECT tests_hash FROM problems WHERE id = ?", (problem_id,)).fetchone()
    if row is None:
        raise problem_not_found()
    rows = conn.execute("SELECT position, is_sample, input, output FROM tests WHERE problem_id = ? ORDER BY position",
                        (problem_id,)).fetchall()
    tests = []
    for r in rows:
        sample = bool(r["is_sample"])
        tests.append({"position": r["position"], "is_sample": sample, "input_bytes": len(r["input"]),
                      "output_bytes": len(r["output"]), "sha256": problems.test_fingerprint(r["input"], r["output"]),
                      # D11: nội dung chỉ với test mẫu; test ẩn luôn null, kể cả với admin
                      "input": _text(r["input"]) if sample else None, "output": _text(r["output"]) if sample else None})
    log.info("ADMIN admin=%s view tests problem=%s", admin.id, problem_id)
    return ApiJSONResponse({"problem_id": problem_id, "tests_hash": row["tests_hash"], "tests": tests})
