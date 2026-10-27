# BÀI TẬP LỚN: LẬP TRÌNH MẠNG  

## [Tên dự án của nhóm]

> 📘 *Mẫu README này là khung hướng dẫn. Sinh viên chỉ cần điền thông tin của nhóm và nội dung dự án theo từng mục.*

---

## 🧑‍💻 THÔNG TIN NHÓM

| STT | Họ và Tên | MSSV | Email | Đóng góp |
|-----|-----------|------|-------|----------|
| 1 | Nguyễn Văn A | 20IT001 | a@example.com | ... |
| 2 | Trần Thị B | 20IT002 | b@example.com | ... |
| 3 | Lê Văn C | 20IT003 | c@example.com | ... |

**Tên nhóm:** Nhóm 01 – Lập trình mạng  
**Chủ đề đã đăng ký:** (…)

---

## 🧠 MÔ TẢ HỆ THỐNG

> Mô tả tổng quan hệ thống mà nhóm triển khai.

Ví dụ:
> Hệ thống bao gồm **server** xử lý yêu cầu và **client** gửi yêu cầu đến server qua giao thức HTTP.  
> Server cung cấp API cho phép tính toán đơn giản hoặc truyền dữ liệu.  
> Client có thể là chương trình CLI, web hoặc ứng dụng desktop.

**Cấu trúc logic tổng quát:**
```
client  <-->  server  <-->  (database / service nếu có)
```

**Sơ đồ hệ thống:**

![System Diagram](./statics/diagram.png)

---

## ⚙️ CÔNG NGHỆ SỬ DỤNG

> Liệt kê công nghệ, framework, thư viện chính mà nhóm sử dụng.

| Thành phần | Công nghệ | Ghi chú |
|------------|-----------|---------|
| Server | Python 3.11 + Flask | REST API |
| Client | Node.js 20 + Axios | Giao tiếp HTTP |
| Database | SQLite | Lưu trữ dữ liệu tạm thời |
| Triển khai | Docker | (nếu có) |

---

## 🚀 HƯỚNG DẪN CHẠY DỰ ÁN

### 1. Clone repository
```bash
git clone <repository-url>
cd assignment-network-project
```

### 2. Chạy server
Tạo shared secret của worker một lần (file `source/secrets/worker.env` bị git bỏ qua, mẫu ở `worker.env.example`): `python -c "import secrets, pathlib; pathlib.Path('source/secrets/worker.env').write_text('JH_WORKER_SECRET=' + secrets.token_hex(32) + '\n', encoding='utf-8', newline='\n')"` (không dùng `>` vì PowerShell 5.1 ghi UTF-16 có BOM; `newline='\n'` để Windows không ghi CRLF).

```bash
cd source
docker compose up --build                    # server + một worker
docker compose up --build --scale worker=3   # ba worker
```

Image server tự build giao diện web trong lúc build (cần mạng ở lần build đầu vì có `npm ci`), nên chỉ cần mở `http://localhost:8000/` là thấy trang đăng nhập; không cần cài Node. Chưa có tài khoản nào, tạo bằng script (DB nằm trong volume Docker nên chạy trong container server; lệnh sẽ hỏi mật khẩu):

```bash
docker compose exec server python -m server.manage users create alice
```

Mật khẩu từ 8 đến 128 byte. Thêm `--role admin` để tạo quản trị viên. Chi tiết các biến môi trường và lệnh quản lý: `source/server/README.md`.

### 3. Chạy client (khi phát triển giao diện)
Giao diện đã có sẵn trong image server (mục 2). Chỉ khi sửa mã giao diện mới cần chạy riêng, với backend đang chạy ở cổng 8000:

```bash
cd source/client
npm ci
npm run dev        # http://localhost:5173, tự chuyển /api sang http://127.0.0.1:8000
```

Cần Node 22.12 trở lên; cách chạy test và lưu ý về phiên bản npm: `source/client/README.md`.

### 4. Kiểm thử nhanh
```bash
# Các lệnh test
```

---

## 🔗 GIAO TIẾP (GIAO THỨC SỬ DỤNG)

Mục này là contract giữa server, judge worker và client web. Nó chỉ mô tả hành vi quan sát được từ bên ngoài: message, field, mã lỗi, thứ tự, timeout và giới hạn. Cách server cài đặt bên trong không thuộc contract.

> Nhãn trong bản nháp: **[ĐỀ XUẤT]** là tên hoặc giá trị đang chờ nhóm duyệt; **[CẦN CHỐT]** là hành vi chưa được quyết định.

### 1. Tổng quan

```
Trình duyệt (React) ── HTTP (REST) + WebSocket ──►  Server  ◄── TCP thuần (JSON lines) ── Judge worker (×N)
```

Trình duyệt và worker chỉ nói chuyện với server, không bao giờ nói chuyện trực tiếp với nhau.

| Cổng | Mặc định | Biến môi trường **[ĐỀ XUẤT]** | Dùng cho |
|------|----------|-------------------------------|----------|
| HTTP/WebSocket | `8000` | `JH_HTTP_PORT` | Trình duyệt: REST `/api/*`, WebSocket `/ws`, file tĩnh của frontend |
| TCP worker | `9000` | `JH_WORKER_PORT` | Judge worker |

Quy ước chung:

- Văn bản luôn mã hóa UTF-8; dữ liệu có cấu trúc luôn là JSON.
- Khoảng thời gian tính bằng mili giây, bộ nhớ tính bằng KiB. Tên field mang hậu tố đơn vị: `_ms`, `_kib`.
- Kích thước: 1 KB = 1 024 byte, 1 MB = 1 048 576 byte.
- Thời điểm là chuỗi ISO 8601 theo UTC, có mili giây, ví dụ `2026-10-23T08:15:30.123Z` **[ĐỀ XUẤT]**.
- `submission_id`, `attempt_id`, `worker_id` là số nguyên.
- Ngôn ngữ: `python`, `cpp`, `java`.
- Verdict: `AC`, `WA`, `TLE`, `MLE`, `RE`, `CE`, `SE`.

### 2. Cổng worker (TCP)

#### 2.1. Kết nối và framing

- Worker là client TCP, chủ động kết nối tới cổng worker của server. Server không bao giờ mở kết nối tới worker.
- Mỗi message là một object JSON nằm trên đúng một dòng, kết thúc bằng `\n`. Xuống dòng bên trong chuỗi phải được escape theo JSON, nên trong message không có ký tự `\n` thô nào khác.
- Bên gửi để ký tự non-ASCII ở dạng UTF-8 thô, không escape thành `\uXXXX`; giới hạn kích thước ở mục 2.2 được tính theo cách viết này. Bên nhận phải chấp nhận cả hai dạng.
- Bên nhận phải có buffer: một lần `recv()` có thể trả về nửa message hoặc nhiều message dính nhau. Chỉ parse khi đã thấy `\n`.
- Mọi message có field `type` (chuỗi in hoa).

#### 2.2. Giới hạn kích thước theo chiều

| Chiều | Độ dài tối đa của một dòng |
|-------|----------------------------|
| worker → server | 1 MB |
| server → worker | 16 MB (`JOB` mang theo cả bộ test) |

- Độ dài một dòng tính theo byte UTF-8, gồm cả ký tự `\n` kết thúc.
- Bên nhận kiểm tra độ dài ngay trong lúc đọc: buffer đã vượt giới hạn mà chưa thấy `\n` thì coi là dòng quá dài, không chờ đọc hết dòng.
- Server nhận dòng quá dài: gửi `ERROR` mã `LINE_TOO_LONG` rồi đóng kết nối.
- Worker nhận dòng quá dài từ server: đóng socket rồi kết nối lại (mục 2.6).
- Worker tự cắt các trường văn bản trước khi gửi (xem `RESULT`), nên một `RESULT` hợp lệ luôn nhỏ hơn nhiều so với 1 MB.
- Server bảo đảm dòng `JOB` không bao giờ vượt 16 MB: tổng test mỗi đề tối đa 8 MB, code nộp tối đa 64 KB, và đề làm dòng `JOB` vượt giới hạn bị từ chối ngay từ lúc nạp hoặc upload.

#### 2.3. Khung chung và xử lý lỗi

- `req_id`: chỉ `REGISTER` là message hỏi–đáp. Worker đặt `req_id` (chuỗi bất kỳ), server trả `REGISTER_OK` hoặc `ERROR` mang đúng `req_id` đó.
- `ERROR` gồm `code` (mã máy đọc) và `message` (mô tả cho người đọc, không dùng để rẽ nhánh). Danh sách mã ở mục 5.
- Nguyên tắc chung: khi trạng thái giữa hai bên không còn nhất quán, kết nối bị đóng. Mọi trường hợp phục hồi đi qua hai luồng sẵn có: server giao lại bài (mục 2.6) và worker kết nối lại (mục 2.6).
- Mọi `ERROR` server gửi cho worker là cố gắng hết sức (best-effort): khi đường gửi tới worker đang bận (ví dụ đang gửi một `JOB` lớn) hoặc bị lỗi, `ERROR` có thể bị bỏ. Điều được bảo đảm là hành động đi kèm (đóng hay giữ kết nối, giao lại bài), không phải việc worker nhận được `ERROR`. Worker không được dựa vào việc nhận `ERROR`; việc kết nối có bị đóng hay không mới là tín hiệu.

Server xử lý message lỗi theo trạng thái kết nối:

