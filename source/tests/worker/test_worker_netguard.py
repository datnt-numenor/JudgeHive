"""Thí sinh không có mạng (D33, sửa D28): seccomp chỉ cho `socket(AF_UNIX)`, chặn `io_uring_*`.

Phần thuần chạy `seccomp.simulate` (cùng tập lệnh BPF với bộ lọc thật) cho cả hai kiến trúc; phần container chạy chương trình
thật dưới sandbox: kết nối loopback tới một listener có thật trong chính container, DNS, UDP, socket Unix, io_uring, JVM."""

import socket

import pytest

from _worker_helpers import case, grade
from worker import sandbox, seccomp

A = seccomp.AUDIT_ARCH
SOCKETPAIR = {"x86_64": 53, "aarch64": 199}
CONNECT = {"x86_64": 42, "aarch64": 203}
READ = {"x86_64": 0, "aarch64": 63}
SOCKET = {"x86_64": 41, "aarch64": 198}  # cố ý ghi cứng, không lấy từ seccomp.SOCKET_NR: bắt được việc sửa nhầm bảng
AF_INET, AF_INET6, AF_NETLINK, AF_PACKET, AF_VSOCK = 2, 10, 16, 17, 40


# ------------------------------------------------------------------------------------- bộ lọc (thuần, không cần kernel)

def test_socket_numbers_are_the_kernel_ones_per_architecture():
    assert seccomp.SOCKET_NR == SOCKET


@pytest.mark.parametrize("arch,other", [("x86_64", "aarch64"), ("aarch64", "x86_64")])
def test_the_other_architectures_socket_number_is_an_ordinary_syscall(arch, other):
    """41 (x86_64) và 198 (aarch64) là syscall khác trên kiến trúc kia: khóa việc tách bảng theo kiến trúc."""
    prog = seccomp.build_filter(arch)
    assert seccomp.simulate(prog, A[arch], SOCKET[other], AF_INET) == seccomp.RET_ALLOW


@pytest.mark.parametrize("arch", ["x86_64", "aarch64"])
def test_socket_allows_only_af_unix(arch):
    prog = seccomp.build_filter(arch)
    nr = SOCKET[arch]
    run = lambda domain: seccomp.simulate(prog, A[arch], nr, domain)  # noqa: E731
    assert run(1) == seccomp.RET_ALLOW  # AF_UNIX = 1 của kernel, ghi cứng
    for domain in (0, AF_INET, AF_INET6, AF_NETLINK, AF_PACKET, AF_VSOCK, 255):
        assert run(domain) == seccomp.RET_ERRNO_EPERM, domain


@pytest.mark.parametrize("arch", ["x86_64", "aarch64"])
def test_socket_domain_is_read_as_a_32_bit_int_so_garbage_in_the_upper_half_changes_nothing(arch):
    prog = seccomp.build_filter(arch)
    nr = SOCKET[arch]
    assert seccomp.simulate(prog, A[arch], nr, (0xDEADBEEF << 32) | AF_INET) == seccomp.RET_ERRNO_EPERM
    assert seccomp.simulate(prog, A[arch], nr, (0xDEADBEEF << 32) | 1) == seccomp.RET_ALLOW
    assert seccomp.simulate(prog, A[arch], nr, 1 << 32) == seccomp.RET_ERRNO_EPERM  # 64 bit khác 1 nhưng nửa thấp là 0


@pytest.mark.parametrize("arch", ["x86_64", "aarch64"])
def test_unix_sockets_and_ordinary_syscalls_stay_allowed(arch):
    prog = seccomp.build_filter(arch)
    for nr in (SOCKETPAIR[arch], CONNECT[arch], READ[arch]):
        assert seccomp.simulate(prog, A[arch], nr, AF_INET) == seccomp.RET_ALLOW, nr  # không phải socket(): bỏ qua args


