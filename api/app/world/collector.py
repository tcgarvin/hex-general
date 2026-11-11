"""Keep the cyclic garbage collector's pauses out of a world turn (WORLD_SPEC §9).

A world state is a large graph of long-lived objects (about 650,000 tracked by
the collector on the 200-slot map). With the collector running, a 100-bot turn
paid for about 190 young and 16 middle collections plus one or two full ones,
and a full one walks the whole graph: 0.15 s with one state alive, up to 0.9 s
with several, landing inside resolution or a bot's planning. Over a measured
100-bot season those collections found no garbage: the engine's objects do
not form reference cycles, and reference counting frees them.

So `quiet_collector` turns the collector off for the turn and, when the turn
is over, runs one collection of the young generations (0 and 1,
`gc.collect(1)`). That pass walks only what the turn allocated and kept, frees
any cycles among it, and moves it to the old generation, so the next automatic
collection does not start by walking a turn's worth of young objects.
Long-lived objects are left to the ordinary full collections outside the turn.
Collection timing never changes what the engine computes.

The collector is process-wide and the API runs handlers on a thread pool, so
two ticks can overlap. Entries are counted under a lock: the first one in
turns the collector off and the last one out restores it, normally or after
an exception.
"""

from __future__ import annotations

import gc
import threading
from collections.abc import Iterator
from contextlib import contextmanager


class _Hold:
    """The open `quiet_collector` blocks in this process, and what to restore."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.open = 0
        self.was_enabled = False


_HOLD = _Hold()


@contextmanager
def quiet_collector() -> Iterator[None]:
    """Run the block with the cyclic collector off; restore it on exit.

    Re-entrant and thread-safe: nested or overlapping blocks share one hold,
    and the outermost (last) exit restores the collector to how the first
    entry found it. If it was on, that exit first collects the young
    generations (`gc.collect(1)`), freeing any cycles among what the blocks
    allocated. Cycles through older objects wait for the next full collection.
    """
    with _HOLD.lock:
        if _HOLD.open == 0:
            _HOLD.was_enabled = gc.isenabled()
            gc.disable()
        _HOLD.open += 1
    try:
        yield
    finally:
        with _HOLD.lock:
            _HOLD.open -= 1
            if _HOLD.open == 0 and _HOLD.was_enabled:
                gc.collect(1)
                gc.enable()
