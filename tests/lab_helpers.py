"""Run one scenario and return its trace events and world records."""

from pathlib import Path

from orderdesk.engine import run
from orderdesk.scenarios import load_scenario

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "scenarios"


class World:
    """Order history, messages sent, and the Payments log for one run."""

    def __init__(self, history: list[dict], messages: list[dict], payments_log: list[dict]) -> None:
        self.messages = messages
        self.payments_log = payments_log
        self._history = history

    def status_at(self, tick: int) -> str:
        """Status after every recorded change at or before tick."""
        chosen = None
        for entry in self._history:
            if entry["tick"] <= tick:
                chosen = entry
            else:
                break
        if chosen is None:
            raise ValueError(f"no status at tick {tick}")
        return chosen["status"]


class ScenarioRun:
    """Events and world records from one scenario."""

    def __init__(self, events: list[dict], world: World) -> None:
        self.events = events
        self.world = world


def run_scenario(order_id: str) -> ScenarioRun:
    """Load scenarios/<order_id>.json, run it, and return the records."""
    result = run(load_scenario(SCENARIOS / f"{order_id}.json"))
    world = World(result["history"], result["messages"], result["payments_log"])
    return ScenarioRun(result["events"], world)
