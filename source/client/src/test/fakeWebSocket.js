// WebSocket giả cho test. Chỉ dựng lại phần client dùng (onopen/onmessage/onclose, send, close, readyState), cộng các hàm
// để test đóng vai server: `open()`, `receive(obj)`, `serverClose(code)`. Client tự gọi `close()` thì không phát sự kiện đóng
// (client luôn gỡ handler trước khi tự đóng, nên sự kiện đó dù có cũng không được xử lý).
export class FakeWebSocket {
  static instances = [];

  static reset() {
    FakeWebSocket.instances = [];
  }

  static get last() {
    return FakeWebSocket.instances.at(-1);
  }

  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.sent = []; // các frame client gửi, đã JSON.parse
    this.closedWith = null; // mã client tự đóng
    this.onopen = null;
    this.onmessage = null;
    this.onclose = null;
    this.onerror = null;
    FakeWebSocket.instances.push(this);
  }

  send(data) {
    if (this.readyState !== 1) throw new Error("socket chưa mở");
    this.sent.push(JSON.parse(data));
  }

  close(code = 1005) {
    if (this.readyState >= 2) return;
    this.readyState = 3;
    this.closedWith = code;
  }

  open() {
    this.readyState = 1;
    this.onopen?.({});
  }

  /** Server gửi một frame: object thì thành JSON, chuỗi/giá trị khác giữ nguyên. */
  receive(frame) {
    if (this.readyState === 0) this.open();
    this.onmessage?.({ data: frame !== null && typeof frame === "object" && !(frame instanceof ArrayBuffer) ? JSON.stringify(frame) : frame });
  }

  serverClose(code) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
}
