"""Khóa đăng ký worker: secret, ERROR, hạn 10 giây, trần 16 kết nối chưa đăng ký."""
import asyncio
import logging

from common.protocol import receive, send
from _support import SECRET, closed_by_server, connect, eventually, register, system


def test_valid_register_gets_register_ok_with_int_id(tmp_path):
    with system(tmp_path) as (client, app):
        async def scenario():
            reader, writer, ack = await register(app)
            assert ack["type"] == "REGISTER_OK" and type(ack["worker_id"]) is int
            writer.close()
        client.portal.call(scenario)


def test_wrong_missing_or_odd_secret_gets_error_then_close(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    with system(tmp_path) as (client, app):
        async def scenario():
            for secret in ("wrong-secret-0123456789", "", "é" * 30, SECRET + "x"):
                reader, writer, reply = await register(app, secret)
                assert reply["type"] == "ERROR"
                assert await closed_by_server(reader)
                writer.close()
            for message in ({"type": "REGISTER", "name": "w"}, {"type": "REGISTER", "name": "", "secret": SECRET},
                            {"type": "REGISTER", "name": "w" * 65, "secret": SECRET},
                            {"type": "REGISTER", "name": "w", "secret": 123}, {"type": "PROGRESS", "secret": SECRET}):
                reader, writer = await connect(app)
                await send(writer, message)
                assert (await asyncio.wait_for(receive(reader), 2))["type"] == "ERROR"
                writer.close()
            reader, writer = await connect(app)
            writer.write(b'{"type":"REGISTER","name":"w","secret":"\\ud800"}\n')
            assert (await asyncio.wait_for(receive(reader), 2))["type"] == "ERROR"
            writer.close()
        client.portal.call(scenario)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], "Đăng ký sai không được gây lỗi không xử lý"


def test_register_deadline_closes_silent_connection(tmp_path):
    # Hạn đăng ký là 10 giây; test chờ thật để không phụ thuộc cách cài đặt.
    with system(tmp_path) as (client, app):
        async def scenario():
            reader, writer = await connect(app)
            started = asyncio.get_running_loop().time()
            assert await closed_by_server(reader, 13)
            assert 9 <= asyncio.get_running_loop().time() - started <= 12.5
            writer.close()
        client.portal.call(scenario)


def test_at_most_16_unregistered_connections(tmp_path):
    with system(tmp_path) as (client, app):
        async def scenario():
            idle = [await connect(app) for _ in range(16)]
            await asyncio.sleep(0.3)
            reader, writer = await connect(app)
            await send(writer, {"type": "REGISTER", "name": "late", "secret": SECRET})
            assert await closed_by_server(reader, 1), "Kết nối thứ 17 chưa đăng ký phải bị đóng"
            writer.close()
            for _, idle_writer in idle:
                idle_writer.close()

            async def registers():
                try:
                    r, w, ack = await register(app)
                except (ConnectionError, TimeoutError, ValueError):
                    return False
                w.close()
                return ack["type"] == "REGISTER_OK"
            # Sau khi các kết nối im lặng đóng, chỗ trống phải được trả lại.
            async with asyncio.timeout(3):
                while not await registers():
                    await asyncio.sleep(0.05)
        client.portal.call(scenario)


def test_many_sequential_registrations_do_not_exhaust_slots(tmp_path):
    with system(tmp_path) as (client, app):
        async def scenario():
            for index in range(20):
                reader, writer, ack = await register(app, name=f"w{index}")
                assert ack["type"] == "REGISTER_OK", index
                writer.close()
                await writer.wait_closed()
            for index in range(20):
                reader, writer, reply = await register(app, "wrong-secret-0123456789")
                assert reply["type"] == "ERROR", index
                writer.close()
            reader, writer, ack = await register(app)
            assert ack["type"] == "REGISTER_OK"
            writer.close()
        client.portal.call(scenario)


def test_disconnected_worker_leaves_worker_list(tmp_path):
    with system(tmp_path) as (client, app):
        async def scenario():
            reader, writer, ack = await register(app)
            writer.close()
            await eventually(lambda: not app.state.engine.view())
        client.portal.call(scenario)
