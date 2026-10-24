"""So output và xử lý văn bản gửi về server (PLAN mục 4, D11). Thuần hàm, không I/O."""

import codecs

CE_MAX_LINES = 50
CE_MAX_BYTES = 4 * 1024
SAMPLE_STDOUT_MAX_BYTES = 4 * 1024


def normalize(data):
    """Chuẩn hóa output: `\\r\\n` thành `\\n`, bỏ khoảng trắng/tab cuối dòng, bỏ dòng trống cuối file."""
    lines = [line.rstrip(b" \t") for line in data.replace(b"\r\n", b"\n").split(b"\n")]
    while lines and not lines[-1]:
        lines.pop()
    return b"\n".join(lines)


def outputs_match(actual, expected):
    """`actual`: bytes stdout của chương trình; `expected`: str đáp án từ `JOB`."""
    return normalize(actual) == normalize(expected.encode("utf-8"))


def cut_text(data, limit_bytes):
    """Cắt theo byte rồi decode `errors="replace"`; kết quả ≤ `limit_bytes` byte UTF-8, không có nửa ký tự và
    không vỡ JSON. Ký tự dở dang ở cuối bị bỏ (decoder tăng dần giữ lại byte chưa đủ ký tự); byte sai ở giữa
    thành U+FFFD, và nếu chúng làm văn bản phình quá giới hạn thì cắt bớt từ cuối."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    text = decoder.decode(data[:limit_bytes], final=False)
    if len(text.encode("utf-8")) <= limit_bytes:
        return text
    used = 0
    for i, ch in enumerate(text):
        used += len(ch.encode("utf-8"))
        if used > limit_bytes:
            return text[:i]
    return text


def sanitize_ce(raw, workdir, source_name):
    """Thông báo biên dịch lỗi gửi về client: thay đường dẫn tạm bằng tên file nguồn (thay trước, cắt sau, để
    không cắt đứt giữa đường dẫn), tối đa 50 dòng và 4 KB (giới hạn nào chạm trước)."""
    wd = workdir.rstrip("/").encode("utf-8")
    src = source_name.encode("utf-8")
    raw = raw.replace(wd + b"/" + src, src).replace(wd + b"/", b"").replace(wd, b"")
    lines = raw.split(b"\n")[:CE_MAX_LINES]
    return cut_text(b"\n".join(lines), CE_MAX_BYTES)
