"""Refund decisions. The ledger holds accepted refunds. The log holds every decision."""

import copy

from orderdesk.clock import Clock, EngineError
from orderdesk.order_service import OrderService


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _order_number(order_id: str) -> str:
    prefix = "ORD-"
    if not isinstance(order_id, str) or not order_id.startswith(prefix):
        raise EngineError(f"unknown order {order_id}")
    number = order_id[len(prefix):]
    if not number.isdigit() or order_id != prefix + number:
        raise EngineError(f"unknown order {order_id}")
    return number


class Payments:
    """Issues a refund only when the order service status at this tick is cancelled."""

    def __init__(self, orders: OrderService, clock: Clock) -> None:
        self._orders = orders
        self._clock = clock
        self._log: list[dict] = []
        self._ledger: list[dict] = []

    def issue_refund(self, order_id: str, amount_cents: int) -> dict:
        """Accept, or reject as precondition_failed or duplicate_refund."""
        if not _is_int(amount_cents):
            raise EngineError("amount_cents must be an integer")
        self._orders.history(order_id)
        status = self._orders.status_at(self._clock.now)
        if status != "cancelled":
            self._log.append(
                {
                    "tick": self._clock.now,
                    "order_id": order_id,
                    "result": "rejected",
                    "reason": "precondition_failed",
                    "order_status": status,
                }
            )
            return {
                "status": "rejected",
                "reason": "precondition_failed",
                "order_status": status,
            }
        if any(entry["order_id"] == order_id for entry in self._ledger):
            self._log.append(
                {
                    "tick": self._clock.now,
                    "order_id": order_id,
                    "result": "rejected",
                    "reason": "duplicate_refund",
                }
            )
            return {"status": "rejected", "reason": "duplicate_refund"}
        refund_id = f"RF-{_order_number(order_id)}-1"
        self._ledger.append(
            {
                "refund_id": refund_id,
                "order_id": order_id,
                "amount_cents": amount_cents,
                "tick": self._clock.now,
            }
        )
        self._log.append(
            {
                "tick": self._clock.now,
                "order_id": order_id,
                "result": "accepted",
                "refund_id": refund_id,
                "amount_cents": amount_cents,
            }
        )
        return {
            "status": "accepted",
            "refund_id": refund_id,
            "amount_cents": amount_cents,
        }

    def log(self) -> list[dict]:
        return copy.deepcopy(self._log)

    def ledger(self) -> list[dict]:
        return copy.deepcopy(self._ledger)
