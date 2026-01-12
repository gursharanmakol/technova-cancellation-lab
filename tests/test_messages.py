import pytest

from orderdesk.clock import EngineError
from tests.support import stack


def test_cancel_confirmed_text_and_ids():
    parts = stack("ORD-4517")
    first = parts["messages"].send("ORD-4517", "cancel_confirmed")
    second = parts["messages"].send("ORD-4517", "cancel_confirmed")
    assert first == {
        "status": "sent",
        "message_id": "MSG-4517-1",
        "template": "cancel_confirmed",
    }
    assert second["message_id"] == "MSG-4517-2"
    assert parts["messages"].sent() == [
        {
            "tick": 1,
            "order_id": "ORD-4517",
            "message_id": "MSG-4517-1",
            "template": "cancel_confirmed",
            "text": "Your order ORD-4517 is cancelled. Your refund is on its way.",
        },
        {
            "tick": 1,
            "order_id": "ORD-4517",
            "message_id": "MSG-4517-2",
            "template": "cancel_confirmed",
            "text": "Your order ORD-4517 is cancelled. Your refund is on its way.",
        },
    ]
    assert parts["clock"].now == 1
    sent = parts["messages"].sent()
    sent.clear()
    assert len(parts["messages"].sent()) == 2


def test_unknown_template_records_nothing():
    parts = stack("ORD-4520")
    with pytest.raises(EngineError, match="unknown message template"):
        parts["messages"].send("ORD-4520", "other")
    assert parts["messages"].sent() == []
    assert parts["clock"].now == 1
