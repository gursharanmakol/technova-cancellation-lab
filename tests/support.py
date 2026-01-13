"""Helpers for the unit tests."""

from pathlib import Path

from orderdesk.clock import Clock
from orderdesk.messages import Messages
from orderdesk.order_cache import OrderCache
from orderdesk.order_client import OrderClient
from orderdesk.order_service import OrderService
from orderdesk.payments import Payments
from orderdesk.scenarios import load_scenario
from orderdesk.trace import Trace

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "scenarios"


def scenario(order_id: str) -> dict:
    return load_scenario(SCENARIOS / f"{order_id}.json")


def load_order(order_id: str):
    loaded = scenario(order_id)
    clock = Clock()
    orders = OrderService(clock)
    orders.load_scenario(loaded)
    return loaded, clock, orders


def step(clock: Clock) -> None:
    """Advance one tick. The clock applies due outcomes."""
    clock.advance()


def accept_cancel_at_tick_two(order_id: str):
    loaded, clock, orders = load_order(order_id)
    step(clock)
    response = orders.request_cancel(order_id)
    return loaded, clock, orders, response


def stack(order_id: str) -> dict:
    loaded, clock, orders = load_order(order_id)
    cache = OrderCache()
    trace = Trace(clock)
    return {
        "scenario": loaded,
        "clock": clock,
        "orders": orders,
        "cache": cache,
        "trace": trace,
        "client": OrderClient(orders, cache, trace, clock),
        "payments": Payments(orders, clock),
        "messages": Messages(clock),
        "order_id": order_id,
    }


def arm(trace: Trace, attempt: int, max_attempts: int = 5) -> None:
    trace.set_status_read("verify_cancellation", attempt, max_attempts)
