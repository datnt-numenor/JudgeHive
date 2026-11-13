# Client

Giao diện web React + Vite. Khi chạy bằng Compose, `server/Dockerfile` tự build client và server phục vụ các file tĩnh ở cổng 8000; không cần Node trên máy.

| File | Vai trò |
|---|---|
| `src/main.jsx` | Toàn bộ trang: đăng nhập, đề, nộp bài, bài nộp, xếp hạng, trang admin (đề, người dùng, worker, mọi bài nộp) |
| `src/api.js` | Gọi REST bằng `fetch`, gửi kèm cookie phiên |
| `src/styles.css` | Giao diện |
| `vite.config.js` | Cấu hình build; khi dev chuyển `/api` và `/ws` sang `localhost:8000` |

Phát triển (cần Node 22, server đang chạy ở cổng 8000):

```bash
npm ci
npm run dev
```

Client dùng REST cho mọi dữ liệu và WebSocket `/ws` chỉ để biết khi nào cần tải lại; mất WebSocket thì tự nối lại sau 3 giây. Client không gửi message định kỳ.
