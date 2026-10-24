# MODULE SERVER

Server chính của JudgeHive: nhận worker qua cổng TCP thuần (framing JSON-lines do nhóm tự viết, xem README gốc mục
"Giao tiếp" 2), giao bài, lưu kết quả vào SQLite. Phần REST/WebSocket cho trình duyệt (nộp bài, đăng nhập, bảng xếp
hạng) thuộc các PR sau; hiện chỉ có `GET /api/health`.

---

## ⚙️ CÔNG NGHỆ SỬ DỤNG

| Thành phần | Công nghệ |
|------------|-----------|
| Ngôn ngữ | Python 3.11 |
| Web | FastAPI + uvicorn (đúng một process, không dùng `--workers > 1`: hàng đợi và danh sách worker nằm trong bộ nhớ) |
| Cổng worker | `socket` + `threading` (tự viết: framing, heartbeat, giao lại bài) |
| Database | SQLite (WAL, `busy_timeout`, mỗi thread một kết nối, `BEGIN IMMEDIATE` cho transaction đọc rồi ghi) |

---

## 📦 CẤU TRÚC

| File | Vai trò |
|------|---------|
| `main.py` | FastAPI và lifespan: lưu event loop, migrate DB, nạp seed `problems/`, nạp lại hàng đợi, rồi mới mở cổng worker và dispatcher |
| `worker_server.py` | Cổng worker: accept, `REGISTER`, `PING`/`PONG`, `PROGRESS`/`RESULT`, watchdog theo từng bước, đánh dấu worker chết |
| `dispatcher.py` | Hàng đợi ba mức (giao lại > bài mới > rejudge), transaction giao bài, commit verdict, giao lại khi worker chết |
| `db.py` | Kết nối SQLite, schema và migration (`PRAGMA user_version`) |
| `problems.py` | Đọc thư mục đề, hash bộ test, kiểm giới hạn 8 MB và 16 MB, seed và reimport |
| `auth.py` | Băm mật khẩu scrypt (chưa có endpoint web) |
| `hooks.py` | Điểm móc để PR sau đẩy `STATUS`/`SCOREBOARD` xuống WebSocket (hiện chỉ ghi log) |
| `config.py` | Cấu hình từ biến môi trường |
| `manage.py` | Script quản lý (xem dưới) |
| `problems/<id>/` | Đề seed: `problem.json`, `statement.txt`, `samples/NN.in\|NN.out`, `tests/NN.in\|NN.out` |

---

## 🚀 HƯỚNG DẪN CHẠY

Từ thư mục `source/`:

```bash
mkdir secrets
python -c "import secrets; print('JH_WORKER_SECRET=' + secrets.token_hex(32))" > secrets/worker.env
docker compose up --build                    # server + một worker
docker compose up --build --scale worker=3   # ba worker
```

Server nghe `8000` (HTTP) và `9000` (cổng worker). Biến môi trường:

| Biến | Mặc định | Ý nghĩa |
|------|----------|---------|
| `JH_WORKER_SECRET_FILE` | | File chứa shared secret của worker (ưu tiên) |
| `JH_WORKER_SECRET` | | Secret trực tiếp; compose đọc từ `secrets/worker.env` (`env_file`), không dùng `secrets:` của compose vì file mount đọc được bởi uid thí sinh |
| `JH_WORKER_PORT` | `9000` | Cổng worker |
| `JH_HTTP_PORT` | `8000` | Cổng HTTP (lệnh chạy uvicorn trong Dockerfile) |
| `JH_DB_PATH` | `/data/judgehive.db` | File DB (named volume `db`) |
| `JH_PROBLEMS_DIR` | `server/problems` | Thư mục đề seed |
| `JH_LOG_LEVEL` | `INFO` | Mức log |

### Quản lý (`manage.py`)

DB nằm trong named volume nên chạy trong container server:

```bash
docker compose exec server python -m server.manage users create alice            # nhắc nhập mật khẩu (8 đến 128 byte)
docker compose exec server python -m server.manage users create root --role admin
docker compose exec server python -m server.manage problems reimport aplusb      # D5: nạp lại một đề từ thư mục
```

`problems/` chỉ là dữ liệu khởi tạo: lúc khởi động server chỉ nạp đề chưa có trong DB, không ghi đè. Sửa file trong
`problems/` rồi restart không có tác dụng; dùng `problems reimport` (và build lại image nếu đổi file, vì `problems/`
nằm trong image). `reimport` đổi hash bộ test nhưng không tự chấm lại và không đổi cờ ẩn.

Công cụ dev tạm (đến khi có REST nộp bài ở PR 5):

```bash
docker compose exec -T server python -m server.manage submissions add --user alice --problem aplusb --language python --file - < sol.py
docker compose exec server python -m server.manage submissions show 1
```

`add` chỉ ghi dòng `queued` vào DB; hàng đợi nằm trong bộ nhớ của server nên bài chỉ được giao sau khi
`docker compose restart server` (lifespan nạp lại mọi bài chưa chấm xong).

---

## 🔒 DÙNG BỞI TEST ĐÃ KHÓA

