"""Định dạng thời điểm ra JSON (README mục 1): ISO 8601 UTC có mili giây. Tách khỏi `rest/` để các module không phụ thuộc
FastAPI (ví dụ bảng xếp hạng) dùng chung được."""

import time


def iso_ms(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ms // 1000)) + f".{ms % 1000:03d}Z"
