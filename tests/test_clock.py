import pytest

from orderdesk.clock import MAX_TICK, Clock, EngineError
from orderdesk.order_service import OrderService
from tests.support import ROOT


def test_reads_do_not_advance_time():
    clock = Clock()
    assert clock.now == 1
    for _ in range(1000):
        assert clock.now == 1
    assert clock.advance() == 2
    assert clock.now == 2


def test_advance_reaches_200_and_the_next_advance_stops():
    clock = Clock()
    for expected in range(2, MAX_TICK + 1):
        assert clock.advance() == expected
    assert clock.now == 200
    assert MAX_TICK == 200
    with pytest.raises(EngineError, match="logical clock stopped at 200 ticks"):
        clock.advance()
    assert clock.now == 200
    assert clock.now == 200


def test_settle_stops_when_an_outcome_is_past_the_clock_limit():
    clock = Clock()
    orders = OrderService(clock)
    orders.load_scenario(
        {
            "scenario_id": "ORD-1",
            "order": {"order_id": "ORD-1", "initial_status": "processing"},
            "outcome_after_request": [
                {"offset": 200, "status": "cancelled", "cause": "order_service"}
            ],
        }
    )
    orders.request_cancel("ORD-1")
    with pytest.raises(EngineError, match="logical clock stopped at 200 ticks"):
        orders.settle()
    assert clock.now == 200
    assert orders.status_at(200) == "cancel_requested"
    history = orders.history("ORD-1")
    assert [entry["status"] for entry in history] == ["processing", "cancel_requested"]
    assert [entry["tick"] for entry in history] == [1, 1]


def test_component_sources_do_not_sleep_or_draw_random_numbers():
    banned = (
        "time.sleep",
        "import random",
        "from random",
        "import time",
        "datetime",
        "secrets",
    )
    paths = list((ROOT / "orderdesk").glob("*.py"))
    assert paths
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for snippet in banned:
            assert snippet not in text
