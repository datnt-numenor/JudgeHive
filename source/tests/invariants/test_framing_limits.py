"""Giới hạn độ dài dòng: README 2.2 (tính theo byte UTF-8, gồm `\\n`; kiểm tra khi đọc)."""

import pytest

from common.protocol import LineDecoder, LineTooLong, Message

from helpers import ONE_MB, SIXTEEN_MB, feed_chunks, line_of_size, raw_line

GOOD = {"type": "PING", "seq": 1}


def is_message(event):
    return isinstance(event, Message)


@pytest.mark.parametrize("limit", [ONE_MB, SIXTEEN_MB])
def test_line_exactly_at_limit_is_accepted(limit):
    decoder = LineDecoder(limit)
    events = feed_chunks(decoder, line_of_size(limit))
    assert len(events) == 1 and is_message(events[0])
    assert not decoder.failed


@pytest.mark.parametrize("limit", [ONE_MB, SIXTEEN_MB])
def test_line_one_byte_over_limit_is_rejected(limit):
    decoder = LineDecoder(limit)
    events = feed_chunks(decoder, line_of_size(limit + 1))
    assert events == [LineTooLong()]
    assert decoder.failed


def test_limit_counts_bytes_not_characters():
    # Toàn tiếng Việt: dưới 1 MB tính theo ký tự nhưng trên 1 MB tính theo byte.
    line = line_of_size(ONE_MB + 3, filler="ệ")
    assert len(line.decode("utf-8")) < ONE_MB
    decoder = LineDecoder(ONE_MB)
    assert feed_chunks(decoder, line) == [LineTooLong()]


def test_vietnamese_line_exactly_at_limit_is_accepted():
    decoder = LineDecoder(ONE_MB)
    events = feed_chunks(decoder, line_of_size(ONE_MB, filler="ệ"))
    assert len(events) == 1 and is_message(events[0])


def test_newline_counts_toward_limit():
    # Nội dung ONE_MB - 1 byte + `\n` = ONE_MB: hợp lệ. Nội dung ONE_MB byte + `\n`: quá dài.
    content_ok = line_of_size(ONE_MB)[:-1]
    content_too_long = line_of_size(ONE_MB + 1)[:-1]
    assert len(content_ok) == ONE_MB - 1
    assert len(content_too_long) == ONE_MB
    events = feed_chunks(LineDecoder(ONE_MB), content_ok + b"\n")
    assert len(events) == 1 and is_message(events[0])
    assert feed_chunks(LineDecoder(ONE_MB), content_too_long + b"\n") == [LineTooLong()]


def test_after_too_long_decoder_stops_and_ignores_input():
    decoder = LineDecoder(ONE_MB)
    assert feed_chunks(decoder, line_of_size(ONE_MB + 1)) == [LineTooLong()]
    assert decoder.failed
    assert decoder.feed(raw_line(GOOD)) == []
    assert decoder.feed(b"anything") == []


@pytest.mark.parametrize("limit", [ONE_MB, SIXTEEN_MB])
def test_over_limit_without_newline_is_rejected_without_waiting(limit):
    decoder = LineDecoder(limit)
    events = feed_chunks(decoder, b"a" * (limit + 1))  # không có `\n`
    assert events == [LineTooLong()]
    assert decoder.failed


def test_message_before_unterminated_overlong_data_is_still_delivered():
    decoder = LineDecoder(ONE_MB)
    events = decoder.feed(raw_line(GOOD) + b"a" * (ONE_MB + 1))
    assert events == [Message(GOOD), LineTooLong()]


def test_overlong_detected_across_chunks_before_newline_arrives():
    decoder = LineDecoder(ONE_MB)
    events = feed_chunks(decoder, b'{"type":"T","pad":"' + b"a" * ONE_MB)
    assert events == [LineTooLong()]


def test_messages_after_overlong_line_in_same_chunk_are_dropped():
    decoder = LineDecoder(ONE_MB)
    chunk = raw_line(GOOD) + line_of_size(ONE_MB + 1) + raw_line(GOOD) + raw_line(GOOD)
    assert decoder.feed(chunk) == [Message(GOOD), LineTooLong()]
    assert decoder.failed


def test_message_after_overlong_non_json_line_in_same_chunk_is_dropped():
    decoder = LineDecoder(ONE_MB)
    events = decoder.feed(b"a" * (ONE_MB + 1) + b"\n" + raw_line(GOOD))
    assert events == [LineTooLong()]


def test_small_messages_after_large_valid_line_still_work():
    decoder = LineDecoder(ONE_MB)
    events = feed_chunks(decoder, line_of_size(ONE_MB) + raw_line(GOOD))
    assert len(events) == 2 and events[1] == Message(GOOD)
