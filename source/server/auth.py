"""Băm mật khẩu bằng scrypt (chậm có chủ đích, có salt); token phiên ngẫu nhiên 256 bit nên SHA-256 là đủ."""
import hashlib
import hmac
import secrets


def hash_password(password):
    if not 8 <= len(password.encode()) <= 128:
        raise ValueError("Mật khẩu phải dài 8–128 byte")
    salt = secrets.token_bytes(16)
    # n=16384, r=8: mỗi lần thử tốn khoảng 16 MiB RAM và vài chục ms, làm dò mật khẩu hàng loạt rất chậm.
    key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"{salt.hex()}:{key.hex()}"  # salt lưu cùng hash


def verify_password(password, stored):
    # Độ dài password đã được pydantic giới hạn 128 ký tự ở endpoint.
    try:
        salt, expected = stored.split(":")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()
