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
