"""Command line: run, trace, show, world, and verify."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _load_runtime() -> None:
    """Import the lab modules after the bundle command has had its chance.

    bundle must still run when a protected file is a symbolic link, so it
    does not load the rest of the package.
    """
    global status_at, EngineError, load_scenario, load_scenarios, canonical_json
    from orderdesk.acceptance import status_at as status_at_fn
    from orderdesk.clock import EngineError as engine_error
    from orderdesk.scenarios import load_scenario as load_one
    from orderdesk.scenarios import load_scenarios as load_all
    from orderdesk.trace import canonical_json as canon

    status_at = status_at_fn
    EngineError = engine_error
    load_scenario = load_one
    load_scenarios = load_all
    canonical_json = canon

ROOT = Path(__file__).resolve().parent
SCENARIOS = ROOT / "scenarios"
RUNS = ROOT / "runs"
VERIFY_TIMEOUT_SECONDS = 40

PYTEST_TIMEOUT_SECONDS = 60
GIT_TIMEOUT_SECONDS = 15


def emit(text: str) -> None:
    if not text.endswith("\n"):
        text += "\n"
    sys.stdout.buffer.write(text.encode("ascii"))


def _presentation() -> bool:
    """Color and the trailing blank line, only on a real terminal."""
    import os

    if "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb":
        return False
    if not sys.stdout.isatty():
        return False
    return _enable_vt()


def _enable_vt() -> bool:
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel.GetStdHandle(-11)
        mode = ctypes.c_uint()
        if kernel.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
            return False
        if kernel.SetConsoleMode(handle, mode.value | 0x0004) == 0:
            return False
    except (AttributeError, OSError):
        return False
    return True


def _finish() -> None:
    if _presentation():
        sys.stdout.buffer.write(b"\n")


def _paint(line: str, failed: bool) -> str:
    if not _presentation():
        return line
    color = "\x1b[31m" if failed else "\x1b[32m"
    return color + line + "\x1b[0m"


def _color_verdicts(text: str) -> str:
    painted = []
    for line in text.splitlines():
        if line.startswith(("V1 ", "V2 ", "V3 ", "V4 ", "V5 ", "Integrity:", "Result:")):
            for word, color in (("PASS", "\x1b[32m"), ("FAIL", "\x1b[31m")):
                token = " " + word
                if token in line:
                    line = line.replace(token, " " + color + word + "\x1b[0m", 1)
                    break
        painted.append(line)
    if text.endswith("\n"):
        return "\n".join(painted) + "\n"
    return "\n".join(painted)


def _write_presented(payload: bytes) -> None:
    if _presentation() and payload:
        payload = _color_verdicts(payload.decode("ascii")).encode("ascii")
    sys.stdout.buffer.write(payload)


def scenario_ids() -> list[str]:
    return sorted(load_scenarios(SCENARIOS))


def dump(value: object) -> bytes:
    text = json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    return text.encode("ascii")


def run_scenario(order_id: str) -> dict:
    from orderdesk.engine import run

    return run(load_scenario(SCENARIOS / f"{order_id}.json"))


def write_run(order_id: str, result: dict) -> None:
    folder = RUNS / order_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "trace.jsonl").write_bytes(result["trace"].encode("ascii"))
    (folder / "world.json").write_bytes(dump(_world_record(order_id, result)))
    (folder / "summary.json").write_bytes(dump(_summary_record(order_id, result)))


def _world_record(order_id: str, result: dict) -> dict:
    return {
        "ledger": result["ledger"],
        "messages": result["messages"],
        "order_service": {"history": result["history"], "order_id": order_id},
        "payments_log": result["payments_log"],
        "tick": result["tick"],
    }


def _summary_record(order_id: str, result: dict) -> dict:
    reasons = [event["reason"] for event in result["events"] if event["kind"] == "escalate"]
    return {
        "cache": result["cache"],
        "escalation": reasons[-1] if reasons else None,
        "messages_sent": [
            {"message_id": message["message_id"], "template": message["template"]}
            for message in result["messages"]
        ],
        "order_id": order_id,
        "refunds": [
            {"amount_cents": entry["amount_cents"], "refund_id": entry["refund_id"]}
            for entry in result["ledger"]
        ],
    }


def summary_text(order_id: str, result: dict) -> str:
    record = _summary_record(order_id, result)
    if record["messages_sent"]:
        messages = ", ".join(
            f"{item['message_id']} {item['template']}" for item in record["messages_sent"]
        )
    else:
        messages = "none"
    if record["refunds"]:
        refunds = ", ".join(
            f"{item['refund_id']} {item['amount_cents']}" for item in record["refunds"]
        )
    else:
        refunds = "none"
    escalation = record["escalation"] if record["escalation"] else "none"
    return (
        f"{order_id}\n"
        f"Messages sent: {messages}\n"
        f"Refunds: {refunds}\n"
        f"Escalation: {escalation}\n"
    )


def unknown_order(order_id: str) -> int:
    emit(f"Unknown order {order_id}.")
    emit("Valid orders: " + ", ".join(scenario_ids()))
    return 2


def resolve_one(token: str) -> str | None:
    if token in scenario_ids():
        return token
    return None


def cmd_run(args: list[str]) -> int:
    if len(args) != 1:
        emit("Usage: python lab.py run <order or all>")
        return 2
    if args[0] == "all":
        order_ids = scenario_ids()
    else:
        order_id = resolve_one(args[0])
        if order_id is None:
            return unknown_order(args[0])
        order_ids = [order_id]
    exit_code = 0
    for index, order_id in enumerate(order_ids):
        if index:
            emit("")
        try:
            result = run_scenario(order_id)
        except EngineError as exc:
            emit(f"{order_id}: {exc}")
            exit_code = 1
            continue
        except Exception as exc:
            emit(f"{order_id}: {_first_line(exc)}")
            exit_code = 1
            continue
        write_run(order_id, result)
        emit(summary_text(order_id, result).rstrip("\n"))
    return exit_code


def _overwrite(order_id: str) -> dict | None:
    """Run the scenario on the current code and overwrite runs/<order>/."""
    try:
        result = run_scenario(order_id)
    except EngineError as exc:
        emit(f"{order_id}: {exc}")
        return None
    except Exception as exc:
        emit(f"{order_id}: {_first_line(exc)}")
        return None
    write_run(order_id, result)
    return result


def cmd_trace(args: list[str]) -> int:
    full = False
    tokens = []
    for arg in args:
        if arg == "--full":
            full = True
        else:
            tokens.append(arg)
    if len(tokens) != 1:
        emit("Usage: python lab.py trace <order> [--full]")
        return 2
    order_id = resolve_one(tokens[0])
    if order_id is None:
        return unknown_order(tokens[0])
    if _overwrite(order_id) is None:
        return 1
    text = (RUNS / order_id / "trace.jsonl").read_text(encoding="ascii")
    if full:
        sys.stdout.buffer.write(text.encode("ascii"))
        if text and not text.endswith("\n"):
            sys.stdout.buffer.write(b"\n")
        return 0
    for line in _trace_lines(_events(text)):
        emit(line)
    return 0


def cmd_show(args: list[str]) -> int:
    if len(args) != 2:
        emit("Usage: python lab.py show <order> <seq>")
        return 2
    order_id = resolve_one(args[0])
    if order_id is None:
        return unknown_order(args[0])
    result = _overwrite(order_id)
    if result is None:
        return 1
    events = result["events"]
    count = len(events)
    raw = args[1]
    if not raw.isdigit() or raw.startswith("0") and raw != "0":
        _seq_range(raw, count)
        return 2
    seq = int(raw)
    if count == 0 or seq < 1 or seq > count:
        _seq_range(raw, count)
        return 2
    event = events[seq - 1]
    text = json.dumps(event, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    sys.stdout.buffer.write(text.encode("ascii"))
    return 0


def cmd_world(args: list[str]) -> int:
    if len(args) != 1:
        emit("Usage: python lab.py world <order>")
        return 2
    order_id = resolve_one(args[0])
    if order_id is None:
        return unknown_order(args[0])
    result = _overwrite(order_id)
    if result is None:
        return 1
    emit(_world_text(_world_record(order_id, result)).rstrip("\n"))
    return 0


def cmd_verify(args: list[str]) -> int:
    if len(args) > 1:
        emit("Usage: python lab.py verify [order or all]")
        return 2
    if not args or args[0] == "all":
        order_ids = scenario_ids()
    else:
        order_id = resolve_one(args[0])
        if order_id is None:
            return unknown_order(args[0])
        order_ids = [order_id]
    code, stdout, stderr, timed_out = _run_captured(
        [sys.executable, str(ROOT / "lab.py"), "--verify-worker", *order_ids],
        ROOT,
        VERIFY_TIMEOUT_SECONDS,
    )
    if timed_out:
        _write_presented(_timed_out_text())
        return 1
    _write_presented(stdout)
    if code == 0:
        return 0
    if stdout.startswith(b"ERROR:") or stdout.startswith(b"V"):
        return 1
    first = _first_bytes(stderr or stdout)
    emit(f"ERROR: could not run scenarios: {first}")
    return 1


def verify_worker(order_ids: list[str]) -> int:
    try:
        from orderdesk.acceptance import backend_tests_pass, evaluate, format_report
        from orderdesk.engine import run
    except Exception as exc:
        emit(f"ERROR: could not run scenarios: {_first_line(exc)}")
        return 1
    runs = {}
    failed: list[tuple[str, int | None]] = []
    for order_id in order_ids:
        try:
            runs[order_id] = run(load_scenario(SCENARIOS / f"{order_id}.json"))
        except EngineError as exc:
            failed.append((order_id, _tick_from_error(exc)))
        except Exception as exc:
            emit(f"ERROR: could not run scenarios: {_first_line(exc)}")
            return 1
    checks = evaluate(runs, failed=failed, v5_passed=backend_tests_pass(ROOT))
    report = format_report(checks)
    sys.stdout.buffer.write(report.encode("ascii"))
    integrity_ok = "\nIntegrity: PASS\n" in report
    if failed or not integrity_ok or not all(check.passed for check in checks):
        return 1
    return 0


def _world_text(world: dict) -> str:
    order = world["order_service"]
    history = order["history"]
    lines = ["Order Service history"]
    lines.extend(_history_table(history))
    lines.append("")
    lines.append("Order Service status at each tick")
    lines.extend(_status_table(history, world["tick"]))
    lines.append("")
    lines.append("Payments log")
    lines.extend(_payment_lines(world["payments_log"]))
    lines.append("")
    lines.append("Payments ledger")
    lines.extend(_record_lines(world["ledger"]))
    lines.append("")
    lines.append("Messages")
    lines.extend(_message_lines(world["messages"]))
    lines.append("")
    return "\n".join(lines)


def _history_table(history: list[dict]) -> list[str]:
    rows = []
    for entry in history:
        request = str(entry["request_id"]) if entry.get("request_id") else ""
        rows.append([str(entry["tick"]), str(entry["status"]), str(entry["cause"]), request])
    return _align(["TICK", "STATUS", "CAUSE", "REQUEST"], rows)


def _status_table(history: list[dict], final_tick: int) -> list[str]:
    rows = [
        [str(tick), str(status_at(history, tick))]
        for tick in range(1, final_tick + 1)
    ]
    return _align(["TICK", "STATUS"], rows)


_PAYMENT_COLUMNS = (
    ("TICK", "tick"),
    ("RESULT", "result"),
    ("REASON", "reason"),
    ("ORDER_STATUS", "order_status"),
    ("REFUND_ID", "refund_id"),
    ("AMOUNT_CENTS", "amount_cents"),
)


def _payment_lines(entries: list[dict]) -> list[str]:
    if not entries:
        return ["none"]
    shown = [
        pair for pair in _PAYMENT_COLUMNS if any(_filled(entry, pair[1]) for entry in entries)
    ]
    rows = []
    for entry in entries:
        rows.append([_cell(entry, key) for _header, key in shown])
    return _align([header for header, _key in shown], rows)


def _filled(entry: dict, key: str) -> bool:
    if key not in entry:
        return False
    value = entry[key]
    return value is not None and value != ""


def _cell(entry: dict, key: str) -> str:
    if not _filled(entry, key):
        return ""
    return str(entry[key])


def _align(headers: list[str], rows: list[list[str]]) -> list[str]:
    table = [headers, *rows]
    count = len(headers)
    widths = [0] * count
    for row in table:
        for index in range(count):
            widths[index] = max(widths[index], len(row[index]))
    lines = []
    for row in table:
        cells = [
            row[index].ljust(widths[index]) if index < count - 1 else row[index]
            for index in range(count)
        ]
        lines.append("  ".join(cells).rstrip())
    return lines


def _record_lines(records: list[dict]) -> list[str]:
    if not records:
        return ["none"]
    return [canonical_json(record) for record in records]


def _message_lines(messages: list[dict]) -> list[str]:
    if not messages:
        return ["none"]
    rows = [
        [
            str(message["tick"]),
            str(message["message_id"]),
            str(message["template"]),
            str(message["text"]),
        ]
        for message in messages
    ]
    return _align(["TICK", "MESSAGE_ID", "TEMPLATE", "TEXT"], rows)


def _trace_lines(events: list[dict]) -> list[str]:
    rows = [
        (
            str(event["seq"]),
            str(event["step"]),
            str(event["tick"]),
            str(event["kind"]),
            _trace_detail(event),
        )
        for event in events
    ]
    headers = ("SEQ", "STEP", "TICK", "KIND", "DETAIL")
    widths = [len(header) for header in headers]
    for row in rows:
        for index, part in enumerate(row):
            widths[index] = max(widths[index], len(part))

    def format_row(parts: tuple[str, ...]) -> str:
        cells = [parts[index].ljust(widths[index]) for index in range(4)]
        line = "  ".join(cells)
        if parts[4]:
            line = f"{line}  {parts[4]}"
        return line.rstrip()

    return [format_row(headers), *[format_row(row) for row in rows]]


def _events(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if line:
            events.append(json.loads(line))
    return events


def _trace_detail(event: dict) -> str:
    kind = event["kind"]
    if kind == "status_read":
        result = "null" if event["result"] is None else event["result"]
        return (
            f"{event['read_from']} -> {result} "
            f"(attempt {event['attempt']} of {event['max_attempts']})"
        )
    if kind == "ticket":
        return str(event.get("ticket_id", ""))
    if kind == "tool":
        return _tool_detail(event)
    if kind == "cache_write":
        record = event.get("record") or {}
        return f"{record.get('status')}/{record.get('basis')}"
    if kind == "decision":
        return str(event.get("decision", ""))
    if kind == "escalate":
        return str(event.get("reason", ""))
    if kind == "case_closed":
        return str(event.get("outcome", ""))
    return ""


def _tool_detail(event: dict) -> str:
    tool = str(event.get("tool", ""))
    response = event.get("response") or {}
    status = str(response.get("status", ""))
    extra = ""
    if tool == "cancel_order" and response.get("request_id"):
        extra = " " + str(response["request_id"])
    elif tool == "issue_refund" and response.get("refund_id"):
        extra = " " + str(response["refund_id"])
    elif tool == "send_customer_message":
        template = (event.get("args") or {}).get("template", "")
        extra = " " + str(template)
    return f"{tool} {status}{extra}".strip()


def _seq_range(raw: str, count: int) -> None:
    if count < 1:
        emit(f"Seq {raw} is out of range. Valid range: none.")
        return
    emit(f"Seq {raw} is out of range. Valid range: 1 to {count}.")


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    if not text:
        return exc.__class__.__name__
    return text.splitlines()[0]


def _first_bytes(payload: bytes) -> str:
    text = payload.decode("ascii", "replace").strip()
    if not text:
        return "could not run scenarios"
    return text.splitlines()[0]


def _tick_from_error(exc: EngineError) -> int | None:
    numbers = [int(part) for part in str(exc).replace(".", " ").split() if part.isdigit()]
    if not numbers:
        return None
    return numbers[-1]


def cmd_check(args: list[str]) -> int:
    if args:
        emit("Usage: python lab.py check")
        return 2
    problems = []
    if sys.version_info < (3, 12):
        problems.append("Python 3.12 or newer is required.")
    try:
        import pytest  # noqa: F401
    except ImportError:
        problems.append("pytest is not importable.")
    try:
        ids = scenario_ids()
        if ids != ["ORD-4517", "ORD-4520", "ORD-4523"]:
            problems.append("scenarios did not load.")
    except Exception as exc:
        problems.append(f"scenarios did not load: {_first_line(exc)}")
        ids = []
    if not problems and ids:
        try:
            from orderdesk.engine import run

            run(load_scenario(SCENARIOS / f"{ids[0]}.json"))
        except Exception as exc:
            problems.append(f"engine run did not complete: {_first_line(exc)}")
    if shutil_which("git") is None:
        emit("git: not found (warning)")
    else:
        emit("git: present")
    from orderdesk.acceptance import integrity_status

    _ok, line = integrity_status(ROOT)
    if _ok:
        emit(line)
    else:
        emit(line + " (warning)")
    if problems:
        for problem in problems:
            emit(_paint(problem, failed=True))
        return 1
    emit(_paint("Ready. Next: python lab.py run ORD-4517", failed=False))
    return 0


def cmd_test(args: list[str]) -> int:
    if args:
        emit("Usage: python lab.py test")
        return 2
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests"],
        cwd=ROOT,
        check=False,
    )
    return completed.returncode


def cmd_bundle(args: list[str]) -> int:
    if args:
        emit("Usage: python lab.py bundle")
        return 2
    try:
        from orderdesk.bundle import build_bundle

        sys.stdout.buffer.write(build_bundle(ROOT))
    except Exception as exc:
        emit(f"bundle failed: {_first_line(exc)}")
    return 0


def shutil_which(name: str) -> str | None:
    return __import__("shutil").which(name)


def _run_captured(args: list[str], cwd: Path, timeout: float) -> tuple[int, bytes, bytes, bool]:
    proc = subprocess.Popen(
        args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc.pid)
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
        return proc.returncode or 1, b"", b"", True
    return proc.returncode or 0, stdout or b"", stderr or b"", False


def _kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            check=False,
        )
        return
    try:
        os.kill(pid, 15)
    except OSError:
        return


def _timed_out_text() -> bytes:
    from orderdesk.acceptance import integrity_status

    _ok, line = integrity_status(ROOT)
    text = (
        "timed out\n"
        "V1 FAIL\n"
        "V2 FAIL\n"
        "V3 FAIL\n"
        "V4 FAIL\n"
        "V5 FAIL\n"
        f"{line}\n"
        "Result: FAIL\n"
    )
    return text.encode("ascii")


def main(argv: list[str]) -> int:
    if argv and argv[0] == "bundle":
        return cmd_bundle(argv[1:])
    _load_runtime()
    if argv and argv[0] == "--verify-worker":
        return verify_worker(argv[1:])
    if not argv:
        emit("Usage: python lab.py <command>")
        emit("Commands: check, run, trace, show, world, test, verify, bundle")
        return 2
    command, args = argv[0], argv[1:]
    if command == "check":
        code = cmd_check(args)
    elif command == "run":
        code = cmd_run(args)
    elif command == "trace":
        code = cmd_trace(args)
    elif command == "show":
        code = cmd_show(args)
    elif command == "world":
        code = cmd_world(args)
    elif command == "verify":
        code = cmd_verify(args)
    elif command == "test":
        return cmd_test(args)
    elif command == "bundle":
        return cmd_bundle(args)
    else:
        emit("Usage: python lab.py <command>")
        emit("Commands: check, run, trace, show, world, test, verify, bundle")
        return 2
    _finish()
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
