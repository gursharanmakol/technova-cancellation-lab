"""Logical clock for one scenario run.

Time starts at tick 1. Reading the clock does not advance it. advance moves
exactly one tick and then applies due outcomes on every order service that
registered at construction. The clock does not sleep and does not draw
random numbers.

Tick 200 is the last valid tick. Calling advance at tick 200 raises
EngineError and leaves the tick at 200. No advance is applied for that call.
"""

MAX_TICK = 200


class EngineError(Exception):
    """The scenario run cannot continue."""


class Clock:
    """Monotonic logical clock. Starts at tick 1."""

    def __init__(self) -> None:
        self._tick = 1
        self._on_advance: list = []

    @property
    def now(self) -> int:
        """Current tick. Reading this does not advance time."""
        return self._tick

    def register(self, apply_due) -> None:
        """Register the callback OrderService passes at construction."""
        self._on_advance.append(apply_due)

    def advance(self) -> int:
        """Move forward one tick, apply outcomes due at that tick, and return it."""
        if self._tick >= MAX_TICK:
            raise EngineError(f"logical clock stopped at {MAX_TICK} ticks")
        self._tick += 1
        for apply_due in self._on_advance:
            apply_due(self._tick)
        return self._tick
