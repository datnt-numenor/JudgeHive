# MODULE CLIENT

Giao diện web của JudgeHive: đăng nhập, xem đề, nộp bài, xem lịch sử và kết quả chấm. Chạy trong trình duyệt, chỉ nói chuyện với server qua REST đã đặc tả ở README gốc, mục "Giao tiếp", phần 3. Chưa dùng WebSocket (kết quả được theo dõi bằng cách gọi lại API, xem bên dưới); chưa có trang admin và bảng xếp hạng.

## Công nghệ

| Thành phần | Công nghệ |
|------------|-----------|
| Giao diện | React 19, React Router 7 |
| Build, dev server | Vite (proxy `/api` và `/ws` sang backend) |
| Test | Vitest, Testing Library, jsdom |
| Giao thức | `fetch` cùng origin, cookie phiên `jh_session` (HttpOnly, trình duyệt tự gửi) |

Phiên bản phụ thuộc được ghim chính xác trong `package.json` và `package-lock.json` (cài bằng `npm ci`). **Cần npm 11 trở lên** (đã thử 11.21.1 và 12.2.1; lockfile sinh bằng 11.21.1): npm 10.9.9, bản đi kèm `node:22-slim`, lỗi nội bộ "Cannot read properties of null (reading 'edgesOut')" khi giải peer dependency của `vitest` 5. Vì vậy các lệnh trong container bên dưới chạy `npm i -g npm@11.21.1` trước. `.npmrc` chỉ đặt `save-exact=true`.

## Chạy khi phát triển

Cần Node 22.12 trở lên, npm 11 trở lên và backend đang chạy ở cổng 8000 (`docker compose up --build` từ `source/`).

```bash
cd source/client
npm ci
npm run dev        # http://localhost:5173, proxy /api và /ws sang http://localhost:8000
```

Backend ở địa chỉ khác: `JH_BACKEND=http://host:8000 npm run dev`.

## Kiểm thử

Chạy trong container `node:22-slim`, không cần Node trên máy host. Từ `source/client` (PowerShell):

```powershell
docker run --rm -v "${PWD}:/src:ro" node:22-slim sh -c "npm i -g npm@11.21.1 && mkdir /app && cd /src && tar cf - --exclude=node_modules . | tar xf - -C /app && cd /app && npm ci --no-audit --no-fund && npx vitest run"
```

Mã nguồn được mount chỉ đọc rồi chép sang `/app` (bỏ `node_modules` của host), nên không để lại file nào trong repo. Đừng pipe output (che mất exit code của vitest). Nếu đã có Node 22.12 trở lên và npm 11 trở lên trên máy: `npm ci && npm test`.

Test dùng `fetch` giả dựng lại đúng hình dạng request/response của README. Việc đối chiếu với server thật làm riêng khi REST của server có mặt.

## Cấu trúc

```
client/
├── index.html, vite.config.js, package.json, package-lock.json, .npmrc
└── src/
    ├── main.jsx, App.jsx, auth.jsx, styles.css
    ├── pages/        # Login, Problems, ProblemDetail (kèm form nộp), Submissions, SubmissionDetail
    └── lib/
        ├── api.js        # mọi request REST: Content-Type, timeout 15 s, lỗi, 401 hết phiên
        ├── submitter.js  # quy tắc client_token của một lần nộp
        ├── poll.js       # theo dõi bài nộp: 1 s sau mỗi phản hồi, giãn khi lỗi, nghỉ khi tab ẩn
        ├── uuid.js       # UUID v4, có đường dự phòng khi trình duyệt không có crypto.randomUUID
        └── format.js     # nhãn hiển thị, thông báo lỗi theo mã
```

## Hành vi cần biết

- **Hết phiên:** mọi 401 `UNAUTHENTICATED` đưa về trang đăng nhập. 401 `INVALID_CREDENTIALS` của chính trang đăng nhập thì không.
- **`client_token`:** mỗi lần nộp một UUID mới. Lỗi mạng, hết giờ chờ hoặc 5xx thì bấm nộp lại dùng đúng token đó (không nộp trùng), nhưng chỉ khi mã, đề và ngôn ngữ không đổi; sửa nội dung là một lần nộp khác nên có token mới. Nút nộp bị khóa trong lúc chờ phản hồi.
- **Theo dõi kết quả:** gọi `GET /api/submissions/{id}` 1 giây một lần tính từ lúc nhận phản hồi trước, cho tới khi `state` là `judged`. Lỗi mạng thì giãn khoảng nghỉ (gấp đôi mỗi lần, tối đa 10 s); 401 hoặc 404 thì dừng; tab ẩn thì không gọi, tab hiện lại thì gọi ngay.
- **Hiển thị:** mọi chuỗi từ server (đề bài, mã nguồn, thông báo biên dịch, output) đi qua escape mặc định của React; không dùng `dangerouslySetInnerHTML` (có test quét `src/`). MLE không hiện số bộ nhớ; SE chỉ hiện thông báo chung.
- **Giới hạn mã 64 KB** tính theo byte UTF-8 được kiểm ở client để báo sớm; server vẫn là nơi quyết định.
