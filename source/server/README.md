# Server

FastAPI chạy trong một process uvicorn, mở hai cổng:

- **8000**: REST API, WebSocket `/ws` và giao diện React đã build.
- **9000**: cổng TCP cho worker (giao thức JudgeHive, đặc tả ở README gốc, mục "Giao tiếp").

| File | Vai trò |
|---|---|
| `main.py` | Tạo app, REST API, WebSocket, mở cổng TCP worker |
| `dispatch.py` | Hàng đợi bài, xử lý từng kết nối worker: đăng ký, giao `JOB`, nhận `PROGRESS`/`RESULT`, giao lại khi mất kết nối |
| `realtime.py` | Đẩy sự kiện bài nộp, bảng xếp hạng, worker qua WebSocket |
| `db.py` | SQLite: bảng, truy vấn, bảng xếp hạng, đề A + B mặc định |
| `auth.py` | Băm mật khẩu, băm token phiên |
| `problems.py` | Kiểm tra file đề JSON và nạp vào database |
| `manage.py` | Lệnh quản trị: `user` (tạo tài khoản), `problem` (nạp đề) |

Chạy bằng Compose (từ `source/`):

```bash
docker compose up --build -d server
docker compose exec server python -m server.manage user admin --role admin
docker compose exec server python -m server.manage problem problems/tong-day-so.json
```

Biến môi trường: `JH_WORKER_SECRET` (bắt buộc, ≥ 16 ký tự), `JH_DB` (đường dẫn SQLite, mặc định `/data/judgehive.sqlite3`), `JH_WORKER_PORT` (mặc định 9000).
