"""Kiểm riêng client WebSocket tự viết của e2e (`tests/e2e/ws_client.py`): nó phải mask mọi frame client gửi lên (RFC 6455 5.1).

Các vector lấy từ RFC 6455: mục 1.3 (khóa và accept mẫu) và mục 5.7 (ví dụ frame). Phần đối chứng với server thật (server từ chối
frame chưa mask bằng 1002, nên client sai mask thì không nói chuyện được) nằm ở `test_ws_endpoint.py`."""

import os
import random
import socket
import struct
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "e2e"))

import ws_client  # noqa: E402
from ws_client import OP_BINARY, OP_CLOSE, OP_PING, OP_TEXT, WsClient, WsError, accept_key, encode_frame, mask_payload  # noqa: E402


def decode_client_frame(data):
    """Giải mã một frame do client gửi (kiểm các bit): trả (fin, opcode, masked, key, payload)."""
    b0, b1 = data[0], data[1]
    masked, length, pos = bool(b1 & 0x80), b1 & 0x7F, 2
    if length == 126:
        length, pos = struct.unpack("!H", data[2:4])[0], 4
    elif length == 127:
        length, pos = struct.unpack("!Q", data[2:10])[0], 10
    key = data[pos:pos + 4] if masked else None
    pos += 4 if masked else 0
    payload = data[pos:pos + length]
    assert len(payload) == length
    return bool(b0 & 0x80), b0 & 0x0F, masked, key, mask_payload(payload, key) if masked else payload


def test_rfc_sample_accept_key():
    assert accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_rfc_5_7_masked_hello_frame():
    assert encode_frame(OP_TEXT, b"Hello", key=bytes.fromhex("37fa213d")).hex() == "818537fa213d7f9f4d5158"


def test_rfc_5_7_unmasked_hello_frame():
    assert encode_frame(OP_TEXT, b"Hello", mask=False).hex() == "810548656c6c6f"


def test_rfc_5_7_masked_ping_frame_and_fragments():
    assert encode_frame(OP_PING, b"Hello", key=bytes.fromhex("37fa213d")).hex() == "898537fa213d7f9f4d5158"
    first = encode_frame(OP_TEXT, b"Hel", key=bytes.fromhex("37fa213d"), fin=False)
    assert first[0] == 0x01 and first[1] == 0x83  # không fin, có mask, độ dài 3


@pytest.mark.parametrize("size", [0, 1, 5, 124, 125, 126, 127, 1000, 65535, 65536, 70000])
def test_every_client_frame_is_masked_with_the_right_length_encoding(size):
    payload = bytes(random.Random(size).randrange(256) for _ in range(size))
    frame = encode_frame(OP_BINARY, payload)
    fin, opcode, masked, key, decoded = decode_client_frame(frame)
    assert (fin, opcode, masked) == (True, OP_BINARY, True) and len(key) == 4
    assert decoded == payload
    expected_header = 2 if size < 126 else 4 if size < 65536 else 10
    assert len(frame) == expected_header + 4 + size
    assert frame[1] & 0x7F == (size if size < 126 else 126 if size < 65536 else 127)


def test_masking_keys_are_random_per_frame():
    keys = {encode_frame(OP_TEXT, b"x")[2:6] for _ in range(50)}
    assert len(keys) > 40  # 4 byte ngẫu nhiên: trùng nhau là cực hiếm


def test_mask_payload_is_its_own_inverse():
    key = bytes([1, 2, 3, 4])
    data = bytes(range(200))
    assert mask_payload(mask_payload(data, key), key) == data


# ---- đối chứng với một server giả chạy trên socket thật ---------------------------------------------------------

