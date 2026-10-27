"""Băm mật khẩu (D10): `hashlib.scrypt`, n=2**14, r=8, p=1, salt 16 byte, so sánh bằng `hmac.compare_digest`.

Lưu dạng tự mô tả `scrypt$n=16384,r=8,p=1$<salt_b64>$<hash_b64>`; khi verify đọc tham số từ chuỗi đã lưu. Module duy nhất
băm mật khẩu cho login, admin đặt lại mật khẩu và `manage.py`. Semaphore là `threading.Semaphore`, nên chỉ gọi từ
endpoint `def` (threadpool) hoặc script, không gọi trong `async def`."""

import base64
import binascii
import hashlib
import hmac
import secrets
import threading

N, R, P = 2**14, 8, 1
SALT_BYTES = 16
KEY_BYTES = 32
MIN_PASSWORD_BYTES = 8  # [ĐỀ XUẤT] (D4, D10)
MAX_PASSWORD_BYTES = 128  # [ĐỀ XUẤT] (D4, D10)

_slots = threading.Semaphore(4)  # giới hạn số lần băm đồng thời: mỗi lần khoảng 16 MB


class PasswordError(ValueError):
    pass


def check_password_length(password):
    if "\x00" in password:  # scrypt của OpenSSL coi "abc" và "abc\x00" là một mật khẩu (đã đo), nên cấm ký tự NUL
        raise PasswordError("mật khẩu không được chứa ký tự NUL")
    size = len(password.encode("utf-8"))
    if size < MIN_PASSWORD_BYTES or size > MAX_PASSWORD_BYTES:
        raise PasswordError(f"mật khẩu phải dài {MIN_PASSWORD_BYTES} đến {MAX_PASSWORD_BYTES} byte UTF-8")


def _scrypt(password, salt, n, r, p, key_bytes):
    maxmem = 2 * 128 * r * (n + p + 2)  # mặc định của OpenSSL (32 MiB) quá thấp nếu tăng n
    with _slots:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=key_bytes, maxmem=maxmem)


def hash_password(password):
    check_password_length(password)
    salt = secrets.token_bytes(SALT_BYTES)
    key = _scrypt(password, salt, N, R, P, KEY_BYTES)
    return f"scrypt$n={N},r={R},p={P}${_b64(salt)}${_b64(key)}"


def verify_password(password, stored):
    """True nếu `password` khớp `stored`. Chuỗi lưu hỏng, mật khẩu quá dài hoặc có NUL thì False, không raise."""
    if "\x00" in password or len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return False
    try:
        scheme, params, salt_b64, key_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        values = dict(item.split("=") for item in params.split(","))
        n, r, p = int(values["n"]), int(values["r"]), int(values["p"])
        salt, expected = _unb64(salt_b64), _unb64(key_b64)
        actual = _scrypt(password, salt, n, r, p, len(expected))
    except (ValueError, KeyError, binascii.Error):
        return False
    return hmac.compare_digest(actual, expected)


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def _unb64(text):
    return base64.b64decode(text.encode("ascii"), validate=True)
