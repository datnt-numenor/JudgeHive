# BÀI TẬP LỚN: LẬP TRÌNH MẠNG

## JudgeHive — hệ thống chấm bài trực tuyến nhiều máy chấm

---

## 🧑‍💻 THÔNG TIN NHÓM

| STT | Họ và Tên | MSSV | Email | Đóng góp |
|-----|-----------|------|-------|----------|
| 1 | Nguyễn Tiến Đạt | B23DCCN139 | — | Giao thức TCP giữa server và worker (framing, đăng ký), điều phối và giao lại bài khi mất kết nối, WebSocket realtime |
| 2 | Nguyễn Văn Phúc | B23DCCN957 | — | REST API, đăng nhập và phân quyền USER/ADMIN, cơ sở dữ liệu SQLite, chức năng quản trị |
| 3 | Nguyễn Công Thành | B23DCCN771 | — | Worker chấm bài trong Docker, phân loại verdict, giao diện React |

**Tên nhóm:** Nhóm 09
**Chủ đề đã đăng ký:** Xây dựng hệ thống chấm code tự động JudgeHive

---

## 🧠 MÔ TẢ HỆ THỐNG

JudgeHive cho phép thí sinh đọc đề, nộp bài bằng Python, C++ hoặc Java và xem kết quả chấm theo thời gian thực. Việc chấm được chia cho nhiều **máy chấm (worker)**; mỗi worker giữ một kết nối TCP tới server và trao đổi bằng giao thức do nhóm tự thiết kế.

Thành phần:

- **Server** (FastAPI + SQLite): REST API cho web, WebSocket báo có thay đổi, cổng TCP cho worker, hàng đợi bài nộp.
- **Worker** (Python asyncio): đăng ký với server, nhận bài, biên dịch và chạy từng test trong container Docker cô lập, gửi tiến độ và kết quả.
- **Client** (React + Vite): giao diện web cho USER và ADMIN.

```
Trình duyệt ──HTTP/REST + WebSocket── Server ──TCP (giao thức JudgeHive)── Worker 1 ── Docker (chạy bài)
                                         │                                └─ Worker 2 ── Docker
                                         └── SQLite
```

Chức năng:

- **Đăng nhập** với hai vai trò USER và ADMIN (cookie phiên HttpOnly, mật khẩu băm).
- **USER**: xem danh sách đề và đề chi tiết (kèm test mẫu), nộp bài Python/C++/Java, xem lịch sử và chi tiết bài nộp của mình, xem **bảng xếp hạng**. Mỗi người có tối đa 3 bài đang chờ chấm cùng lúc.
- **Chấm nhiều worker**: các worker cùng lấy bài từ một hàng đợi FIFO, mỗi worker chấm một bài tại một thời điểm.
- **Giao lại khi mất kết nối**: worker mất kết nối hoặc quá hạn công việc thì bài được giao cho worker khác; tối đa 3 lần giao, quá thì kết quả là SE.
- **Kết quả**: 6 verdict của bài làm `AC`, `WA`, `TLE`, `MLE`, `RE`, `CE`, cộng `SE` khi lỗi hệ thống.
- **Realtime**: trạng thái bài nộp, tiến độ từng test và bảng xếp hạng cập nhật qua WebSocket, không cần tải lại trang.
- **ADMIN**: quản lý đề (ẩn/hiện trên web; thêm đề bằng lệnh nạp file JSON), quản lý người dùng (tạo, khóa, đặt lại mật khẩu), theo dõi máy chấm và hàng đợi, xem mọi bài nộp.

---

## ⚙️ CÔNG NGHỆ SỬ DỤNG

| Thành phần | Công nghệ |
|---|---|
| Server | Python 3.11, FastAPI, Uvicorn (websockets), SQLite |
| Worker | Python 3.11 asyncio, Docker CLI |
| Chạy bài | Docker container: Python 3, g++ (C++17), OpenJDK 21 |
| Client | React, React Router, Vite (Node 22) |
| Triển khai | Docker Compose v2 |