class FakeServer:
    """Server tối thiểu: làm handshake đúng RFC, rồi chạy `script(conn)` trên socket đã nâng cấp."""

    def __init__(self, script, accept=None):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.received = b""
        self.accept_override = accept
        self.thread = threading.Thread(target=self._serve, args=(script,), daemon=True)
        self.thread.start()

    def _serve(self, script):
        conn, _ = self.sock.accept()
        conn.settimeout(5)
        request = b""
        while b"\r\n\r\n" not in request:
            request += conn.recv(4096)
        key = [l.split(b":", 1)[1].strip() for l in request.split(b"\r\n") if l.lower().startswith(b"sec-websocket-key")][0]
        accept = self.accept_override or accept_key(key.decode())
        self.request = request
        conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        try:
            script(conn)
        finally:
            conn.close()
            self.sock.close()


def test_client_handshake_sends_the_required_headers_and_validates_accept():
    server = FakeServer(lambda conn: None)
    client = WsClient("127.0.0.1", server.port, origin="http://127.0.0.1", cookie="abc")
    server.thread.join(5)
    lower = server.request.lower()
    assert lower.startswith(b"get /ws http/1.1") and b"upgrade: websocket" in lower and b"sec-websocket-version: 13" in lower
    assert b"origin: http://127.0.0.1" in lower and b"cookie: jh_session=abc" in lower
    client.close()


def test_client_rejects_a_wrong_accept_key():
    server = FakeServer(lambda conn: None, accept="AAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    with pytest.raises(WsError):
        WsClient("127.0.0.1", server.port)


def test_client_reads_unmasked_server_frames_and_fragments():
    def script(conn):
        conn.sendall(encode_frame(OP_TEXT, b'{"a":1}', mask=False))
        conn.sendall(encode_frame(OP_TEXT, b'{"b"', mask=False, fin=False))  # phân mảnh
        conn.sendall(encode_frame(0x0, b':2}', mask=False))
        conn.sendall(encode_frame(OP_CLOSE, struct.pack("!H", 4401), mask=False))

    server = FakeServer(script)
    client = WsClient("127.0.0.1", server.port)
    assert client.recv_json() == {"a": 1}
    assert client.recv_json() == {"b": 2}
    assert client.recv() == ("close", 4401)
    client.close()


def test_client_answers_a_ping_with_a_masked_pong_carrying_the_same_payload():
    seen = {}

    def script(conn):
        conn.sendall(encode_frame(OP_PING, b"abc", mask=False))
        conn.sendall(encode_frame(OP_TEXT, b"x", mask=False))
        data = b""
        while len(data) < 2 + 4 + 3:
            data += conn.recv(64)
        seen["frame"] = data

    server = FakeServer(script)
    client = WsClient("127.0.0.1", server.port)
    assert client.recv() == ("text", "x")  # ping được trả lời ngầm, không hiện ra như message
    server.thread.join(5)
    fin, opcode, masked, key, payload = decode_client_frame(seen["frame"])
    assert (opcode, masked, payload) == (0xA, True, b"abc")
    client.close()


def test_client_sends_text_masked_and_returns_timeout_without_hanging():
    seen = {}

    def script(conn):
        data = b""
        while len(data) < 2 + 4 + 5:
            data += conn.recv(64)
        seen["frame"] = data

    server = FakeServer(script)
    client = WsClient("127.0.0.1", server.port, timeout=2)
    client.send_text("Hello")
    server.thread.join(5)
    fin, opcode, masked, key, payload = decode_client_frame(seen["frame"])
    assert (opcode, masked, payload) == (OP_TEXT, True, b"Hello")
    assert client.recv(timeout=0.2) in (("timeout", None), ("eof", None))
    client.close()


def test_pong_helper_echoes_the_seq():
    seen = {}

    def script(conn):
        data = b""
        while len(data) < 2 + 4 + len(b'{"type": "PONG", "seq": 9}'):
            data += conn.recv(64)
        seen["frame"] = data

    server = FakeServer(script)
    client = WsClient("127.0.0.1", server.port)
    client.send_pong_for({"type": "PING", "seq": 9})
    server.thread.join(5)
    assert decode_client_frame(seen["frame"])[4] == b'{"type": "PONG", "seq": 9}'
    client.close()


def test_module_exposes_the_constants_the_e2e_relies_on():
    assert ws_client.GUID == "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
