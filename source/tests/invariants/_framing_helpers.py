"""Hàm dựng dữ liệu dùng chung cho test invariant framing."""

import json

ONE_MB = 1_048_576
SIXTEEN_MB = 16 * ONE_MB
CHUNK = 64 * 1024


def raw_line(obj):
    """Dòng hợp lệ dựng độc lập với common.protocol: UTF-8 thô + `\\n`."""
    return json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n"


def line_of_size(size, filler="a"):
    """Dòng JSON hợp lệ dài đúng `size` byte (gồm `\\n`); `filler` là chuỗi lặp để độn."""
    prefix = b'{"type":"T","pad":"'
    suffix = b'"}\n'
    room = size - len(prefix) - len(suffix)
    unit = len(filler.encode("utf-8"))
    count, rest = divmod(room, unit)
    body = filler * count + "a" * rest
    line = prefix + body.encode("utf-8") + suffix
    assert len(line) == size
    return line


def feed_chunks(decoder, data, chunk=CHUNK):
    events = []
    for i in range(0, len(data), chunk):
        events.extend(decoder.feed(data[i:i + chunk]))
    return events
