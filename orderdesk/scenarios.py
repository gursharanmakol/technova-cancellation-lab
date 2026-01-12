"""Load and validate scenario JSON. Depends on no other lab module."""

import json
from pathlib import Path

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


def _object_pairs(pairs: list[tuple[str, object]]) -> dict:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key}")
        result[key] = value
    return result


def _require_mapping(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or value == "" or not value.isascii():
        raise ValueError(f"{label} must be a non-empty ASCII string")
    return value


def _check_outcomes(outcomes: object) -> None:
    if not isinstance(outcomes, list):
        raise ValueError("outcome_after_request must be a list")
    current = "cancel_requested"
    previous = 0
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            raise ValueError("outcome must be an object")
        if "offset" not in outcome or "status" not in outcome or "cause" not in outcome:
            raise ValueError("outcome must include offset, status and cause")
        offset = outcome["offset"]
        status = outcome["status"]
        cause = outcome["cause"]
        if not _is_int(offset) or offset <= 0:
            raise ValueError("outcome offset must be a positive integer")
        if offset <= previous:
            raise ValueError("outcome offsets must be strictly increasing")
        allowed = _NEXT.get(current, set())
        if not isinstance(status, str) or status not in allowed:
            raise ValueError(f"status {status} does not follow {current}")
        _require_text(cause, "outcome cause")
        current = status
        previous = offset


def validate_scenario(scenario: object) -> None:
    """Raise ValueError when the scenario does not match the published schema."""
    data = _require_mapping(scenario, "scenario")
    for key in ("scenario_id", "ticket", "order", "outcome_after_request"):
        if key not in data:
            raise ValueError(f"missing field {key}")
    scenario_id = data["scenario_id"]
    if not _is_order_id(scenario_id):
        raise ValueError("scenario_id must be an ORD- id")
    ticket = _require_mapping(data["ticket"], "ticket")
    for key in ("ticket_id", "order_id", "customer_ref", "request", "amount_cents"):
        if key not in ticket:
            raise ValueError(f"missing field ticket.{key}")
    _require_text(ticket["ticket_id"], "ticket.ticket_id")
    _require_text(ticket["customer_ref"], "ticket.customer_ref")
    _require_text(ticket["request"], "ticket.request")
    if not _is_order_id(ticket["order_id"]):
        raise ValueError("ticket.order_id must be an ORD- id")
    amount = ticket["amount_cents"]
    if not _is_int(amount) or amount <= 0:
        raise ValueError("amount_cents must be a positive integer")
    order = _require_mapping(data["order"], "order")
    for key in ("order_id", "initial_status"):
        if key not in order:
            raise ValueError(f"missing field order.{key}")
    if not _is_order_id(order["order_id"]):
        raise ValueError("order.order_id must be an ORD- id")
    if scenario_id != ticket["order_id"] or scenario_id != order["order_id"]:
        raise ValueError("scenario_id, ticket.order_id and order.order_id must match")
    if order["initial_status"] != "processing":
        raise ValueError("initial_status must be processing")
    _check_outcomes(data["outcome_after_request"])


def load_scenario(path: str | Path) -> dict:
    """Read one scenario file. The file name stem must equal scenario_id."""
    file_path = Path(path)
    raw = file_path.read_bytes()
    if b"\r" in raw:
        raise ValueError(f"scenario file must use LF line endings: {file_path.name}")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError(f"scenario file is not ASCII: {file_path.name}") from exc
    try:
        data = json.loads(text, object_pairs_hook=_object_pairs)
    except ValueError as exc:
        if str(exc).startswith("duplicate key"):
            raise
        raise ValueError(f"scenario file is not JSON: {file_path.name}") from exc
    validate_scenario(data)
    if file_path.stem != data["scenario_id"]:
        raise ValueError("scenario_id does not match the file name")
    return data


def load_scenarios(directory: str | Path) -> dict[str, dict]:
    """Load every *.json file in a directory, keyed by scenario_id."""
    folder = Path(directory)
    if not folder.is_dir():
        raise ValueError(f"scenario directory is missing: {folder}")
    loaded: dict[str, dict] = {}
    for file_path in sorted(folder.glob("*.json")):
        scenario = load_scenario(file_path)
        scenario_id = scenario["scenario_id"]
        if scenario_id in loaded:
            raise ValueError(f"duplicate scenario_id {scenario_id}")
        loaded[scenario_id] = scenario
    return loaded
