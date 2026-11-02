"""Kích thước ảnh nạp của file ELF64 little-endian: tổng `p_memsz` của các segment `PT_LOAD`, chỉ dùng thư viện chuẩn.

Dùng cho ca biên MLE của C++ (D31): mảng toàn cục lớn nằm trong `.bss`, kernel không map được dưới `RLIMIT_AS`
nên chương trình chết bằng SIGSEGV chứ không có `std::bad_alloc`, và không có chữ ký OOM nào trong stderr."""

import struct

PT_LOAD = 1
_MAX_PHNUM = 256


def load_size_kib(path):
    """Tổng `p_memsz` các `PT_LOAD` làm tròn lên KiB, hoặc None nếu không đọc được / không phải ELF64 little-endian."""
    try:
        with open(path, "rb") as f:
            ehdr = f.read(64)
            if len(ehdr) < 64 or ehdr[:4] != b"\x7fELF" or ehdr[4] != 2 or ehdr[5] != 1:
                return None
            (phoff,) = struct.unpack_from("<Q", ehdr, 32)
            phentsize, phnum = struct.unpack_from("<HH", ehdr, 54)
            if phentsize < 56 or not 0 < phnum <= _MAX_PHNUM:
                return None
            f.seek(phoff)
            table = f.read(phentsize * phnum)
    except (OSError, OverflowError):  # OverflowError: e_phoff >= 2^63 trong file hỏng
        return None
    if len(table) < phentsize * phnum:
        return None
    total = 0
    for i in range(phnum):
        base = i * phentsize
        (ptype,) = struct.unpack_from("<I", table, base)
        if ptype == PT_LOAD:
            (memsz,) = struct.unpack_from("<Q", table, base + 40)
            total += memsz
    return -(-total // 1024)
