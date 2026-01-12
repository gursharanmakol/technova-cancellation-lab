"""Customer messages that were sent."""

import copy

from orderdesk.clock import Clock, EngineError

_TEMPLATES = {
    "cancel_confirmed": "Your order {order_id} is cancelled. Your refund is on its way.",
}


def _order_number(order_id: str) -> str:
    prefix = "ORD-"
    if not isinstance(order_id, str) or not order_id.startswith(prefix):
        raise EngineError(f"unknown order {order_id}")
    number = order_id[len(prefix):]
    if not number.isdigit() or order_id != prefix + number:
        raise EngineError(f"unknown order {order_id}")
    return number


class Messages:
    """Records each message at the current tick. Sending does not move the clock."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._sent: list[dict] = []
        self._counts: dict[str, int] = {}

    def send(self, order_id: str, template: str) -> dict:
        if template not in _TEMPLATES:
            raise EngineError(f"unknown message template {template}")
        number = _order_number(order_id)
        count = self._counts.get(order_id, 0) + 1
        self._counts[order_id] = count
        message_id = f"MSG-{number}-{count}"
        self._sent.append(
            {
                "tick": self._clock.now,
                "order_id": order_id,
                "message_id": message_id,
                "template": template,
                "text": _TEMPLATES[template].format(order_id=order_id),
            }
        )
        return {"status": "sent", "message_id": message_id, "template": template}

    def sent(self) -> list[dict]:
        return copy.deepcopy(self._sent)
