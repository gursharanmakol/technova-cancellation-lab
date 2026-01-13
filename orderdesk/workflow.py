"""Support controller: cancel, verify, message the customer, refund, or escalate. One workflow step records that step's events, then advances the clock once."""

from collections.abc import Callable

from orderdesk.clock import Clock, EngineError
from orderdesk.messages import Messages
from orderdesk.order_cache import OrderCache
from orderdesk.order_client import OrderClient
from orderdesk.payments import Payments
from orderdesk.trace import Trace

MAX_ATTEMPTS = 5

_DECISION = {
    "verified": "cancellation_verified",
    "pending": "cancellation_pending",
    "rejected": "cancellation_rejected",
    "not_confirmed": "cancellation_not_confirmed",
    "unexpected": "unexpected_status",
}

_ESCALATE = {
    "rejected": "cancellation_rejected",
    "not_confirmed": "cancellation_not_confirmed",
    "unexpected": "unexpected_status",
}


def _classify(status: object, attempt: int) -> str:
    """Map one read to the section 4 outcome code."""
    if status == "cancelled":
        return "verified"
    if status == "cancel_requested":
        if attempt < MAX_ATTEMPTS:
            return "pending"
        return "not_confirmed"
    if status == "cancel_rejected" or status == "shipped":
        return "rejected"
    return "unexpected"


class Controller:
    def __init__(
        self,
        clock: Clock,
        client: OrderClient,
        payments: Payments,
        messages: Messages,
        trace: Trace,
        scenario: dict,
        cache: OrderCache,
        read_status: Callable[[str], str | None],
    ) -> None:
        self._clock = clock
        self._client = client
        self._payments = payments
        self._messages = messages
        self._trace = trace
        self._scenario = scenario
        self._cache = cache
        self._read_status = read_status

    def cache_snapshot(self) -> dict | None:
        """Copy of this order's cache record, for display. None when it is empty."""
        return self._cache.get(self._scenario["scenario_id"])

    def run(self) -> None:
        order_id = self._scenario["scenario_id"]
        ticket = self._scenario["ticket"]
        self._step(lambda: self._record_ticket(order_id, ticket))
        response = self._step(lambda: self._client.cancel(order_id))
        if response["status"] != "accepted":
            self._step(lambda: self._escalate(order_id, "cancel_request_rejected"))
            return
        outcome = self._verify(order_id)
        if outcome != "verified":
            self._step(lambda: self._escalate(order_id, _ESCALATE[outcome]))
            return
        self._step(lambda: self._send(order_id))
        refund = self._step(lambda: self._refund(order_id, ticket["amount_cents"]))
        if refund["status"] == "accepted":
            self._step(
                lambda: self._trace.record(
                    "case_closed", order_id, outcome="refunded"
                )
            )
            return
        self._step(lambda: self._escalate(order_id, "refund_rejected"))

    def _verify(self, order_id: str) -> str:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            code = self._step(
                lambda attempt=attempt: self._read_and_decide(order_id, attempt)
            )
            if code != "pending":
                return code
        raise EngineError("verification loop ended without a decision")

    def _step(self, action):
        """Record this step, then move the clock one tick."""
        result = action()
        self._clock.advance()
        return result

    def _record_ticket(self, order_id: str, ticket: dict) -> None:
        self._trace.record(
            "ticket",
            order_id,
            ticket_id=ticket["ticket_id"],
            customer_ref=ticket["customer_ref"],
            request=ticket["request"],
            amount_cents=ticket["amount_cents"],
        )

    def _read_and_decide(self, order_id: str, attempt: int) -> str:
        before = len(self._trace.events())
        self._trace.set_status_read("verify_cancellation", attempt, MAX_ATTEMPTS)
        status = self._read_status(order_id)
        added = self._trace.events()[before:]
        reads = [event for event in added if event["kind"] == "status_read"]
        if len(reads) != 1:
            raise EngineError("verification read did not record one status_read")
        code = _classify(status, attempt)
        self._trace.record(
            "decision",
            order_id,
            decision=_DECISION[code],
            based_on_seq=reads[0]["seq"],
            state={
                "cancellation": code,
                "verification_attempts": attempt,
            },
        )
        return code

    def _send(self, order_id: str) -> None:
        template = "cancel_confirmed"
        response = self._messages.send(order_id, template)
        self._trace.record(
            "tool",
            order_id,
            tool="send_customer_message",
            args={"order_id": order_id, "template": template},
            response=response,
        )

    def _refund(self, order_id: str, amount_cents: int) -> dict:
        response = self._payments.issue_refund(order_id, amount_cents)
        self._trace.record(
            "tool",
            order_id,
            tool="issue_refund",
            args={"order_id": order_id, "amount_cents": amount_cents},
            response=response,
        )
        return response

    def _escalate(self, order_id: str, reason: str) -> None:
        self._trace.record("escalate", order_id, reason=reason)
