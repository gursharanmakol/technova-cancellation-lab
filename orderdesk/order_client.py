"""Cancel requests and the two status readers.

cancel records a cancel_order tool event. On an accepted request it then
records cache_write of the full cache record (status cancelled, basis
requested). A later cancel is skipped while that record still has basis
requested: the tool event has request_sent false and there is no cache_write.

The reader that asks the order service records status_read with read_from
order_service, then writes basis confirmed and records cache_write.
The reader that asks the cache records only status_read with read_from
order_cache.

Call Trace.set_status_read before either reader. The reader itself takes
only an order id. Neither reader moves the clock or the order history.
"""

from orderdesk.clock import Clock, EngineError
from orderdesk.order_cache import OrderCache
from orderdesk.order_service import OrderService
from orderdesk.trace import Trace


class OrderClient:
    def __init__(
        self,
        orders: OrderService,
        cache: OrderCache,
        trace: Trace,
        clock: Clock,
    ) -> None:
        self._orders = orders
        self._cache = cache
        self._trace = trace
        self._clock = clock

    def cancel(self, order_id: str) -> dict:
        self._orders.history(order_id)
        existing = self._cache.get(order_id)
        if existing is not None and existing.get("basis") == "requested":
            request_id = existing.get("request_id")
            if not isinstance(request_id, str) or request_id == "":
                raise EngineError(f"order {order_id} cache request is missing request_id")
            response = {"status": "accepted", "request_id": request_id}
            self._record_cancel(order_id, response, request_sent=False)
            return response
        response = self._orders.request_cancel(order_id)
        self._record_cancel(order_id, response, request_sent=True)
        if response["status"] == "accepted":
            record = {
                "order_id": order_id,
                "status": "cancelled",
                "basis": "requested",
                "written_at": self._clock.now,
                "request_id": response["request_id"],
            }
            self._cache.put(order_id, record)
            self._trace.record("cache_write", order_id, record=record)
        return response

    def service_status(self, order_id: str) -> str:
        """Read the order service, then refresh the cache with basis confirmed."""
        context = self._ready(order_id)
        status = self._orders.status_at(self._clock.now)
        previous = self._cache.get(order_id)
        stored = {
            "order_id": order_id,
            "status": status,
            "basis": "confirmed",
            "written_at": self._clock.now,
        }
        request_id = None if previous is None else previous.get("request_id")
        if isinstance(request_id, str) and request_id != "":
            stored["request_id"] = request_id
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
        self._cache.put(order_id, stored)
        self._trace.record("cache_write", order_id, record=stored)
        return status

    def cached_status(self, order_id: str) -> str | None:
        """Return the cached status, or None when the cache has no record."""
        context = self._ready(order_id)
        record = self._cache.get(order_id)
        if record is None:
            result = None
            shown = None
        else:
            result = record.get("status")
            if not isinstance(result, str):
                raise EngineError(f"order {order_id} cache record has no status")
            shown = {key: value for key, value in record.items() if key != "order_id"}
        self._trace.record(
            "status_read",
            order_id,
            purpose=context["purpose"],
            read_from="order_cache",
            result=result,
            attempt=context["attempt"],
            max_attempts=context["max_attempts"],
            record=shown,
        )
        return result

    def _ready(self, order_id: str) -> dict:
        context = self._trace.status_read_context()
        self._orders.history(order_id)
        return context

    def _record_cancel(self, order_id: str, response: dict, request_sent: bool) -> None:
        fields: dict[str, object] = {
            "tool": "cancel_order",
            "args": {"order_id": order_id},
            "response": response,
        }
        if not request_sent:
            fields["request_sent"] = False
        self._trace.record("tool", order_id, **fields)
