"""Order status history for one scenario.

The service registers with its clock at construction. request_cancel records
cancel_requested at the current tick and schedules outcomes at offsets from
that tick. Every Clock.advance applies outcomes due at the new tick.
status_at and history do not apply outcomes and do not move the clock.
settle advances the clock until every scheduled outcome is applied.
"""

import copy

from orderdesk.clock import Clock, EngineError

# Outcome order after an accepted cancel request. Section 4 state machine.
_NEXT = {
    "cancel_requested": {"cancelled", "cancel_rejected"},
    "cancel_rejected": {"shipped"},
    "cancelled": set(),
    "shipped": set(),
}


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_order_id(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("ORD-"):
        return False
    number = value[4:]
    return number.isdigit() and value == "ORD-" + number


def _order_number(order_id: str) -> str:
    if not _is_order_id(order_id):
        raise EngineError(f"unknown order {order_id}")
    return order_id[4:]


def _checked_outcomes(order_id: str, outcomes: object) -> tuple[tuple[int, str, str], ...]:
    if not isinstance(outcomes, list):
        raise EngineError("outcome_after_request must be a list")
    current = "cancel_requested"
    previous = 0
    checked: list[tuple[int, str, str]] = []
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            raise EngineError("outcome must be an object")
        offset = outcome.get("offset")
        status = outcome.get("status")
        cause = outcome.get("cause")
        if not _is_int(offset) or offset <= 0:
            raise EngineError("outcome offset must be a positive integer")
        if offset <= previous:
            raise EngineError("outcome offsets must be strictly increasing")
        allowed = _NEXT.get(current, set())
        if not isinstance(status, str) or status not in allowed:
            raise EngineError(f"order {order_id} cannot move from {current} to {status}")
        if not isinstance(cause, str) or cause == "" or not cause.isascii():
            raise EngineError("outcome cause must be a non-empty ASCII string")
        checked.append((offset, status, cause))
        current = status
        previous = offset
    return tuple(checked)


def _parse_scenario(scenario: object) -> tuple[str, tuple[tuple[int, str, str], ...]]:
    if not isinstance(scenario, dict):
        raise EngineError("scenario must be an object")
    scenario_id = scenario.get("scenario_id")
    order = scenario.get("order")
    if not isinstance(order, dict):
        raise EngineError("scenario order must be an object")
    order_id = order.get("order_id")
    if not _is_order_id(scenario_id) or scenario_id != order_id:
        raise EngineError("scenario_id and order.order_id must be an ORD- id")
    if order.get("initial_status") != "processing":
        raise EngineError("initial_status must be processing")
    if "outcome_after_request" not in scenario:
        raise EngineError("missing field outcome_after_request")
    outcomes = _checked_outcomes(order_id, scenario["outcome_after_request"])
    return order_id, outcomes


class OrderService:
    """Status history for the single order in one scenario run."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        clock.register(self._apply_due)
        self._order_id: str | None = None
        self._history: list[dict] = []
        self._outcomes: tuple[tuple[int, str, str], ...] = ()
        self._scheduled: list[dict] = []
        self._request_id: str | None = None

    def load_scenario(self, scenario: dict) -> None:
        """Record the initial status at tick 1. The clock must be at tick 1."""
        if self._clock.now != 1:
            raise EngineError("an order can only be loaded at tick 1")
        if self._order_id is not None:
            raise EngineError("scenario already loaded")
        order_id, outcomes = _parse_scenario(scenario)
        self._order_id = order_id
        self._outcomes = outcomes
        self._history = [
            {"tick": 1, "status": "processing", "cause": "initial"}
        ]
        self._scheduled = []
        self._request_id = None

    def request_cancel(self, order_id: str) -> dict:
        """Accept a cancel while processing, or repeat the same acceptance."""
        self._require(order_id)
        status = self.status_at(self._clock.now)
        if status == "cancel_requested":
            return {"status": "accepted", "request_id": self._request_id}
        if status != "processing":
            return {
                "status": "rejected",
                "reason": "not_cancellable",
                "order_status": status,
            }
        request_tick = self._clock.now
        planned = [
            {
                "tick": request_tick + offset,
                "status": outcome_status,
                "cause": cause,
                "applied": False,
            }
            for offset, outcome_status, cause in self._outcomes
        ]
        request_id = f"CR-{_order_number(order_id)}-1"
        self._history.append(
            {
                "tick": request_tick,
                "status": "cancel_requested",
                "cause": "cancel_request",
                "request_id": request_id,
            }
        )
        self._request_id = request_id
        self._scheduled = planned
        return {"status": "accepted", "request_id": request_id}

    def _apply_due(self, tick: int) -> None:
        """Apply outcomes scheduled at this tick. Called by Clock.advance."""
        if not _is_int(tick) or tick < 1:
            raise EngineError("tick must be a positive integer")
        if tick != self._clock.now:
            raise EngineError(
                f"outcomes can only be applied at the current tick {self._clock.now}"
            )
        if self._order_id is None:
            return
        for item in self._scheduled:
            if item["applied"] or item["tick"] != tick:
                continue
            current = self.status_at(tick)
            allowed = _NEXT.get(current, set())
            if item["status"] not in allowed:
                raise EngineError(
                    f"order {self._order_id} cannot move from {current} to {item['status']}"
                )
            self._history.append(
                {"tick": tick, "status": item["status"], "cause": item["cause"]}
            )
            item["applied"] = True

    def status_at(self, tick: int) -> str:
        """Status after every change recorded at or before tick. Does not apply outcomes."""
        if not _is_int(tick) or tick < 1:
            raise EngineError("tick must be a positive integer")
        if self._order_id is None:
            raise EngineError("no order is loaded")
        chosen = None
        for entry in self._history:
            if entry["tick"] <= tick:
                chosen = entry
            else:
                break
        if chosen is None:
            raise EngineError(f"no status at tick {tick}")
        return chosen["status"]

    def history(self, order_id: str) -> list[dict]:
        """Copy of the status history. Mutating the copy does not change the record."""
        self._require(order_id)
        return copy.deepcopy(self._history)

    def settle(self) -> None:
        """Advance one tick at a time until every scheduled outcome is applied."""
        if self._order_id is None:
            return
        while True:
            pending = [item["tick"] for item in self._scheduled if not item["applied"]]
            if not pending:
                return
            nxt = min(pending)
            if nxt <= self._clock.now:
                raise EngineError(f"scheduled outcome at tick {nxt} was not applied")
            self._clock.advance()

    def _require(self, order_id: str) -> None:
        if self._order_id is None:
            raise EngineError("no order is loaded")
        if order_id != self._order_id:
            raise EngineError(f"unknown order {order_id}")
