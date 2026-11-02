# tools/

## load_test.py: load test qua HTTP + WebSocket (D25)

Giả lập nhiều thí sinh như một **client bên ngoài** (không import `server/`, không đọc DB). Mặc định: 10 người × 5 bài = 50 bài nộp **cùng lúc** (đúng trần 5 bài đang chờ mỗi người), rồi kiểm:

| Mã | Kiểm tra |
| --- | --- |
| C1 | mọi bài được nhận đều tới `judged` trong hạn `--deadline` (mặc định 120 s) |
| C2 | không bài nào có hơn một `STATUS judged` (không chấm trùng); thứ tự `queued → judging → judged`, `tests_done` không giảm; `queued` sau `judging` là giao lại, chỉ đếm |
| C3 | verdict đúng như mong đợi (AC, WA, CE) và `STATUS` khớp `GET /api/submissions/{id}` |
| C4 | WebSocket của mỗi người chỉ nhận bài của chính họ |
| C5 | lịch sử `GET /api/submissions` có đúng các bài đã nộp, mỗi bài một lần |
| C6 | nộp lại cùng `client_token` trả 200 và cùng `submission_id` |
| C7 | `STATUS` và danh sách bài nộp không mang `code`, `ce_message`, `sample_*` |

Hỗn hợp mặc định (`--mix`): `py-ac=50, cpp-ac=20, java-ac=10, py-wa=10, py-ce=10` (đề seed `aplusb`). Báo thời gian: ack, chờ hàng đợi, chấm, tổng (p50/p90/p99/max, theo ngôn ngữ) và thông lượng.

**Hạn chờ không tự nâng.** Bài nào chưa `judged` khi hết `--deadline` (ví dụ Java chậm dưới tải) được báo là *phát hiện* ở C1, kèm danh sách theo ngôn ngữ; muốn hạn khác thì đặt `--deadline`, công cụ không nới ngầm.

Kịch bản tùy chọn:
- `--with-s2`: một người thứ `users+1` nộp 8 bài chậm cùng lúc; đúng 5 bài được nhận, 3 bài bị 429 `TOO_MANY_PENDING` kèm `Retry-After: 5`, bài bị từ chối không có trong lịch sử.
- `--queue-full`: `--queue-full-users` (mặc định 45) người, mỗi người 5 bài chậm, để làm đầy hàng đợi toàn hệ thống (200 bài `queued`) và thấy 503 `QUEUE_FULL` kèm `Retry-After: 10`. Nặng và lâu (đợi chấm hết các bài chậm), chỉ chạy tay khi cần.
- `--delivery-log FILE`: đối chiếu số lần giao với log server (`docker compose logs --no-color --no-log-prefix server > FILE`); chỉ là kiểm bổ sung, bằng chứng chính vẫn là `STATUS`.

### 1. Tạo tài khoản (công cụ không tự tạo)

Cần `COUNT` tài khoản `loadtest-01..`: **11** để chạy kịch bản chính + `--with-s2` (10 cho kịch bản chính, tài khoản thứ 11 cho S2); chỉ 10 nếu không bật `--with-s2`; thêm 45 nữa (tổng 56) nếu bật `--queue-full`. Chạy ở `source/`, stack đã `docker compose up`:

```bash
export JH_LOADTEST_PASSWORD='đặt-mật-khẩu-8-đến-128-byte'
COUNT=11
for i in $(seq 1 "$COUNT"); do
  name=$(printf 'loadtest-%02d' "$i")
  printf '%s\n' "$JH_LOADTEST_PASSWORD" | docker compose exec -T server python -m server.manage users create "$name" --password-stdin
done
```

(Nếu compose chạy dưới project khác, thêm `-p <project>` sau `docker compose`.)

### 2. Chạy (trong container, không cài gì lên máy host)

Origin của WebSocket được suy **đúng từ `--base`** (cùng scheme, host, cổng với header `Host` mà công cụ gửi), nên qua được kiểm Origin của server (README mục 4.1). Có hai đường kết nối, chọn một:

**(a) Qua cổng đã publish của host** (`ports: 8000:8000`): `--base http://host.docker.internal:8000` (Docker Desktop). Origin = `http://host.docker.internal:8000`.

**(b) Qua mạng compose** (không đi qua host): gắn container vào mạng của stack, `--base http://server:8000` (tên service `server`). Mạng tên `<project>_default`, ví dụ `judgehive_default`. Origin = `http://server:8000`.

```bash
cd source
docker run --rm --name judgehive-test-A-$(date +%H%M%S) \
  -e JH_LOADTEST_PASSWORD -v "$PWD/tools:/t:ro" python:3.11-slim \
  sh -c 'pip install -q --no-cache-dir -r /t/requirements.txt && python /t/load_test.py --base http://host.docker.internal:8000 --out /tmp/load.json'
# đường (b): thêm --network judgehive_default và đổi --base thành http://server:8000
```

(`--name` theo luật `judgehive-test-<A|B>-<HHmmss>` của CLAUDE.md; chữ A/B là phiên đang chạy. Cần mạng để `pip install`; `websockets==17.3` chưa kiểm chứng chạy được trên Python 3.11.)

Exit code 0 khi mọi kiểm tra PASS, 1 khi có FAIL, 2 khi tham số sai. Mật khẩu chỉ đọc từ biến môi trường `JH_LOADTEST_PASSWORD`, không bao giờ in; `--out` ghi số liệu thô không có `code`, cookie hay token.