Thư viện Python liệt kê trong `source/requirements.txt` (chạy) và `source/requirements-dev.txt` (kiểm thử); thư viện client trong `source/client/package.json`.

---

## 🚀 HƯỚNG DẪN CHẠY DỰ ÁN

### Yêu cầu

- Docker Desktop (Windows/macOS, chế độ Linux containers) hoặc Docker Engine (Linux), Docker Compose v2.
- Python 3 trên máy host chỉ để chạy `setup_env.py` (hoặc tự tạo file `.env`).
- Lần build đầu cần Internet để tải image và thư viện.

### 1. Clone repository

```bash
git clone <URL repo của nhóm>
cd assignment-network-project/source
```

### 2. Tạo secret và chạy

```bash
python setup_env.py
docker compose up --build -d
docker compose exec server python -m server.manage user admin --role admin
```

- `setup_env.py` tạo `source/.env` chứa `JH_WORKER_SECRET` ngẫu nhiên (không commit file này).
- Lệnh cuối tạo tài khoản ADMIN tên `admin`, hỏi mật khẩu hai lần.
- Mở **http://localhost:8000**, đăng nhập admin, tạo tài khoản USER ở trang **Người dùng**. Đề A + B có sẵn.

Nạp thêm đề từ file JSON (thư mục `source/problems/` có sẵn hai đề, trong đó một đề RAM 64 MiB để minh họa MLE):

```bash
docker compose exec server python -m server.manage problem problems/tong-day-so.json
docker compose exec server python -m server.manage problem problems/a-cong-b-ram-64.json
```

Định dạng file đề: `title`, `statement`, `time_limit_ms` (100–10000), `memory_limit_kib` (65536–524288), `hidden` (tùy chọn), `difficulty` (tùy chọn: `easy`, `medium` hoặc `hard`, mặc định `medium`), `tests` là 1–20 phần tử `{input, output, is_sample}`, tổng input/output tối đa 1 MiB.

Lệnh hay dùng:

```bash
docker compose logs -f server worker      # xem log
docker compose up -d --scale worker=3     # chạy 3 worker
docker compose down                       # dừng, GIỮ dữ liệu
```

`docker compose down -v` xóa luôn database. Service `runner-image` chỉ build image chạy bài rồi thoát — đó là bình thường.

### 3. Worker trên máy khác trong LAN

Máy A chạy server (cổng 8000 cho web, 9000 cho worker). Trên máy B có Docker, chép thư mục `source/`, tạo `.env` có **cùng** `JH_WORKER_SECRET` với máy A, rồi chạy từ `source/`:

```bash
docker build -f runner.Dockerfile -t judgehive-runner:local .
docker build -f worker/Dockerfile -t judgehive-worker:local .
docker run --rm --env-file .env -e JH_SERVER_HOST=<IP máy A> -e JH_WORKER_NAME=may-B -v /var/run/docker.sock:/var/run/docker.sock judgehive-worker:local
```

Mở TCP 9000 trên firewall máy A trong LAN. Secret đi dạng rõ qua TCP; chỉ dùng trong mạng được kiểm soát.

### 4. Chạy client ở chế độ phát triển (tùy chọn)

```bash
cd source/client
npm ci
npm run dev
```

Vite chuyển `/api` và `/ws` sang server ở cổng 8000.

### 5. Kiểm thử

Chạy trong container, từ `source/`, sau khi `docker compose up --build -d` đã build image:

```bash
docker run --rm --name judgehive-test -v "$PWD:/src" -w /src -v /var/run/docker.sock:/var/run/docker.sock -e JH_DOCKER_TESTS=1 judgehive-v2-worker sh -c "pip install -q -r requirements-dev.txt && python -m pytest -p no:cacheprovider -q tests"
```

