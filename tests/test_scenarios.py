import copy
import json

import pytest

from orderdesk.clock import Clock
from orderdesk.order_service import OrderService
from orderdesk.scenarios import load_scenario, load_scenarios, validate_scenario
from tests.support import SCENARIOS, scenario


def test_directory_loads_the_three_orders():
    loaded = load_scenarios(SCENARIOS)
    assert list(loaded) == ["ORD-4517", "ORD-4520", "ORD-4523"]


def test_shipped_files_are_canonical_ascii_lf_json():
    for path in sorted(SCENARIOS.glob("*.json")):
        raw = path.read_bytes()
        text = raw.decode("ascii")
        assert "\r" not in text
        data = json.loads(text)
        rendered = json.dumps(data, indent=2, ensure_ascii=True, sort_keys=True) + "\n"
        assert text == rendered


def test_ord_4517_fields():
    data = scenario("ORD-4517")
    assert data["scenario_id"] == "ORD-4517"
    assert data["ticket"] == {
        "amount_cents": 8900,
        "customer_ref": "C-2231",
        "order_id": "ORD-4517",
        "request": "Please cancel my order and refund me.",
        "ticket_id": "SUP-3301",
    }
    assert data["order"] == {"initial_status": "processing", "order_id": "ORD-4517"}
    assert data["outcome_after_request"] == [
        {"cause": "warehouse", "offset": 3, "status": "cancel_rejected"},
        {"cause": "warehouse", "offset": 4, "status": "shipped"},
    ]


def test_ord_4520_and_ord_4523_specified_fields():
    first = scenario("ORD-4520")
    assert first["ticket"]["ticket_id"] == "SUP-3302"
    assert first["ticket"]["customer_ref"] == "C-2236"
    assert first["ticket"]["amount_cents"] == 4500
    assert first["ticket"]["order_id"] == "ORD-4520"
    assert first["order"]["initial_status"] == "processing"
    assert first["outcome_after_request"] == [
        {"cause": "order_service", "offset": 2, "status": "cancelled"}
    ]
    second = scenario("ORD-4523")
    assert second["ticket"]["ticket_id"] == "SUP-3303"
    assert second["ticket"]["customer_ref"] == "C-2240"
    assert second["ticket"]["amount_cents"] == 13250
    assert second["ticket"]["order_id"] == "ORD-4523"
    assert second["outcome_after_request"] == [
        {"cause": "order_service", "offset": 4, "status": "cancelled"}
    ]


def test_loaded_scenarios_are_accepted_by_the_order_service():
    for order_id in ("ORD-4517", "ORD-4520", "ORD-4523"):
        clock = Clock()
        orders = OrderService(clock)
        orders.load_scenario(scenario(order_id))
        assert orders.history(order_id) == [
            {"tick": 1, "status": "processing", "cause": "initial"}
        ]
        assert orders.status_at(1) == "processing"
        assert clock.now == 1


def _edited(**changes) -> dict:
    data = copy.deepcopy(scenario("ORD-4517"))
    for key, value in changes.items():
        data[key] = value
    return data


@pytest.mark.parametrize("offset", [0, -1, True, 1.5])
def test_offset_must_be_a_positive_integer(offset):
    data = scenario("ORD-4517")
    edited = copy.deepcopy(data)
    edited["outcome_after_request"][0]["offset"] = offset
    with pytest.raises(ValueError, match="positive integer"):
        validate_scenario(edited)


@pytest.mark.parametrize("second_offset", [3, 2])
def test_offsets_must_be_strictly_increasing(second_offset):
    edited = copy.deepcopy(scenario("ORD-4517"))
    edited["outcome_after_request"][1]["offset"] = second_offset
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_scenario(edited)


def test_status_must_follow_the_state_machine():
    edited = copy.deepcopy(scenario("ORD-4517"))
    edited["outcome_after_request"] = [
        {"offset": 1, "status": "shipped", "cause": "warehouse"}
    ]
    with pytest.raises(ValueError, match="does not follow"):
        validate_scenario(edited)
    edited["outcome_after_request"] = [
        {"offset": 1, "status": "cancelled", "cause": "order_service"},
        {"offset": 2, "status": "shipped", "cause": "warehouse"},
    ]
    with pytest.raises(ValueError, match="does not follow"):
        validate_scenario(edited)


def test_empty_outcomes_are_valid():
    edited = copy.deepcopy(scenario("ORD-4517"))
    edited["outcome_after_request"] = []
    validate_scenario(edited)


def test_ids_must_match_and_amount_must_be_positive():
    edited = copy.deepcopy(scenario("ORD-4517"))
    edited["order"]["order_id"] = "ORD-1"
    with pytest.raises(ValueError, match="must match"):
        validate_scenario(edited)
    edited = copy.deepcopy(scenario("ORD-4517"))
    edited["ticket"]["amount_cents"] = 0
    with pytest.raises(ValueError, match="amount_cents"):
        validate_scenario(edited)
    edited = copy.deepcopy(scenario("ORD-4517"))
    del edited["ticket"]["request"]
    with pytest.raises(ValueError, match="missing field ticket.request"):
        validate_scenario(edited)


def test_file_rejects_crlf_non_ascii_duplicate_keys_and_stem_mismatch(tmp_path):
    canonical = (SCENARIOS / "ORD-4517.json").read_text(encoding="ascii")
    crlf = tmp_path / "ORD-4517.json"
    crlf.write_bytes(canonical.replace("\n", "\r\n").encode("ascii"))
    with pytest.raises(ValueError, match="LF line endings"):
        load_scenario(crlf)
    raw = tmp_path / "ORD-4517.json"
    raw.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="not ASCII"):
        load_scenario(raw)
    duplicate = tmp_path / "ORD-1.json"
    duplicate.write_bytes(b'{"scenario_id": "ORD-1", "scenario_id": "ORD-1"}\n')
    with pytest.raises(ValueError, match="duplicate key"):
        load_scenario(duplicate)
    renamed = tmp_path / "ORD-9999.json"
    renamed.write_text(canonical, encoding="ascii", newline="\n")
    with pytest.raises(ValueError, match="does not match the file name"):
        load_scenario(renamed)


def test_service_rejects_an_illegal_outcome_without_keeping_it():
    from orderdesk.clock import EngineError

    clock = Clock()
    orders = OrderService(clock)
    with pytest.raises(EngineError, match="cannot move from cancel_requested to shipped"):
        orders.load_scenario(
            {
                "scenario_id": "ORD-4517",
                "order": {"order_id": "ORD-4517", "initial_status": "processing"},
                "outcome_after_request": [
                    {"offset": 1, "status": "shipped", "cause": "warehouse"}
                ],
            }
        )
    with pytest.raises(EngineError, match="no order is loaded"):
        orders.status_at(1)
    orders.load_scenario(scenario("ORD-4520"))
    assert orders.status_at(1) == "processing"
