import pytest

from orderdesk.clock import Clock, EngineError
from orderdesk.order_cache import OrderCache
from orderdesk.order_client import OrderClient
from orderdesk.order_service import OrderService
from orderdesk.trace import Trace
from tests.support import arm, scenario, stack, step


class CountingService(OrderService):
    def __init__(self, clock: Clock) -> None:
        super().__init__(clock)
        self.calls = 0

    def request_cancel(self, order_id: str) -> dict:
        self.calls += 1
        return super().request_cancel(order_id)


def _counting(order_id: str) -> dict:
    loaded = scenario(order_id)
    clock = Clock()
    orders = CountingService(clock)
    orders.load_scenario(loaded)
    cache = OrderCache()
    trace = Trace(clock)
    return {
        "clock": clock,
        "orders": orders,
        "cache": cache,
        "trace": trace,
        "client": OrderClient(orders, cache, trace, clock),
        "order_id": order_id,
    }


def test_both_readers_record_source_result_and_attempt():
    parts = stack("ORD-4517")
    step(parts["clock"])
    parts["client"].cancel("ORD-4517")
    parts["cache"].put(
        "ORD-4517",
        {
            "status": "cancelled",
            "basis": "confirmed",
            "written_at": 2,
            "request_id": "CR-4517-1",
        },
    )
    parts["trace"].set_status_read("verify_cancellation", 1, 5)
    assert parts["client"].cached_status("ORD-4517") == "cancelled"
    step(parts["clock"])
    parts["trace"].set_status_read("verify_cancellation", 2, 5)
    assert parts["client"].service_status("ORD-4517") == "cancel_requested"
    events = parts["trace"].events()
    assert [event["kind"] for event in events] == [
        "tool",
        "cache_write",
        "status_read",
        "status_read",
        "cache_write",
    ]
    cached = events[2]
    assert cached["seq"] == 3
    assert cached["step"] == cached["tick"] == 2
    assert cached["read_from"] == "order_cache"
    assert cached["result"] == "cancelled"
    assert cached["attempt"] == 1
    assert cached["max_attempts"] == 5
    assert cached["purpose"] == "verify_cancellation"
    assert cached["record"] == {
        "status": "cancelled",
        "basis": "confirmed",
        "written_at": 2,
        "request_id": "CR-4517-1",
    }
    assert "order_id" not in cached["record"]
    service = events[3]
    assert service["seq"] == 4
    assert service["step"] == service["tick"] == 3
    assert service["read_from"] == "order_service"
    assert service["result"] == "cancel_requested"
    assert service["record"] == {"status": "cancel_requested"}
    assert events[4]["kind"] == "cache_write"
    assert events[4]["record"]["order_id"] == "ORD-4517"
    assert events[4]["record"]["basis"] == "confirmed"
    assert events[4]["step"] == events[4]["tick"] == 3
    assert all(event["step"] == event["tick"] for event in events)
    assert parts["orders"].history("ORD-4517")[1]["status"] == "cancel_requested"


def test_confirmed_cache_record_is_returned_without_a_write():
    parts = stack("ORD-4517")
    parts["cache"].put(
        "ORD-4517",
        {
            "status": "cancelled",
            "basis": "confirmed",
            "written_at": 1,
            "request_id": "CR-4517-1",
        },
    )
    arm(parts["trace"], 1)
    assert parts["client"].cached_status("ORD-4517") == "cancelled"
    assert [event["kind"] for event in parts["trace"].events()] == ["status_read"]
    event = parts["trace"].events()[0]
    assert event["result"] == "cancelled"
    assert event["read_from"] == "order_cache"
    assert parts["clock"].now == 1


def test_service_read_of_processing_has_no_request_id():
    parts = stack("ORD-4517")
    arm(parts["trace"], 1)
    assert parts["client"].service_status("ORD-4517") == "processing"
    stored = parts["cache"].get("ORD-4517")
    assert stored == {
        "order_id": "ORD-4517",
        "status": "processing",
        "basis": "confirmed",
        "written_at": 1,
    }
    events = parts["trace"].events()
    assert [event["kind"] for event in events] == ["status_read", "cache_write"]
    assert events[0]["read_from"] == "order_service"
    assert events[1]["record"] == stored
    assert events[0]["step"] == events[0]["tick"] == 1


