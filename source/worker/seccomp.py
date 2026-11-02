"""Bộ lọc seccomp-BPF áp riêng cho tiến trình thí sinh (D28), nạp bằng `setpriv --seccomp-filter`.

Worker (root) vẫn dùng `memfd_create` cho input test nên không thể chặn cả container; bộ lọc chỉ áp cho con.
Luật:
- kiến trúc khác kiến trúc máy chủ (x86_64 hoặc aarch64): KILL_PROCESS (chặn `int 0x80` ABI i386 và mọi ABI lạ);
- x86_64: syscall có bit `__X32_SYSCALL_BIT` (0x40000000): KILL_PROCESS (ABI x32 gọi được `memfd_create` thật);
- `memfd_create`, `memfd_secret`, `shmget`: ERRNO(EPERM) (thí sinh nhận lỗi như mọi lỗi I/O khác);
- còn lại: ALLOW.
Chỉ dùng thư viện chuẩn, không cần libseccomp."""

import platform
import struct

AUDIT_ARCH = {"x86_64": 0xC000003E, "aarch64": 0xC00000B7}
# Số syscall theo kiến trúc (linux/arch/x86/entry/syscalls/syscall_64.tbl; asm-generic/unistd.h cho aarch64).
BLOCKED = {"x86_64": (("memfd_create", 319), ("shmget", 29), ("memfd_secret", 447)),
           "aarch64": (("memfd_create", 279), ("shmget", 194), ("memfd_secret", 447))}
X32_BIT = 0x40000000

RET_KILL_PROCESS = 0x80000000
RET_ERRNO_EPERM = 0x00050001  # SECCOMP_RET_ERRNO | EPERM
RET_ALLOW = 0x7FFF0000

LD_ABS, JEQ, JGE, RET = 0x20, 0x15, 0x35, 0x06  # BPF_LD|W|ABS, BPF_JMP|JEQ|K, BPF_JMP|JGE|K, BPF_RET|K
_MACHINES = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}


class GuardError(Exception):
    """Môi trường không đủ điều kiện để chạy code thí sinh an toàn: worker không được khởi động."""


def host_arch(machine=None):
    name = _MACHINES.get((machine or platform.machine()).lower())
    if name is None:
        raise GuardError(f"kiến trúc {machine or platform.machine()!r} chưa được hỗ trợ cho bộ lọc seccomp")
    return name


def _ins(code, jt, jf, k):
    return struct.pack("<HBBI", code, jt, jf, k)


def build_filter(arch=None):
    """Chương trình BPF (bytes, mỗi lệnh 8 byte) cho kiến trúc `arch` (mặc định: của máy chủ)."""
    arch = arch or host_arch()
    prog = [_ins(LD_ABS, 0, 0, 4),                       # A = seccomp_data.arch
            _ins(JEQ, 1, 0, AUDIT_ARCH[arch]),           # đúng kiến trúc thì bỏ qua lệnh kế
            _ins(RET, 0, 0, RET_KILL_PROCESS),
            _ins(LD_ABS, 0, 0, 0)]                       # A = seccomp_data.nr
    if arch == "x86_64":
        prog += [_ins(JGE, 0, 1, X32_BIT), _ins(RET, 0, 0, RET_KILL_PROCESS)]
    for _, nr in BLOCKED[arch]:
        prog += [_ins(JEQ, 0, 1, nr), _ins(RET, 0, 0, RET_ERRNO_EPERM)]
    prog.append(_ins(RET, 0, 0, RET_ALLOW))
    return b"".join(prog)


def simulate(prog, arch_value, nr):
    """Chạy chương trình BPF trên (arch, nr) như kernel; trả giá trị RET. Chỉ để test (cùng tập lệnh với build_filter)."""
    insns = [struct.unpack_from("<HBBI", prog, i) for i in range(0, len(prog), 8)]
    a, pc = 0, 0
    while True:
        code, jt, jf, k = insns[pc]
        pc += 1
        if code == LD_ABS:
            a = {0: nr, 4: arch_value}[k]
        elif code == JEQ:
            pc += jt if a == k else jf
        elif code == JGE:
            pc += jt if a >= k else jf
        elif code == RET:
            return k
        else:
            raise ValueError(f"lệnh BPF lạ {code:#x}")
