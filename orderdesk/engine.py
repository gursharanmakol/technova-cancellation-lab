"""Run one scenario and return its records.

Builds the clock, order service, Payments, Messages, and Trace, then asks
wiring for the controller. After the controller returns, settle applies any
outcomes still due. The cache snapshot is included for display only.
"""

from orderdesk.clock import Clock
from orderdesk.messages import Messages
from orderdesk.order_service import OrderService
from orderdesk.payments import Payments
from orderdesk.trace import Trace
from orderdesk.wiring import build


def run(scenario: dict) -> dict:
    """Load the scenario, run the controller, settle, and return the records."""
    clock = Clock()
    orders = OrderService(clock)
    orders.load_scenario(scenario)
    payments = Payments(orders, clock)
    messages = Messages(clock)
    trace = Trace(clock)
    controller = build(clock, orders, payments, messages, trace, scenario)
    controller.run()
    orders.settle()
    order_id = scenario["scenario_id"]
    return {
        "cache": controller.cache_snapshot(),
        "events": trace.events(),
        "history": orders.history(order_id),
        "ledger": payments.ledger(),
        "messages": messages.sent(),
        "payments_log": payments.log(),
        "tick": clock.now,
        "trace": trace.to_jsonl(),
    }