| File | Nội dung | Cần Docker |
|---|---|---|
| `tests/conftest.py` | Hàm dùng chung: server thật trên loopback, worker giả qua TCP, Docker giả cho runner | Không |
| `tests/test_protocol.py` | Framing: nửa message, message dính nhau, dòng sai, giới hạn 64 KiB/8 MiB | Không |
| `tests/test_worker_link.py` | Đăng ký, trần 16 kết nối, không heartbeat, giao bài, `attempt_id`, hạn công việc, giao lại tối đa 3 lần | Không |
| `tests/test_web.py` | Phân quyền, không lộ test ẩn, phiên, Origin, giới hạn nộp bài, file đề, WebSocket | Không |
| `tests/test_grading.py` | Verdict, không lộ output/mã thoát, đo thời gian, worker hủy bài khi mất server | Không |
| `tests/test_docker.py` | Chấm thật trong container (bật bằng `JH_DOCKER_TESTS=1`) | Có |
| `tests/test_compose.py` | Smoke test hệ thống đang chạy (bật bằng `JH_COMPOSE_TESTS=1`) | Có |

Năm file đầu là bộ test khóa contract của nhóm: sau khi đặt tag khóa thì không sửa test cũ, code phải theo test.

---

## 🔗 GIAO TIẾP (GIAO THỨC SỬ DỤNG)

### 1. Tổng quan

| Kênh | Hai đầu | Giao thức |
|---|---|---|
| Web | Trình duyệt ↔ Server (cổng 8000) | HTTP REST (JSON) + WebSocket `/ws` |
| Chấm bài | Worker ↔ Server (cổng 9000) | TCP, giao thức JudgeHive (JSON theo dòng) |

### 2. Giao thức JudgeHive (TCP, cổng 9000)

**Framing.** Mỗi message là một object JSON mã hóa UTF-8, kết thúc bằng một byte `\n` (giới hạn kích thước bên dưới tính cả byte này). Xuống dòng bên trong code hay dữ liệu test được JSON escape nên không lẫn với dấu kết thúc. Bên nhận đệm byte tới khi gặp `\n` (TCP là luồng byte: một lần `recv` có thể chứa nửa message hoặc nhiều message).

**Giới hạn kích thước:** worker → server tối đa 64 KiB mỗi dòng; server → worker tối đa 8 MiB mỗi dòng (JOB chứa code và test). Vượt giới hạn, JSON sai, hoặc `type` lạ → đóng kết nối.

**Các message:**

| Message | Chiều | Trường | Ý nghĩa |
|---|---|---|---|
| `REGISTER` | W → S | `name`, `secret` | Worker xin đăng ký |
| `REGISTER_OK` | S → W | `worker_id` | Đăng ký thành công |
| `JOB` | S → W | `submission_id`, `attempt_id`, `language`, `code`, `time_limit_ms`, `memory_limit_kib`, `tests[{input,output}]` | Giao một bài |
| `PROGRESS` | W → S | `submission_id`, `attempt_id`, `tests_done` | Số test đã qua (tăng dần) |
| `RESULT` | W → S | `submission_id`, `attempt_id`, `verdict`, `time_ms`, `message` | Kết quả cuối |
| `ERROR` | S → W | `message` | Từ chối đăng ký, rồi đóng kết nối |

Ví dụ:

```json
{"type":"REGISTER","name":"may-B","secret":"<secret của nhóm>"}
{"type":"REGISTER_OK","worker_id":1}
{"type":"JOB","submission_id":10,"attempt_id":1,"language":"python","code":"print(sum(map(int,input().split())))","time_limit_ms":2000,"memory_limit_kib":262144,"tests":[{"input":"2 3\n","output":"5\n"}]}
{"type":"PROGRESS","submission_id":10,"attempt_id":1,"tests_done":1}
{"type":"RESULT","submission_id":10,"attempt_id":1,"verdict":"AC","time_ms":45,"message":""}
```

`time_ms` là thời gian chạy của test chậm nhất (mili giây), không gồm biên dịch. `message` không bao giờ chứa stdout/stderr của test ẩn hay giá trị mã thoát cụ thể của chương trình.