| Tình huống | Trước `REGISTER` thành công | Sau `REGISTER` thành công |
|------------|-----------------------------|---------------------------|
| JSON hỏng (không parse được, không phải object, UTF-8 sai) | `ERROR` `BAD_JSON` rồi đóng | `ERROR` `BAD_JSON`, giữ kết nối |
| Thiếu `type` hoặc `type` không phải chuỗi | (thuộc dòng `REGISTER_REJECTED`) | `ERROR` `BAD_JSON`, giữ kết nối |
| Message đầu tiên không phải `REGISTER`; `REGISTER` thiếu field hoặc sai kiểu; thiếu hoặc sai `secret` | `ERROR` `REGISTER_REJECTED` rồi đóng (một mã chung cho mọi trường hợp) | — |
| `protocol_version` không khớp (và `secret` đúng) | `ERROR` `UNSUPPORTED_VERSION` rồi đóng | — |
| Không gửi `REGISTER` hợp lệ trong 10 giây kể từ khi kết nối | `ERROR` `REGISTER_TIMEOUT` rồi đóng | — |
| `type` lạ, hoặc `type` có trong bảng message nhưng sai chiều (ví dụ worker gửi `PING`, `JOB`, `REGISTER_OK`) | (thuộc dòng `REGISTER_REJECTED`) | `ERROR` `UNKNOWN_TYPE`, giữ kết nối |
| `PONG` hoặc `PROGRESS` thiếu field hoặc sai kiểu | (thuộc dòng `REGISTER_REJECTED`) | `ERROR` `BAD_MESSAGE`, bỏ message, giữ kết nối |
| `PROGRESS` khớp `JOB` nhưng `tests_done` sai thứ tự, hoặc `tests_total` khác số phần tử của `tests` (mục `PROGRESS`) | (thuộc dòng `REGISTER_REJECTED`) | `ERROR` `BAD_MESSAGE`, bỏ message, giữ kết nối |
| `RESULT` thiếu field, sai kiểu hoặc có số nguyên ngoài khoảng (mục `RESULT`) | (thuộc dòng `REGISTER_REJECTED`) | `ERROR` `BAD_MESSAGE` rồi đóng; bài đang giữ được giao lại |
| Gửi `REGISTER` lần nữa | — | `ERROR` `ALREADY_REGISTERED` rồi đóng; bài đang giữ (nếu có) được giao lại |
| `PROGRESS` không khớp `submission_id`/`attempt_id` của `JOB` đang giao cho kết nối này | — | Bị bỏ, server ghi log, không gửi `ERROR` |
| `RESULT` không khớp `JOB` đang giao cho kết nối này, kể cả khi kết nối không giữ `JOB` nào | — | `ERROR` `UNEXPECTED_RESULT` rồi đóng; bài đang giữ (nếu có) được giao lại |
| `RESULT` khớp `JOB` nhưng `failed_test` ngoài khoảng `1..tests_total`, hoặc `failed_is_sample` không bằng cờ `is_sample` của test thứ `failed_test` trong lần giao đó | — | `ERROR` `BAD_MESSAGE` rồi đóng; bài đang giữ được giao lại |
| `ERROR` do worker gửi | (thuộc dòng `REGISTER_REJECTED`) | Server chỉ ghi log |
| Dòng quá dài | `ERROR` `LINE_TOO_LONG` rồi đóng | `ERROR` `LINE_TOO_LONG` rồi đóng; bài đang giữ (nếu có) được giao lại |

Ý nghĩa đầy đủ của từng mã ở mục 5.

Worker xử lý lỗi phía mình:

| Tình huống | Worker làm gì |
|------------|---------------|
| Nhận dòng quá dài, JSON hỏng hoặc message sai định dạng từ server | Đóng socket rồi kết nối lại |
| Nhận message có `type` mà worker không biết (kể cả `type` đúng tên nhưng sai chiều) | Đóng socket rồi kết nối lại |
| Nhận `JOB` khi đang chấm một `JOB` khác | Đóng kết nối rồi kết nối lại; server giao lại cả hai bài |
| Muốn báo lỗi cho server | Được gửi `ERROR`; server chỉ ghi log, không đổi trạng thái |

Hệ quả tương thích: thêm một loại message server → worker mới sẽ làm mọi worker cũ chưa biết loại đó ngắt kết nối mỗi khi nhận nó. Muốn thêm, phải cập nhật worker trước server, hoặc tăng `protocol_version` (khi đó server từ chối worker khác phiên bản bằng `UNSUPPORTED_VERSION`, nên worker cũ bị từ chối ngay lúc đăng ký thay vì bị ngắt giữa chừng).

#### 2.4. Bảng message

| `type` | Chiều | Mục đích |
|--------|-------|----------|
| `REGISTER` | worker → server | Đăng ký, xác thực bằng shared secret |
| `REGISTER_OK` | server → worker | Chấp nhận đăng ký, cấp `worker_id` |
| `ERROR` | hai chiều | Báo lỗi (mã + mô tả). Worker → server: server chỉ ghi log |
| `PING` | server → worker | Kiểm tra kết nối, mang `seq` |
| `PONG` | worker → server | Trả lời `PING`, mang lại đúng `seq` |
| `JOB` | server → worker | Giao một bài nộp kèm bộ test |
| `PROGRESS` | worker → server | Báo tiến độ sau mỗi test |
| `RESULT` | worker → server | Kết quả cuối của một lần giao |

#### 2.5. Chi tiết từng message

Tên và kiểu field của message worker đã chốt (khóa bằng test ở tag `m2-tests-locked`), trừ `se_detail` và giá trị `OTHER_SIGNAL` của `re_kind` chưa có test khóa.

##### `REGISTER` (worker → server)

| Field | Kiểu | Bắt buộc | Ràng buộc |
|-------|------|----------|-----------|
| `type` | string | có | `"REGISTER"` |
| `req_id` | string | có | Do worker đặt |
| `name` | string | có | Tên chỉ để hiển thị, cho phép trùng giữa các worker; 1–64 ký tự |
| `secret` | string | có | Shared secret của hệ thống |
| `protocol_version` | integer | có | Phiên bản hiện tại: `1` |

Secret không được lộ cho tiến trình chạy code thí sinh, kể cả qua biến môi trường mà tiến trình đó kế thừa.

Thứ tự kiểm tra của server: định dạng và `secret` trước, `protocol_version` sau. `secret` thiếu hoặc sai thì luôn trả `REGISTER_REJECTED`, kể cả khi `protocol_version` cũng sai.

```json
{"type": "REGISTER", "req_id": "r1", "name": "w1", "secret": "3f9c…", "protocol_version": 1}
```

##### `REGISTER_OK` (server → worker)

| Field | Kiểu | Bắt buộc | Ràng buộc |
|-------|------|----------|-----------|
| `type` | string | có | `"REGISTER_OK"` |
| `req_id` | string | có | Bằng `req_id` của `REGISTER` |
| `worker_id` | integer | có | Server cấp riêng cho kết nối này. Kết nối lại thì nhận `worker_id` mới |

```json
{"type": "REGISTER_OK", "req_id": "r1", "worker_id": 3}
```

##### `ERROR` (hai chiều)

| Field | Kiểu | Bắt buộc | Ràng buộc |
|-------|------|----------|-----------|
| `type` | string | có | `"ERROR"` |
| `code` | string | có | Một mã trong mục 5 |
| `message` | string | có | Mô tả cho người đọc |
| `req_id` | string | không | Có khi server trả lời `REGISTER` |

```json
{"type": "ERROR", "code": "UNSUPPORTED_VERSION", "message": "server expects protocol_version 1", "req_id": "r1"}
```

##### `PING` (server → worker) và `PONG` (worker → server)

| Field | Kiểu | Bắt buộc | Ràng buộc |
|-------|------|----------|-----------|
| `type` | string | có | `"PING"` hoặc `"PONG"` |
| `seq` | integer | có | `PING`: tăng dần trong một kết nối. `PONG`: bằng `seq` của `PING` đang trả lời |

Worker trả `PONG` ngay khi nhận `PING`, kể cả khi đang chấm bài. Server chỉ kiểm `seq` của `PONG` là số nguyên; không so nó với `seq` của `PING` nào, nên `PONG` sai `seq` không bị `ERROR` và vẫn được tính là worker còn sống.

```json
{"type": "PING", "seq": 41}
{"type": "PONG", "seq": 41}
```

##### `JOB` (server → worker)

| Field | Kiểu | Bắt buộc | Ràng buộc |
|-------|------|----------|-----------|
| `type` | string | có | `"JOB"` |
| `submission_id` | integer | có | |
| `attempt_id` | integer | có | Lần giao này; mỗi lần giao lại có `attempt_id` lớn hơn |
| `language` | string | có | `python`, `cpp` hoặc `java` |
| `code` | string | có | Mã nguồn, tối đa 64 KB (byte UTF-8) |
| `time_limit_ms` | integer | có | Giới hạn thời gian gốc của đề cho mỗi test; worker tự áp hệ số thời gian theo ngôn ngữ |
| `memory_limit_kib` | integer | có | Giới hạn bộ nhớ của đề |
| `tests` | array | có | Danh sách test, xem bên dưới |

Mỗi phần tử của `tests`:

| Field | Kiểu | Bắt buộc | Ràng buộc |
|-------|------|----------|-----------|
| `input` | string | có | Dữ liệu vào |
| `output` | string | có | Đáp án |
| `is_sample` | boolean | có | `true` nếu là test mẫu (công khai) |

- Server sắp test mẫu lên trước, giữ nguyên thứ tự tương đối giữa các test mẫu và giữa các test ẩn.
- Worker chạy đúng thứ tự trong `tests`. Số thứ tự test là vị trí trong mảng, bắt đầu từ 1.

```json
{"type": "JOB", "submission_id": 42, "attempt_id": 7, "language": "python", "code": "print(sum(map(int, input().split())))\n", "time_limit_ms": 1000, "memory_limit_kib": 262144, "tests": [{"input": "1 2\n", "output": "3\n", "is_sample": true}, {"input": "5 7\n", "output": "12\n", "is_sample": false}]}
```

##### `PROGRESS` (worker → server)

| Field | Kiểu | Bắt buộc | Ràng buộc |
|-------|------|----------|-----------|
| `type` | string | có | `"PROGRESS"` |
| `submission_id` | integer | có | Của `JOB` đang chấm |
| `attempt_id` | integer | có | Của `JOB` đang chấm |
| `tests_done` | integer | có | Số test đã chạy xong |
| `tests_total` | integer | có | Số phần tử của `tests` |

- Bắt buộc gửi `PROGRESS` với `tests_done = 0` ngay khi biên dịch xong. Ngôn ngữ không biên dịch vẫn gửi ngay khi bắt đầu chấm, không có ngoại lệ. Biên dịch lỗi thì không gửi `tests_done = 0`, gửi thẳng `RESULT` với `CE` trong cửa sổ biên dịch (xem bảng dưới).
- Sau đó gửi một `PROGRESS` sau mỗi test, không giới hạn tần suất.

Hạn chót từng bước: mỗi cửa sổ bắt đầu từ mốc gần nhất trong hai mốc: lúc server gửi xong `JOB` (thời gian gửi không tính), hoặc lúc nhận `PROGRESS` khớp `JOB` gần nhất.

| Cửa sổ | Chạy từ | Tới | Hạn |
|--------|---------|-----|-----|
| Biên dịch | Gửi xong `JOB` | `PROGRESS` với `tests_done = 0`, hoặc `RESULT` (khi `CE`) | `COMPILE_TIMEOUT_MAX + SLACK` |
| Test thứ i | `PROGRESS` với `tests_done = i−1` | `PROGRESS` với `tests_done = i`, hoặc `RESULT` | `time_limit_ms × MAX_TIME_FACTOR + PER_TEST_OVERHEAD` |
| Chờ `RESULT` | `PROGRESS` với `tests_done = tests_total` | `RESULT` | Như cửa sổ test thứ i: `time_limit_ms × MAX_TIME_FACTOR + PER_TEST_OVERHEAD` |

