"""`quiet_collector`: the collector is off for the block and always restored."""

from __future__ import annotations

import gc
import threading
from collections.abc import Iterator

import pytest

from app.world.collector import quiet_collector


class _Boom(Exception):
    pass


class _Cycle:
    def __init__(self) -> None:
        self.me = self


@pytest.fixture(autouse=True)
def collector_on() -> Iterator[None]:
    """Every test starts, and must leave the process, with the collector on."""
    assert gc.isenabled()
    yield
    assert gc.isenabled()


def test_the_collector_is_off_in_the_block_and_back_on_after() -> None:
    with quiet_collector():
        assert not gc.isenabled()
    assert gc.isenabled()


def test_the_collector_comes_back_when_the_block_raises() -> None:
    with pytest.raises(_Boom):
        with quiet_collector():
            raise _Boom
    assert gc.isenabled()
    with quiet_collector():  # still usable after a failed block
        assert not gc.isenabled()


def test_nested_blocks_keep_it_off_until_the_outermost_exits() -> None:
    with quiet_collector():
        with quiet_collector():
            assert not gc.isenabled()
        assert not gc.isenabled()
    assert gc.isenabled()


def test_an_inner_raise_keeps_the_outer_block_quiet() -> None:
    with quiet_collector():
        with pytest.raises(_Boom):
            with quiet_collector():
                raise _Boom
        assert not gc.isenabled()


def test_overlapping_threads_restore_only_when_the_last_leaves() -> None:
    inside = threading.Event()
    leave = threading.Event()
    seen_after_main_left: list[bool] = []

    def tick() -> None:
        with quiet_collector():
            inside.set()
            assert leave.wait(5)
            seen_after_main_left.append(gc.isenabled())

    thread = threading.Thread(target=tick)
    with quiet_collector():
        thread.start()
        assert inside.wait(5)
    # This block closed first; the other thread's tick is still running.
    assert not gc.isenabled()
    leave.set()
    thread.join(5)
    assert seen_after_main_left == [False]


def test_a_collector_already_off_is_left_off() -> None:
    gc.disable()
    try:
        with quiet_collector():
            assert not gc.isenabled()
        assert not gc.isenabled()
    finally:
        gc.enable()


def test_cycles_made_in_the_block_are_freed_on_exit() -> None:
    gc.collect()
    with quiet_collector():
        _Cycle()
        assert any(type(o) is _Cycle for o in gc.get_objects())
    assert not any(type(o) is _Cycle for o in gc.get_objects())
