"""Cancel requests and the status reader.

cancel records a cancel_order tool event.

The reader that asks the order service records status_read with read_from
order_service.

Call Trace.set_status_read before the reader. The reader itself takes
only an order id. The reader does not move the clock or the order history.
"""

from orderdesk.clock import Clock
from orderdesk.order_service import OrderService
from orderdesk.trace import Trace


class OrderClient:
    def __init__(
        self,
        orders: OrderService,
        trace: Trace,
        clock: Clock,
    ) -> None:
        self._orders = orders
        self._trace = trace
        self._clock = clock

    def cancel(self, order_id: str) -> dict:
        self._orders.history(order_id)
        response = self._orders.request_cancel(order_id)
        self._record_cancel(order_id, response)
        return response

    def service_status(self, order_id: str) -> str:
        """Read the order service."""
        context = self._ready(order_id)
        status = self._orders.status_at(self._clock.now)
        self._trace.record(
            "status_read",
            order_id,
            purpose=context["purpose"],
            read_from="order_service",
            result=status,
            attempt=context["attempt"],
            max_attempts=context["max_attempts"],
            record={"status": status},
        )
        return status

    def _ready(self, order_id: str) -> dict:
        context = self._trace.status_read_context()
        self._orders.history(order_id)
        return context

    def _record_cancel(self, order_id: str, response: dict) -> None:
        fields: dict[str, object] = {
            "tool": "cancel_order",
            "args": {"order_id": order_id},
            "response": response,
        }
        self._trace.record("tool", order_id, **fields)