- Quá hạn, kể cả khi chưa nhận được `PROGRESS` với `tests_done = 0` trong cửa sổ biên dịch, thì server coi worker đã mất, đóng kết nối và giao lại bài (mục 2.6); lần giao đó vẫn tính vào số lần giao tối đa. Trước khi đóng, server cố gửi `ERROR` `DEADLINE_EXCEEDED` (best-effort: gửi không được thì vẫn đóng).
- Chỉ `PROGRESS` khớp `JOB` mà `tests_done` bằng giá trị của `PROGRESS` khớp trước đó cộng 1 (bắt đầu từ 0, và `0 ≤ tests_done ≤ tests_total`) mới làm mới hạn.
- `PROGRESS` không khớp `JOB` thì bị bỏ, không làm mới hạn. `PROGRESS` khớp `JOB` nhưng `tests_done` sai thứ tự (lặp lại, nhảy cóc, vượt `tests_total`), hoặc `tests_total` khác số phần tử của `tests`, bị coi là sai định dạng: server gửi `ERROR` `BAD_MESSAGE`, bỏ message, không làm mới hạn, giữ kết nối.
- Giá trị chốt tạm, đo lại trên máy demo (đã đo ở spike): `COMPILE_TIMEOUT_MAX` = 20 giây (xem lại sau khi đo trên máy khác), `SLACK` = 5 giây, `MAX_TIME_FACTOR` = 5, `PER_TEST_OVERHEAD` = 3 giây (trùm thời gian khởi động JVM, đọc và so output). Hệ số thời gian theo ngôn ngữ: xem mục "Lưu ý cho client" ở `RESULT`.
- Ràng buộc với worker: mọi hệ số thời gian theo ngôn ngữ phải ≤ `MAX_TIME_FACTOR` và mọi timeout biên dịch phải ≤ `COMPILE_TIMEOUT_MAX`. Worker kiểm tra lúc khởi động; vi phạm thì thoát với mã ≠ 0 kèm log.

```json
{"type": "PROGRESS", "submission_id": 42, "attempt_id": 7, "tests_done": 1, "tests_total": 2}
```

##### `RESULT` (worker → server)

| Field | Kiểu | Bắt buộc | Ràng buộc |
|-------|------|----------|-----------|
| `type` | string | có | `"RESULT"` |
| `submission_id` | integer | có | Của `JOB` đang chấm |
| `attempt_id` | integer | có | Của `JOB` đang chấm |
| `verdict` | string | có | `AC`, `WA`, `TLE`, `MLE`, `RE`, `CE` hoặc `SE` |
| `time_ms` | integer | khi `AC`, `WA`, `TLE`, `MLE` | Wall time. Giá trị lớn nhất trên các test đã chạy, gồm cả test sai. Với `TLE`, `time_ms` ≥ giới hạn đã nhân hệ số của ngôn ngữ |
| `memory_kib` | integer | khi `AC`, `WA`, `TLE`, `MLE` | Bộ nhớ đỉnh lớn nhất trên các test đã chạy, gồm cả test sai. Với `MLE`, giá trị này có thể thấp hơn nhiều so với giới hạn (xem lưu ý dưới bảng) |
| `failed_test` | integer | khi `WA`, `TLE`, `MLE`, `RE` | Số thứ tự test sai đầu tiên |
| `failed_is_sample` | boolean | khi `WA`, `TLE`, `MLE`, `RE` | Test sai là test mẫu hay ẩn |
| `re_kind` | string | khi `RE` | `SIGSEGV`, `SIGFPE`, `SIGABRT`, `SIGKILL`, `OTHER_SIGNAL` (signal ngoài danh sách) hoặc `EXIT_NONZERO` |
| `ce_message` | string | khi `CE` | Lỗi biên dịch rút gọn: tối đa 50 dòng và 4 KB (giới hạn nào chạm trước), đường dẫn tạm thay bằng `Main.cpp`/`Main.java`, cắt an toàn UTF-8 |
| `sample_stdout` | string | khi `failed_is_sample = true` | stdout của chương trình trên test mẫu bị sai, tối đa 4 KB. Gửi với mọi verdict có `failed_test`; `AC` không gửi |
| `se_detail` | string | không, chỉ khi `SE` | Chi tiết lỗi máy chấm, server chỉ ghi log, không chuyển cho client. Không chứa input/output của test |

Server kiểm tra `RESULT` theo bảng dưới. Cột "Bắt buộc" là các field phải có và đúng kiểu; cột "Bị bỏ qua" là các field server không kiểm tra, không lưu, nên có thể vắng hoặc `null` (worker thật không gửi chúng); ngoại lệ duy nhất là `se_detail` của `SE`.

| `verdict` | Bắt buộc | Bị bỏ qua |
|-----------|----------|-----------|
| `AC` | `time_ms`, `memory_kib` | `failed_test`, `failed_is_sample`, `re_kind`, `ce_message`, `sample_stdout` |
| `WA`, `TLE`, `MLE` | `time_ms`, `memory_kib`, `failed_test`, `failed_is_sample`, và `sample_stdout` khi `failed_is_sample = true` | `re_kind`, `ce_message`, và `sample_stdout` khi `failed_is_sample = false` |
| `RE` | `failed_test`, `failed_is_sample`, `re_kind`, và `sample_stdout` khi `failed_is_sample = true` | `time_ms`, `memory_kib`, `ce_message`, và `sample_stdout` khi `failed_is_sample = false` |
| `CE` | `ce_message` | `time_ms`, `memory_kib`, `failed_test`, `failed_is_sample`, `re_kind`, `sample_stdout` |
| `SE` | (không có) | mọi field khác, trừ `se_detail`: tùy chọn, nhưng nếu có mặt thì phải là chuỗi (`null` cũng bị coi là sai kiểu) |

Ngoài ra `submission_id`, `attempt_id` và `verdict` luôn bắt buộc.

Quy tắc kiểu và khoảng, chỉ áp dụng cho field đang ở cột "Bắt buộc" của verdict đó:

- Field số nguyên (`submission_id`, `attempt_id`, `time_ms`, `memory_kib`, `failed_test`) phải là số nguyên JSON (không phải số thực, chuỗi, `true`/`false` hay `null`) trong khoảng 0..2^31−1; riêng `failed_test` tối thiểu là 1.
- `failed_is_sample` phải là boolean. `ce_message`, `sample_stdout` và `se_detail` (khi có) phải là chuỗi. `re_kind` phải là một giá trị trong bảng trên. `verdict` phải là một trong bảy verdict.
- Vi phạm một quy tắc nào ở trên thì `RESULT` bị coi là sai định dạng: server gửi `ERROR` `BAD_MESSAGE` rồi đóng kết nối, và bài đang giữ được giao lại (mục 2.3, 2.6). Server kiểm tra định dạng trước, rồi mới kiểm tra `RESULT` có khớp `JOB` không.

Sau khi `RESULT` khớp `JOB`, server còn kiểm tra: `failed_test` nằm trong `1..tests_total` của lần giao đó, và `failed_is_sample` bằng cờ `is_sample` của test thứ `failed_test` trong `JOB` đã gửi. Vi phạm thì server gửi `ERROR` `BAD_MESSAGE` rồi đóng kết nối, và bài đang giữ được giao lại; verdict của `RESULT` đó không được ghi.

Worker không bao giờ gửi stdout/stderr của test ẩn, và không gửi giá trị mã thoát cụ thể của chương trình.

Chương trình in ra stdout vượt 16 MB bị dừng và nhận `WA`.

`SE` do worker báo trong `RESULT` là verdict cuối ngay: server không giao lại bài (trừ khi đang rejudge: verdict cũ giữ nguyên, bài chỉ được đánh dấu chấm lại lỗi). Chỉ `SE` do mất kết nối hoặc quá hạn (mục 2.6) đi qua luồng giao lại và đếm tối đa 3 lần giao.

Lưu ý cho client:

- Với Python và C++, giới hạn bộ nhớ chặn theo không gian địa chỉ ảo, nên một lần cấp phát lớn có thể thất bại khi bộ nhớ thực còn thấp; Java bị giới hạn theo heap. Vì vậy UI hiển thị `MLE` là "vượt giới hạn bộ nhớ", không hiện con số `memory_kib`.
- `time_ms` so với giới hạn **đã nhân hệ số ngôn ngữ**, không phải giới hạn gốc của đề. Hệ số thời gian theo ngôn ngữ (chốt tạm, đo lại trên máy demo):

  | Ngôn ngữ | Hệ số |
  |----------|-------|
  | `cpp` | 1 |
  | `java` | 2 |
  | `python` | 5 |

  Ví dụ: `time_limit_ms` = 1000 với `python` thì giới hạn thực tế là 5000 ms.

  Hạn chế: hệ số không bù đủ cho Python ở bài thiên về CPU (đo được khoảng 22 lần so với C++ ở vòng lặp lồng, với hệ số 5), nên giới hạn thời gian của đề phải đủ cho lời giải Python chuẩn.

```json
{"type": "RESULT", "submission_id": 42, "attempt_id": 7, "verdict": "WA", "time_ms": 31, "memory_kib": 9216, "failed_test": 2, "failed_is_sample": false}
```

#### 2.6. Luồng hoạt động

##### Đăng ký

```
worker                                server
  │── REGISTER (req_id, name, secret, protocol_version) ─►│
  │◄─ REGISTER_OK (req_id, worker_id) ─────────────────────│   hoặc  ERROR rồi đóng kết nối
```

- Kết nối mới phải gửi `REGISTER` hợp lệ trong 10 giây; quá thì server gửi `ERROR` `REGISTER_TIMEOUT` rồi đóng.
- Server nhận tối đa 16 kết nối chưa đăng ký cùng lúc. Kết nối vượt số đó bị đóng ngay, không đọc message nào.
- Server chỉ bắt đầu gửi `PING` và `JOB` sau `REGISTER_OK`.

##### Chấm một bài

```
worker                                server
  │◄─ JOB (submission_id=42, attempt_id=7, tests[...]) ────│
  │── PROGRESS (tests_done=0, tests_total=2) ─────────────►│   biên dịch xong
  │── PROGRESS (tests_done=1, tests_total=2) ─────────────►│
  │── PROGRESS (tests_done=2, tests_total=2) ─────────────►│
  │── RESULT (verdict=AC, ...) ───────────────────────────►│
```

- Mỗi worker giữ tối đa một `JOB` tại một thời điểm. Server không gửi `JOB` mới cho worker đó trước khi nhận `RESULT` của `JOB` hiện tại.
- Worker nhận `JOB` khi đang bận (trái với bảo đảm trên): worker đóng kết nối rồi kết nối lại; server giao lại cả hai bài.
- Worker chạy test theo thứ tự và dừng ở test sai đầu tiên. Test mẫu sai thì không chạy test ẩn nào.
- `PROGRESS` và `RESULT` mang đúng `submission_id` và `attempt_id` của `JOB`.

