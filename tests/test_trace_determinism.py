import json

import pytest

from orderdesk.clock import Clock, EngineError
from orderdesk.trace import Trace




def test_every_event_has_step_equal_to_tick():
    clock = Clock()
    trace = Trace(clock)
    assert not hasattr(trace, "set_step")
    first = trace.record("ticket", "ORD-4517")
    clock.advance()
    clock.advance()
    second = trace.record("ticket", "ORD-4517", note="second")
    clock.advance()
    third = trace.record("escalate", "ORD-4517", reason="refund_rejected")
    assert clock.now == 4
    assert [(event["seq"], event["step"], event["tick"]) for event in (first, second, third)] == [
        (1, 1, 1),
        (2, 3, 3),
        (3, 4, 4),
    ]
    assert all(event["step"] == event["tick"] for event in trace.events())
    text = trace.to_jsonl()
    assert text == (
        json.dumps(
            {
                "kind": "ticket",
                "order_id": "ORD-4517",
                "seq": 1,
                "step": 1,
                "tick": 1,
            },
            sort_keys=True,
        )
        + "\n"
        + json.dumps(
            {
                "kind": "ticket",
                "note": "second",
                "order_id": "ORD-4517",
                "seq": 2,
                "step": 3,
                "tick": 3,
            },
            sort_keys=True,
        )
        + "\n"
        + json.dumps(
            {
                "kind": "escalate",
                "order_id": "ORD-4517",
                "reason": "refund_rejected",
                "seq": 3,
                "step": 4,
                "tick": 4,
            },
            sort_keys=True,
        )
        + "\n"
    )
    copied = trace.events()
    copied[0]["seq"] = 99
    assert trace.events()[0]["seq"] == 1


def test_trace_rejects_bad_stamps_and_non_ascii_values():
    clock = Clock()
    trace = Trace(clock)
    recorded = trace.record("ticket", "ORD-4517")
    assert recorded["step"] == recorded["tick"] == 1
    with pytest.raises(EngineError, match="reserved trace field seq"):
        trace.record("ticket", "ORD-4517", seq=3)
    with pytest.raises(EngineError, match="kind is not allowed"):
        trace.record("note", "ORD-4517")
    with pytest.raises(EngineError, match="trace values must be ASCII"):
        trace.record("ticket", "ORD-4517", note="caf\u00e9")
    assert len(trace.events()) == 1
    assert trace.events()[0]["kind"] == "ticket"
    assert clock.now == 1




