"""Append-only trace. Assigns seq and stamps step and tick from the clock.

step is clock.now. tick is clock.now. seq is the event number and can differ
from both. Recording an event does not advance the clock.
"""

import copy
import json

from orderdesk.clock import Clock, EngineError

_KINDS = frozenset(
    {
        "ticket",
        "tool",
        "cache_write",
        "status_read",
        "decision",
        "escalate",
        "case_closed",
    }
)
_STAMPED = frozenset({"seq", "step", "tick", "kind", "order_id"})


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def canonical_json(value: object) -> str:
    """ASCII JSON with sorted keys and LF-friendly separators."""
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(", ", ": "))


def _check_json_value(value: object) -> None:
    if isinstance(value, bool) or value is None or _is_int(value):
        return
    if isinstance(value, str):
        if value.isascii():
            return
        raise EngineError("trace values must be ASCII")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or key == "" or not key.isascii():
                raise EngineError("trace names must be ASCII")
            _check_json_value(item)
        return
    if isinstance(value, list):
        for item in value:
            _check_json_value(item)
        return
    raise EngineError("trace value is not JSON data")


class Trace:
    """Event log for one scenario run."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._events: list[dict] = []
        self._status_read: dict | None = None

    def set_status_read(self, purpose: str, attempt: int, max_attempts: int) -> None:
        """Store the fields a one-argument status reader cannot receive itself.

        The controller calls this before each read. max_attempts is supplied
        by the caller because it is not fixed at 5.
        """
        if not isinstance(purpose, str) or purpose == "" or not purpose.isascii():
            raise EngineError("status read purpose must be a non-empty ASCII string")
        if not _is_int(attempt) or attempt < 1:
            raise EngineError("status read attempt must be a positive integer")
        if not _is_int(max_attempts) or max_attempts < 1:
            raise EngineError("status read max_attempts must be a positive integer")
        self._status_read = {
            "purpose": purpose,
            "attempt": attempt,
            "max_attempts": max_attempts,
        }

    def status_read_context(self) -> dict:
        if self._status_read is None:
            raise EngineError("status read context is not set")
        return dict(self._status_read)

    def record(self, kind: str, order_id: str, **fields: object) -> dict:
        """Append one event. seq starts at 1. step and tick are stamped here."""
        if kind not in _KINDS:
            raise EngineError("kind is not allowed")
        if not isinstance(order_id, str) or order_id == "" or not order_id.isascii():
            raise EngineError("order_id must be a non-empty ASCII string")
        safe_fields: dict[str, object] = {}
        for key, value in fields.items():
            if key in _STAMPED:
                raise EngineError(f"reserved trace field {key}")
            if not isinstance(key, str) or key == "" or not key.isascii():
                raise EngineError("trace names must be ASCII")
            _check_json_value(value)
            safe_fields[key] = copy.deepcopy(value)
        tick = self._clock.now
        event = {
            "seq": len(self._events) + 1,
            "step": tick,
            "tick": tick,
            "kind": kind,
            "order_id": order_id,
        }
        event.update(safe_fields)
        self._events.append(event)
        return copy.deepcopy(event)

    def events(self) -> list[dict]:
        return copy.deepcopy(self._events)

    def to_jsonl(self) -> str:
        """One sorted-key JSON object per line, LF endings, no CR."""
        if not self._events:
            return ""
        return "".join(canonical_json(event) + "\n" for event in self._events)