##### Giữ kết nối

- Server gửi `PING` mỗi 5 giây; worker trả `PONG` cùng `seq` ngay lập tức.
- Server coi worker đã mất kết nối khi socket đóng, hoặc khi quá 15 giây không nhận được message nào từ worker. Trong lúc server đang gửi một `JOB` lớn cho worker, server có thể không gửi `PING`, và không tính khoảng thời gian đó là worker im lặng.
- Một lần gửi từ server tới worker không hoàn tất trong 30 giây (ví dụ worker ngừng đọc socket) thì server đóng kết nối và coi worker đã mất.
- Worker coi server đã mất khi socket đóng, hoặc khi quá 15 giây không nhận được byte nào. Mọi byte nhận được đều tính là còn sống, kể cả khi đang nhận dở một `JOB` lớn. Khi đó worker đóng socket, kết nối lại và `REGISTER` lại từ đầu.
- Khoảng chờ giữa các lần kết nối lại tăng theo lũy thừa: 1 → 2 → 4 … giây, tối đa 10 giây, cộng một khoảng ngẫu nhiên (jitter). Sau mỗi `REGISTER_OK` thành công, khoảng chờ đặt lại về 1 giây.
- Cấu hình demo có thể hạ chu kỳ và timeout xuống 2 giây / 6 giây.

##### Giao lại khi worker mất kết nối

```
server ── JOB (attempt_id=7) ──► worker A      (kết nối của A bị đóng khi đang chấm)
server: bài 42 được đưa lại hàng đợi
server ── JOB (attempt_id=8) ──► worker rảnh tiếp theo (có thể là A sau khi kết nối lại)
worker ── RESULT (attempt_id=8) ──► server: được ghi nhận
```

Server coi worker đã mất và đóng kết nối của nó khi:

- socket đóng, hoặc quá 15 giây không nhận được message nào (mục "Giữ kết nối");
- một lần gửi tới worker không hoàn tất trong 30 giây;
- quá hạn chót của một bước (mục `PROGRESS`);
- `RESULT` sai định dạng, ngoài khoảng, vi phạm kiểm tra ngữ nghĩa, hoặc không khớp `JOB` của kết nối (mục 2.3);
- worker gửi `REGISTER` lần nữa, hoặc gửi dòng quá dài sau khi đã đăng ký (mục 2.3);
- worker tự đóng vì nhận `JOB` khi đang bận.

Khi đó:

- Bài worker đang giữ được đưa lại hàng đợi và giao cho worker rảnh tiếp theo (có thể chính worker đó sau khi kết nối lại), với `attempt_id` mới lớn hơn.
- Mỗi bài được giao tối đa 3 lần cho một lần chấm (mỗi lần rejudge đếm lại từ đầu). Mọi lần giao bị cắt ngang đều tính vào 3 lần, bất kể lý do: mất kết nối, quá hạn chót, `RESULT` sai định dạng, ngoài khoảng, không khớp `JOB` hay vi phạm kiểm tra ngữ nghĩa. Lần giao thứ 3 bị cắt ngang thì bài nhận verdict `SE`, không giao lần thứ 4; riêng bài đang được chấm lại (rejudge) thì verdict cũ không bị ghi đè, bài chỉ được đánh dấu chấm lại lỗi (`rejudge_failed`). Cơ chế rejudge được mô tả ở phần admin (PR sau). Vì vậy một worker lỗi tất định không làm một bài bị giao lại vô hạn.
- Một lần giao được tính khi server ghi nhận việc giao vào cơ sở dữ liệu, trước khi gửi `JOB`. Worker mất kết nối đúng giữa lúc ghi nhận và lúc gửi `JOB` vẫn bị tính một lần. Hệ quả: trường hợp xấu nhất, một bài nhận `SE` sau khi server thực sự gửi `JOB` ít hơn 3 lần, và chỉ khi worker mất kết nối đúng khoảng đó.
- Riêng khi server nhận một `RESULT` hợp lệ nhưng không ghi được kết quả vào cơ sở dữ liệu: kết nối được giữ, nhưng bài vẫn được giao lại và lần giao đó tính vào 3 lần.
- Worker mất kết nối khi đang chấm thì hủy bài đó ngay (kill cả nhóm tiến trình), dọn thư mục tạm, và không gửi lại `RESULT` của bài đó qua kết nối mới.
- `PROGRESS`/`RESULT` mang `attempt_id` cũ chỉ là trường hợp phòng thủ: kết nối cũ đã đóng nên bình thường chúng không thể tới server. Nếu vẫn tới, server xử lý theo bảng ở mục 2.3.
- Server khởi động lại: worker tự kết nối lại; các bài đang chờ được giao lại. Bài đang chấm lúc server tắt cũng được giao lại, và lần giao bị ngắt đó không tính vào 3 lần giao.

##### Hạn chế đã biết của cổng worker

- Race khi khởi động lại: mỗi bài đang chấm dở được hoàn lại một lần giao. Nếu một worker vừa mất kết nối mà server chưa kịp ghi lại việc mất đó thì server tắt, lần giao bị cắt thật cũng được hoàn lại. Hệ quả: một bài có thể được giao thêm tối đa 1 lần mỗi lần khởi động lại, ngoài giới hạn 3 lần. Cửa sổ này rất hẹp; chưa kiểm chứng ở runtime.

### 3. Cổng web: REST (JSON), tập tối thiểu cho M2

Phần này mô tả mười endpoint đủ để đăng nhập, xem đề, nộp bài, xem kết quả và xem bảng xếp hạng. WebSocket `/ws` ở mục 4. Chưa có trong phần này, sẽ thêm ở PR sau: endpoint admin.

#### 3.1. Bảng tóm tắt

| Endpoint | Protocol | Method | Input | Output |
|----------|----------|--------|-------|--------|
| `/api/health` | HTTP/1.1 | GET | — | 200 `{"status":"ok"}`, không cần phiên |
| `/api/login` | HTTP/1.1 | POST | `{"username","password"}` | 200 `{"user":{"id","username","role"}}` + cookie `jh_session` |
| `/api/logout` | HTTP/1.1 | POST | `{}` | 204, xóa cookie |
| `/api/me` | HTTP/1.1 | GET | — | 200 `{"user":{"id","username","role"}}` |
| `/api/problems` | HTTP/1.1 | GET | — | 200 `{"problems":[{"id","title","time_limit_ms","memory_limit_kib","hidden"}]}` |
| `/api/problems/{id}` | HTTP/1.1 | GET | — | 200 đề đầy đủ kèm test mẫu |
| `/api/submissions` | HTTP/1.1 | POST | `{"problem_id","language","code","client_token"}` | 201 (hoặc 200 khi trùng token) `{"submission_id","state","received_at"}` |
| `/api/submissions` | HTTP/1.1 | GET | query `limit`, `before_id` (tùy chọn) | 200 `{"submissions":[…],"next_before_id"}` |
| `/api/submissions/{id}` | HTTP/1.1 | GET | — | 200 một bài nộp của chính người gọi, kèm chi tiết |
| `/api/scoreboard` | HTTP/1.1 | GET | — | 200 `{"version","generated_at","rows":[{"rank","username","solved","wrong","last_ac_at"}]}` |

Mọi lỗi dùng chung một định dạng và một bảng mã (mục 3.5).

#### 3.2. Quy ước chung

**Định dạng.**

- Request có body và response đều là JSON UTF-8. Response có `Content-Type: application/json; charset=utf-8` và `Cache-Control: no-store`.
- Mọi `POST` phải có `Content-Type: application/json` (cho phép tham số `charset`). Thiếu hoặc khác thì trả 415. Body phải là một object JSON, kể cả khi endpoint không cần field nào (`/api/logout` gửi `{}`).
- Field thừa trong request bị bỏ qua. Server không bao giờ dùng `user_id`, `role` hay field tương tự do client gửi; danh tính và quyền chỉ lấy từ phiên.
- Quy ước ở mục 1 áp dụng: thời điểm ISO 8601 UTC có mili giây, thời gian `_ms`, bộ nhớ `_kib`, ngôn ngữ `python`/`cpp`/`java`, verdict `AC`/`WA`/`TLE`/`MLE`/`RE`/`CE`/`SE`.
- Mã đề (`problem_id`) là chuỗi 1–64 ký tự trong `[A-Za-z0-9_-]`; `submission_id` là số nguyên dương.
- Mọi `{id}` trên đường dẫn không đúng định dạng được coi như không tồn tại (404, không phải 400).

**Phiên và cookie `jh_session`.**

- `POST /api/login` thành công thì server tạo phiên mới và đặt cookie: `jh_session=<giá trị ngẫu nhiên không đoán được>; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800`. Không có `Secure` vì hệ thống không có HTTPS.
- Phiên hết hạn đúng 7 ngày kể từ lúc đăng nhập (tuyệt đối, dùng hoạt động cũng không gia hạn). Phiên lưu ở server nên khởi động lại server không làm mất phiên.
- Một người có thể có nhiều phiên cùng lúc (nhiều tab, nhiều máy). `POST /api/logout` chỉ hủy phiên đang dùng.
- Đăng nhập khi request đã mang một phiên còn hiệu lực thì phiên cũ bị hủy và cấp phiên mới.
- Chỉ `POST /api/login`, `GET /api/health` (và file tĩnh) không cần phiên. Mọi endpoint khác: chưa đăng nhập, cookie sai hoặc phiên hết hạn thì 401 `UNAUTHENTICATED`; kể cả `POST /api/logout`, client coi 401 ở đây là đã đăng xuất.
- Đã đăng nhập mà không đủ quyền thì 403 `FORBIDDEN`. Endpoint trong phần này không trả 403 (chưa có endpoint admin).

**Định dạng lỗi.** Mọi response lỗi của `/api/*`, kể cả đường dẫn không tồn tại (404), sai method (405), body quá lớn (413) và lỗi server (500), có đúng dạng:

```json
{"error": {"code": "INVALID_REQUEST", "message": "field 'language' is invalid"}}
```

- `code` là mã máy đọc (mục 3.5), client chỉ rẽ nhánh theo `code` và HTTP status. `message` chỉ để người đọc.
- `message` không bao giờ chứa lại giá trị client đã gửi (mật khẩu, mã nguồn, token…), chỉ có thể nêu tên field.
- Validation sai luôn là 400, không có 422.
- Server không ghi mật khẩu hay body của `POST /api/login` vào log.

