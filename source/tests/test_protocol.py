"""Framing của giao thức TCP: một object JSON UTF-8 mỗi dòng, giới hạn kích thước theo chiều."""
import asyncio

import pytest

from common.protocol import MAX_LINE, MAX_LINE_TO_SERVER, encode, job_timeout, receive
from server.dispatch import REGISTER_TIMEOUT
from conftest import SECRET, closed_by_server, connect, register, system


def test_half_and_combined_messages():
    async def scenario():
        reader = asyncio.StreamReader(limit=MAX_LINE)
        first = encode({"type": "JOB", "code": "print(1)\nprint(2)\n"})  # \n trong code được JSON escape
        second = encode({"type": "ERROR", "message": "x"})
        assert first.count(b"\n") == 1
        reader.feed_data(first[:5])
        pending = asyncio.create_task(receive(reader))
        await asyncio.sleep(0)
        assert not pending.done(), "Nửa message thì phải chờ thêm byte"
        reader.feed_data(first[5:] + second)
        assert (await pending)["code"] == "print(1)\nprint(2)\n"
        assert (await receive(reader))["type"] == "ERROR"
    asyncio.run(scenario())


def test_encode_is_utf8_and_rejects_unknown_type_or_oversize():
    assert "dòng".encode() in encode({"type": "PROGRESS", "note": "dòng"})
    for bad in ({"type": "PING"}, {"type": "JOB", "code": "x" * MAX_LINE}):
        with pytest.raises(ValueError):
            encode(bad)


@pytest.mark.parametrize("bad", [b"not json\n", b"[]\n", b'{"type":"PING"}\n', b'{"no_type":1}\n', b"\xff\xfe\n"])
def test_malformed_lines_are_rejected(bad):
    async def scenario():
        reader = asyncio.StreamReader(limit=MAX_LINE)
        reader.feed_data(bad)
        with pytest.raises(ValueError):
            await receive(reader)
    asyncio.run(scenario())


def test_receive_enforces_given_limit():
    async def scenario():
        reader = asyncio.StreamReader(limit=MAX_LINE)
        reader.feed_data(encode({"type": "RESULT", "pad": "x" * MAX_LINE_TO_SERVER}))
        with pytest.raises(ValueError):
            await receive(reader, MAX_LINE_TO_SERVER)
    asyncio.run(scenario())


def test_server_closes_line_over_64_kib(tmp_path):
    with system(tmp_path) as (client, app):
        async def scenario():
            reader, writer = await connect(app)
            writer.write(encode({"type": "REGISTER", "name": "x" * MAX_LINE_TO_SERVER, "secret": SECRET}))
            assert await closed_by_server(reader)
            writer.close()
            reader, writer = await connect(app)
            writer.write(b"y" * (MAX_LINE_TO_SERVER + 1))  # không có \n
            assert await closed_by_server(reader)
            writer.close()
            reader, writer, ack = await register(app)
            assert ack["type"] == "REGISTER_OK"
            writer.write(b"{not json}\n")  # JSON hỏng sau khi đã đăng ký
            assert await closed_by_server(reader)
        client.portal.call(scenario)


def test_timeouts_match_readme():
    assert REGISTER_TIMEOUT == 10
    # README: 20 + số_test × (time_limit_ms/1000 + 10) + 60 giây; 2 test, 2 giây → 104.
    assert job_timeout({"tests": [{}, {}], "time_limit_ms": 2000}) == 104
