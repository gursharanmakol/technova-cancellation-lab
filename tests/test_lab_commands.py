"""Command output and acceptance checks."""

import json
import subprocess
import sys

from orderdesk.acceptance import evaluate, format_report
from tests.support import ROOT

DASHES = ("\u2013", "\u2014")


def _lab(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(ROOT / "lab.py"), *args],
        cwd=ROOT,
        capture_output=True,
        check=False,
        timeout=15,
    )


def _text(completed: subprocess.CompletedProcess[bytes]) -> str:
    assert completed.stderr == b""
    text = completed.stdout.decode("ascii")
    for dash in DASHES:
        assert dash not in text
    return text


def test_commands_for_ord_4517_are_ascii_fast_and_neutral():
    run_done = _lab("run", "ORD-4517")
    run_text = _text(run_done)
    assert run_done.returncode == 0
    assert run_text.startswith("ORD-4517\n")
    assert "Messages sent:" in run_text
    assert "Refunds:" in run_text
    assert "Escalation:" in run_text
    trace = _lab("trace", "ORD-4517")
    assert trace.returncode == 0
    header = _text(trace).splitlines()[0]
    assert header.split() == ["SEQ", "STEP", "TICK", "KIND", "DETAIL"]
    full = _lab("trace", "ORD-4517", "--full")
    assert full.returncode == 0
    saved = (ROOT / "runs" / "ORD-4517" / "trace.jsonl").read_text(encoding="ascii")
    assert _text(full) == saved
    shown = _lab("show", "ORD-4517", "1")
    assert shown.returncode == 0
    event = json.loads(_text(shown))
    assert event["seq"] == 1
    missing = _lab("show", "ORD-4517", "99")
    assert missing.returncode == 2
    assert "Valid range:" in _text(missing)
    world = _text(_lab("world", "ORD-4517"))
    assert "Client cache" not in world
    assert "tick 1 status" not in world
    history, status = world.split("Order Service status at each tick", 1)
    assert history.startswith("Order Service history\nTICK  STATUS            CAUSE           REQUEST\n")
    assert "1     processing        initial\n" in history
    assert "2     cancel_requested  cancel_request  CR-4517-1\n" in history
    assert status.startswith("\nTICK  STATUS\n")
    payments = status.split("Payments log\n", 1)[1].split("Payments ledger", 1)[0].strip()
    assert payments == "none" or payments.startswith("TICK  RESULT")
    messages = world.split("Messages\n", 1)[1].strip()
    assert messages == "none" or messages.startswith("TICK  MESSAGE_ID")
    other = _text(_lab("world", "ORD-4520"))
    other_payments = other.split("Payments log\n", 1)[1].split("\nPayments ledger", 1)[0]
    assert other_payments.startswith("TICK  RESULT")
    assert "REASON" not in other_payments
    world_file = json.loads(
        (ROOT / "runs" / "ORD-4517" / "world.json").read_text(encoding="ascii")
    )
    summary_file = json.loads(
        (ROOT / "runs" / "ORD-4517" / "summary.json").read_text(encoding="ascii")
    )
    assert "cache" not in world_file
    assert "cache" in summary_file
    verified = _lab("verify")
    verified_text = _text(verified)
    assert verified.returncode in (0, 1)
    for label in ("V1 ", "V2 ", "V3 ", "V4 ", "V5 ", "Integrity: ", "Result: "):
        assert label in verified_text


def test_missing_tick_names_the_order_and_a_fact():
    runs = {
        "ORD-4517": {
            "events": [{"kind": "case_closed", "tick": 6}],
            "history": [
                {"cause": "initial", "status": "processing", "tick": 1},
            ],
            "ledger": [],
            "messages": [],
            "payments_log": [],
            "tick": 6,
        },
        "ORD-4520": _closed("ORD-4520", 4, 6, 4500),
        "ORD-4523": {
            "events": [{"kind": "escalate", "reason": "refund_rejected", "tick": 6}],
            "history": [
                {"cause": "initial", "status": "processing", "tick": 1},
            ],
            "ledger": [],
            "messages": [],
            "payments_log": [],
            "tick": 6,
        },
    }
    report = format_report(evaluate(runs))
    assert "V3 FAIL ORD-4523 (0 refunds)" in report
    assert "V4 FAIL ORD-4517 (no escalation)" in report


def test_unknown_order_lists_valid_orders_and_exits_2():
    completed = _lab("run", "ORD-9999")
    assert completed.returncode == 2
    text = _text(completed)
    assert "Unknown order ORD-9999." in text
    assert "Valid orders: ORD-4517, ORD-4520, ORD-4523" in text


def test_acceptance_names_scenario_and_tick_and_passes_the_reference_runs():
    shipped = {
        "ORD-4517": {
            "events": [
                {"kind": "escalate", "reason": "refund_rejected", "seq": 8, "tick": 6}
            ],
            "history": [
                {"cause": "initial", "status": "processing", "tick": 1},
                {
                    "cause": "cancel_request",
                    "request_id": "CR-4517-1",
                    "status": "cancel_requested",
                    "tick": 2,
                },
                {"cause": "warehouse", "status": "cancel_rejected", "tick": 5},
                {"cause": "warehouse", "status": "shipped", "tick": 6},
            ],
            "ledger": [],
            "messages": [
                {
                    "message_id": "MSG-4517-1",
                    "template": "cancel_confirmed",
                    "tick": 4,
                }
            ],
            "payments_log": [
                {
                    "order_status": "cancel_rejected",
                    "reason": "precondition_failed",
                    "result": "rejected",
                    "tick": 5,
                }
            ],
            "tick": 6,
        },
        "ORD-4520": _closed("ORD-4520", 4, 6, 4500),
        "ORD-4523": _closed("ORD-4523", 6, 8, 13250),
    }
    report = format_report(evaluate(shipped))
    assert report == (
        "V1 FAIL ORD-4517 t4, ORD-4517 t5\n"
        "V2 PASS\n"
        "V3 PASS\n"
        "V4 FAIL ORD-4517 t4, ORD-4517 t5\n"
        "V5 PASS\n"
        "Integrity: PASS\n"
        "Result: FAIL\n"
    )
    assert "precondition_failed" not in report
    assert "refund_rejected" not in report


def _closed(order_id: str, cancelled_tick: int, refund_tick: int, amount: int) -> dict:
    number = order_id.removeprefix("ORD-")
    return {
        "events": [{"kind": "case_closed", "outcome": "refunded", "tick": refund_tick}],
        "history": [
            {"cause": "initial", "status": "processing", "tick": 1},
            {
                "cause": "cancel_request",
                "request_id": f"CR-{number}-1",
                "status": "cancel_requested",
                "tick": 2,
            },
            {"cause": "order_service", "status": "cancelled", "tick": cancelled_tick},
        ],
        "ledger": [
            {
                "amount_cents": amount,
                "order_id": order_id,
                "refund_id": f"RF-{number}-1",
                "tick": refund_tick,
            }
        ],
        "messages": [
            {
                "message_id": f"MSG-{number}-1",
                "template": "cancel_confirmed",
                "tick": refund_tick - 1,
            }
        ],
        "payments_log": [
            {
                "amount_cents": amount,
                "order_id": order_id,
                "refund_id": f"RF-{number}-1",
                "result": "accepted",
                "tick": refund_tick,
            }
        ],
        "tick": refund_tick,
    }