**Giới hạn body (413).** Giới hạn tính theo byte của body như nhận từ mạng. Quá giới hạn thì 413 `BODY_TOO_LARGE`; server có thể đóng kết nối sau khi trả lời.

| Endpoint | Body tối đa |
|----------|-------------|
| `POST /api/submissions` | 512 KB |
| Các endpoint khác của phần này | 128 KB |

Riêng mã nguồn nộp bị giới hạn 64 KB tính theo byte UTF-8 sau khi parse JSON, quá thì 413 `CODE_TOO_LARGE`. Hai giới hạn khác nhau vì 64 KB mã sau khi escape JSON có thể vượt 128 KB.

**Rate limit (429).**

- Mỗi người được nộp tối đa 10 bài trong bất kỳ khoảng 60 giây nào (cửa sổ trượt, tính cả admin). Vượt thì 429 `RATE_LIMITED` kèm header `Retry-After` là số giây nguyên ≥ 1 cần chờ.
- Chỉ bài được chấp nhận mới tính vào giới hạn. Request lỗi (400, 404, 413, 415) không tính. Request trùng `client_token` (mục 3.3) không tính và không bị chặn.
- Các endpoint khác trong phần này không có rate limit.
- `POST /api/login`: tối đa 10 lần thử trong bất kỳ khoảng 60 giây nào cho mỗi `username` (cửa sổ trượt). Vượt thì 429 `RATE_LIMITED` kèm `Retry-After`, kể cả khi mật khẩu đúng. Mọi lần thử hợp lệ về định dạng đều tính (đúng, sai, hay username không tồn tại, để không lộ username nào có thật); request 400 hoặc 413 không tính. Server kiểm tra giới hạn trước khi kiểm tra mật khẩu. Ngoài ra tối đa 8 đăng nhập được xử lý cùng lúc trên toàn server (băm mật khẩu tốn bộ nhớ và thread); lượt thứ 9 trở đi trả ngay 429 `RATE_LIMITED` kèm `Retry-After: 1`, không chờ, và không tính vào giới hạn theo `username`. Hạn chế: người khác có thể cố ý khóa tạm một username bằng cách thử liên tục, nhưng chỉ tối đa mỗi lần 60 giây và không ảnh hưởng username khác.

**Thứ tự kiểm tra của `POST /api/submissions`** (lỗi đầu tiên gặp được trả về): phiên (401) → `Content-Type` (415) → kích thước body (413 `BODY_TOO_LARGE`) → JSON và field (400) → kích thước mã (413 `CODE_TOO_LARGE`) → trùng `client_token` (200) → đề tồn tại và hiện với người gọi (404) → rate limit (429) → chấp nhận (201).

#### 3.3. Chi tiết từng endpoint

##### `GET /api/health`

- Không cần phiên; dùng cho healthcheck của `docker compose`. Luôn 200 `{"status": "ok"}` khi server đã khởi động xong. Cố ý chỉ trả trạng thái tối thiểu: không có phiên bản, số worker hay đường dẫn.

##### `POST /api/login`

| Field | Kiểu | Bắt buộc | Ràng buộc |
|-------|------|----------|-----------|
| `username` | string | có | 1–64 ký tự; so khớp chính xác, phân biệt hoa thường |
| `password` | string | có | Tối đa 128 byte UTF-8 (độ dài tối thiểu chỉ áp khi admin đặt mật khẩu, PR sau) |

- Thành công: 200, header `Set-Cookie` như mục 3.2, body `{"user": {"id": 5, "username": "alice", "role": "user"}}`. `role` là `user` hoặc `admin`.
- Username không tồn tại, mật khẩu sai, và mật khẩu vượt độ dài tối đa: cùng 401 `INVALID_CREDENTIALS`, cùng `message`, thời gian trả lời xấp xỉ nhau; response không cho biết trường hợp nào.
- Thiếu field, sai kiểu, username rỗng hoặc quá dài: 400 `INVALID_REQUEST`.
- Quá 10 lần thử cho cùng `username` trong 60 giây, hoặc đã có 8 đăng nhập đang xử lý cùng lúc: 429 `RATE_LIMITED` (mục 3.2).

```json
{"username": "alice", "password": "…"}
```

##### `POST /api/logout`

- Body `{}`. Thành công: 204, không có body, header `Set-Cookie: jh_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax`. Phiên bị hủy ngay ở server: dùng lại cookie cũ thì 401.
- Các WebSocket thuộc phiên đó bị đóng với close code 4401 (mục 4.5).

##### `GET /api/me`

- 200 `{"user": {"id": 5, "username": "alice", "role": "user"}}` (cùng dạng `user` như `login`). Chưa đăng nhập hoặc phiên hết hạn: 401. Client dùng endpoint này để biết còn phiên hay không khi tải lại trang.

##### `GET /api/problems`

- Người không phải admin chỉ thấy đề không ẩn. Admin thấy mọi đề.
- Sắp theo `id` tăng dần, không phân trang.

| Field của mỗi phần tử | Kiểu | Ghi chú |
|-----------------------|------|---------|
| `id` | string | Mã đề |
| `title` | string | |
| `time_limit_ms` | integer | Giới hạn gốc của đề cho mỗi test; giới hạn thực tế nhân hệ số ngôn ngữ (mục 2.5, `RESULT`) |
| `memory_limit_kib` | integer | |
| `hidden` | boolean | Đề đang ẩn hay không; với người không phải admin luôn là `false` |

```json
{"problems": [{"id": "sum-two", "title": "Tổng hai số", "time_limit_ms": 1000, "memory_limit_kib": 262144, "hidden": false}]}
```

##### `GET /api/problems/{id}`

- 200: các field của danh sách, thêm `statement` và `samples`.
- Đề không tồn tại và đề ẩn (với người không phải admin) trả cùng 404 `PROBLEM_NOT_FOUND`, cùng `message`, cùng định dạng, để không dò được đề ẩn.

| Field thêm | Kiểu | Ghi chú |
|------------|------|---------|
| `statement` | string | Đề bài là văn bản thường (không Markdown). Client hiển thị giữ nguyên xuống dòng, bằng cơ chế escape mặc định |
| `samples` | array | Test mẫu theo đúng thứ tự chạy; mỗi phần tử `{"input": string, "output": string}`. Không bao giờ chứa test ẩn |

```json
{"id": "sum-two", "title": "Tổng hai số", "statement": "Cho hai số nguyên…", "time_limit_ms": 1000, "memory_limit_kib": 262144, "hidden": false, "samples": [{"input": "1 2\n", "output": "3\n"}]}
```

##### `POST /api/submissions`

| Field | Kiểu | Bắt buộc | Ràng buộc |
|-------|------|----------|-----------|
| `problem_id` | string | có | Mã đề |
| `language` | string | có | `python`, `cpp` hoặc `java` |
| `code` | string | có | Không rỗng, tối đa 64 KB (byte UTF-8) |
| `client_token` | string | có | UUID dạng `8-4-4-4-12` chữ số hex, không phân biệt hoa thường |

- Thành công: **201** `{"submission_id": 42, "state": "queued", "received_at": "2026-10-24T08:15:30.123Z"}`. Bài đã được lưu và nằm trong hàng đợi; từ lúc này worker có chết thì bài vẫn được chấm (mục 2.6).
- Trùng `client_token` của chính người gọi (mỗi người một không gian token riêng): **200**, cùng dạng body, với `submission_id` và `received_at` của bài cũ và `state` hiện tại của nó; không tạo bài mới, không đưa vào hàng đợi, không tính rate limit. Nội dung mới của request lần sau (đề, ngôn ngữ, mã nguồn) bị bỏ qua hoàn toàn: server trả bài gốc, không so sánh. Client phân biệt "mới" và "trùng" bằng status 201/200.
- Thiếu field, sai kiểu, `problem_id` không đúng định dạng mục 3.2 (khác với đường dẫn, nơi id sai định dạng là 404), `language` lạ, `code` rỗng hoặc chứa surrogate lẻ, `client_token` thiếu hoặc không phải UUID: 400 `INVALID_REQUEST`.
- Đề không tồn tại, hoặc đề ẩn mà người gọi không phải admin: 404 `PROBLEM_NOT_FOUND` (giống `GET /api/problems/{id}`). Admin nộp được vào đề ẩn; bài của admin không vào bảng xếp hạng.
- Quy tắc client: giữ nguyên `client_token` khi request lỗi mạng hoặc hết thời gian chờ mà chưa nhận phản hồi; sinh token mới sau khi đã nhận `submission_id`; vô hiệu hóa nút nộp trong lúc chờ phản hồi.

```json
{"problem_id": "sum-two", "language": "python", "code": "print(sum(map(int, input().split())))\n", "client_token": "6f1c2d3e-4a5b-4c6d-8e7f-0a1b2c3d4e5f"}
```

##### Đối tượng bài nộp (dùng cho hai endpoint `GET` dưới đây)

Mọi field luôn có mặt; field không áp dụng có giá trị `null`.

| Field | Kiểu | Ghi chú |
|-------|------|---------|
| `submission_id` | integer | |
| `problem_id` | string | |
| `problem_title` | string | Hiện cả khi đề đã bị ẩn |
| `language` | string | |
| `received_at` | string | Thời điểm server nhận bài |
| `state` | string | `queued` (chờ, kể cả đang giao lại hoặc chờ chấm lại), `judging` (đang chấm), `judged` (đã có kết quả cho lần chấm gần nhất) |
| `verdict` | string hoặc null | Kết quả gần nhất; `null` cho tới khi có kết quả đầu tiên. Khi chấm lại, `verdict` giữ giá trị cũ trong lúc `state` ≠ `judged` |
| `time_ms` | integer hoặc null | Có khi `verdict` là `AC`, `WA`, `TLE`, `MLE` (nghĩa như trong `RESULT`) |
| `memory_kib` | integer hoặc null | Như `time_ms`. Với `MLE`, UI không hiện con số này (mục 2.5, `RESULT`) |
| `failed_test` | integer hoặc null | Có khi `WA`, `TLE`, `MLE`, `RE`: số thứ tự test sai đầu tiên |
| `failed_is_sample` | boolean hoặc null | Có khi `failed_test` có |
| `re_kind` | string hoặc null | Có khi `RE`: `SIGSEGV`, `SIGFPE`, `SIGABRT`, `SIGKILL`, `OTHER_SIGNAL`, `EXIT_NONZERO` |
| `rejudged_at` | string hoặc null | Lần cuối kết quả được ghi từ một lượt chấm lại |

Server không bao giờ trả cho client: input hay output của test ẩn, stdout/stderr của test ẩn, giá trị mã thoát cụ thể, và chi tiết lỗi máy chấm (`SE` chỉ có `verdict` = `SE`, client hiển thị thông báo chung).

##### `GET /api/submissions`

