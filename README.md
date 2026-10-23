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
```bash
cd source/server
# Các lệnh để khởi động server
```

### 3. Chạy client
```bash
cd source/client
# Các lệnh để khởi động client
```

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
- Khoảng thời gian tính bằng mili giây, bộ nhớ tính bằng KiB. Tên field mang hậu tố đơn vị: `_ms`, `_kib` **[ĐỀ XUẤT]**.
- Kích thước: 1 KB = 1 024 byte, 1 MB = 1 048 576 byte **[ĐỀ XUẤT]**.
- Thời điểm là chuỗi ISO 8601 theo UTC, có mili giây, ví dụ `2026-10-23T08:15:30.123Z` **[ĐỀ XUẤT]**.
- `submission_id`, `attempt_id`, `worker_id` là số nguyên **[ĐỀ XUẤT]**.
- Ngôn ngữ: `python`, `cpp`, `java` **[ĐỀ XUẤT]**.
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

- `req_id` **[ĐỀ XUẤT]**: chỉ `REGISTER` là message hỏi–đáp. Worker đặt `req_id` (chuỗi bất kỳ), server trả `REGISTER_OK` hoặc `ERROR` mang đúng `req_id` đó.
- `ERROR` gồm `code` (mã máy đọc) và `message` (mô tả cho người đọc, không dùng để rẽ nhánh). Danh sách mã ở mục 5.
- Nguyên tắc chung: khi trạng thái giữa hai bên không còn nhất quán, kết nối bị đóng. Mọi trường hợp phục hồi đi qua hai luồng sẵn có: server giao lại bài (mục 2.6) và worker kết nối lại (mục 2.6).

Server xử lý message lỗi theo trạng thái kết nối:

| Tình huống | Trước `REGISTER` thành công | Sau `REGISTER` thành công |
|------------|-----------------------------|---------------------------|
| JSON hỏng (không parse được, không phải object, UTF-8 sai) | `ERROR` `BAD_JSON` rồi đóng | `ERROR` `BAD_JSON`, giữ kết nối |
| Thiếu `type` hoặc `type` không phải chuỗi | (thuộc dòng `REGISTER_REJECTED`) | `ERROR` `BAD_JSON`, giữ kết nối |
| Message đầu tiên không phải `REGISTER`; `REGISTER` thiếu field hoặc sai kiểu; thiếu hoặc sai `secret` | `ERROR` `REGISTER_REJECTED` rồi đóng (một mã chung cho mọi trường hợp) | — |
| `protocol_version` không khớp (và `secret` đúng) | `ERROR` `UNSUPPORTED_VERSION` rồi đóng | — |
| Không gửi `REGISTER` hợp lệ trong 10 giây kể từ khi kết nối | `ERROR` `REGISTER_TIMEOUT` rồi đóng | — |
| `type` lạ | (thuộc dòng `REGISTER_REJECTED`) | `ERROR` `UNKNOWN_TYPE`, giữ kết nối |
| `PONG` hoặc `PROGRESS` thiếu field hoặc sai kiểu | (thuộc dòng `REGISTER_REJECTED`) | `ERROR` `BAD_MESSAGE`, bỏ message, giữ kết nối |
| `PROGRESS` khớp `JOB` nhưng `tests_done` sai thứ tự (mục `PROGRESS`) | (thuộc dòng `REGISTER_REJECTED`) | `ERROR` `BAD_MESSAGE`, bỏ message, giữ kết nối |
| `RESULT` thiếu field hoặc sai kiểu | (thuộc dòng `REGISTER_REJECTED`) | `ERROR` `BAD_MESSAGE` rồi đóng; bài đang giữ được giao lại |
| Gửi `REGISTER` lần nữa | — | `ERROR` `ALREADY_REGISTERED` rồi đóng |
| `PROGRESS` không khớp `submission_id`/`attempt_id` của `JOB` đang giao cho kết nối này | — | Bị bỏ, server ghi log, không gửi `ERROR` |
| `RESULT` không khớp `JOB` đang giao cho kết nối này, kể cả khi kết nối không giữ `JOB` nào | — | `ERROR` `UNEXPECTED_RESULT` rồi đóng; bài đang giữ (nếu có) được giao lại |
| `ERROR` do worker gửi | (thuộc dòng `REGISTER_REJECTED`) | Server chỉ ghi log |
| Dòng quá dài | `ERROR` `LINE_TOO_LONG` rồi đóng | `ERROR` `LINE_TOO_LONG` rồi đóng |

Mọi tên mã lỗi trong bảng là **[ĐỀ XUẤT]**.

Worker xử lý lỗi phía mình:

| Tình huống | Worker làm gì |
|------------|---------------|
| Nhận dòng quá dài, JSON hỏng hoặc message sai định dạng từ server | Đóng socket rồi kết nối lại |
| Nhận `JOB` khi đang chấm một `JOB` khác | Đóng kết nối rồi kết nối lại; server giao lại cả hai bài |
| Muốn báo lỗi cho server | Được gửi `ERROR`; server chỉ ghi log, không đổi trạng thái |

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

Tên field là **[ĐỀ XUẤT]**, trừ `type`, `attempt_id`, `submission_id`, `seq`, `worker_id`, `name`, `secret`, `protocol_version` đã chốt.

