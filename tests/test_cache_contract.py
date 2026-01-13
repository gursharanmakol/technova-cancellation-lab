from tests.support import arm, stack, step


PUBLISHED_CACHE = {
    "order_id": "ORD-4517",
    "status": "cancelled",
    "basis": "requested",
    "written_at": 2,
    "request_id": "CR-4517-1",
}


def test_put_get_missing_and_independent_copies():
    parts = stack("ORD-4517")
    cache = parts["cache"]
    assert cache.get("ORD-4517") is None
    cache.put("ORD-4517", {"status": "processing", "basis": "confirmed", "written_at": 1})
    first = cache.get("ORD-4517")
    assert first["order_id"] == "ORD-4517"
    assert first["status"] == "processing"
    first["status"] = "changed"
    assert cache.get("ORD-4517")["status"] == "processing"
    cache.put(
        "ORD-4517",
        {"order_id": "ORD-4517", "status": "cancelled", "basis": "requested", "written_at": 2},
    )
    assert cache.get("ORD-4517")["basis"] == "requested"


def test_accepted_cancel_writes_the_published_cache_record():
    parts = stack("ORD-4517")
    step(parts["clock"])
    response = parts["client"].cancel("ORD-4517")
    assert response == {"status": "accepted", "request_id": "CR-4517-1"}
    assert parts["cache"].get("ORD-4517") == PUBLISHED_CACHE
    events = parts["trace"].events()
    assert [event["kind"] for event in events] == ["tool", "cache_write"]
    assert events[0]["tool"] == "cancel_order"
    assert events[0]["args"] == {"order_id": "ORD-4517"}
    assert events[0]["response"] == response
    assert "request_sent" not in events[0]
    assert events[1]["record"] == PUBLISHED_CACHE
    assert all(event["step"] == event["tick"] == 2 for event in events)
    assert parts["clock"].now == 2


def test_service_read_writes_confirmed_and_cache_read_does_not():
    parts = stack("ORD-4517")
    step(parts["clock"])
    parts["client"].cancel("ORD-4517")
    parts["cache"].put(
        "ORD-4517",
        {
            "status": "cancelled",
            "basis": "confirmed",
            "written_at": 2,
            "request_id": "CR-4517-1",
        },
    )
    confirmed = parts["cache"].get("ORD-4517")
    arm(parts["trace"], 1)
    before = [event["kind"] for event in parts["trace"].events()]
    assert parts["client"].cached_status("ORD-4517") == "cancelled"
    assert [event["kind"] for event in parts["trace"].events()] == before + ["status_read"]
    assert parts["cache"].get("ORD-4517") == confirmed
    step(parts["clock"])
    arm(parts["trace"], 2)
    assert parts["client"].service_status("ORD-4517") == "cancel_requested"
    assert [event["kind"] for event in parts["trace"].events()][-2:] == [
        "status_read",
        "cache_write",
    ]
    assert parts["cache"].get("ORD-4517") == {
        "order_id": "ORD-4517",
        "status": "cancel_requested",
        "basis": "confirmed",
        "written_at": 3,
        "request_id": "CR-4517-1",
    }


def test_written_at_follows_the_clock():
    parts = stack("ORD-4520")
    response = parts["client"].cancel("ORD-4520")
    assert response["status"] == "accepted"
    record = parts["cache"].get("ORD-4520")
    assert record["written_at"] == 1
    assert record["basis"] == "requested"
    assert record["status"] == "cancelled"
    assert parts["clock"].now == 1