- Chỉ trả bài nộp của chính người gọi (kể cả admin), kể cả bài trên đề hiện đang ẩn. Sắp theo `submission_id` giảm dần (mới nhất trước). Mỗi phần tử là đối tượng bài nộp ở trên, không có `code`, `ce_message` và các field `sample_*`.
- Query: `limit` (1–200, mặc định 50), `before_id` (chỉ lấy bài có `submission_id` nhỏ hơn giá trị này). Giá trị sai kiểu hoặc ngoài khoảng (chỉ nhận chữ số ASCII; `before_id` tối đa 2^63−1), hoặc tham số xuất hiện nhiều lần: 400 `INVALID_REQUEST`. Tham số lạ bị bỏ qua.
- Response: `{"submissions": [...], "next_before_id": 17}`; `next_before_id` là `submission_id` nhỏ nhất của trang này nếu còn bài cũ hơn, ngược lại là `null`. Muốn trang kế, gọi lại với `before_id` = giá trị đó.
- Client gọi endpoint này sau khi mất kết nối để lấy kết quả bị lỡ.

```json
{"submissions": [{"submission_id": 42, "problem_id": "sum-two", "problem_title": "Tổng hai số", "language": "python", "received_at": "2026-10-24T08:15:30.123Z", "state": "judged", "verdict": "WA", "time_ms": 31, "memory_kib": 9216, "failed_test": 2, "failed_is_sample": false, "re_kind": null, "rejudged_at": null}], "next_before_id": null}
```

##### `GET /api/submissions/{id}`

- Trả đối tượng bài nộp (không bọc trong object khác) cộng các field chi tiết dưới đây.
- Bài không tồn tại và bài của người khác (kể cả khi người gọi là admin) trả cùng 404 `SUBMISSION_NOT_FOUND`, cùng `message`. Xem bài của người khác là việc của endpoint admin (PR sau).
- Có field `code`: mã nguồn của chính người gọi, đúng như đã nộp.

| Field thêm | Kiểu | Ghi chú |
|------------|------|---------|
| `code` | string | Mã nguồn đã nộp |
| `ce_message` | string hoặc null | Có khi `CE`: lỗi biên dịch đã rút gọn (mục 2.5, `RESULT`) |
| `sample_stdout` | string hoặc null | Có khi `failed_is_sample` là `true`: stdout của chương trình trên test mẫu sai, tối đa 4 KB; `AC` không có |
| `sample_input` | string hoặc null | Input của test mẫu sai |
| `sample_expected_output` | string hoặc null | Output mong đợi của test mẫu sai |

- `sample_input` và `sample_expected_output` lấy từ test mẫu số `failed_test` của đề. Chúng là `null` khi đề đang ẩn mà người gọi không phải admin, hoặc khi bộ test của đề đã đổi sau lần chấm này (không còn tra được đúng test đã chấm). `sample_stdout` vẫn có trong các trường hợp đó.
- Client theo dõi bài vừa nộp bằng cách gọi lại endpoint này, mỗi lần cách lần nhận phản hồi trước 1 giây, cho tới khi `state` là `judged`. Endpoint này không có rate limit. Khi WebSocket (PR sau) có mặt, cách này chỉ còn là đường dự phòng.

```json
{"submission_id": 42, "problem_id": "sum-two", "problem_title": "Tổng hai số", "language": "python", "received_at": "2026-10-24T08:15:30.123Z", "state": "judged", "verdict": "WA", "time_ms": 12, "memory_kib": 9216, "failed_test": 1, "failed_is_sample": true, "re_kind": null, "rejudged_at": null, "code": "print(int(input()) + int(input()))\n", "ce_message": null, "sample_stdout": "4\n", "sample_input": "1 2\n", "sample_expected_output": "3\n"}
```

##### `GET /api/scoreboard`

- Cần phiên. Mọi người đã đăng nhập, kể cả admin, thấy cùng một bảng. Thiếu phiên: 401 `UNAUTHENTICATED`.
- Trả bản đã tính gần nhất; endpoint này không bắt server tính lại. Không phân trang.

| Field | Kiểu | Ghi chú |
|-------|------|---------|
| `version` | integer | Tăng mỗi lần server tính lại bảng. Bắt đầu lại từ giá trị nhỏ khi server khởi động lại, nên chỉ so sánh được trong cùng một lần chạy của server (mục 4.7) |
| `generated_at` | string | Thời điểm tính bảng |
| `rows` | array | Đã sắp theo thứ tự hiển thị |

Mỗi phần tử của `rows`:

| Field | Kiểu | Ghi chú |
|-------|------|---------|
| `rank` | integer | Từ 1. Hòa thì cùng hạng và hạng kế tiếp bị bỏ qua (1, 2, 2, 4) |
| `username` | string | |
| `solved` | integer | Số đề đã giải |
| `wrong` | integer | Số lần sai tính điểm (luật bên dưới) |
| `last_ac_at` | string hoặc null | Thời điểm nhận bài AC đầu tiên muộn nhất trong các đề đã giải; `null` khi `solved` là 0 |

```json
{"version": 12, "generated_at": "2026-10-27T08:15:30.123Z", "rows": [{"rank": 1, "username": "alice", "solved": 3, "wrong": 2, "last_ac_at": "2026-10-27T07:58:12.045Z"}, {"rank": 2, "username": "bob", "solved": 2, "wrong": 0, "last_ac_at": "2026-10-27T07:20:01.310Z"}, {"rank": 3, "username": "carol", "solved": 0, "wrong": 0, "last_ac_at": null}]}
```

**Luật xếp hạng.**

- *Đề được tính:* chỉ đề không ẩn tại thời điểm tính. Ẩn một đề thì mọi bài trên đề đó thôi được tính; hiện lại thì được tính lại.
- *Người được xếp:* chỉ tài khoản có role `user`. Admin không bao giờ có hàng, kể cả khi đã nộp bài. Một người có hàng khi có ít nhất một bài nộp đã có verdict (mọi verdict, kể cả `CE` hay `SE`) trên một đề được tính, kể cả khi chưa AC đề nào. Bài chưa có verdict lần đầu thì chưa được tính; bài đang chấm lại vẫn được tính theo verdict cũ cho tới khi có kết quả mới.
- *AC đầu tiên* của một người trên một đề: trong các bài nộp của người đó trên đề đó có verdict hiện tại là `AC`, bài có thời điểm nhận sớm nhất (hòa thì `submission_id` nhỏ hơn). Có bài như vậy thì đề được tính là đã giải. Chấm lại làm một bài trở thành AC hoặc hết AC thì bảng tự đúng theo verdict mới.
- `wrong` là tổng, trên các đề đã giải, số bài nộp có verdict `WA`, `TLE`, `MLE` hoặc `RE` được nhận **trước** AC đầu tiên của đề đó. `CE`, `SE` và bài chưa có verdict không tính. Bài nộp sau AC đầu tiên không ảnh hưởng. Trên đề chưa giải không tính lần sai nào.
- `last_ac_at` là thời điểm nhận lớn nhất, trên các đề đã giải, của bài AC đầu tiên mỗi đề.
- *Thứ tự:* `solved` giảm dần, rồi `wrong` tăng dần, rồi `last_ac_at` tăng dần (sớm hơn xếp trước; `null` xếp sau cùng). Ba giá trị bằng nhau thì cùng `rank`; các hàng cùng hạng xếp theo `username` tăng dần theo thứ tự byte UTF-8.
- *Mốc thời gian* là lúc server nhận bài (giờ server, UTC, mili giây), không phải lúc chấm xong.
- Server tính lại bảng sau khi kết quả một bài được ghi, hoặc khi một đề được ẩn hay hiện, và gộp tối đa một lần mỗi giây. Vì vậy bảng có thể chậm vài giây so với kết quả mới nhất.

#### 3.4. Hạn chế đã biết của phần này

- Bảng xếp hạng hiện tên người dùng cho mọi người đã đăng nhập. Với hàng chục nghìn bài nộp, mỗi lần server tính lại bảng (tối đa một lần mỗi giây) có thể làm các request khác chậm đi vài chục mili giây; chưa đo dưới tải. Khi một đề bị ẩn, các AC trên đề đó biến khỏi bảng (hiện lại thì trở về). Trong lúc chấm lại dở dang, bảng mang verdict cũ.
- Không có HTTPS: mật khẩu đi dạng rõ trong mạng nội bộ.
- Cookie `SameSite=Lax` không chặn request từ origin khác cùng site (ví dụ khác cổng trên cùng máy). Lớp bảo vệ còn lại là: `POST` chỉ nhận `application/json` và không mở CORS, nên trình duyệt không cho trang khác gửi được request như vậy.
- Số test sai, thời gian và bộ nhớ trong kết quả là kênh rò thông tin rất hẹp về test ẩn; rate limit nộp bài là chặn trên của kênh này.
- Giới hạn đăng nhập (theo `username` và số lượt đồng thời) nằm trong bộ nhớ của process: khởi động lại server thì mất. Giới hạn nộp bài đếm bằng cơ sở dữ liệu nên không mất.

#### 3.5. Mã lỗi REST

| `code` | HTTP | Khi nào |
|--------|------|---------|
| `INVALID_REQUEST` | 400 | JSON hỏng hoặc không phải object, thiếu field, sai kiểu hoặc sai giá trị (kể cả `language` lạ, `client_token` không phải UUID, tham số query sai hoặc trùng) |
| `UNAUTHENTICATED` | 401 | Thiếu cookie, phiên sai hoặc hết hạn |
| `INVALID_CREDENTIALS` | 401 | `POST /api/login` sai thông tin đăng nhập |
| `FORBIDDEN` | 403 | Đã đăng nhập nhưng không đủ quyền (chưa endpoint nào ở phần này dùng) |
| `NOT_FOUND` | 404 | Đường dẫn `/api/*` không tồn tại |
| `PROBLEM_NOT_FOUND` | 404 | Đề không tồn tại hoặc ẩn với người gọi |
| `SUBMISSION_NOT_FOUND` | 404 | Bài nộp không tồn tại hoặc không phải của người gọi |
| `METHOD_NOT_ALLOWED` | 405 | Sai method cho đường dẫn; kèm header `Allow` |
| `BODY_TOO_LARGE` | 413 | Body vượt giới hạn mục 3.2 |
| `CODE_TOO_LARGE` | 413 | `code` vượt 64 KB (byte UTF-8) |
| `UNSUPPORTED_MEDIA_TYPE` | 415 | `POST` không có `Content-Type: application/json` |
| `RATE_LIMITED` | 429 | Vượt 10 bài nộp mỗi 60 giây (mỗi người), 10 lần đăng nhập mỗi 60 giây (mỗi `username`), hoặc quá 8 đăng nhập đang xử lý cùng lúc; kèm `Retry-After` |
| `INTERNAL_ERROR` | 500 | Lỗi server; `message` chung, chi tiết chỉ ở log |

