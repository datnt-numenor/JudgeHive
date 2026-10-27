# MODULE SERVER

Server chính của JudgeHive: nhận worker qua cổng TCP thuần (framing JSON-lines do nhóm tự viết, xem README gốc mục
"Giao tiếp" 2), giao bài, lưu kết quả vào SQLite. REST cho trình duyệt (đăng nhập, phiên, đề, nộp bài, lịch sử; README gốc mục 3)
nằm trong `rest/`, bảng xếp hạng (`GET /api/scoreboard`) trong `scoreboard/`. WebSocket và endpoint admin thuộc các PR sau.

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
| `auth.py` | Băm mật khẩu scrypt (từ chối mật khẩu có ký tự NUL: scrypt của OpenSSL bỏ qua NUL cuối) |
| `rest/` | REST: `errors.py` (định dạng lỗi), `body.py` (Content-Type, giới hạn body, JSON), `sessions.py` (cookie và bảng `sessions`), `limits.py` (giới hạn đăng nhập), `views.py` (đối tượng bài nộp, whitelist theo verdict), `auth_routes.py`, `problem_routes.py`, `submission_routes.py` |
| `scoreboard/` | Bảng xếp hạng: `ranking.py` (hàm thuần theo README 3.3), `loader.py` (một câu SELECT), `service.py` (`ScoreboardService`: giữ bản gần nhất, tính lại gộp tối đa một lần mỗi `scoreboard_min_interval_s`, phát bản mới cho subscriber) |
| `static.py` | Phục vụ giao diện web đã build (xem "Giao diện web" dưới): `/assets` bằng `StaticFiles`, fallback SPA trong bộ xử lý 404 |
| `hooks.py` | Cầu thread → event loop. `scoreboard_dirty` đánh dấu `ScoreboardService` bằng `call_soon_threadsafe`; `status` (STATUS) còn chỉ ghi log, WebSocket dùng ở PR sau |
| `timefmt.py` | `iso_ms`: thời điểm ISO 8601 UTC có mili giây, dùng chung cho REST và bảng xếp hạng |
| `config.py` | Cấu hình từ biến môi trường |
| `manage.py` | Script quản lý (xem dưới) |
| `problems/<id>/` | Đề seed: `problem.json`, `statement.txt`, `samples/NN.in\|NN.out`, `tests/NN.in\|NN.out` |

---

## 🚀 HƯỚNG DẪN CHẠY

Từ thư mục `source/`:

```bash
python -c "import secrets, pathlib; pathlib.Path('secrets/worker.env').write_text('JH_WORKER_SECRET=' + secrets.token_hex(32) + '\n', encoding='utf-8', newline='\n')"   # một lần; thư mục secrets/ có sẵn trong repo
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
| `JH_STATIC_DIR` | `/app/static` | Thư mục giao diện web đã build (`index.html` và `assets/`). Không có `index.html` ở đó thì server chỉ có API |
| `JH_LOG_LEVEL` | `INFO` | Mức log |

Cờ uvicorn cố định trong `CMD` của `server/Dockerfile` (không đọc từ biến môi trường):

| Cờ | Giá trị | Lý do |
|----|---------|-------|
| `--no-server-header` | | Không lộ `server: uvicorn` trong response |
| `--timeout-keep-alive` | `5` | Kết nối HTTP rảnh bị đóng sau 5 giây, kết nối bỏ không không chiếm chỗ lâu |
| `--limit-concurrency` | `200` | Quá 200 kết nối/tác vụ đồng thời thì uvicorn trả 503 ngay (một process, một event loop). Cổng worker là socket riêng nên không tính. PR WebSocket phải cân lại số này vì mỗi WebSocket là một kết nối |
| `--workers` | không dùng | Đúng một process |

Chưa có timeout đọc body ở tầng ứng dụng: client chậm chiếm một kết nối (không chiếm thread vì phần đọc body là `async`) tới khi gửi xong, tối đa 512 KB; trần đồng thời ở trên là lớp chặn kết nối treo hàng loạt.

### Giao diện web

Image `runtime` build giao diện trong một stage Node riêng (`client-build`, `node:22-slim`, cần mạng lúc build) và chép `dist/` vào `/app/static`, nên chạy `docker compose up --build` là mở được `http://localhost:8000/` mà không cần cài Node. Giao diện và API cùng origin, không có CORS. Server **không có route khớp mọi đường dẫn**; chỉ có:

| Yêu cầu | Kết quả |
|---------|---------|
| `GET`/`HEAD` `/assets/<file>` | File thật trong `assets/`, `Cache-Control: public, max-age=31536000, immutable` (tên file có băm nội dung). Không có file thì 404 |
| `GET`/`HEAD` đường dẫn không khớp route nào, không có đuôi file, không thuộc `/api`, `/ws`, `/assets` (ví dụ `/`, `/login`, `/problems/sum`) | `index.html`, `Cache-Control: no-cache`; trình duyệt tự định tuyến phía client |
| Đường dẫn có đuôi file mà không tồn tại (`/missing.js`, `/favicon.ico`, `/index.html`) | 404, không phải HTML |
| `/api/*` | Như cũ: JSON, kể cả 404 và 405 |
| `/ws`, `/ws/*` | Giữ chỗ cho WebSocket (PR sau): hiện là 404 |
| `POST`, `PUT`, `DELETE` ngoài `/api` | 404 hoặc 405, không bao giờ `index.html` |

Đường dẫn của client không bao giờ được ghép vào đường dẫn file (chỉ `/assets` đọc file, qua `StaticFiles`: chặn `..`, đường dẫn tuyệt đối, symlink thoát thư mục, byte NUL). Khi phát triển giao diện, dùng `npm run dev` (Vite proxy `/api`), không cần thư mục này.

### Quản lý (`manage.py`)

DB nằm trong named volume nên chạy trong container server:

```bash
docker compose exec server python -m server.manage users create alice            # nhắc nhập mật khẩu (8 đến 128 byte)
docker compose exec server python -m server.manage users create root --role admin
docker compose exec server python -m server.manage problems reimport aplusb      # D5: nạp lại một đề từ thư mục
```

`--password-stdin` đọc mật khẩu từ dòng đầu của stdin theo UTF-8 và bỏ BOM (U+FEFF) ở đầu luồng (Windows PowerShell 5.1 tự chèn BOM khi pipe); mật khẩu bắt đầu bằng U+FEFF không được hỗ trợ.

`problems/` chỉ là dữ liệu khởi tạo: lúc khởi động server chỉ nạp đề chưa có trong DB, không ghi đè. Sửa file trong
`problems/` rồi restart không có tác dụng; dùng `problems reimport` (và build lại image nếu đổi file, vì `problems/`
nằm trong image). `reimport` đổi hash bộ test nhưng không tự chấm lại và không đổi cờ ẩn.

**Cờ ẩn của đề không đến từ `problems/`.** `problem.json` không có trường `hidden`: đề seed luôn được tạo với `hidden = 0`, và với đề đã có trong DB thì lúc khởi động server không đụng gì tới nó (cả cờ ẩn), `reimport` cũng giữ nguyên cờ ẩn. Hệ quả: seed và reimport không bao giờ làm bảng xếp hạng đổi vì lý do ẩn/hiện; cờ ẩn chỉ đổi qua DB hay endpoint admin (chưa có), và nơi đổi cờ phải gọi `ScoreboardService.mark_dirty()` (xem dưới).

Nộp bài và xem kết quả qua REST (`POST /api/submissions`, `GET /api/submissions/{id}`). `submissions show ID` chỉ để
xem một dòng bài nộp khi gỡ lỗi (in cả cờ nội bộ như `delivery_count`).

### REST và SQLite

Endpoint và dependency của REST khai báo bằng `def`, nên FastAPI chạy chúng trong threadpool; event loop không gọi
SQLite (chỉ `rest/body.py` là `async`, và nó chỉ đọc stream). Mỗi thread của pool tự mở kết nối riêng qua
`Database.conn()` (thread-local, không chia sẻ giữa thread, cùng mô hình với cổng worker) và giữ nó suốt đời thread,
tối đa khoảng 40 kết nối. Đọc dùng autocommit; ghi dùng `Database.write()` (`BEGIN IMMEDIATE`). Nộp bài kiểm token
trùng, kiểm đề, đếm rate limit và ghi bài trong một transaction như vậy, rồi mới `Dispatcher.enqueue` sau commit.
Hết 5 giây `busy_timeout` thì endpoint trả 500; client gửi lại cùng `client_token` là an toàn.

### Bảng xếp hạng và event loop

`ScoreboardService` giữ mọi trạng thái (bản gần nhất, phiên bản, cờ cần tính lại, danh sách subscriber) chỉ trên event loop. Truy vấn SQLite chạy trong một thread riêng của dịch vụ (`scoreboard-db`, một thread duy nhất, nên không có hai lần tính song song), vẫn qua `Database.conn()` nên cùng pragma với mọi chỗ khác. Thread khác (dispatcher, cổng worker) chỉ gọi `LoopHooks.scoreboard_dirty`, hàm này làm `loop.call_soon_threadsafe(service.mark_dirty)` rồi trả về và không bao giờ ném. Số phiên bản tăng trên event loop ngay trước khi giao việc cho thread, nên bản mang phiên bản v phản ánh mọi commit đã đánh dấu trước đó; bản đầu (phiên bản 1) tính xong trước khi mở cổng worker, lỗi thì server không khởi động. `GET /api/scoreboard` chỉ đọc bản đã tính, không bao giờ tự tính lại.

