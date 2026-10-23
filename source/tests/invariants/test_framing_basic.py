"""Framing cơ bản: README 2.1 (một object JSON mỗi dòng, có buffer)."""

from common.protocol import LineDecoder, Message

from helpers import ONE_MB, raw_line

M1 = {"type": "PING", "seq": 1}
M2 = {"type": "PONG", "seq": 1}
M3 = {"type": "ERROR", "code": "X", "message": "Xin chào, Đà Nẵng ạ"}


def test_single_line():
    assert LineDecoder(ONE_MB).feed(raw_line(M1)) == [Message(M1)]


def test_two_lines_in_order():
    assert LineDecoder(ONE_MB).feed(raw_line(M1) + raw_line(M2)) == [Message(M1), Message(M2)]


def test_empty_chunk_yields_nothing():
    decoder = LineDecoder(ONE_MB)
    assert decoder.feed(b"") == []
    assert decoder.feed(raw_line(M1)) == [Message(M1)]


def test_no_event_until_newline():
    decoder = LineDecoder(ONE_MB)
    raw = raw_line(M1)
    assert decoder.feed(raw[:-1]) == []
    assert decoder.feed(raw[-1:]) == [Message(M1)]


def test_split_at_every_byte_position():
    raw = raw_line(M3)
    for i in range(len(raw) + 1):
        decoder = LineDecoder(ONE_MB)
        events = decoder.feed(raw[:i]) + decoder.feed(raw[i:])
        assert events == [Message(M3)], f"split at {i}"


def test_one_byte_at_a_time():
    decoder = LineDecoder(ONE_MB)
    raw = raw_line(M3) + raw_line(M1)
    events = []
    for i in range(len(raw)):
        events.extend(decoder.feed(raw[i:i + 1]))
    assert events == [Message(M3), Message(M1)]


def test_split_inside_multibyte_character():
    raw = raw_line({"type": "T", "text": "ệ"})
    cut = raw.index("ệ".encode("utf-8")) + 1
    decoder = LineDecoder(ONE_MB)
    assert decoder.feed(raw[:cut]) == []
    assert decoder.feed(raw[cut:]) == [Message({"type": "T", "text": "ệ"})]


def test_many_messages_in_one_chunk():
    chunk = raw_line(M1) + raw_line(M2) + raw_line(M3)
    assert LineDecoder(ONE_MB).feed(chunk) == [Message(M1), Message(M2), Message(M3)]


def test_trailing_half_message_completes_in_next_chunk():
    decoder = LineDecoder(ONE_MB)
    second = raw_line(M2)
    assert decoder.feed(raw_line(M1) + second[:5]) == [Message(M1)]
    assert decoder.feed(second[5:] + raw_line(M3)) == [Message(M2), Message(M3)]

