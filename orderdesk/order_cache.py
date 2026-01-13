"""Client cache of a status the client requested or confirmed."""

import copy


class OrderCache:
    """Stores one record per order. get returns None when no record exists."""

    def __init__(self) -> None:
        self._records: dict[str, dict] = {}

    def put(self, order_id: str, record: dict) -> None:
        if not isinstance(order_id, str) or order_id == "":
            raise ValueError("order_id must be a non-empty string")
        if not isinstance(record, dict):
            raise ValueError("cache record must be an object")
        if "order_id" in record and record["order_id"] != order_id:
            raise ValueError("cache record order_id does not match")
        stored = copy.deepcopy(record)
        stored["order_id"] = order_id
        self._records[order_id] = stored

    def get(self, order_id: str) -> dict | None:
        record = self._records.get(order_id)
        if record is None:
            return None
        return copy.deepcopy(record)
