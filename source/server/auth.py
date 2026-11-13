"""Một định dạng mật khẩu cố định, đủ để nhóm tự lần theo hash/verify."""
import hashlib
import hmac
import secrets


def hash_password(password):
    if not isinstance(password, str) or not 8 <= len(password.encode()) <= 128 or "\0" in password:
        raise ValueError("Mật khẩu phải dài 8–128 byte và không chứa NUL")
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"{salt.hex()}:{key.hex()}"


def verify_password(password, stored):
    try:
        if not isinstance(password, str) or len(password.encode()) > 128 or "\0" in password:
            return False
        salt, expected = stored.split(":")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()
