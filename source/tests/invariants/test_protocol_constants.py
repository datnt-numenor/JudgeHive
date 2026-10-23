"""Hằng số đã chốt trong README / PLAN (không khóa các giá trị [ĐỀ XUẤT] còn chỉnh)."""

from common import protocol


def test_line_limits_match_readme_2_2():
    assert protocol.MAX_LINE_WORKER_TO_SERVER == 1_048_576
    assert protocol.MAX_LINE_SERVER_TO_WORKER == 16 * 1_048_576


def test_protocol_version_is_1():
    assert protocol.PROTOCOL_VERSION == 1


def test_size_limits_match_d3():
    assert protocol.MAX_TESTS_TOTAL_BYTES == 8 * 1_048_576
    assert protocol.MAX_CODE_BYTES == 64 * 1024


def test_job_line_always_fits_in_server_to_worker_limit():
    # Tests + code (trường hợp xấu nhất khi JSON escape code: 6 byte mỗi byte) phải chừa chỗ.
    assert protocol.MAX_TESTS_TOTAL_BYTES + 6 * protocol.MAX_CODE_BYTES < protocol.MAX_LINE_SERVER_TO_WORKER
