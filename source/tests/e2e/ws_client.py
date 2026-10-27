"""Client WebSocket tối thiểu (RFC 6455) chỉ dùng thư viện chuẩn, cho e2e: máy host không cần cài `websockets`.

Điểm quan trọng: MỌI frame client gửi lên đều được mask bằng khóa ngẫu nhiên 4 byte (RFC 6455 mục 5.1); server từ chối frame chưa
mask bằng mã 1002. Mã hóa/giải mã frame nằm trong các hàm thuần (`encode_frame`, `mask_payload`, `accept_key`) để kiểm riêng bằng
các vector trong RFC (xem `tests/server/test_ws_client.py`). Hỗ trợ: handshake có kiểm `Sec-WebSocket-Accept`, frame văn bản,
phân mảnh (continuation), tự trả `pong` (có mask) khi nhận `ping`, frame đóng. Không hỗ trợ nén (không xin extension)."""

import base64
import hashlib
import os
import socket
import struct
import time

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class WsError(Exception):
    pass


class HandshakeRejected(WsError):
    def __init__(self, status):
        super().__init__(f"handshake bị từ chối: {status}")
        self.status = status


def accept_key(key):
    """`Sec-WebSocket-Accept` đúng cho `Sec-WebSocket-Key` đã gửi (RFC 6455 mục 4.2.2)."""
    return base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")


def mask_payload(payload, key):
    return bytes(b ^ key[i % 4] for i, b in enumerate(payload))


def encode_frame(opcode, payload=b"", *, mask=True, key=None, fin=True):
    """Một frame. `mask=True` (mặc định, và là điều RFC bắt buộc với client) thì đặt bit mask và gắn khóa 4 byte."""
    first = (0x80 if fin else 0) | opcode
    n = len(payload)
    flag = 0x80 if mask else 0
    if n < 126:
        header = struct.pack("!BB", first, flag | n)
    elif n < 65536:
        header = struct.pack("!BBH", first, flag | 126, n)
    else:
        header = struct.pack("!BBQ", first, flag | 127, n)
    if mask:
        key = key if key is not None else os.urandom(4)
        return header + key + mask_payload(payload, key)
    return header + payload


class WsClient:
    def __init__(self, host, port, *, path="/ws", origin=None, cookie=None, timeout=10.0):
        self.timeout = timeout
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._buf = b""
        self.closed_code = None
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        lines = [f"GET {path} HTTP/1.1", f"Host: {host}:{port}", "Upgrade: websocket", "Connection: Upgrade",
                 f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
        if origin is not None:
            lines.append(f"Origin: {origin}")
        if cookie is not None:
            lines.append(f"Cookie: jh_session={cookie}")
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
        head = self._read_until(b"\r\n\r\n")
        text = head.decode("latin-1")
        self.status_line = text.split("\r\n")[0]
        self.status = int(self.status_line.split(" ")[1])
        self.headers = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in text.split("\r\n")[1:])}
        if self.status != 101:
            self.sock.close()
            raise HandshakeRejected(self.status_line)
        if self.headers.get("sec-websocket-accept") != accept_key(key):
            self.sock.close()
            raise WsError("Sec-WebSocket-Accept sai")

    # ------------------------------------------------------------------ đọc

    def _read_until(self, marker):
        while marker not in self._buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WsError("kết nối đóng khi đang đọc")
            self._buf += chunk
        head, _, self._buf = self._buf.partition(marker)
        return head

    def _read_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError("kết nối đóng")
            self._buf += chunk
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    def _read_frame(self):
        b0, b1 = self._read_exact(2)
        fin, opcode, masked, length = bool(b0 & 0x80), b0 & 0x0F, bool(b1 & 0x80), b1 & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        key = self._read_exact(4) if masked else None  # server không được mask, nhưng đọc cho đúng nếu có
        payload = self._read_exact(length)
        return fin, opcode, mask_payload(payload, key) if key else payload

    def recv(self, timeout=None):
        """Message tiếp theo: `("text", str)`, `("binary", bytes)`, `("close", code)` hoặc `("eof", None)`; `("timeout", None)`
        nếu quá hạn. Tự trả pong (có mask) cho ping."""
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        fragments, first_op = [], None
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return ("timeout", None)
            self.sock.settimeout(left)
            try:
                fin, opcode, payload = self._read_frame()
            except socket.timeout:
                return ("timeout", None)
            except (EOFError, ConnectionError, WsError):
                return ("eof", None)
            if opcode == OP_PING:
                self._send(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                self.closed_code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 1005
                return ("close", self.closed_code)
            if opcode in (OP_TEXT, OP_BINARY):
                first_op, fragments = opcode, [payload]
            elif opcode == OP_CONT:
                fragments.append(payload)
            if fin and first_op is not None:
                data = b"".join(fragments)
                return ("text", data.decode("utf-8")) if first_op == OP_TEXT else ("binary", data)

    def recv_json(self, timeout=None):
        import json

        kind, value = self.recv(timeout)
        return json.loads(value) if kind == "text" else (kind, value)

    # ------------------------------------------------------------------ gửi (luôn mask)

    def _send(self, opcode, payload=b""):
        self.sock.sendall(encode_frame(opcode, payload, mask=True))

    def send_text(self, text):
        self._send(OP_TEXT, text.encode("utf-8"))

    def send_binary(self, data):
        self._send(OP_BINARY, data)

    def send_pong_for(self, ping_message):
        import json

        self.send_text(json.dumps({"type": "PONG", "seq": ping_message["seq"]}))

    def close(self, code=1000):
        try:
            self._send(OP_CLOSE, struct.pack("!H", code))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