##### `REGISTER` (worker → server)

| Field | Kiểu | Bắt buộc | Ràng buộc |
|-------|------|----------|-----------|
| `type` | string | có | `"REGISTER"` |
| `req_id` | string | có | Do worker đặt |
| `name` | string | có | Tên chỉ để hiển thị, cho phép trùng giữa các worker; 1–64 ký tự **[ĐỀ XUẤT]** |
| `secret` | string | có | Shared secret của hệ thống |
| `protocol_version` | integer | có | Phiên bản hiện tại: `1` **[ĐỀ XUẤT]** |

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

Worker trả `PONG` ngay khi nhận `PING`, kể cả khi đang chấm bài.

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
| `time_limit_ms` | integer | có | Giới hạn thời gian gốc của đề cho mỗi test **[ĐỀ XUẤT]**; worker tự áp hệ số thời gian theo ngôn ngữ |
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

- Quá hạn, kể cả khi chưa nhận được `PROGRESS` với `tests_done = 0` trong cửa sổ biên dịch, thì server coi worker đã mất, đóng kết nối và giao lại bài (mục 2.6); lần giao đó vẫn tính vào số lần giao tối đa. Trước khi đóng, server cố gửi `ERROR` `DEADLINE_EXCEEDED` (best-effort: gửi không được thì vẫn đóng).
- Chỉ `PROGRESS` khớp `JOB` mà `tests_done` bằng giá trị của `PROGRESS` khớp trước đó cộng 1 (bắt đầu từ 0, và `0 ≤ tests_done ≤ tests_total`) mới làm mới hạn.
- `PROGRESS` không khớp `JOB` thì bị bỏ, không làm mới hạn. `PROGRESS` khớp `JOB` nhưng `tests_done` sai thứ tự (lặp lại, nhảy cóc, vượt `tests_total`) bị coi là sai định dạng: server gửi `ERROR` `BAD_MESSAGE`, bỏ message, không làm mới hạn, giữ kết nối.
- Giá trị **[ĐỀ XUẤT]**, đã đo ở spike, chốt tạm: `COMPILE_TIMEOUT_MAX` = 20 giây (xem lại sau khi đo trên máy khác), `SLACK` = 5 giây, `MAX_TIME_FACTOR` = 5, `PER_TEST_OVERHEAD` = 3 giây (trùm thời gian khởi động JVM, đọc và so output). Hệ số thời gian theo ngôn ngữ: xem mục "Lưu ý cho client" ở `RESULT`.
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

Worker không bao giờ gửi stdout/stderr của test ẩn, và không gửi giá trị mã thoát cụ thể của chương trình.

Chương trình in ra stdout vượt 16 MB bị dừng và nhận `WA`.

`SE` do worker báo trong `RESULT` là verdict cuối ngay: server không giao lại bài. Chỉ `SE` do mất kết nối hoặc quá hạn (mục 2.6) đi qua luồng giao lại và đếm tối đa 3 lần giao.

Lưu ý cho client:

- Với Python và C++, giới hạn bộ nhớ chặn theo không gian địa chỉ ảo, nên một lần cấp phát lớn có thể thất bại khi bộ nhớ thực còn thấp; Java bị giới hạn theo heap. Vì vậy UI hiển thị `MLE` là "vượt giới hạn bộ nhớ", không hiện con số `memory_kib`.
- `time_ms` so với giới hạn **đã nhân hệ số ngôn ngữ**, không phải giới hạn gốc của đề. Hệ số thời gian theo ngôn ngữ **[ĐỀ XUẤT]**:

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
- `RESULT` sai định dạng, hoặc không khớp `JOB` của kết nối (mục 2.3);
- worker tự đóng vì nhận `JOB` khi đang bận.

Khi đó:

- Bài worker đang giữ được đưa lại hàng đợi và giao cho worker rảnh tiếp theo (có thể chính worker đó sau khi kết nối lại), với `attempt_id` mới lớn hơn.
- Mỗi bài được giao tối đa 3 lần cho một lần chấm (mỗi lần rejudge đếm lại từ đầu). Quá 3 lần thì bài nhận verdict `SE`.
- Worker mất kết nối khi đang chấm thì hủy bài đó ngay (kill cả nhóm tiến trình), dọn thư mục tạm, và không gửi lại `RESULT` của bài đó qua kết nối mới.
- `PROGRESS`/`RESULT` mang `attempt_id` cũ chỉ là trường hợp phòng thủ: kết nối cũ đã đóng nên bình thường chúng không thể tới server. Nếu vẫn tới, server xử lý theo bảng ở mục 2.3.
- Server khởi động lại: worker tự kết nối lại; các bài đang chờ hoặc đang chấm dở được giao lại, không tính vào 3 lần.

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
- [ ] Thêm tính năng xác thực và phân quyền
- [ ] Tối ưu hóa hiệu suất
- [ ] Triển khai trên cloud

---

## 📝 GHI CHÚ

- Repo tuân thủ đúng cấu trúc đã hướng dẫn trong `INSTRUCTION.md`.
- Đảm bảo test kỹ trước khi submit.

---

## 📚 TÀI LIỆU THAM KHẢO

> (Nếu có) Liệt kê các tài liệu, API docs, hoặc nguồn tham khảo đã sử dụng.