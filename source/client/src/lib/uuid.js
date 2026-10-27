// `crypto.randomUUID` chỉ có trong secure context (HTTPS hoặc localhost); mở qua LAN bằng HTTP thì không có (chưa kiểm
// chứng trên trình duyệt đích), nên có đường dự phòng bằng `getRandomValues`, vốn dùng được ở mọi context.

export function uuidv4(c = globalThis.crypto) {
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  if (!c || typeof c.getRandomValues !== "function") throw new Error("không có nguồn ngẫu nhiên an toàn trong trình duyệt");
  const b = new Uint8Array(16);
  c.getRandomValues(b);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
