"""Tiến trình cha nhỏ: posix_spawn chương trình, wait4, báo rusage của chính chương trình qua một fd.

Dùng: python3 -S -I launcher.py <fd_kết_quả> <lệnh...>. Cha nhỏ để ru_maxrss của con không bị cộng đỉnh RSS
của tiến trình điều phối lớn (kernel gộp đỉnh RSS của mm cũ vào ru_maxrss lúc exec). SIGUSR1: kill con rồi
vẫn reap để lấy rusage (dùng khi TLE).
"""

import json
import os
import signal
import sys
import time

fd = int(sys.argv[1])
argv = sys.argv[2:]
os.set_inheritable(fd, False)  # chương trình thí sinh không được thấy/ghi fd kết quả
t0 = time.monotonic()
child = os.posix_spawnp(argv[0], argv, os.environ)


def kill_child(*_):
    try:
        os.kill(child, signal.SIGKILL)
    except ProcessLookupError:
        pass


signal.signal(signal.SIGUSR1, kill_child)
_, status, ru = os.wait4(child, 0)
wall_ms = (time.monotonic() - t0) * 1000
os.write(fd, json.dumps({"status": status, "maxrss": ru.ru_maxrss, "utime": ru.ru_utime, "stime": ru.ru_stime,
                         "wall_ms": wall_ms}).encode())
