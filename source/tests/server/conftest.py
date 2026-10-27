import pytest

from _support import Stack


@pytest.fixture
def make_stack(tmp_path):
    """Tạo (và tự dừng) một Stack đã khởi động; `overrides` ghi đè field của ServerConfig."""
    stacks = []

    def make(**overrides):
        stack = Stack(tmp_path, **overrides).start()
        stacks.append(stack)
        return stack

    yield make
    for stack in stacks:
        stack.stop()


@pytest.fixture
def stack(make_stack):
    return make_stack()


@pytest.fixture
def make_web(tmp_path):
    """Tạo (và tự dừng) một server REST thật. `Web(...)` nhận `clock`, `prepare(app)` và field ghi đè của ServerConfig."""
    from _rest_support import Web

    webs = []

    def make(**kwargs):
        web = Web(tmp_path, **kwargs)
        webs.append(web)
        web.db.migrate()
        return web

    yield make
    for web in webs:
        web.stop()


@pytest.fixture
def web(make_web):
    """Server REST đã chạy, với alice/bob (user), root (admin), đề `sum` hiện và `secret` ẩn."""
    from _rest_support import populate

    w = make_web()
    populate(w.db)
    return w.start()
