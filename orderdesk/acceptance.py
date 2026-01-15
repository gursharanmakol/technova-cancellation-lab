"""V1 to V5 from finished records, plus integrity.

The checks read the order history, the payments log, the ledger, messages,
and trace events. They do not run the workflow. Integrity compares protected
files with lab_manifest.json and checks that protected modules were loaded
from those files.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HEALTHY = ("ORD-4520", "ORD-4523")
REJECTED_ORDER = "ORD-4517"


class Check:
    """One invariant result. A violation is (order_id, tick or None, fact or None)."""

    def __init__(self, name: str, violations: list[tuple[str, int | None]]) -> None:
        self.name = name
        self.violations = violations

    @property
    def passed(self) -> bool:
        return not self.violations


def status_at(history: list[dict], tick: int) -> str:
    """Status after every recorded change at or before tick."""
    chosen = None
    for entry in history:
        if entry["tick"] <= tick:
            chosen = entry
        else:
            break
    if chosen is None:
        raise ValueError(f"no status at tick {tick}")
    return chosen["status"]


def backend_tests_pass(root: Path) -> bool:
    """V5. True when the backend invariant tests pass."""
    env = dict(os.environ)
    for name in list(env):
        if name.startswith("PYTEST_"):
            env.pop(name)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_backend_invariant.py",
            "--tb=no",
            "-p",
            "no:cacheprovider",
        ],
        cwd=root,
        capture_output=True,
        env=env,
        check=False,
    )
    return completed.returncode == 0


def evaluate(
    runs: dict[str, dict],
    *,
    failed: list[tuple[str, int | None]] | None = None,
    v5_passed: bool = True,
) -> list[Check]:
    """Judge V1 to V5. failed lists scenarios that did not finish, with a tick."""
    unfinished = list(failed or [])
    present = set(runs) | {order_id for order_id, _tick in unfinished}
    v1: list[tuple] = []
    v2: list[tuple] = []
    v3: list[tuple] = []
    v4: list[tuple] = []
    for order_id, tick in unfinished:
        v1.append((order_id, tick, None))
        v2.append((order_id, tick, None))
        if order_id in HEALTHY:
            v3.append(_unticked(order_id, tick, "0 refunds"))
        if order_id == REJECTED_ORDER:
            v4.append(_unticked(order_id, tick, "no escalation"))
    for order_id, record in runs.items():
        history = record["history"]
        for message in record["messages"]:
            if message.get("template") != "cancel_confirmed":
                continue
            tick = message["tick"]
            if status_at(history, tick) != "cancelled":
                v1.append((order_id, tick, None))
        for entry in record["payments_log"]:
            tick = entry["tick"]
            if status_at(history, tick) != "cancelled":
                v1.append((order_id, tick, None))
        for entry in record["ledger"]:
            tick = entry["tick"]
            if status_at(history, tick) != "cancelled":
                v2.append((order_id, tick, None))
        if order_id == REJECTED_ORDER:
            v4.extend(_rejected_order_violations(record))
    for order_id in HEALTHY:
        if order_id not in present:
            v3.append((order_id, None, "0 refunds"))
            continue
        record = runs.get(order_id)
        if record is None:
            continue
        ledger = record["ledger"]
        if len(ledger) == 1:
            continue
        if not ledger:
            v3.append((order_id, None, "0 refunds"))
            continue
        for entry in ledger:
            v3.append((order_id, entry["tick"], None))
    if REJECTED_ORDER not in present:
        v4.append((REJECTED_ORDER, None, "no escalation"))
    v5 = [] if v5_passed else [("V5", None, None)]
    return [
        Check("V1", _unique(v1)),
        Check("V2", _unique(v2)),
        Check("V3", _unique(v3)),
        Check("V4", _unique(v4)),
        Check("V5", v5),
    ]


def integrity_status(root: Path | None = None) -> tuple[bool, str]:
    """PASS, or FAIL and the protected paths that do not match."""
    folder = ROOT if root is None else root
    failed = _integrity_paths(folder)
    if not failed:
        return True, "Integrity: PASS"
    return False, "Integrity: FAIL " + ", ".join(failed)


def format_report(checks: list[Check], root: Path | None = None) -> str:
    """V1 to V5, integrity, and Result. No cause text."""
    lines = [_format_check(check) for check in checks]
    integrity_ok, integrity_line = integrity_status(root)
    lines.append(integrity_line)
    passed = integrity_ok and all(check.passed for check in checks)
    lines.append("Result: PASS" if passed else "Result: FAIL")
    return "\n".join(lines) + "\n"


def _integrity_paths(root: Path) -> list[str]:
    manifest_path = root / "lab_manifest.json"
    if not manifest_path.is_file():
        return ["lab_manifest.json"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest["files"]
        protected = list(manifest["protected"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return ["lab_manifest.json"]
    failed = []
    for rel in protected:
        path = root / rel
        digest = files.get(rel)
        if not path.is_file() or not isinstance(digest, str) or _sha256_lf(path) != digest:
            failed.append(rel)
    failed.extend(_shadow_paths(root, protected))
    chosen = []
    for rel in failed:
        if rel not in chosen:
            chosen.append(rel)
    return chosen


def _sha256_lf(path: Path) -> str:
    data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(data).hexdigest()


def _shadow_paths(root: Path, protected: list[str]) -> list[str]:
    failed = []
    for rel in protected:
        name = _module_name(rel)
        if name is None:
            continue
        module = sys.modules.get(name)
        if module is None:
            continue
        loaded = getattr(module, "__file__", None)
        expected = (root / rel).resolve()
        if loaded is None or Path(loaded).resolve() != expected:
            failed.append(rel)
    return failed


def _module_name(rel: str) -> str | None:
    if not rel.startswith("orderdesk/") or not rel.endswith(".py"):
        return None
    if rel == "orderdesk/__init__.py":
        return "orderdesk"
    return "orderdesk." + Path(rel).stem


def _unticked(order_id: str, tick: int | None, fact: str) -> tuple:
    if tick is None:
        return (order_id, None, fact)
    return (order_id, tick, None)


def _rejected_order_violations(record: dict) -> list[tuple]:
    found: list[tuple] = []
    for message in record["messages"]:
        if message.get("template") == "cancel_confirmed":
            found.append((REJECTED_ORDER, message["tick"], None))
    for entry in record["payments_log"]:
        found.append((REJECTED_ORDER, entry["tick"], None))
    events = record["events"]
    if not events or events[-1].get("kind") != "escalate":
        found.append((REJECTED_ORDER, None, "no escalation"))
    return found


def _format_check(check: Check) -> str:
    if check.passed:
        return f"{check.name} PASS"
    if check.name == "V5":
        return "V5 FAIL"
    parts = [_violation_text(item) for item in check.violations]
    parts = [part for part in parts if part]
    if not parts:
        return f"{check.name} FAIL"
    return f"{check.name} FAIL " + ", ".join(parts)


def _violation_text(item: tuple) -> str:
    order_id, tick, fact = item
    if order_id == "V5":
        return ""
    if tick is not None:
        return f"{order_id} t{tick}"
    if fact:
        return f"{order_id} ({fact})"
    return order_id


def _unique(items: list[tuple]) -> list[tuple]:
    chosen: list[tuple] = []
    for item in items:
        if item not in chosen:
            chosen.append(item)
    return chosen