@pytest.mark.parametrize("arch", ["x86_64", "aarch64"])
def test_io_uring_is_blocked_with_eperm(arch):
    prog = seccomp.build_filter(arch)
    for nr in (425, 426, 427):  # io_uring_setup, io_uring_enter, io_uring_register (số chung asm-generic)
        assert seccomp.simulate(prog, A[arch], nr) == seccomp.RET_ERRNO_EPERM, nr


def test_foreign_abi_socket_is_still_killed_not_just_denied():
    prog = seccomp.build_filter("x86_64")
    assert seccomp.simulate(prog, A["x86_64"], seccomp.SOCKET_NR["x86_64"] | seccomp.X32_BIT, seccomp.AF_UNIX) \
        == seccomp.RET_KILL_PROCESS
    assert seccomp.simulate(prog, 0x40000003, 359, AF_INET) == seccomp.RET_KILL_PROCESS  # socket của i386


# ------------------------------------------------------------------------- chương trình thật dưới sandbox (container)

@pytest.fixture
def listener():
    """Một listener thật ở loopback của container: nếu thí sinh kết nối được thì kết nối thành công, nên bị chặn là do bộ lọc."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(50)
    port = srv.getsockname()[1]
    with socket.create_connection(("127.0.0.1", port), timeout=2):  # đối chứng: tiến trình không bị lọc kết nối được
        pass
    yield port
    srv.close()


PY_NET = r'''import socket, errno, ctypes, sys, os, tempfile
port = int(sys.stdin.read().split()[0])
def t(name, f):
    try:
        f(); print(name, "OK")
    except socket.gaierror:
        print(name, "GAIERROR")
    except OSError as e:
        print(name, "ERR", errno.errorcode.get(e.errno, e.errno))
    except Exception as e:
        print(name, "EXC", type(e).__name__)
t("inet", lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM))
t("inet6", lambda: socket.socket(socket.AF_INET6))
t("udp", lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
t("netlink", lambda: socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 0))
t("packet", lambda: socket.socket(socket.AF_PACKET, socket.SOCK_RAW))
t("unix_pair", lambda: socket.socketpair())
def unix():
    p = tempfile.mktemp(); a = socket.socket(socket.AF_UNIX); a.bind(p); a.listen(1)
    b = socket.socket(socket.AF_UNIX); b.connect(p); c, _ = a.accept(); b.send(b"x"); assert c.recv(1) == b"x"; os.unlink(p)
t("unix_bind_connect_send", unix)
t("connect_loopback", lambda: socket.create_connection(("127.0.0.1", port), timeout=2))
t("dns", lambda: socket.getaddrinfo("example.com", 80))
libc = ctypes.CDLL(None, use_errno=True)
buf = ctypes.create_string_buffer(120)
for name, nr in (("io_uring_setup", 425), ("io_uring_enter", 426), ("io_uring_register", 427)):
    r = libc.syscall(nr, 4, buf); print(name, r, errno.errorcode.get(ctypes.get_errno()))
'''
PY_NET_EXPECTED = """inet ERR EPERM
inet6 ERR EPERM
udp ERR EPERM
netlink ERR EPERM
packet ERR EPERM
unix_pair OK
unix_bind_connect_send OK
connect_loopback ERR EPERM
dns GAIERROR
io_uring_setup -1 EPERM
io_uring_enter -1 EPERM
io_uring_register -1 EPERM
"""


def test_python_candidate_has_no_network_but_keeps_unix_sockets(work_root, listener):
    body, _ = grade(work_root, "python", PY_NET, [case(f"{listener}\n", PY_NET_EXPECTED, True)])
    assert body["verdict"] == "AC", body


CPP_NET = r'''#include <sys/socket.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <cstdio>
#include <cerrno>
#include <cstring>
int main() {
    int fd = socket(AF_INET, SOCK_STREAM, 0); printf("inet %d %s\n", fd, fd < 0 ? strerror(errno) : "OK");
    fd = socket(AF_INET6, SOCK_DGRAM, 0); printf("inet6 %d %s\n", fd, fd < 0 ? strerror(errno) : "OK");
    int sv[2]; int r = socketpair(AF_UNIX, SOCK_STREAM, 0, sv); printf("unix_pair %d\n", r);
    fd = socket(AF_UNIX, SOCK_STREAM, 0); printf("unix %s\n", fd >= 0 ? "OK" : "ERR");
    long r2 = syscall(SYS_io_uring_setup, 4, nullptr); printf("io_uring_setup %ld %s\n", r2, strerror(errno));
}
'''
CPP_NET_EXPECTED = """inet -1 Operation not permitted
inet6 -1 Operation not permitted
unix_pair 0
unix OK
io_uring_setup -1 Operation not permitted
"""


def test_cpp_candidate_has_no_network_but_keeps_unix_sockets(work_root):
    body, _ = grade(work_root, "cpp", CPP_NET, [case("", CPP_NET_EXPECTED, True)])
    assert body["verdict"] == "AC", body


JAVA_NET = r'''import java.net.*; import java.nio.channels.*;
public class Main {
  interface F { void run() throws Exception; }
  static void t(String n, F f) { try { f.run(); System.out.println(n + " OK"); }
    catch (Throwable e) { System.out.println(n + " " + e.getClass().getSimpleName()); } }
  public static void main(String[] a) throws Exception {
    int port = new java.util.Scanner(System.in).nextInt();
    t("socket_loopback", () -> new Socket("127.0.0.1", port).close());
    t("serversocket", () -> new ServerSocket(0).close());
    t("datagram", () -> new DatagramSocket().close());
    t("dns", () -> InetAddress.getByName("example.com"));
    t("unix_channel", () -> SocketChannel.open(StandardProtocolFamily.UNIX).close());
    t("selector", () -> java.nio.channels.Selector.open().close());
    t("pipe", () -> java.nio.channels.Pipe.open());
  } }
'''
JAVA_NET_EXPECTED = """socket_loopback SocketException
serversocket SocketException
datagram SocketException
dns UnknownHostException
unix_channel OK
selector OK
pipe OK
"""


def test_java_candidate_has_no_network_the_jvm_still_runs_and_unix_channels_work(work_root, listener):
    body, _ = grade(work_root, "java", JAVA_NET, [case(f"{listener}\n", JAVA_NET_EXPECTED, True)])
    assert body["verdict"] == "AC", body


# ---------------------------------------------------------------------------------- khởi động: fail-closed (D28, D33)

def test_verify_guard_fails_closed_when_the_filter_does_not_block_inet_sockets(monkeypatch):
    """Bộ lọc không chặn socket(AF_INET) (đổi số syscall socket thành số không tồn tại) thì worker không được khởi động."""
    monkeypatch.setitem(seccomp.SOCKET_NR, seccomp.host_arch(), 9999)
    monkeypatch.setattr(sandbox, "_filter_path", None)  # dựng lại file BPF với bảng đã đổi; tự khôi phục khi hết test
    with pytest.raises(seccomp.GuardError, match="AF_INET"):
        sandbox.verify_guard()


def test_verify_guard_fails_closed_when_the_filter_also_blocks_unix_sockets(monkeypatch):
    """Đối chứng dương: bộ lọc "chặn mọi socket" (AF_UNIX đổi thành số khác) làm hỏng socketpair/JVM nên cũng không được qua."""
    monkeypatch.setattr(seccomp, "AF_UNIX", 99)
    monkeypatch.setattr(sandbox, "_filter_path", None)
    with pytest.raises(seccomp.GuardError, match="không đo được chặn mạng"):
        sandbox.verify_guard()


def test_io_uring_assertions_in_this_file_are_backed_by_the_pure_filter_tests():
    """Docker mới có seccomp mặc định cũng trả EPERM cho io_uring, nên các dòng `io_uring_* -1 EPERM` ở trên không tự chứng
    minh bộ lọc của repo: người giữ ranh giới là `test_io_uring_is_blocked_with_eperm` (BPF thuần) và đột biến bỏ io_uring."""
    for arch in ("x86_64", "aarch64"):
        assert {"io_uring_setup", "io_uring_enter", "io_uring_register"} <= {n for n, _ in seccomp.BLOCKED[arch]}
