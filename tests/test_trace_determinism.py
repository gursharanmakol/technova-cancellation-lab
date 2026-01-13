import json
import subprocess
import sys

import pytest

from orderdesk.clock import Clock, EngineError
from orderdesk.trace import Trace
from tests.scripted_run import scripted_bytes, scripted_state
from tests.support import ROOT


def test_scripted_records_are_deterministic_and_do_not_move_history():
    state = scripted_state()
    assert state["trace"].isascii()
    assert "\r" not in state["trace"]
    for event in (json.loads(line) for line in state["trace"].splitlines()):
        assert event["step"] == event["tick"]
    assert state["tick"] == 6
    assert state["history"] == [
        {"tick": 1, "status": "processing", "cause": "initial"},
        {
            "tick": 2,
            "status": "cancel_requested",
            "cause": "cancel_request",
            "request_id": "CR-4517-1",
        },
        {"tick": 5, "status": "cancel_rejected", "cause": "warehouse"},
        {"tick": 6, "status": "shipped", "cause": "warehouse"},
    ]
    assert state["payments_log"] == [
        {
            "tick": 6,
            "order_id": "ORD-4517",
            "result": "rejected",
            "reason": "precondition_failed",
            "order_status": "shipped",
        }
    ]
    assert state["ledger"] == []
    assert state["messages"] == []
    assert state["cache"] == {
        "order_id": "ORD-4517",
        "status": "cancel_requested",
        "basis": "confirmed",
        "written_at": 4,
        "request_id": "CR-4517-1",
    }


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


def _subprocess_bytes() -> bytes:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "scripted_run.py")],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    assert completed.stderr == b""
    return completed.stdout


def test_repeated_runs_are_byte_identical():
    first = scripted_bytes()
    second = scripted_bytes()
    outside_a = _subprocess_bytes()
    outside_b = _subprocess_bytes()
    assert first == second == outside_a == outside_b
    assert first.isascii()
    parsed = json.loads(first.decode("ascii"))
    assert parsed["tick"] == 6
    assert parsed["history"][-1]["status"] == "shipped"