`tests_done` trong `PROGRESS` phải tăng chặt so với lần trước của cùng JOB và nằm trong khoảng 1 … số test; vi phạm → đóng kết nối và giao lại bài.

**Luồng hoạt động:**

1. Worker mở TCP, gửi `REGISTER` trong vòng 10 giây. Secret sai hoặc thiếu → `ERROR` rồi đóng. Server giữ tối đa 16 kết nối chưa đăng ký; kết nối vượt trần bị đóng ngay, không có message.
2. Server trả `REGISTER_OK`. Worker rảnh chờ, không gửi gì.
3. Khi có bài, server tăng `attempt_id`, gửi `JOB`. Worker chạy từng test, gửi `PROGRESS` sau mỗi test qua, cuối cùng gửi `RESULT`.
4. Server chỉ nhận `PROGRESS`/`RESULT` khớp đúng `submission_id` và `attempt_id` đang giao cho kết nối đó; sai, hoặc worker đang rảnh mà gửi `PROGRESS`/`RESULT` → đóng kết nối.

**Hạn công việc, không heartbeat.** Hệ thống không dùng PING/PONG hay keepalive định kỳ. Mỗi JOB có một hạn hoàn thành tính từ lúc gửi:
`20 + số_test × (time_limit_ms/1000 + 10) + 60` giây. `PROGRESS` không gia hạn. Hết hạn mà chưa có `RESULT` → server coi worker đã mất.

**Giao lại khi mất kết nối.** Khi kết nối đóng, lỗi, hoặc quá hạn công việc: nếu bài mới giao dưới 3 lần thì trả về hàng đợi để worker khác nhận; lần thứ 3 vẫn hỏng thì verdict `SE`. `attempt_id` bảo đảm kết quả muộn của lần giao trước không ghi đè lần giao mới. Worker thấy mất kết nối thì hủy bài đang chạy, dọn container rồi tự nối lại sau 3 giây. Server khởi động lại cũng đưa bài đang chấm về hàng đợi theo cùng luật 3 lần.

### 3. Verdict

| Verdict | Điều kiện |
|---|---|
| AC | Qua mọi test |
| WA | Output khác đáp án (so theo token, bỏ qua khoảng trắng) hoặc output vượt 1 MiB |
| TLE | Thời gian chạy một test vượt giới hạn của đề (quá giới hạn + 1 giây mà chưa xong thì worker dừng container) |
| MLE | Docker báo container bị OOM-kill, hoặc Java thoát lỗi với `java.lang.OutOfMemoryError` |
| RE | Chương trình thoát mã khác 0 mà không phải MLE |
| CE | Biên dịch C++/Java lỗi |
| SE | Lỗi hệ thống: lỗi Docker, hoặc hết 3 lần giao |

Mỗi test chạy trong container mới: không mạng, RAM theo đề (không swap), 1 CPU, tối đa 128 tiến trình, user thường, hệ thống file chỉ đọc. Thời gian một test lấy theo mốc bắt đầu/kết thúc của container do Docker ghi, nên không gồm thời gian Docker chuẩn bị container nhưng vẫn gồm thời gian khởi động trình thông dịch/JVM.

### 4. REST API (cổng 8000)

