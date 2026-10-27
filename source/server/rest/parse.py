"""Parse số nguyên không dấu từ dữ liệu client (query, path id, header). Mọi chỗ của `rest/` đổi chuỗi của client sang số
đều đi qua đây, không gọi `int()` trực tiếp: `int()` của Python 3.11 ném `ValueError` với chuỗi trên 4300 chữ số (nên
một query dài thành 500), và chấp nhận cả chữ số Unicode, dấu, khoảng trắng và dấu gạch dưới."""

INT64_MAX = 2**63 - 1
MAX_DIGITS = len(str(INT64_MAX))  # 19: dài hơn thì chắc chắn vượt int64, không cần gọi int()


def is_ascii_digits(raw):
    return isinstance(raw, str) and raw != "" and raw.isascii() and raw.isdigit()


def parse_uint(raw, low=0, high=INT64_MAX, canonical=False):
    """Số nguyên trong `[low, high]` (và không quá int64), hoặc None nếu `raw` không phải số thập phân ASCII, quá 19
    chữ số hoặc ngoài khoảng. `canonical=True` từ chối số 0 đứng đầu ("01"), dùng cho id trên đường dẫn."""
    if not is_ascii_digits(raw) or len(raw) > MAX_DIGITS:
        return None
    if canonical and raw[0] == "0" and len(raw) > 1:
        return None
    value = int(raw)
    return value if low <= value <= min(high, INT64_MAX) else None
