from tests.support import arm, stack, step


def test_status_and_client_reads_leave_history_and_clock_unchanged():
    parts = stack("ORD-4517")
    step(parts["clock"])
    parts["client"].cancel("ORD-4517")
    history = parts["orders"].history("ORD-4517")
    tick = parts["clock"].now
    cache_before_service_read = parts["cache"].get("ORD-4517")
    arm(parts["trace"], 1)
    expected = {
        1: "processing",
        2: "cancel_requested",
        3: "cancel_requested",
        5: "cancel_requested",
        6: "cancel_requested",
    }
    for query, status in expected.items():
        assert parts["orders"].status_at(query) == status
    for _ in range(4):
        parts["orders"].status_at(6)
        parts["orders"].history("ORD-4517")
    assert parts["clock"].now == tick
    assert parts["orders"].history("ORD-4517") == history
    assert parts["cache"].get("ORD-4517") == cache_before_service_read
    assert parts["client"].service_status("ORD-4517") == "cancel_requested"
    assert parts["client"].cached_status("ORD-4517") == "cancel_requested"
    parts["orders"].status_at(2)
    parts["orders"].status_at(6)
    assert parts["clock"].now == tick
    assert parts["orders"].history("ORD-4517") == history
    assert [entry["status"] for entry in parts["orders"].history("ORD-4517")] == [
        "processing",
        "cancel_requested",
    ]


def test_repeated_mixed_reads_do_not_apply_later_outcomes():
    parts = stack("ORD-4523")
    step(parts["clock"])
    parts["orders"].request_cancel("ORD-4523")
    arm(parts["trace"], 1, max_attempts=10)
    for _ in range(7):
        parts["orders"].status_at(6)
        parts["client"].service_status("ORD-4523")
        parts["client"].cached_status("ORD-4523")
    assert parts["clock"].now == 2
    assert parts["orders"].status_at(6) == "cancel_requested"
    assert len(parts["orders"].history("ORD-4523")) == 2
