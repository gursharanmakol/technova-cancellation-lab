"""In-memory component sequence used to check deterministic output.

This is not the support workflow. It loads ORD-4517, accepts a cancel at
tick 2, asks the cache for a status at tick 3, asks the order service at
tick 4, settles, and attempts one refund.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orderdesk.clock import Clock
from orderdesk.messages import Messages
from orderdesk.order_cache import OrderCache
from orderdesk.order_client import OrderClient
from orderdesk.order_service import OrderService
from orderdesk.payments import Payments
from orderdesk.scenarios import load_scenario
from orderdesk.trace import Trace, canonical_json

ORDER_ID = "ORD-4517"


def scripted_state() -> dict:
    scenario = load_scenario(ROOT / "scenarios" / f"{ORDER_ID}.json")
    clock = Clock()
    orders = OrderService(clock)
    orders.load_scenario(scenario)
    cache = OrderCache()
    trace = Trace(clock)
    client = OrderClient(orders, cache, trace, clock)
    payments = Payments(orders, clock)
    messages = Messages(clock)

    clock.advance()
    client.cancel(ORDER_ID)

    clock.advance()
    trace.set_status_read("verify_cancellation", 1, 5)
    client.cached_status(ORDER_ID)

    clock.advance()
    trace.set_status_read("verify_cancellation", 2, 5)
    client.service_status(ORDER_ID)

    orders.settle()
    payments.issue_refund(ORDER_ID, scenario["ticket"]["amount_cents"])

    return {
        "cache": cache.get(ORDER_ID),
        "history": orders.history(ORDER_ID),
        "ledger": payments.ledger(),
        "messages": messages.sent(),
        "payments_log": payments.log(),
        "tick": clock.now,
        "trace": trace.to_jsonl(),
    }


def scripted_bytes() -> bytes:
    return (canonical_json(scripted_state()) + "\n").encode("ascii")


def main() -> None:
    sys.stdout.buffer.write(scripted_bytes())


if __name__ == "__main__":
    main()