**Ngoại lệ: 503 do hạ tầng.** Khi có quá nhiều kết nối đồng thời (giới hạn `--limit-concurrency` của uvicorn, xem `server/README.md`), uvicorn trả 503 ngay ở tầng hạ tầng, trước khi request tới ứng dụng. Response này **không** theo định dạng `{"error": {...}}` (thân là văn bản thường) và không có `Cache-Control: no-store`. Client coi mọi 503 là lỗi tạm thời: thử lại sau, không phân tích thân response, và với `POST /api/submissions` giữ nguyên `client_token` khi thử lại (mục 3.3).


### 4. WebSocket (server đẩy)

Trình duyệt mở một WebSocket tới `/ws` (cùng cổng HTTP, mục 1) để nhận trạng thái chấm bài và bảng xếp hạng ngay khi thay đổi, không cần gọi lại REST. Mọi message là một frame văn bản chứa một object JSON có field `type`. Client chỉ gửi lên `PONG`; mọi dữ liệu khác lấy bằng REST (mục 3). Message `WORKERS` dành cho admin sẽ thêm cùng phần admin ở PR sau; người không phải admin không bao giờ nhận.

#### 4.1. Kết nối và xác thực

- Client gửi `GET /ws` với header `Upgrade: websocket`. Cookie phiên `jh_session` đi kèm tự động khi cùng origin.
- **Kiểm tra `Origin` (chống chiếm kết nối từ trang khác):** handshake bị từ chối bằng HTTP 403, không mở WebSocket, nếu thiếu header `Origin`, hoặc `Origin` không nằm trong các origin được phép. Origin được phép là:
  - origin có cùng host và cổng với header `Host` của request, với scheme `http` hoặc `https` (host so sánh không phân biệt hoa thường; cổng mặc định 80 của `http` và 443 của `https` có thể bỏ); và
  - các origin liệt kê trong biến môi trường `JH_ALLOWED_ORIGINS` (danh sách origin đầy đủ, phân tách bằng dấu phẩy, ví dụ `http://localhost:5173,https://judge.example.org`). Dùng biến này khi host của `Origin` khác với host trong `Host` của request, ví dụ server đứng sau proxy đổi `Host`.
  - Khi chạy frontend bằng Vite dev server, proxy `/ws` phải đặt `ws: true` và `changeOrigin: false`, để `Host` giữ nguyên là địa chỉ của Vite và khớp với `Origin` của trang.
  - Trình duyệt không cho JavaScript đọc mã HTTP của handshake bị từ chối: client chỉ thấy kết nối đóng bất thường (mã 1006), không phân biệt được với lỗi mạng.
- **Phiên:** thiếu cookie, phiên sai hoặc đã hết hạn thì handshake vẫn hoàn tất, rồi server đóng ngay với mã **4401**. Client gặp 4401 thì ngừng nối lại và quay về trang đăng nhập. (Cố ý không từ chối ở handshake, để client phân biệt được "hết phiên" với "mất mạng".)
- Kết nối hợp lệ thì server gửi ngay `SCOREBOARD` hiện có (mục 4.2), nên client không cần gọi `GET /api/scoreboard` riêng sau khi nối.
- **Giới hạn:** mỗi người tối đa 10 kết nối cùng lúc (nhiều tab), toàn server tối đa 400. Vượt giới hạn của người đó thì handshake hoàn tất rồi server đóng với mã **4429**; vượt giới hạn toàn server thì đóng với mã 1013. Hai mã khác nhau vì cách xử lý khác nhau: 4429 là lỗi của chính người dùng (quá nhiều tab) nên nối lại ngay cũng bị từ chối tiếp, còn 1013 là quá tải tạm thời (mục 4.7).
- Một người có thể mở nhiều kết nối. `STATUS` chỉ được gửi tới các kết nối của đúng chủ bài nộp; không ai nhận `STATUS` của người khác, kể cả admin.

#### 4.2. Message server gửi

| `type` | Khi nào | Nội dung |
|--------|---------|----------|
| `STATUS` | Trạng thái một bài nộp của chính người nhận thay đổi | Bên dưới |
| `SCOREBOARD` | Ngay sau khi nối, và mỗi khi bảng xếp hạng được tính lại | Như thân của `GET /api/scoreboard` (mục 3.3), kèm `type` |
| `PING` | Mỗi 30 giây | `{"type": "PING", "seq": 7}`; `seq` là số nguyên tăng dần trên mỗi kết nối |

**`STATUS`** luôn có `submission_id` và `state` (cùng giá trị với REST: `queued`, `judging`, `judged`):

```json
{"type": "STATUS", "submission_id": 42, "state": "queued"}
{"type": "STATUS", "submission_id": 42, "state": "judging", "progress": {"tests_done": 1, "tests_total": 4}}
{"type": "STATUS", "submission_id": 42, "state": "judged", "submission": {"submission_id": 42, "problem_id": "sum-two", "problem_title": "Tổng hai số", "language": "python", "received_at": "2026-10-27T08:15:30.123Z", "state": "judged", "verdict": "AC", "time_ms": 31, "memory_kib": 9216, "failed_test": null, "failed_is_sample": null, "re_kind": null, "rejudged_at": null}}
```

- `queued`: bài mới nộp, hoặc bị đưa lại hàng đợi để giao lại khi worker mất kết nối (mục 2.6). Bài được chấm lại (rejudge) giữ `state` là `queued` và không đẩy `STATUS` lúc xếp hàng; `STATUS` chỉ đẩy khi có kết quả mới.
- `judging`: bài đã giao cho worker (không có `progress`), và mỗi khi worker báo `PROGRESS` (có `progress`). `tests_total` là tổng số test của đề, gồm cả test ẩn (xem mục 4.8).
- `judged`: có kết quả. Field `submission` là đối tượng bài nộp giống phần tử của `GET /api/submissions` (mục 3.3), qua cùng quy tắc chỉ trả field phù hợp với verdict. Nó là ảnh chụp tại lúc gửi, có thể mới hơn sự kiện đã gây ra. Một lượt chấm lại bị lỗi (verdict cũ được giữ) vẫn đẩy một `STATUS` `judged` mang đối tượng với verdict cũ.
- `STATUS` không bao giờ mang `code`, `ce_message`, `sample_stdout`, `sample_input`, `sample_expected_output`: client lấy chúng bằng `GET /api/submissions/{id}` khi cần. Test ẩn, stdout/stderr của test ẩn và mã thoát không bao giờ xuất hiện (quy tắc ở mục 3.3).

#### 4.3. Message client gửi

- Chỉ `{"type": "PONG", "seq": 7}`, trong đó `seq` **phải lặp lại** đúng `seq` của `PING` vừa nhận (số nguyên). Server không đối chiếu giá trị `seq` với `PING` đã gửi, nhưng `PONG` thiếu `seq` hoặc `seq` không phải số nguyên bị coi là message sai và bỏ qua (xem dòng dưới). Server coi mọi frame JSON nhận được, kể cả frame bị bỏ qua, là bằng chứng kết nối còn sống (mốc 60 giây ở mục 4.4).
- Message có `type` khác `PONG`, `PONG` thiếu hoặc sai kiểu `seq`, hoặc frame không phải object JSON: bị bỏ qua (server ghi log), kết nối giữ nguyên.
- Frame lớn hơn 4 096 byte: server đóng với mã 1009. Frame nhị phân: đóng với mã 1003.

#### 4.4. Giữ kết nối và gửi chậm

- Server gửi `PING` mỗi 30 giây và cứ 30 giây kiểm tra các kết nối. Kết nối không nhận được gì từ client quá 60 giây thì bị đóng với mã 4408 và gỡ, nên một kết nối chết thường bị đóng sau khoảng 60 đến 90 giây.
- Client coi kết nối đã chết khi quá 60 giây không nhận được message nào (mọi `type` đều tính), rồi tự đóng và nối lại.
- Mỗi kết nối có hàng đợi gửi riêng, tối đa 32 message, và mỗi lần gửi có hạn 5 giây. Kết nối nhận chậm (hàng đợi đầy, hoặc một lần gửi không xong trong 5 giây) thì bị đóng với mã 1013 và gỡ; một kết nối chậm không làm chậm các kết nối khác.
- `SCOREBOARD` chưa gửi được mà đã có bản mới hơn thì bản cũ bị bỏ; client luôn nhận bản mới nhất, không nhất thiết nhận từng bản trung gian.

#### 4.5. Đóng kết nối do phiên

- `POST /api/logout`: mọi WebSocket thuộc phiên bị hủy bị đóng với mã 4401 ngay sau khi logout trả lời. WebSocket thuộc các phiên khác của cùng người giữ nguyên.
- `POST /api/login` khi request đã mang một phiên còn hiệu lực (mục 3.2: phiên cũ bị hủy và cấp phiên mới): mọi WebSocket thuộc phiên cũ bị đóng với mã 4401, giống logout.
- Phiên hết hạn khi WebSocket đang mở: server kiểm tra lại phiên của mọi kết nối định kỳ 30 giây một lần, và đóng 4401 kết nối không còn phiên hợp lệ; nên có thể trễ tối đa khoảng 30 giây sau khi hết hạn.
- Khi admin đặt lại mật khẩu của một người (PR sau), mọi WebSocket của người đó bị đóng 4401.
- Server tắt: đóng mọi kết nối với mã 1001.

#### 4.6. Bảo đảm chuyển phát

- Các message `STATUS` và `PING` tới một kết nối theo đúng thứ tự phát sinh; `SCOREBOARD` chưa gửi luôn được gửi sau các message đang chờ (nên có thể tới sau một `STATUS` phát sinh muộn hơn). Không có phát lại: message phát khi kết nối chưa có hoặc đã mất thì bị lỡ (tối đa một lần, cố gắng hết sức).
- `STATUS` và `SCOREBOARD` là gợi ý để cập nhật giao diện ngay; nguồn sự thật là REST (`GET /api/submissions`, `GET /api/submissions/{id}`, `GET /api/scoreboard`).

#### 4.7. Nối lại (quy tắc cho client)

