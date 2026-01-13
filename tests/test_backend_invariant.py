import pytest

from orderdesk.clock import EngineError
from tests.support import accept_cancel_at_tick_two, stack, step


def _payments_at(order_id: str):
    parts = stack(order_id)
    return parts


def test_refund_rejected_while_processing_cancel_requested_rejected_or_shipped():
    processing = _payments_at("ORD-4517")
    assert processing["payments"].issue_refund("ORD-4517", 8900) == {
        "status": "rejected",
        "reason": "precondition_failed",
        "order_status": "processing",
    }
    assert processing["payments"].ledger() == []
    assert processing["clock"].now == 1

    requested = _payments_at("ORD-4517")
    step(requested["clock"])
    requested["orders"].request_cancel("ORD-4517")
    assert requested["payments"].issue_refund("ORD-4517", 8900)["order_status"] == (
        "cancel_requested"
    )
    assert requested["payments"].ledger() == []
    assert requested["clock"].now == 2

    _, clock, orders, _ = accept_cancel_at_tick_two("ORD-4517")
    from orderdesk.payments import Payments

    payments = Payments(orders, clock)
    while clock.now < 5:
        step(clock)
    assert payments.issue_refund("ORD-4517", 8900) == {
        "status": "rejected",
        "reason": "precondition_failed",
        "order_status": "cancel_rejected",
    }
    while clock.now < 6:
        step(clock)
    assert payments.issue_refund("ORD-4517", 8900) == {
        "status": "rejected",
        "reason": "precondition_failed",
        "order_status": "shipped",
    }
    assert payments.ledger() == []
    assert [entry["result"] for entry in payments.log()] == ["rejected", "rejected"]


def test_refund_accepted_once_only_when_status_is_cancelled():
    _, clock, orders, _ = accept_cancel_at_tick_two("ORD-4520")
    from orderdesk.payments import Payments

    payments = Payments(orders, clock)
    orders.settle()
    assert clock.now == 4
    assert orders.status_at(4) == "cancelled"
    history = orders.history("ORD-4520")
    accepted = payments.issue_refund("ORD-4520", 4500)
    assert accepted == {
        "status": "accepted",
        "refund_id": "RF-4520-1",
        "amount_cents": 4500,
    }
    assert payments.ledger() == [
        {
            "refund_id": "RF-4520-1",
            "order_id": "ORD-4520",
            "amount_cents": 4500,
            "tick": 4,
        }
    ]
    assert payments.log()[0]["result"] == "accepted"
    duplicate = payments.issue_refund("ORD-4520", 1200)
    assert duplicate == {"status": "rejected", "reason": "duplicate_refund"}
    assert payments.ledger() == [
        {
            "refund_id": "RF-4520-1",
            "order_id": "ORD-4520",
            "amount_cents": 4500,
            "tick": 4,
        }
    ]
    assert [entry["result"] for entry in payments.log()] == ["accepted", "rejected"]
    assert payments.log()[1]["reason"] == "duplicate_refund"
    assert "order_status" not in payments.log()[1]
    assert orders.history("ORD-4520") == history
    assert clock.now == 4


def test_refund_uses_order_service_status_at_the_current_tick():
    parts = stack("ORD-4517")
    parts["cache"].put(
        "ORD-4517",
        {
            "status": "cancelled",
            "basis": "requested",
            "written_at": 1,
            "request_id": "CR-4517-1",
        },
    )
    response = parts["payments"].issue_refund("ORD-4517", 8900)
    assert response == {
        "status": "rejected",
        "reason": "precondition_failed",
        "order_status": "processing",
    }
    assert parts["payments"].ledger() == []
    assert parts["orders"].status_at(1) == "processing"


def test_failed_refund_call_does_not_write_a_log_entry():
    parts = stack("ORD-4520")
    with pytest.raises(EngineError, match="unknown order"):
        parts["payments"].issue_refund("ORD-9999", 4500)
    with pytest.raises(EngineError, match="amount_cents must be an integer"):
        parts["payments"].issue_refund("ORD-4520", True)
    assert parts["payments"].log() == []
    assert parts["payments"].ledger() == []
    assert parts["clock"].now == 1


def test_log_and_ledger_copies_are_independent():
    _, clock, orders, _ = accept_cancel_at_tick_two("ORD-4520")
    from orderdesk.payments import Payments

    payments = Payments(orders, clock)
    orders.settle()
    payments.issue_refund("ORD-4520", 4500)
    log = payments.log()
    ledger = payments.ledger()
    log.clear()
    ledger[0]["amount_cents"] = 1
    assert len(payments.log()) == 1
    assert payments.ledger()[0]["amount_cents"] == 4500
