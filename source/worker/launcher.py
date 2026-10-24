"""Tiến trình cha nhỏ: posix_spawn chương trình, wait4, báo rusage của chính chương trình qua một fd.

Dùng: python3 -S -I launcher.py <fd_kết_quả> <lệnh...>. Cha nhỏ để ru_maxrss của con không bị cộng đỉnh RSS
của tiến trình điều phối lớn (kernel gộp đỉnh RSS của mm cũ vào ru_maxrss lúc exec; D13). SIGUSR1: kill con rồi
vẫn reap để lấy rusage (dùng khi TLE hoặc hủy bài). Chỉ dùng thư viện chuẩn, import ít để RSS nhỏ.
"""

import json
import os
import signal
import sys
import time

fd = int(sys.argv[1])
argv = sys.argv[2:]
os.set_inheritable(fd, False)  # chương trình thí sinh không được thấy/ghi fd kết quả
child = None
killed = False


def kill_child(*_):
    global killed
    killed = True
    if child is not None:
        try:
            os.kill(child, signal.SIGKILL)
        except ProcessLookupError:
            pass


signal.signal(signal.SIGUSR1, kill_child)  # cài trước khi spawn: SIGUSR1 đến sớm không được giết launcher
t0 = time.monotonic()
try:
    child = os.posix_spawnp(argv[0], argv, os.environ)
except OSError as e:
    os.write(fd, json.dumps({"spawn_error": repr(e)}).encode())
    sys.exit(0)
if killed:
    kill_child()
_, status, ru = os.wait4(child, 0)
wall_ms = (time.monotonic() - t0) * 1000
os.write(fd, json.dumps({"status": status, "maxrss": ru.ru_maxrss, "utime": ru.ru_utime, "stime": ru.ru_stime,
                         "wall_ms": wall_ms}).encode())
