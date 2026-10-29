# MODULE CLIENT

Giao diện web của JudgeHive: đăng nhập, xem đề, nộp bài, xem lịch sử, kết quả chấm và bảng xếp hạng. Chạy trong trình duyệt, chỉ nói chuyện với server qua REST (README gốc, mục "Giao tiếp", phần 3) và WebSocket `/ws` (phần 4). Chưa có trang admin.

## Công nghệ

| Thành phần | Công nghệ |
|------------|-----------|
| Giao diện | React 19, React Router 7 |
| Build, dev server | Vite (proxy `/api` và `/ws` sang backend) |
| Test | Vitest, Testing Library, jsdom |
| Giao thức | `fetch` cùng origin, cookie phiên `jh_session` (HttpOnly, trình duyệt tự gửi) |

Phiên bản phụ thuộc được ghim chính xác trong `package.json` và `package-lock.json`. **Sinh hoặc cập nhật lockfile (`npm install`) cần npm 11 trở lên** (lockfile sinh bằng 11.21.1; thử 12.2.1 cũng được): npm 10.9.9, bản đi kèm `node:22-slim`, lỗi nội bộ "Cannot read properties of null (reading 'edgesOut')" khi giải peer dependency của `vitest` 5, nên các lệnh trong container bên dưới chạy `npm i -g npm@11.21.1` trước. Chỉ **cài từ lockfile (`npm ci`) thì npm 10.9.3 vẫn chạy được** (đã thử trên Windows với Node 22.18, `npm test` 61 test pass), kèm cảnh báo `EBADENGINE`: `engines` ghi npm >=11, và một số phụ thuộc của jsdom đòi Node 22.19 hoặc 22.22. `.npmrc` chỉ đặt `save-exact=true`.

## Chạy khi phát triển

Cần Node 22.12 trở lên, npm 11 trở lên và backend đang chạy ở cổng 8000 (`docker compose up --build` từ `source/`).

```bash
cd source/client
npm ci
npm run dev        # http://localhost:5173, proxy /api và /ws sang http://127.0.0.1:8000
```

Backend ở địa chỉ khác: `JH_BACKEND=http://host:8000 npm run dev`.

## Kiểm thử

Chạy trong container `node:22-slim`, không cần Node trên máy host. Từ `source/client` (PowerShell):

```powershell
docker run --rm -v "${PWD}:/src:ro" node:22-slim sh -c "npm i -g npm@11.21.1 && mkdir /app && cd /src && tar cf - --exclude=node_modules . | tar xf - -C /app && cd /app && npm ci --no-audit --no-fund && npx vitest run"
```

Mã nguồn được mount chỉ đọc rồi chép sang `/app` (bỏ `node_modules` của host), nên không để lại file nào trong repo. Đừng pipe output (che mất exit code của vitest). Nếu đã có Node 22.12 trở lên và npm 11 trở lên trên máy: `npm ci && npm test`.

Test dùng `fetch` và `WebSocket` giả (`src/test/`) dựng lại đúng hình dạng request/response/message của README. Việc đối chiếu với server thật (handshake `Origin` qua proxy của Vite, mã đóng thật, restart server...) là các lượt kiểm tay riêng, không nằm trong bộ test này.

## Cấu trúc

```
client/
├── index.html, vite.config.js, package.json, package-lock.json, .npmrc
└── src/
    ├── main.jsx, App.jsx, auth.jsx, realtime.jsx, styles.css
    ├── pages/        # Login, Problems, ProblemDetail (kèm form nộp), Submissions, SubmissionDetail, Scoreboard
    └── lib/
        ├── api.js        # mọi request REST: Content-Type, timeout 15 s, lỗi, 401 hết phiên
        ├── submitter.js  # quy tắc client_token của một lần nộp
        ├── poll.js       # theo dõi bài nộp: 1 s sau mỗi phản hồi, giãn khi lỗi, nghỉ khi tab ẩn
        ├── realtime.js   # kết nối /ws: mã đóng, backoff, PING/PONG, quá 60 s im lặng (máy trạng thái, không phụ thuộc React)
        ├── uuid.js       # UUID v4, có đường dự phòng khi trình duyệt không có crypto.randomUUID
        └── format.js     # nhãn hiển thị, thông báo lỗi theo mã
```

## Hành vi cần biết

- **Hết phiên:** mọi 401 `UNAUTHENTICATED` đưa về trang đăng nhập. 401 `INVALID_CREDENTIALS` của chính trang đăng nhập thì không.
- **`client_token`:** mỗi lần nộp một UUID mới. Lỗi mạng, hết giờ chờ hoặc 5xx thì bấm nộp lại dùng đúng token đó (không nộp trùng), nhưng chỉ khi mã, đề và ngôn ngữ không đổi; sửa nội dung là một lần nộp khác nên có token mới. Nút nộp bị khóa trong lúc chờ phản hồi.
- **Một kết nối WebSocket cho cả ứng dụng** (`realtime.jsx`): mở sau khi đăng nhập, đóng (mã 1000, không nối lại) khi đăng xuất. Mã đóng 4401 đưa về trang đăng nhập và không nối lại. Mã 4429 (quá 10 tab) không nối lại, hiện thông báo; chỉ tải lại trang mới thử WebSocket lại. Mọi trường hợp khác (mất mạng, 1006, 1008, 1012, 1013, 4408, quá 60 s không nhận gì) nối lại sau 1, 2, 4, 8, 10 s (cộng ngẫu nhiên tới 25%); khoảng chờ về 1 s khi nhận message hợp lệ đầu tiên của kết nối mới. `PING` được trả `PONG` với đúng `seq`.
- **Theo dõi kết quả:** khi WebSocket đang sống, trang chi tiết và lịch sử bài nộp cập nhật theo `STATUS` và không poll; khi `judged`, trang chi tiết gọi `GET /api/submissions/{id}` một lần để lấy mã nguồn và các field chi tiết (STATUS không mang chúng). Khi WebSocket mất hoặc bị 4429 thì quay về gọi REST: trang chi tiết 1 giây một lần tính từ lúc nhận phản hồi trước cho tới khi `state` là `judged`, lịch sử gọi lại trang 1 mỗi 2 giây chừng nào còn bài chưa chấm xong. Lỗi mạng thì giãn khoảng nghỉ (gấp đôi mỗi lần, tối đa 10 s); 401 hoặc 404 thì dừng; tab ẩn thì không gọi, tab hiện lại thì gọi ngay. Sau khi nối lại, trang lấy lại dữ liệu từ REST (kết quả bị lỡ trong lúc mất kết nối); `STATUS` đến trễ không hạ bài đã `judged`.
- **Bảng xếp hạng** (`/scoreboard`): hiển thị theo `SCOREBOARD` (server gửi ngay khi nối và mỗi khi tính lại). Chỉ nhận bản có `version` lớn hơn bản đang giữ, và bỏ mốc này mỗi khi trạng thái kết nối đổi (server khởi động lại thì `version` bắt đầu lại). `GET /api/scoreboard` chỉ gọi khi chưa có bản nào, hoặc mỗi 5 s khi không có WebSocket.
- **Hiển thị:** mọi chuỗi từ server (đề bài, mã nguồn, thông báo biên dịch, output) đi qua escape mặc định của React; không dùng `dangerouslySetInnerHTML` (có test quét `src/`). MLE không hiện số bộ nhớ; SE chỉ hiện thông báo chung.
- **Giới hạn mã 64 KB** tính theo byte UTF-8 được kiểm ở client để báo sớm; server vẫn là nơi quyết định.
