import pytest

from orderdesk.clock import Clock, EngineError
from orderdesk.order_service import OrderService
from tests.support import accept_cancel_at_tick_two, load_order, scenario, step

EXPECTED_4517 = [
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


def test_ord_4517_settled_history_and_status_at_each_tick():
    _, clock, orders, response = accept_cancel_at_tick_two("ORD-4517")
    assert response == {"status": "accepted", "request_id": "CR-4517-1"}
    assert clock.now == 2
    orders.settle()
    assert clock.now == 6
    assert orders.history("ORD-4517") == EXPECTED_4517
    assert [orders.status_at(tick) for tick in range(1, 7)] == [
        "processing",
        "cancel_requested",
        "cancel_requested",
        "cancel_requested",
        "cancel_rejected",
        "shipped",
    ]


def test_ord_4520_and_ord_4523_tick_status():
    _, clock, orders, response = accept_cancel_at_tick_two("ORD-4520")
    assert response["request_id"] == "CR-4520-1"
    orders.settle()
    assert clock.now == 4
    assert [orders.status_at(tick) for tick in range(1, 5)] == [
        "processing",
        "cancel_requested",
        "cancel_requested",
        "cancelled",
    ]
    _, clock, orders, response = accept_cancel_at_tick_two("ORD-4523")
    assert response["request_id"] == "CR-4523-1"
    orders.settle()
    assert clock.now == 6
    assert [orders.status_at(tick) for tick in range(1, 7)] == [
        "processing",
        "cancel_requested",
        "cancel_requested",
        "cancel_requested",
        "cancel_requested",
        "cancelled",
    ]


def test_manual_ticks_match_settle_for_all_three_orders():
    expected_last = {"ORD-4517": 6, "ORD-4520": 4, "ORD-4523": 6}
    for order_id, last in expected_last.items():
        _, settled_clock, settled, _ = accept_cancel_at_tick_two(order_id)
        settled.settle()
        _, manual_clock, manual, _ = accept_cancel_at_tick_two(order_id)
        while manual_clock.now < last:
            step(manual_clock)
        assert manual.history(order_id) == settled.history(order_id)
        assert manual_clock.now == settled_clock.now == last


def test_outcomes_are_offsets_from_the_accepted_request():
    _, clock, orders = load_order("ORD-4520")
    while clock.now < 4:
        step(clock)
    assert orders.status_at(4) == "processing"
    assert orders.request_cancel("ORD-4520") == {
        "status": "accepted",
        "request_id": "CR-4520-1",
    }
    orders.settle()
    assert clock.now == 6
    assert orders.history("ORD-4520") == [
        {"tick": 1, "status": "processing", "cause": "initial"},
        {
            "tick": 4,
            "status": "cancel_requested",
            "cause": "cancel_request",
            "request_id": "CR-4520-1",
        },
        {"tick": 6, "status": "cancelled", "cause": "order_service"},
    ]
    assert orders.status_at(5) == "cancel_requested"
    assert orders.status_at(6) == "cancelled"


def test_status_at_does_not_preview_or_apply_scheduled_outcomes():
    _, clock, orders, _ = accept_cancel_at_tick_two("ORD-4517")
    assert orders.status_at(5) == "cancel_requested"
    assert orders.status_at(6) == "cancel_requested"
    assert clock.now == 2
    assert len(orders.history("ORD-4517")) == 2


def test_duplicate_cancel_keeps_one_request_and_one_outcome_chain():
    _, clock, orders, first = accept_cancel_at_tick_two("ORD-4517")
    before = orders.history("ORD-4517")
    second = orders.request_cancel("ORD-4517")
    assert second == first == {"status": "accepted", "request_id": "CR-4517-1"}
    assert orders.history("ORD-4517") == before
    assert clock.now == 2
    orders.settle()
    assert orders.history("ORD-4517") == EXPECTED_4517


@pytest.mark.parametrize(
    ("order_id", "tick", "status"),
    [
        ("ORD-4517", 5, "cancel_rejected"),
        ("ORD-4517", 6, "shipped"),
        ("ORD-4520", 4, "cancelled"),
    ],
)
def test_cancel_on_a_non_cancellable_status_changes_nothing(order_id, tick, status):
    _, clock, orders, _ = accept_cancel_at_tick_two(order_id)
    while clock.now < tick:
        step(clock)
    assert orders.status_at(tick) == status
    before = orders.history(order_id)
    assert orders.request_cancel(order_id) == {
        "status": "rejected",
        "reason": "not_cancellable",
        "order_status": status,
    }
    assert orders.history(order_id) == before
    assert clock.now == tick


def test_settle_twice_does_not_add_history():
    _, clock, orders, _ = accept_cancel_at_tick_two("ORD-4520")
    step(clock)
    step(clock)
    assert clock.now == 4
    before = orders.history("ORD-4520")
    assert orders.history("ORD-4520") == before
    orders.settle()
    assert clock.now == 4
    settled = orders.history("ORD-4520")
    orders.settle()
    assert clock.now == 4
    assert orders.history("ORD-4520") == settled


def test_history_copy_is_independent_and_reads_do_not_move_the_clock():
    _, clock, orders, _ = accept_cancel_at_tick_two("ORD-4517")
    snapshot = orders.history("ORD-4517")
    snapshot.append({"tick": 9, "status": "shipped", "cause": "initial"})
    snapshot[0]["status"] = "changed"
    assert orders.history("ORD-4517")[0]["status"] == "processing"
    assert len(orders.history("ORD-4517")) == 2
    for _ in range(20):
        orders.status_at(1)
        orders.status_at(6)
        orders.history("ORD-4517")
    assert clock.now == 2


def test_advancing_only_the_clock_applies_outcomes():
    _, clock, orders, _ = accept_cancel_at_tick_two("ORD-4517")
    assert not hasattr(orders, "apply_due")
    assert orders.status_at(clock.now) == "cancel_requested"
    clock.advance()
    clock.advance()
    assert clock.now == 4
    assert orders.status_at(4) == "cancel_requested"
    assert len(orders.history("ORD-4517")) == 2
    clock.advance()
    assert orders.status_at(5) == "cancel_rejected"
    clock.advance()
    assert clock.now == 6
    assert orders.history("ORD-4517") == EXPECTED_4517


def test_no_sequence_of_advances_can_skip_an_outcome():
    _, clock, orders, _ = accept_cancel_at_tick_two("ORD-4517")
    visited = []
    while clock.now < 6:
        previous = clock.now
        clock.advance()
        assert clock.now == previous + 1
        visited.append((clock.now, orders.status_at(clock.now)))
    assert visited == [
        (3, "cancel_requested"),
        (4, "cancel_requested"),
        (5, "cancel_rejected"),
        (6, "shipped"),
    ]
    assert [entry["status"] for entry in orders.history("ORD-4517")] == [
        "processing",
        "cancel_requested",
        "cancel_rejected",
        "shipped",
    ]
    with pytest.raises(TypeError):
        clock.advance(6)
    assert clock.now == 6


def test_unknown_order_and_second_load_and_late_load():
    loaded, clock, orders = load_order("ORD-4517")
    with pytest.raises(EngineError, match="unknown order ORD-1"):
        orders.request_cancel("ORD-1")
    assert len(orders.history("ORD-4517")) == 1
    with pytest.raises(EngineError, match="scenario already loaded"):
        orders.load_scenario(loaded)
    late = Clock()
    late.advance()
    with pytest.raises(EngineError, match="loaded at tick 1"):
        OrderService(late).load_scenario(scenario("ORD-4520"))


def test_cancel_at_tick_one_uses_that_tick_as_the_offset_base():
    _, clock, orders = load_order("ORD-4520")
    assert orders.request_cancel("ORD-4520")["status"] == "accepted"
    assert orders.status_at(1) == "cancel_requested"
    orders.settle()
    assert clock.now == 3
    assert orders.status_at(2) == "cancel_requested"
    assert orders.status_at(3) == "cancelled"


def test_order_without_outcomes_stays_cancel_requested():
    clock = Clock()
    orders = OrderService(clock)
    orders.load_scenario(
        {
            "scenario_id": "ORD-9",
            "order": {"order_id": "ORD-9", "initial_status": "processing"},
            "outcome_after_request": [],
        }
    )
    step(clock)
    orders.request_cancel("ORD-9")
    orders.settle()
    assert clock.now == 2
    assert orders.status_at(2) == "cancel_requested"
