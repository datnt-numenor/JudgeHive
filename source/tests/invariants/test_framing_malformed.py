"""Dòng hỏng: README 2.3 (JSON hỏng, UTF-8 sai, thiếu `type`) và non-ASCII (README 2.1).

Chỉ khẳng định kiểu sự kiện, không khẳng định `reason`.
"""

import pytest

from common.protocol import LineDecoder, Malformed, Message

from helpers import ONE_MB, feed_chunks, raw_line

GOOD = {"type": "PING", "seq": 1}

BAD_LINES = [
    pytest.param(b'{"type":"X","v":"\xff"}\n', id="utf8-invalid-byte"),
    pytest.param(b'{"type":"X","v":"\xc3\x28"}\n', id="utf8-bad-continuation"),
    pytest.param(b'{"type":"X","v":"\xed\xa0\x80"}\n', id="utf8-encoded-surrogate"),
    pytest.param(b'{"type":"X"\n', id="json-truncated"),
    pytest.param(b'not json at all\n', id="json-garbage"),
    pytest.param(b'{type:"X"}\n', id="json-unquoted-key"),
    pytest.param(b"{'type':'X'}\n", id="json-single-quotes"),
    pytest.param(b'{"type":"X"} trailing\n', id="json-trailing-data"),
    pytest.param(b'\n', id="empty-line"),
    pytest.param(b'{"type":"X","v":NaN}\n', id="json-nan"),
    pytest.param(b'{"type":"X","v":Infinity}\n', id="json-infinity"),
    pytest.param(b'{"type":"X","v":-Infinity}\n', id="json-minus-infinity"),
    pytest.param(b'[]\n', id="not-object-list"),
    pytest.param(b'1\n', id="not-object-number"),
    pytest.param(b'"x"\n', id="not-object-string"),
    pytest.param(b'null\n', id="not-object-null"),
    pytest.param(b'true\n', id="not-object-bool"),
    pytest.param(b'{}\n', id="missing-type-empty"),
    pytest.param(b'{"seq":1}\n', id="missing-type"),
    pytest.param(b'{"type":null}\n', id="type-null"),
    pytest.param(b'{"type":1}\n', id="type-number"),
    pytest.param(b'{"type":["PING"]}\n', id="type-list"),
]


@pytest.mark.parametrize("bad", BAD_LINES)
def test_bad_line_is_malformed_and_decoder_survives(bad):
    decoder = LineDecoder(ONE_MB)
    events = decoder.feed(bad)
    assert len(events) == 1 and isinstance(events[0], Malformed)
    assert not decoder.failed
    assert decoder.feed(raw_line(GOOD)) == [Message(GOOD)]


@pytest.mark.parametrize("bad", BAD_LINES)
def test_bad_line_between_good_lines_loses_nothing(bad):
    events = LineDecoder(ONE_MB).feed(raw_line(GOOD) + bad + raw_line(GOOD))
    assert [type(e) for e in events] == [Message, Malformed, Message]
    assert events[0] == events[2] == Message(GOOD)


def test_bad_line_split_across_chunks():
    decoder = LineDecoder(ONE_MB)
    bad = b'{"type":"X","v":"\xff"}\n'
    events = decoder.feed(bad[:10]) + decoder.feed(bad[10:] + raw_line(GOOD))
    assert [type(e) for e in events] == [Malformed, Message]


# --- JSON lồng sâu -----------------------------------------------------------

def deep_line(depth):
    return b'{"type":"X","a":' + b"[" * depth + b"]" * depth + b"}\n"


def test_deeply_nested_json_is_malformed_and_decoder_survives():
    decoder = LineDecoder(ONE_MB)
    events = decoder.feed(deep_line(100_000))
    assert len(events) == 1 and isinstance(events[0], Malformed)
    assert not decoder.failed
    assert decoder.feed(raw_line(GOOD)) == [Message(GOOD)]


def test_deeply_nested_json_followed_by_good_line_in_same_chunk():
    events = LineDecoder(ONE_MB).feed(deep_line(100_000) + raw_line(GOOD))
    assert [type(e) for e in events] == [Malformed, Message]


def test_deeply_nested_objects_are_malformed():
    line = b'{"type":"X","a":' + b'{"a":' * 100_000 + b"1" + b"}" * 100_000 + b"}\n"
    events = LineDecoder(ONE_MB).feed(line + raw_line(GOOD))
    assert [type(e) for e in events] == [Malformed, Message]


# --- surrogate đơn lẻ --------------------------------------------------------

LONE_SURROGATE_LINES = [
    pytest.param(rb'{"type":"X","v":"\ud800"}' + b"\n", id="value-high"),
    pytest.param(rb'{"type":"X","v":"\udc00"}' + b"\n", id="value-low"),
    pytest.param(rb'{"type":"X","v":"\ud800a"}' + b"\n", id="high-then-ascii"),
    pytest.param(rb'{"type":"X","v":"\ude00\ud83d"}' + b"\n", id="reversed-pair"),
    pytest.param(rb'{"type":"X","\ud800":1}' + b"\n", id="key"),
    pytest.param(rb'{"type":"\ud800"}' + b"\n", id="type-value"),
    pytest.param(rb'{"type":"X","v":["a",{"k":["\ud800"]}]}' + b"\n", id="nested-list-and-dict"),
    pytest.param(rb'{"type":"X","a":{"b":{"c":{"\udfff":0}}}}' + b"\n", id="nested-key"),
]


@pytest.mark.parametrize("bad", LONE_SURROGATE_LINES)
def test_lone_surrogate_is_malformed_and_decoder_survives(bad):
    decoder = LineDecoder(ONE_MB)
    events = decoder.feed(bad + raw_line(GOOD))
    assert [type(e) for e in events] == [Malformed, Message]
    assert not decoder.failed


def test_valid_surrogate_pair_escape_is_accepted():
    esc = b"\\u"  # dựng từng phần để file nguồn giữ nguyên dạng escape
    line = (b'{"type":"X","v":"' + esc + b"d83d" + esc + b'de00","' + esc + b"d83d" + esc + b'de01":[1]}\n')
    assert LineDecoder(ONE_MB).feed(line) == [Message({"type": "X", "v": "\U0001F600", "\U0001F601": [1]})]


# --- non-ASCII ---------------------------------------------------------------

def test_raw_utf8_and_escaped_forms_decode_to_same_object():
    raw = 'Tiếng Việt: Đà Nẵng, ơ ư ă â ê ô ạ ệ 😀'
    raw_form = ('{"type":"T","text":"%s"}\n' % raw).encode("utf-8")
    escaped = ''.join(c if ord(c) < 128 else '\\u%04x' % ord(c) if ord(c) < 0x10000
                      else '\\u%04x\\u%04x' % (0xD800 + ((ord(c) - 0x10000) >> 10),
                                               0xDC00 + ((ord(c) - 0x10000) & 0x3FF))
                      for c in raw)
    escaped_form = ('{"type":"T","text":"%s"}\n' % escaped).encode("ascii")
    assert b"\\u" in escaped_form and b"\\u" not in raw_form
    expected = Message({"type": "T", "text": raw})
    assert LineDecoder(ONE_MB).feed(raw_form) == [expected]
    assert LineDecoder(ONE_MB).feed(escaped_form) == [expected]


def test_vietnamese_message_in_small_chunks():
    obj = {"type": "T", "text": "Xin chào, Đà Nẵng ạ"}
    events = feed_chunks(LineDecoder(ONE_MB), raw_line(obj), chunk=3)
    assert events == [Message(obj)]
