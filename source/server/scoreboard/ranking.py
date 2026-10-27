"""Luật xếp hạng (README mục 3.3, "Luật xếp hạng"; D12) dưới dạng hàm thuần: không DB, không đồng hồ, không sửa đầu vào.

Đầu vào là các bài nộp đã được lọc sẵn (xem `loader.py`): chỉ tài khoản role `user`, chỉ đề đang hiện, chỉ bài đã có
verdict. Hàm này không tự lọc lại những điều đó, nên test của nó chỉ cần dữ liệu thuần."""

from dataclasses import dataclass
from typing import NamedTuple, Optional

WRONG_VERDICTS = frozenset({"WA", "TLE", "MLE", "RE"})  # CE, SE không tính là lần sai


class Entry(NamedTuple):
    """Một bài nộp đã có verdict. `received_ms` là lúc server nhận bài (UTC, mili giây)."""

    user_id: int
    username: str
    problem_id: str
    submission_id: int
    received_ms: int
    verdict: str


@dataclass(frozen=True)
class Row:
    rank: int
    username: str
    solved: int
    wrong: int
    last_ac_ms: Optional[int]  # None khi solved = 0


def compute_rows(entries):
    """Danh sách `Row` đã sắp theo thứ tự hiển thị.

    - Với mỗi (người, đề), sắp bài theo `(received_ms, submission_id)`. AC đầu tiên là bài `AC` sớm nhất; có bài như vậy
      thì đề được tính là đã giải. `wrong` là số bài WA/TLE/MLE/RE đứng trước AC đầu đó (đề chưa giải: không tính).
    - `last_ac_ms` là max, trên các đề đã giải, thời điểm nhận của AC đầu.
    - Sắp: solved giảm dần, wrong tăng dần, last_ac_ms tăng dần (None xếp sau cùng), rồi username theo byte UTF-8.
      `rank` = 1 + số hàng tốt hơn hẳn (cùng ba giá trị thì cùng hạng): 1, 2, 2, 4.
    """
    by_pair = {}
    usernames = {}
    for e in entries:
        by_pair.setdefault((e.user_id, e.problem_id), []).append(e)
        usernames[e.user_id] = e.username

    stats = {uid: [0, 0, None] for uid in usernames}  # solved, wrong, last_ac_ms
    for (uid, _), items in by_pair.items():
        items.sort(key=lambda e: (e.received_ms, e.submission_id))
        wrong = 0
        for e in items:
            if e.verdict == "AC":
                s = stats[uid]
                s[0] += 1
                s[1] += wrong
                s[2] = e.received_ms if s[2] is None else max(s[2], e.received_ms)
                break
            if e.verdict in WRONG_VERDICTS:
                wrong += 1

    def order(item):
        uid, (solved, wrong, last_ac) = item
        return (-solved, wrong, last_ac is None, last_ac or 0, usernames[uid].encode("utf-8"))

    ordered = sorted(stats.items(), key=order)
    rows = []
    previous_key = None
    rank = 0
    for position, (uid, (solved, wrong, last_ac)) in enumerate(ordered, 1):
        key = (solved, wrong, last_ac)
        if key != previous_key:
            rank = position
            previous_key = key
        rows.append(Row(rank, usernames[uid], solved, wrong, last_ac))
    return rows
