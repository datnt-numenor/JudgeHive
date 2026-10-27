"""Dựng đối tượng bài nộp trả cho client (README mục 3.3, D11).

Mỗi field đi qua whitelist theo verdict, không sao chép nguyên dòng DB: dữ liệu không áp dụng cho verdict đó (ví dụ
`failed_test` của một bài AC, `sample_stdout` khi test sai là test ẩn) không bao giờ ra khỏi server dù DB có lỡ chứa."""

from server.timefmt import iso_ms  # noqa: F401  (các route REST dùng `views.iso_ms`)

RE_KINDS = frozenset({"SIGSEGV", "SIGFPE", "SIGABRT", "SIGKILL", "OTHER_SIGNAL", "EXIT_NONZERO"})
_HAS_TIMING = frozenset({"AC", "WA", "TLE", "MLE"})
_HAS_FAILED_TEST = frozenset({"WA", "TLE", "MLE", "RE"})
PUBLIC_STATE = {"queued": "queued", "judging": "judging", "done": "judged"}

LIST_COLUMNS = ("s.id, s.problem_id, p.title AS problem_title, s.language, s.submitted_at, s.state, s.verdict, "
                "s.time_ms, s.memory_kib, s.failed_test, s.failed_is_sample, s.re_kind, s.rejudged_at")
DETAIL_COLUMNS = (LIST_COLUMNS + ", s.code, s.ce_message, s.sample_stdout, s.verdict_test_hash, "
                  "p.tests_hash AS problem_tests_hash, p.hidden AS problem_hidden")


def _text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def submission_view(row):
    """Đối tượng bài nộp (các cột của `LIST_COLUMNS`)."""
    verdict = row["verdict"]
    failed = verdict in _HAS_FAILED_TEST and row["failed_test"] is not None
    return {
        "submission_id": row["id"],
        "problem_id": row["problem_id"],
        "problem_title": row["problem_title"],
        "language": row["language"],
        "received_at": iso_ms(row["submitted_at"]),
        "state": PUBLIC_STATE[row["state"]],
        "verdict": verdict,
        "time_ms": row["time_ms"] if verdict in _HAS_TIMING else None,
        "memory_kib": row["memory_kib"] if verdict in _HAS_TIMING else None,
        "failed_test": row["failed_test"] if failed else None,
        "failed_is_sample": (None if row["failed_is_sample"] is None else bool(row["failed_is_sample"])) if failed
        else None,
        "re_kind": row["re_kind"] if verdict == "RE" and row["re_kind"] in RE_KINDS else None,
        "rejudged_at": None if row["rejudged_at"] is None else iso_ms(row["rejudged_at"]),
    }


def detail_view(conn, row, is_admin):
    """`submission_view` cộng các field chi tiết. `conn`: kết nối của thread hiện tại, để tra test mẫu."""
    view = submission_view(row)
    verdict = row["verdict"]
    on_sample = view["failed_is_sample"] is True
    view["code"] = row["code"]
    view["ce_message"] = row["ce_message"] if verdict == "CE" else None
    view["sample_stdout"] = row["sample_stdout"] if on_sample else None
    view["sample_input"] = view["sample_expected_output"] = None
    # Chỉ tra được đúng test đã chấm khi bộ test của đề chưa đổi từ lần chấm đó, và đề không bị ẩn với người gọi.
    visible = is_admin or not row["problem_hidden"]
    if on_sample and visible and view["failed_test"] >= 1 and row["verdict_test_hash"] == row["problem_tests_hash"]:
        sample = conn.execute("SELECT input, output FROM tests WHERE problem_id = ? AND is_sample = 1 "
                              "ORDER BY position LIMIT 1 OFFSET ?", (row["problem_id"], view["failed_test"] - 1)
                              ).fetchone()
        if sample is not None:
            view["sample_input"], view["sample_expected_output"] = _text(sample["input"]), _text(sample["output"])
    return view
