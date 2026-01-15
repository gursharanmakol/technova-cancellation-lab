"""Regression checks for the cancellation evidence.

The worked example is already filled in. TODO 1 and TODO 2 stop until they
are replaced with real checks.
"""

import pytest

from tests.lab_helpers import run_scenario


def test_cancel_for_ord_4517_is_accepted():
    result = run_scenario("ORD-4517")
    cancel = next(
        event
        for event in result.events
        if event.get("kind") == "tool" and event.get("tool") == "cancel_order"
    )
    assert cancel["response"]["status"] == "accepted"


# TODO 1. For every cancellation_verified decision, the status_read event named
# by its based_on_seq must have a result equal to world.status_at(tick) at
# that read's tick. Check ORD-4517 and ORD-4520.


def test_todo_1_verified_read_matches_world_status():
    pytest.fail("Complete TODO 1 above, then delete this line.")


# TODO 2. ORD-4517 has no cancel_confirmed message and no Payments log entry.


def test_todo_2_rejected_order_has_no_message_and_no_payment():
    pytest.fail("Complete TODO 2 above, then delete this line.")