def test_reader_without_context_records_nothing():
    parts = stack("ORD-4517")
    with pytest.raises(EngineError, match="status read context is not set"):
        parts["client"].service_status("ORD-4517")
    with pytest.raises(EngineError, match="status read context is not set"):
        parts["client"].cached_status("ORD-4517")
    assert parts["cache"].get("ORD-4517") is None
    assert parts["trace"].events() == []
    assert parts["clock"].now == 1


def test_client_skips_a_second_cancel_while_basis_is_requested():
    parts = _counting("ORD-4517")
    step(parts["clock"])
    first = parts["client"].cancel("ORD-4517")
    assert parts["orders"].calls == 1
    written_at = parts["cache"].get("ORD-4517")["written_at"]
    second = parts["client"].cancel("ORD-4517")
    assert second == first
    assert parts["orders"].calls == 1
    assert parts["cache"].get("ORD-4517")["written_at"] == written_at
    assert len(parts["orders"].history("ORD-4517")) == 2
    skipped = parts["trace"].events()
    assert [event["kind"] for event in skipped] == ["tool", "cache_write", "tool"]
    assert skipped[2]["request_sent"] is False
    assert skipped[2]["tool"] == "cancel_order"
    assert skipped[2]["args"] == {"order_id": "ORD-4517"}
    assert skipped[2]["response"] == first
    assert skipped[2]["step"] == skipped[2]["tick"]
    parts["orders"].settle()
    assert parts["orders"].status_at(parts["clock"].now) == "shipped"
    third = parts["client"].cancel("ORD-4517")
    assert third == first
    assert parts["orders"].calls == 1
    assert parts["cache"].get("ORD-4517")["basis"] == "requested"
    kinds = [event["kind"] for event in parts["trace"].events()]
    assert kinds.count("cache_write") == 1
    assert parts["trace"].events()[-1]["request_sent"] is False


def test_confirmed_cache_does_not_skip_and_rejection_does_not_rewrite_it():
    parts = _counting("ORD-4517")
    step(parts["clock"])
    parts["client"].cancel("ORD-4517")
    arm(parts["trace"], 1)
    parts["client"].service_status("ORD-4517")
    assert parts["cache"].get("ORD-4517")["basis"] == "confirmed"
    again = parts["client"].cancel("ORD-4517")
    assert again["status"] == "accepted"
    assert parts["orders"].calls == 2
    assert parts["cache"].get("ORD-4517")["basis"] == "requested"
    assert parts["cache"].get("ORD-4517")["status"] == "cancelled"
    assert len(parts["orders"].history("ORD-4517")) == 2
    arm(parts["trace"], 2)
    parts["client"].service_status("ORD-4517")
    confirmed = parts["cache"].get("ORD-4517")
    parts["orders"].settle()
    rejected = parts["client"].cancel("ORD-4517")
    assert rejected == {
        "status": "rejected",
        "reason": "not_cancellable",
        "order_status": "shipped",
    }
    assert parts["cache"].get("ORD-4517") == confirmed
    assert parts["orders"].calls == 3
    rejected_event = parts["trace"].events()[-1]
    assert rejected_event["kind"] == "tool"
    assert rejected_event["response"] == rejected
    assert "request_sent" not in rejected_event


def test_cache_request_without_request_id_does_not_call_the_service():
    parts = _counting("ORD-4517")
    parts["cache"].put("ORD-4517", {"status": "cancelled", "basis": "requested"})
    with pytest.raises(EngineError, match="missing request_id"):
        parts["client"].cancel("ORD-4517")
    assert parts["orders"].calls == 0
    assert len(parts["orders"].history("ORD-4517")) == 1
    assert parts["trace"].events() == []
