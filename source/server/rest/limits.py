"""Giới hạn tần suất của REST (README mục 3.2). Nằm trong bộ nhớ vì chỉ có một process uvicorn; restart thì mất
(README 3.4). Rate limit nộp bài không ở đây: đếm bằng DB cùng transaction với việc chấp nhận bài."""

import math
import threading
from collections import deque


class SlidingWindow:
    """Tối đa `limit` lần trong bất kỳ khoảng `window_ms` nào, theo từng khóa. Chỉ lần được chấp nhận mới ghi lại,
    nên bị từ chối liên tục không kéo dài thời gian khóa."""

    SWEEP_ABOVE = 4096  # số khóa; vượt thì dọn khóa đã hết hạn để bộ nhớ không phình khi bị dò nhiều username

    def __init__(self, limit, window_ms):
        self.limit, self.window_ms = limit, window_ms
        self._hits = {}
        self._lock = threading.Lock()

    def hit(self, key, now_ms):
        """0 nếu được phép (và ghi lại); ngược lại số giây nguyên >= 1 phải chờ."""
        with self._lock:
            if len(self._hits) > self.SWEEP_ABOVE:
                self._sweep(now_ms)
            times = self._hits.get(key)
            if times is None:
                times = self._hits[key] = deque()
            while times and times[0] <= now_ms - self.window_ms:
                times.popleft()
            if len(times) >= self.limit:
                return max(1, math.ceil((times[0] + self.window_ms - now_ms) / 1000))
            times.append(now_ms)
            return 0

    def _sweep(self, now_ms):
        for key in [k for k, t in self._hits.items() if not t or t[-1] <= now_ms - self.window_ms]:
            del self._hits[key]


class ConcurrencyGate:
    """Tối đa `limit` lượt đang xử lý cùng lúc; vượt thì từ chối ngay, không chờ (không chiếm thêm thread)."""

    def __init__(self, limit):
        self.limit = limit
        self._active = 0
        self._lock = threading.Lock()

    def try_enter(self):
        with self._lock:
            if self._active >= self.limit:
                return False
            self._active += 1
            return True

    def leave(self):
        with self._lock:
            self._active -= 1
