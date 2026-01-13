"""Build the cache, the order client, and the controller for one run."""

from orderdesk.clock import Clock
from orderdesk.messages import Messages
from orderdesk.order_cache import OrderCache
from orderdesk.order_client import OrderClient
from orderdesk.order_service import OrderService
from orderdesk.payments import Payments
from orderdesk.trace import Trace
from orderdesk.workflow import Controller


def build(
    clock: Clock,
    orders: OrderService,
    payments: Payments,
    messages: Messages,
    trace: Trace,
    scenario: dict,
) -> Controller:
    cache = OrderCache()
    client = OrderClient(orders, cache, trace, clock)
    read_status = client.service_status
    return Controller(
        clock, client, payments, messages, trace, scenario, cache, read_status
    )