Điểm cắm cho PR WebSocket (đều gọi trên event loop): `service.latest()` (bản hiện có, gửi ngay khi một kết nối nối thành công), `service.subscribe(callback)` (`callback(snapshot)` được gọi theo đúng thứ tự phiên bản và không bao giờ đồng thời; phải trả về nhanh; trả hàm hủy đăng ký) và `service.mark_dirty()`. **Mọi chỗ làm đổi dữ liệu mà bảng phụ thuộc ngoài việc ghi một kết quả chấm (ẩn/hiện đề, đổi role của người dùng) phải gọi `mark_dirty()`** bằng `loop.call_soon_threadsafe` nếu đứng ngoài event loop; hiện chưa có endpoint nào như vậy.

### Số đo chỉ mục `idx_submissions_board` (migration 3)

Chạy bằng `python tests/perf/measure_scoreboard.py --rows N --code-kib K` từ `source/` (script chạy tay, không nằm trong bộ test;
mỗi cấu hình chạy một lần, mỗi bên 7 lần truy vấn, lấy trung vị). Lý do của chỉ mục: cột `code` (tới 64 KB) nằm trước `verdict`
trong bảng `submissions`, nên quét bảng phải đi qua các trang tràn của `code`. Số đo trong container (`judgehive-server-test`,
Linux/WSL2, Python 3.11.17, SQLite 3.46.1, đĩa thật của container) và trực tiếp trên Windows 11 (Python 3.13.3, SQLite 3.49.1):

| Dữ liệu | Nơi chạy | Đọc SQL, có chỉ mục | Đọc SQL, không chỉ mục | Cả lần tính (đọc + xếp hạng), có / không |
|---------|----------|---------------------|------------------------|------------------------------------------|
| 5 000 bài x 64 KiB (316 MiB), 300 người | container | 6,2 ms | 73,5 ms | 8,8 / 75,8 ms (8,6 lần) |
| 5 000 bài x 64 KiB | Windows | 8,9 ms (tổng) | 759 ms (tổng) | khoảng 85 lần |
| 50 000 bài x 4 KiB (225 MiB), 1 000 người | container | 72,0 ms | 156,7 ms | 99 / 195 ms (2,0 lần) |
| 50 000 bài x 4 KiB | Windows | 242 ms | 944 ms | 294 / 1 030 ms (3,5 lần) |

Xếp hạng bằng Python (`ranking.py`) với 50 000 bài: 22,6 ms (container) và 69 đến 95 ms (Windows). Chi phí ghi thêm một bài do
chỉ mục: 0,19 so với 0,12 ms (50 000 x 4 KiB, container), 0,35 so với 0,44 ms (5 000 x 64 KiB, container): cùng bậc, phần chênh
nằm trong nhiễu của hệ điều hành. Lần đo đầu trong container dùng nhầm tmpfs (RAM) nên đã bỏ; số ở bảng là trên đĩa thật.
Kết luận: giữ chỉ mục (nhanh hơn 2 đến 8,6 lần trong container, nhiều nhất khi `code` lớn).

**Hạn chế đã biết (bảng xếp hạng):** phần xếp hạng chạy Python thuần (`ranking.py`) trong thread riêng của dịch vụ nhưng giữ GIL, nên trong lúc nó chạy các request khác trên event loop có thể chậm đi. Với 50 000 bài nó mất khoảng 23 ms (container) đến 70 đến 95 ms (Windows) mỗi lần, cộng 72 đến 240 ms đọc SQL (phần này không giữ GIL), và dịch vụ chỉ tính tối đa một lần mỗi `scoreboard_min_interval_s` (mặc định 1 giây). Mỗi lần tính cũng nạp toàn bộ bài có verdict vào bộ nhớ. Chưa đo tác động lên độ trễ của request khác khi có tải; **phải kiểm lại khi đo tải** (script giả lập nhiều thí sinh), và nếu cần thì tổng hợp bằng SQL hoặc tính tăng dần.

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
| `config.ServerConfig(bind_host, worker_port, secret, db_path, max_unauthenticated, ping_interval_s, peer_dead_after_s)` | Tạo bằng keyword; `worker_port=0` là cổng tự chọn. `ping_interval_s` và `peer_dead_after_s` được truyền tường minh (đủ lớn so với thời gian chạy của test, vì worker giả không trả PONG), test không dựa vào mặc định của hai field này. Mọi field khác dùng mặc định, và test dựa vào `max_deliveries` mặc định là 3 (README gốc 2.6, D1) |
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