Các test hợp đồng cổng worker (`tests/invariants/test_server_contract_*.py`, khóa ở tag `m3-tests-locked`) chỉ dùng
đúng bề mặt dưới đây. Sau khi khóa, quy tắc là **chỉ được mở rộng tương thích**: thêm tham số mới có giá trị mặc
định, thêm key mới vào dict trả về, thêm field mới có mặc định vào `ServerConfig`. Không đổi tên, không bỏ, không
đổi kiểu hay ý nghĩa của mục nào dưới đây; cần làm vậy thì dừng và hỏi chủ repo. Mọi thứ khác trong `server/` tự do
đổi; test của chúng ở lại `tests/server/`, không khóa. Hạ tầng test (worker giả, `Stack`, fixture) nằm trong
`tests/invariants/_server_contract_support.py`, không thuộc bề mặt này.

| Mục | Hợp đồng test khóa dựa vào |
| --- | --- |
| `config.ServerConfig(bind_host, worker_port, secret, db_path, max_unauthenticated)` | Tạo bằng keyword; `worker_port=0` là cổng tự chọn. Mọi field khác dùng mặc định, và test dựa vào `max_deliveries` mặc định là 3 (README gốc 2.6, D1) |
| `db.Database(path)` | `.migrate()`; `.write()` (context manager, trả connection trong transaction); `.create_user(username, password_hash)` trả id; `.create_submission(user_id, problem_id, language, code)` trả id, trạng thái `queued`; `.get_submission(id)` trả dict hoặc `None` |
| key của `get_submission` | `state` (các giá trị `queued`, `judging`, `done`), `attempt_id`, `delivered_test_hash`, `verdict`, `verdict_test_hash`, `time_ms`, `memory_kib`, `failed_test`, `failed_is_sample`, `re_kind`, `ce_message`, `sample_stdout` |
| `problems.TestCase(is_sample, input, output)`, `problems.ProblemData(id, title, statement, time_limit_ms, memory_limit_kib, tests)` | Dataclass bất biến, tham số theo vị trí |
| `problems.store_problem(conn, data, *, replace)`, `problems.compute_tests_hash(tests)` | Gọi trong `db.write()`; hash ổn định theo nội dung và thứ tự test |
| `worker_server.WorkerServer(cfg, db, hooks)` | `.set_handler(dispatcher)`; `.start()`; `.stop()`; `.port` (cổng thật sau `start`); `.snapshot()` (dãy, độ dài = số worker đã `REGISTER` và còn kết nối) |
| `dispatcher.Dispatcher(cfg, db, server, hooks)` | `.start()`; `.stop()`; `.enqueue(submission_id, level)`; `.load_pending()` (gọi trước `start` để nạp lại bài chưa chấm xong) |
| `dispatcher.LEVEL_NEW`, `LEVEL_REJUDGE` | Mức truyền vào `enqueue`; bài `LEVEL_NEW` được giao trước bài `LEVEL_REJUDGE` (bài giao lại sau khi worker chết đi trước cả hai, qua đường nội bộ, test không import mức này) |
| `hooks.Hooks` | `.status(submission_id, user_id, event, **info)` với `event` thuộc `queued`, `judging`, `progress`, `done`; `.scoreboard_dirty()`. Test định nghĩa subclass ghi đè hai hàm này và **không gọi `super().__init__()`**, nên `Hooks.__init__` không được thêm state bắt buộc (mọi state phải có mặc định ở mức lớp, hoặc được khởi tạo lười) |

Ngoài `server/`, test khóa còn dùng `common.protocol` (đã khóa ở `m1-tests-locked`): `encode`, `LineDecoder`,
`Message`, `MAX_LINE_SERVER_TO_WORKER`, `MAX_LINE_WORKER_TO_SERVER`.

---

## 🧪 TEST

Test server chạy trong image riêng (có FastAPI), mã nguồn bind mount nên chỉ build lại khi đổi phụ thuộc:

```powershell
cd source
docker build -f server/Dockerfile --target test -t judgehive-server-test .
docker run --rm --pids-limit=512 --memory=2g --cap-drop=ALL --security-opt=no-new-privileges -v "${PWD}:/src" judgehive-server-test python -m pytest -p no:cacheprovider tests/server -q
```

Test `tests/invariants/` và `tests/worker/` vẫn chạy bằng lệnh chuẩn của worker (xem `worker/README.md`).
Test đầu cuối với worker thật (cần Docker và Python 3 trên máy host, dùng project compose riêng):

```bash
python tests/e2e/e2e.py
```

---

## 📝 GHI CHÚ

- Chạy đúng một process uvicorn.
- Sau `REGISTER`, mỗi worker giữ tối đa một `JOB`. Worker mất kết nối giữa chừng thì bài được giao lại với `attempt_id` mới,
  tối đa 3 lần giao rồi `SE` (README gốc 2.6, D1).
- Hạn chót từng bước (watchdog) tính từ lúc `sendall(JOB)` xong; `PROGRESS` đúng thứ tự mới làm mới hạn. Hạn sau
  `PROGRESS` cuối dùng lại công thức cửa sổ một test (PLAN D18).
- Mã lỗi cổng worker (đầu vào cho README mục 5): `LINE_TOO_LONG`, `BAD_JSON`, `REGISTER_REJECTED`,
  `UNSUPPORTED_VERSION`, `REGISTER_TIMEOUT`, `UNKNOWN_TYPE`, `BAD_MESSAGE`, `ALREADY_REGISTERED`,
  `UNEXPECTED_RESULT`, `DEADLINE_EXCEEDED`.