1. Mã 4401: ngừng nối lại, về trang đăng nhập. Mã 4429 (quá 10 kết nối của cùng một người): ngừng nối lại và không tự thử lại, chuyển sang lấy dữ liệu bằng REST (gọi lại định kỳ `GET /api/submissions/{id}` cho bài đang chờ như mục 3.3, và `GET /api/scoreboard` cho bảng xếp hạng) và báo cho người dùng biết đang có quá nhiều tab mở; chỉ thử WebSocket lại khi người dùng tải lại trang.
2. Mọi trường hợp khác, gồm 1013 và 4408 (mất mạng, mã khác 4401 và 4429, 1006, quá 60 giây không nhận gì): nối lại sau 1, 2, 4, 8 rồi 10 giây (tối đa 10 giây), cộng một khoảng ngẫu nhiên (jitter). Nối thành công thì khoảng chờ đặt lại về 1 giây.
3. Sau khi nối lại, client gọi `GET /api/submissions` để lấy kết quả bị lỡ trong lúc mất kết nối. Bảng xếp hạng không cần gọi riêng: server gửi `SCOREBOARD` ngay khi nối.
4. Client giữ bản `SCOREBOARD` có `version` lớn nhất nhận được **kể từ lần nối WebSocket gần nhất**, từ cả WebSocket và `GET /api/scoreboard`. Mỗi lần nối lại phải bỏ mốc `version` đang giữ, vì `version` bắt đầu lại khi server khởi động lại.
5. Khi cần đóng một kết nối cũ để thay bằng kết nối mới, client dùng mã 4000 và gỡ các handler của kết nối cũ trước khi đóng, để sự kiện đóng đến muộn không kích hoạt nối lại lần thứ hai. Server không bao giờ gửi mã 4000.

#### 4.8. Hạn chế đã biết của phần này

- Không có HTTPS: cookie phiên và message đi dạng rõ trong mạng nội bộ.
- Header `Origin` chỉ do trình duyệt bảo đảm. Client không phải trình duyệt có thể giả `Origin`, nhưng vẫn cần cookie phiên hợp lệ, nên không có quyền hơn chính người sở hữu phiên.
- `progress.tests_total` cho biết số test của đề, gồm cả test ẩn, nhưng chỉ người nộp bài thấy và chỉ cho bài của chính họ. Cùng loại với kênh rò hẹp đã ghi ở mục 3.4.
- Không phát lại và không có số thứ tự trên `STATUS`; client dựa vào REST để đồng bộ lại sau khi mất kết nối.
- Khi một bài được giao lại vì worker mất kết nối (mục 2.6) và còn worker rảnh, client có thể thấy `STATUS` `judging` trước `queued` trong chốc lát; message kế tiếp (`progress` hoặc `judged`) sửa lại, và REST luôn cho trạng thái đúng.
- Mọi WebSocket tính vào giới hạn số kết nối đồng thời của server (`--limit-concurrency`, xem `server/README.md`): khi gần hết hạn mức, request HTTP mới và handshake `/ws` mới có thể nhận 503 (mục 3.5).
- Đóng một kết nối đã chết có thể làm sự kiện đóng đến chậm ở phía client; vì vậy client tự phát hiện bằng mốc 60 giây (mục 4.4) chứ không chờ sự kiện đóng.
- Một proxy hoặc giới hạn kết nối của hạ tầng có thể từ chối handshake (ví dụ 503 khi quá nhiều kết nối đồng thời, mục 3.5); client chỉ thấy 1006 và xử lý như mất mạng.

### 5. Danh sách mã lỗi

#### 5.1. Cổng worker

Mọi mã dưới đây do server gửi cho worker trong message `ERROR` (mục 2.5). Cột "Kết nối" là điều xảy ra sau khi gửi; `ERROR` chỉ là cố gắng hết sức (mục 2.3), việc đóng kết nối mới là bảo đảm.

| `code` | Chiều | Ý nghĩa | Kết nối |
|--------|-------|---------|---------|
| `REGISTER_REJECTED` | server → worker | Trước `REGISTER` thành công: message đầu tiên không phải `REGISTER`, `REGISTER` thiếu field hoặc sai kiểu, thiếu hoặc sai `secret`; hoặc message khác (thiếu `type`, `type` lạ, `PONG`, `PROGRESS`, `RESULT`, `ERROR`) gửi trước khi đăng ký xong. Một mã chung, không cho biết lý do cụ thể | Đóng |
| `UNSUPPORTED_VERSION` | server → worker | `protocol_version` không khớp (và `secret` đúng) | Đóng |
| `REGISTER_TIMEOUT` | server → worker | Không gửi `REGISTER` hợp lệ trong 10 giây kể từ khi kết nối | Đóng |
| `ALREADY_REGISTERED` | server → worker | `REGISTER` lần nữa trên kết nối đã đăng ký | Đóng, bài đang giữ (nếu có) được giao lại |
| `BAD_JSON` | server → worker | Dòng không phải JSON hợp lệ, không phải object, UTF-8 sai; hoặc (sau đăng ký) thiếu `type` hay `type` không phải chuỗi | Trước đăng ký: đóng. Sau đăng ký: giữ |
| `UNKNOWN_TYPE` | server → worker | Sau đăng ký: `type` không nằm trong bảng message, hoặc nằm trong bảng nhưng không phải loại worker được gửi lên (`PING`, `JOB`, `REGISTER_OK`) | Giữ |
| `BAD_MESSAGE` | server → worker | `PONG` hoặc `PROGRESS` thiếu field hoặc sai kiểu; `PROGRESS` khớp `JOB` nhưng `tests_done` sai thứ tự hoặc `tests_total` khác số phần tử của `tests` (message bị bỏ). `RESULT` thiếu field bắt buộc, sai kiểu hoặc có số nguyên ngoài 0..2^31−1 (bảng ở mục `RESULT`); hoặc `RESULT` khớp `JOB` nhưng `failed_test` ngoài `1..tests_total` hay `failed_is_sample` không khớp cờ của test thật | `PONG`, `PROGRESS`: giữ. `RESULT`: đóng, bài đang giữ được giao lại |
| `UNEXPECTED_RESULT` | server → worker | `RESULT` không khớp `submission_id`/`attempt_id` của `JOB` đang giao cho kết nối này, kể cả khi kết nối không giữ `JOB` nào | Đóng, bài đang giữ (nếu có) được giao lại |
| `LINE_TOO_LONG` | server → worker | Dòng từ worker vượt 1 MB (mục 2.2), trước hoặc sau đăng ký | Đóng; sau đăng ký, bài đang giữ (nếu có) được giao lại |
| `DEADLINE_EXCEEDED` | server → worker | Quá hạn chót một bước chấm (mục `PROGRESS`). Gửi theo kiểu cố gắng hết sức: gửi không được thì vẫn đóng | Đóng, bài đang giữ được giao lại |

- `ERROR` do worker gửi lên (mọi `code` đều được): nếu gửi trước khi đăng ký xong thì thuộc `REGISTER_REJECTED` (server từ chối và đóng, vì message đầu tiên không phải `REGISTER`); nếu gửi sau khi đăng ký xong thì server chỉ ghi log, giữ kết nối và không đổi trạng thái. Các mã trong bảng trên chỉ do server gửi.
- Server không gửi `ERROR` khi đóng kết nối vì đã có 16 kết nối chưa đăng ký (mục 2.6) hay vì mất kết nối.
- "Bài đang giữ được giao lại" nghĩa là lần giao đó bị cắt ngang và vẫn tính vào 3 lần giao (mục 2.6); lần thứ 3 bị cắt thì bài nhận `SE`. Riêng server khởi động lại thì lần giao dở dang không tính.

#### 5.2. REST

Mã lỗi của REST nằm ở mục 3.5, cùng định dạng `{"error": {"code", "message"}}`; hai bảng mã độc lập nhau.

#### 5.3. WebSocket

WebSocket không có thân lỗi JSON; lý do đóng nằm ở mã đóng (close code). Cột "Client" là cách xử lý bắt buộc (chi tiết ở mục 4.7).

| Mã | Ai gửi | Khi nào | Client |
|----|--------|---------|--------|
| 403 (HTTP, không phải close code) | server | Handshake: thiếu `Origin` hoặc `Origin` không được phép (mục 4.1). Client chỉ thấy 1006 | Nối lại có backoff |
| 4401 | server | Thiếu phiên, phiên sai hoặc hết hạn; logout; admin đặt lại mật khẩu (mục 4.1, 4.5) | Ngừng nối lại, về trang đăng nhập |
| 4408 | server | Không nhận được gì từ client quá 60 giây (mục 4.4) | Nối lại có backoff |
| 4429 | server | Người dùng đã có 10 kết nối WebSocket (mục 4.1) | Ngừng nối lại, chuyển sang lấy dữ liệu bằng REST, báo người dùng (mục 4.7) |
| 1013 | server | Vượt 400 kết nối toàn server (mục 4.1); kết nối nhận quá chậm (mục 4.4) | Nối lại có backoff |
| 1009 | server | Client gửi frame lớn hơn 4 096 byte | Nối lại có backoff |
| 1003 | server | Client gửi frame nhị phân | Nối lại có backoff |
| 1001 | server | Server tắt | Nối lại có backoff |
| 4000 | client | Client đóng kết nối cũ để thay bằng kết nối mới (mục 4.7); server không bao giờ gửi mã này | |
| 1006 | không ai gửi | Kết nối đóng bất thường (không có frame đóng): mất mạng, handshake bị từ chối, server không phản hồi | Nối lại có backoff |

---

## 📊 KẾT QUẢ THỰC NGHIỆM

> Đưa ảnh chụp kết quả hoặc mô tả log chạy thử.

![Demo Result](./statics/result.png)

---

## 🧩 CẤU TRÚC DỰ ÁN
```
assignment-network-project/
├── README.md
├── INSTRUCTION.md
├── statics/
│   ├── diagram.png
│   └── dataset_sample.csv
└── source/
    ├── .gitignore
    ├── client/
    │   ├── README.md
    │   └── (client source files...)
    ├── server/
    │   ├── README.md
    │   └── (server source files...)
    └── (các module khác nếu có)
```

---

## 🧩 HƯỚNG PHÁT TRIỂN THÊM

> Nêu ý tưởng mở rộng hoặc cải tiến hệ thống.

- [ ] Cải thiện giao diện người dùng
- [ ] Đề bài viết bằng Markdown (render qua thư viện có sanitize); hiện `statement` chỉ là văn bản thường
- [ ] Thêm tính năng xác thực và phân quyền
- [ ] Bảng xếp hạng có lưới theo từng đề (AC hay số lần sai của mỗi người trên mỗi đề); hiện `GET /api/scoreboard` chỉ có tổng hợp theo người
- [ ] Tối ưu hóa hiệu suất
- [ ] Triển khai trên cloud

---

## 📝 GHI CHÚ

- Repo tuân thủ đúng cấu trúc đã hướng dẫn trong `INSTRUCTION.md`.
- Đảm bảo test kỹ trước khi submit.

---

## 📚 TÀI LIỆU THAM KHẢO

> (Nếu có) Liệt kê các tài liệu, API docs, hoặc nguồn tham khảo đã sử dụng.