| Method / URL | Quyền | Mô tả |
|---|---|---|
| POST `/api/auth/login` · POST `/api/auth/logout` · GET `/api/auth/me` | — | Đăng nhập, đăng xuất, thông tin phiên |
| GET `/api/problems` | Đăng nhập | Danh sách đề; mỗi đề có `difficulty` (`easy`/`medium`/`hard`), và thêm `status` (`"solved"` nếu người đang đăng nhập đã AC, `"attempted"` nếu đã nộp mà chưa AC, `null` nếu chưa nộp) và `ac_rate` (số bài AC / số bài đã chấm xong của đề, từ 0 đến 1; `null` nếu chưa có bài chấm xong) |
| GET `/api/problems/{id}` | Đăng nhập | Đề (kèm `difficulty`) + test mẫu |
| POST `/api/submissions` | Đăng nhập | Nộp `{problem_id, language, code}`; 429 nếu đã có 3 bài chờ chấm |
| GET `/api/submissions` · GET `/api/submissions/{id}` | Đăng nhập | 100 bài mới nhất và chi tiết bài của mình |
| GET `/api/scoreboard` | Đăng nhập | Bảng xếp hạng |
| PATCH `/api/admin/problems/{id}` | Admin | Ẩn/hiện đề `{hidden}` |
| GET/POST `/api/admin/users` · PATCH `/api/admin/users/{id}` | Admin | Quản lý người dùng |
| GET `/api/admin/workers` | Admin | Worker đang kết nối và độ dài hàng đợi |
| GET `/api/admin/submissions` | Admin | 100 bài nộp mới nhất của mọi người |

Lỗi trả `{"detail": "..."}`. Giới hạn: code 64 KiB; ngôn ngữ `python`, `cpp`, `java`.

### 5. WebSocket `/ws`

Xác thực bằng cookie phiên; server kiểm tra `Origin`. WebSocket chỉ **báo có thay đổi**, không mang dữ liệu; client nhận rồi tự tải lại bằng REST (REST đã xét quyền):

```json
{"type":"submission","id":10}
{"type":"scoreboard"}
{"type":"workers_changed"}
```

`submission` (bài nộp đổi trạng thái/tiến độ/kết quả) chỉ gửi cho chủ bài và admin; `scoreboard` gửi mọi phiên; `workers_changed` chỉ gửi admin. Phiên đã đăng xuất hoặc bị khóa sẽ bị đóng WebSocket ở lần gửi kế tiếp. Client không gửi message định kỳ; mất kết nối thì tự nối lại sau 3 giây và tải lại dữ liệu qua REST.

Bảng xếp hạng: số đề AC giảm dần, rồi thời điểm nộp lần AC đầu của đề giải sau cùng tăng dần.

---

## ⚠️ HẠN CHẾ ĐÃ BIẾT

- Không HTTPS và TCP không mã hóa: chỉ dùng trong LAN được kiểm soát.
- Worker rảnh mất mạng im lặng có thể chưa bị phát hiện ngay (không heartbeat); bài đang chấm vẫn được giao lại nhờ hạn công việc.
- Kẻ lạ chiếm đủ 16 kết nối chưa đăng ký có thể làm worker thật phải chờ tới 10 giây mới đăng ký được.
- Thời gian đo gồm cả thời gian khởi động trình thông dịch/JVM trong container; không phải CPU time. Nếu Docker trên máy chấm mất hơn 1 giây để chạy container, bài đúng chạy sát giới hạn có thể bị TLE.
- Máy chấm quá chậm (mỗi lệnh Docker có thể chờ tới 10 giây) có thể làm bài vượt hạn công việc, bị giao lại và cuối cùng thành SE.
- Java dùng heap bằng một nửa giới hạn bộ nhớ của đề để chừa chỗ cho JVM; một mảng đơn chỉ dùng được khoảng 30% giới hạn (đo ở 64 MiB và 256 MiB).
- Worker có quyền điều khiển Docker trên máy nó chạy; không chạy hệ thống trên máy có dữ liệu quan trọng.

## 📂 CẤU TRÚC THƯ MỤC

```
source/
├── common/protocol.py   # framing, kiểu message, hạn công việc
├── server/              # FastAPI, cổng TCP worker, WebSocket, SQLite (xem server/README.md)
├── worker/              # kết nối server, chạy bài trong Docker
├── client/              # React (xem client/README.md)
├── problems/            # đề mẫu dạng JSON
├── samples/             # bài nộp mẫu cho từng verdict
├── tests/               # kiểm thử
└── compose.yaml, runner.Dockerfile, setup_env.py
```
