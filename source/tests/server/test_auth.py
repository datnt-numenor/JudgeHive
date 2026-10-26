"""auth.py: băm mật khẩu theo D10."""

import base64
import hashlib
import re

import pytest

from server import auth


def test_hash_has_the_self_describing_format():
    stored = auth.hash_password("correct horse")
    assert re.fullmatch(r"scrypt\$n=16384,r=8,p=1\$[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+", stored)
    assert "correct horse" not in stored


def test_verify_accepts_the_right_password_and_rejects_others():
    stored = auth.hash_password("correct horse")
    assert auth.verify_password("correct horse", stored)
    assert not auth.verify_password("correct horsf", stored)
    assert not auth.verify_password("", stored)


def test_same_password_gets_different_salts():
    assert auth.hash_password("correct horse") != auth.hash_password("correct horse")


def test_unicode_password_roundtrip():
    stored = auth.hash_password("mật khẩu tiếng Việt")
    assert auth.verify_password("mật khẩu tiếng Việt", stored)
    assert not auth.verify_password("mat khau tieng Viet", stored)


def test_verify_reads_parameters_from_the_stored_string_not_from_constants():
    salt = b"0123456789abcdef"
    key = hashlib.scrypt(b"old-password", salt=salt, n=2**10, r=4, p=2, dklen=16)
    stored = "scrypt$n=1024,r=4,p=2${}${}".format(base64.b64encode(salt).decode(), base64.b64encode(key).decode())
    assert auth.verify_password("old-password", stored)
    assert not auth.verify_password("other-password", stored)


@pytest.mark.parametrize("stored", ["", "garbage", "scrypt$n=1$a$b", "bcrypt$n=1,r=1,p=1$YQ==$YQ==",
                                    "scrypt$n=x,r=8,p=1$YQ==$YQ==", "scrypt$n=16384,r=8,p=1$!!!$!!!",
                                    "scrypt$n=16384,r=8,p=1$YQ==", "scrypt$r=8,p=1$YQ==$YQ=="])
def test_corrupt_stored_value_is_a_failed_verification_not_an_exception(stored):
    assert auth.verify_password("whatever1", stored) is False


@pytest.mark.parametrize("password", ["short", "x" * (auth.MAX_PASSWORD_BYTES + 1), "é" * 65])
def test_hash_enforces_the_length_limits_in_bytes(password):
    with pytest.raises(auth.PasswordError):
        auth.hash_password(password)


def test_length_limits_are_inclusive():
    auth.hash_password("x" * auth.MIN_PASSWORD_BYTES)
    auth.hash_password("x" * auth.MAX_PASSWORD_BYTES)
    auth.hash_password("é" * (auth.MAX_PASSWORD_BYTES // 2))


def test_verify_rejects_overlong_passwords_without_hashing_them():
    stored = auth.hash_password("correct horse")
    assert auth.verify_password("x" * 100_000, stored) is False
