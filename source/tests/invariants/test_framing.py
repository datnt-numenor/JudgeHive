"""Khóa framing: một object JSON UTF-8 mỗi dòng, giới hạn kích thước theo chiều."""
import asyncio

import pytest

from common.protocol import MAX_LINE, MAX_LINE_TO_SERVER, encode, receive
from _support import SECRET, closed_by_server, connect, register, system


def test_limits_per_direction():
    assert MAX_LINE_TO_SERVER == 64 * 1024
    assert MAX_LINE == 8 * 1024 * 1024


def test_encode_is_one_utf8_json_line():
    data = encode({"type": "PROGRESS", "note": "dòng 1\ndòng 2"})
    assert data.endswith(b"\n") and data.count(b"\n") == 1
    assert "dòng".encode() in data
    with pytest.raises(ValueError):
        encode({"type": "PING"})
    with pytest.raises(ValueError):
        encode({"type": "JOB", "code": "x" * MAX_LINE})


def test_fragmented_and_combined_messages():
    async def scenario():
        reader = asyncio.StreamReader(limit=MAX_LINE)
        first, second = encode({"type": "REGISTER_OK", "worker_id": 1}), encode({"type": "ERROR", "message": "x"})
        reader.feed_data(first[:3])
        pending = asyncio.create_task(receive(reader))
        await asyncio.sleep(0)
        assert not pending.done()
        reader.feed_data(first[3:] + second)
        assert (await pending)["type"] == "REGISTER_OK"
        assert (await receive(reader))["type"] == "ERROR"
    asyncio.run(scenario())


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
            writer.write(b"y" * (MAX_LINE_TO_SERVER + 1))
            assert await closed_by_server(reader)
            writer.close()
            # Dòng hợp lệ dưới giới hạn vẫn được nhận.
            reader, writer, ack = await register(app)
            assert ack["type"] == "REGISTER_OK"
            writer.close()
        client.portal.call(scenario)


def test_bad_json_after_register_closes_connection(tmp_path):
    with system(tmp_path) as (client, app):
        async def scenario():
            reader, writer, ack = await register(app)
            assert ack["type"] == "REGISTER_OK"
            writer.write(b"{not json}\n")
            assert await closed_by_server(reader)
        client.portal.call(scenario)
