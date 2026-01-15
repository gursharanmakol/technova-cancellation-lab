"""Evidence bundle. Does not import learner-editable modules.

pytest and verify run in subprocesses. Git calls are bounded. The pasted
text is ASCII. Local files bundle_full.txt and evidence/ are gitignored.
"""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import zlib
from pathlib import Path

GIT_TIMEOUT_SECONDS = 15
PYTEST_TIMEOUT_SECONDS = 60
VERIFY_TIMEOUT_SECONDS = 60
MAX_PLAIN_LINES = 400
CLAIM_LIMIT = 80
FAILURE_LIMIT = 40
SNAPSHOT_LIMIT = 256 * 1024
IGNORED_DIRS = {".git", "runs", "evidence", "__pycache__", ".venv", ".pytest_cache"}
IGNORED_FILES = {"bundle_full.txt"}


def build_bundle(root: Path) -> bytes:
    """Return the four pasted sections. Always safe for the caller to print."""
    full = _render(root)
    (root / "bundle_full.txt").write_bytes(full)
    return full


def _render(root: Path) -> bytes:
    version = _version(root)
    manifest_sha = _file_sha(root / "lab_manifest.json")
    changes = _learner_changes(root, version, manifest_sha)
    automated = _automated(root)
    claims = _claims(root)
    environment = _environment(root, version, manifest_sha, changes)
    automated_text, automated_note = _truncate(automated, FAILURE_LIMIT, claims=False)
    claims_text, claims_note = _truncate(claims, CLAIM_LIMIT, claims=True)
    auto = "== AUTOMATED RESULTS ==\n" + _display(automated_text).rstrip("\n")
    if automated_note:
        auto += "\n" + _display(automated_note)
    claims_block = "== LEARNER CLAIMS (INCIDENT.md) ==\n" + _display(claims_text).rstrip("\n")
    if claims_note:
        claims_block += "\n" + _display(claims_note)
    changes_section = changes["section"]
    if not changes_section.endswith("\n"):
        changes_section += "\n"
    text = (
        _display(environment).rstrip("\n")
        + "\n\n"
        + changes_section
        + "\n"
        + auto
        + "\n\n"
        + claims_block
        + "\n"
    )
    return text.encode("ascii")


def _learner_changes(root: Path, version: str, manifest_sha: str | None) -> dict:
    git_ok = _git_available()
    starter = _starter(root) if git_ok else None
    if git_ok and starter and _is_repo(root):
        return _git_diff_changes(root, starter)
    manifest = _read_manifest(root)
    if manifest is not None and manifest_sha is not None:
        return _snapshot_changes(root, manifest, version, manifest_sha)
    return _none_changes(root, "no usable git base and the manifest is missing or unparseable")


def _git_diff_changes(root: Path, starter: str) -> dict:
    excluded = []
    name_status = _git(root, ["diff", "--name-status", "--no-renames", starter, "--", "."])
    tracked = name_status.stdout if name_status.returncode == 0 else ""
    diff = _git(
        root,
        [
            "diff",
            "--binary",
            "--no-color",
            "--no-ext-diff",
            "--full-index",
            starter,
            "--",
            ".",
        ],
    )
    body = diff.stdout if diff.returncode == 0 else ""
    modified = added = deleted = 0
    for line in tracked.splitlines():
        if not line.strip():
            continue
        status = line.split("\t", 1)[0]
        if status.startswith("D"):
            deleted += 1
        elif status.startswith("A"):
            added += 1
        else:
            modified += 1
    for path in _untracked(root):
        file_path = root / path
        if file_path.is_symlink():
            excluded.append((path, "symlink", _link_size(file_path)))
            continue
        try:
            data = file_path.read_bytes()
        except OSError:
            excluded.append((path, "unreadable", 0))
            continue
        if _binary(data):
            excluded.append((path, "binary" if b"\0" in data else "not utf-8", len(data)))
            continue
        text = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n").decode("utf-8")
        body += _new_file_diff(path, text)
        added += 1
    for rel, size in _recorded_symlinks(root):
        if any(item[0] == rel for item in excluded):
            continue
        excluded.append((rel, "symlink", size))
    if not body.strip():
        body = "no changes\n"
    elif not body.endswith("\n"):
        body += "\n"
    return _pack(
        root,
        "git-diff",
        "PARTIAL" if excluded else "COMPLETE",
        f"commit {starter} (fourth commit from root)",
        modified,
        added,
        deleted,
        excluded,
        body,
    )


def _snapshot_changes(root: Path, manifest: dict, version: str, manifest_sha: str) -> dict:
    recorded = manifest.get("files")
    if not isinstance(recorded, dict):
        return _none_changes(root, "manifest files are not an object")
    present = _tree_files(root)
    excluded = []
    chunks = []
    modified = added = deleted = 0
    for rel in sorted(present):
        path = root / rel
        if path.is_symlink():
            excluded.append((rel, "symlink", _link_size(path)))
            continue
        try:
            data = path.read_bytes()
        except OSError:
            excluded.append((rel, "unreadable", 0))
            continue
        if len(data) > SNAPSHOT_LIMIT:
            excluded.append((rel, "over 256 KB", len(data)))
            continue
        if _binary(data):
            reason = "binary" if b"\0" in data else "not utf-8"
            excluded.append((rel, reason, len(data)))
            continue
        normalized = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        digest = hashlib.sha256(normalized).hexdigest()
        previous = recorded.get(rel)
        if previous == digest:
            continue
        kind = "modified" if rel in recorded else "added"
        if kind == "modified":
            modified += 1
        else:
            added += 1
        chunks.append(f"FILE {digest} {len(normalized)} {rel}\n")
        chunks.append(normalized.decode("utf-8"))
        if not normalized.endswith(b"\n"):
            chunks.append("\n")
        chunks.append("\n")
    for rel in sorted(recorded):
        if rel not in present:
            deleted += 1
            chunks.append(f"DELETE {rel}\n")
    body = "".join(chunks) if chunks else "no changes\n"
    return _pack(
        root,
        "file-snapshot",
        "PARTIAL" if excluded else "COMPLETE",
        f"manifest {version} sha256 {manifest_sha}",
        modified,
        added,
        deleted,
        excluded,
        body,
    )


def _none_changes(root: Path, reason: str) -> dict:
    paths = []
    if _is_repo(root) and _git_available():
        completed = _git(root, ["status", "--porcelain"])
        paths = [line[3:] for line in completed.stdout.splitlines() if len(line) > 3]
    body = reason + "\n"
    if paths:
        body += "\n".join(paths) + "\n"
    return _pack(root, "none", "NOT REPLAYABLE", "unknown", 0, 0, 0, [], body)


def _pack(root, fmt, status, base, modified, added, deleted, excluded, body) -> dict:
    if (not _is_ascii(body)) or len(body.splitlines()) > MAX_PLAIN_LINES:
        shown = _compress(body)
    else:
        shown = body
    excluded_text = "none"
    if excluded:
        parts = [f"{path} ({reason}, {size} bytes)" for path, reason, size in excluded]
        excluded_text = "; ".join(parts)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    header = "\n".join(
        [
            "== LEARNER CHANGES ==",
            f"CHANGES FORMAT: {fmt}",
            f"CHANGES STATUS: {status}",
            f"BASE: {base}",
            f"FILES: {modified} modified, {added} added, {deleted} deleted",
            f"EXCLUDED: {excluded_text}",
            f"CHANGES SHA-256: {digest}",
            f"CHANGES LINES: {len(body.splitlines())}",
        ]
    )
    _write_evidence(root, body, digest, fmt)
    return {"section": header + "\n" + shown, "format": fmt, "status": status, "body": body}


def _write_evidence(root: Path, body: str, digest: str, fmt: str) -> None:
    folder = root / "evidence"
    folder.mkdir(parents=True, exist_ok=True)
    suffix = "patch" if fmt == "git-diff" else "snapshot"
    (folder / f"learner.{suffix}").write_text(body, encoding="utf-8", newline="\n")
    (folder / f"learner.{suffix}.sha256").write_text(digest + "\n", encoding="ascii", newline="\n")


def _compress(body: str) -> str:
    raw = zlib.compress(body.encode("utf-8"), level=9)
    import base64

    encoded = base64.b64encode(raw).decode("ascii")
    wrapped = "\n".join(encoded[index:index + 76] for index in range(0, len(encoded), 76))
    return "BEGIN LEARNER CHANGES zlib+base64\n" + wrapped + "\nEND LEARNER CHANGES\n"


def _automated(root: Path) -> str:
    code, out, err, timed = _limited(
        [sys.executable, "-m", "pytest", "-q", "tests"],
        root,
        PYTEST_TIMEOUT_SECONDS,
    )
    if timed:
        pytest_text = "pytest exit: timed out after 60 s"
    else:
        text = (out + b"\n" + err).decode("utf-8", "replace")
        pytest_text = f"pytest exit: {code}\n" + _pytest_view(text)
    vcode, vout, verr, vtimed = _limited(
        [sys.executable, str(root / "lab.py"), "verify"],
        root,
        VERIFY_TIMEOUT_SECONDS,
    )
    if vtimed:
        verify_text = "verify exit: timed out after 60 s"
    else:
        verify_text = f"verify exit: {vcode}\n" + (vout + verr).decode("utf-8", "replace")
    return pytest_text.rstrip("\n") + "\n" + verify_text.rstrip("\n")


def _pytest_view(text: str) -> str:
    lines = text.splitlines()
    summary = ""
    for line in lines:
        if " passed" in line or " failed" in line or line.startswith("="):
            summary = line
    failed_ids = [line.split()[1] for line in lines if line.startswith("FAILED ") and len(line.split()) > 1]
    start = None
    for index, line in enumerate(lines):
        if "FAILURES" in line or "ERRORS" in line:
            start = index
            break
    block = lines[start:start + FAILURE_LIMIT] if start is not None else lines[:FAILURE_LIMIT]
    remaining = failed_ids[1:]
    parts = [summary] if summary else []
    parts.extend(block)
    if remaining:
        parts.append("remaining: " + ", ".join(remaining))
    return "\n".join(parts)


def _claims(root: Path) -> str:
    path = root / "INCIDENT.md"
    if not path.is_file():
        return "missing"
    text = path.read_text(encoding="utf-8")
    sections = _incident_sections(text)
    lines = []
    for title, body in sections:
        state = "filled" if body.strip() else "empty"
        lines.append(f"{title} [{state}]")
        if body.strip():
            lines.extend(body.strip("\n").splitlines())
    return "\n".join(lines)


def _incident_sections(text: str) -> list[tuple[str, str]]:
    import re

    cleaned = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    matches = list(re.finditer(r"^##\s+(\d+\.\s+.+)$", cleaned, flags=re.M))
    found = []
    for index, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
        found.append((title, cleaned[start:end]))
    return found


def _environment(root: Path, version: str, manifest_sha: str | None, changes: dict) -> str:
    head = _git_line(root, ["rev-parse", "HEAD"])
    starter = _starter(root) if _git_available() and _is_repo(root) else None
    since = "unavailable"
    status_count = "unavailable"
    if starter:
        since = _git_line(root, ["rev-list", "--count", f"{starter}..HEAD"])
        status = _git(root, ["status", "--porcelain"])
        status_count = str(len([line for line in status.stdout.splitlines() if line.strip()]))
    manifest_changes = _manifest_change_count(root)
    lines = [
        "== ENVIRONMENT ==",
        f"OS: {platform.system()} {platform.release()}",
        f"platform: {platform.platform()}",
        f"Python: {platform.python_version()}",
        f"lab version: {version}",
        f"manifest sha256: {manifest_sha or 'unavailable'}",
        f"git HEAD: {head}",
        f"starter: {starter or 'unavailable'}",
        f"commits since starter: {since}",
        f"git status: {status_count}",
        f"manifest changes: {manifest_changes}",
    ]
    return "\n".join(lines)


def _manifest_change_count(root: Path) -> str:
    manifest = _read_manifest(root)
    if manifest is None or not isinstance(manifest.get("files"), dict):
        return "unavailable"
    recorded = manifest["files"]
    present = _tree_files(root)
    count = 0
    for rel in present:
        path = root / rel
        try:
            data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        except OSError:
            count += 1
            continue
        if recorded.get(rel) != hashlib.sha256(data).hexdigest():
            count += 1
    for rel in recorded:
        if rel not in present:
            count += 1
    return str(count)


def _truncate(text: str, limit: int, claims: bool) -> tuple[str, str]:
    lines = text.splitlines()
    if not claims and len(lines) <= limit:
        return text, ""
    if claims and len(lines) <= limit:
        return text, ""
    if not claims:
        # Keep the exit line and cap the failure block. The caller already capped
        # the failure view; this catches a long verify transcript.
        if len(lines) <= 200:
            return text, ""
        kept = lines[:200]
        note = f"[truncated: {len(lines) - len(kept)} lines; full text in bundle_full.txt]"
        return "\n".join(kept), note
    kept = lines[:limit]
    note = f"[truncated: {len(lines) - len(kept)} lines; full text in bundle_full.txt]"
    return "\n".join(kept), note


def _starter(root: Path) -> str | None:
    completed = _git(root, ["rev-list", "--first-parent", "--reverse", "HEAD"])
    if completed.returncode != 0:
        return None
    shas = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(shas) < 4:
        return None
    return shas[3]


def _untracked(root: Path) -> list[str]:
    completed = _git(root, ["ls-files", "--others", "--exclude-standard"])
    if completed.returncode != 0:
        return []
    return [line.strip().replace("\\", "/") for line in completed.stdout.splitlines() if line.strip()]


def _link_size(path: Path) -> int:
    try:
        return path.lstat().st_size
    except OSError:
        return 0


def _recorded_symlinks(root: Path) -> list[tuple[str, int]]:
    found = []
    seen = set()
    completed = _git(root, ["ls-files", "-s"])
    if completed.returncode == 0:
        for line in completed.stdout.splitlines():
            if not line.startswith("120000 "):
                continue
            rel = line.split("\t", 1)[-1].replace("\\", "/")
            if not rel or rel in seen:
                continue
            seen.add(rel)
            found.append((rel, _link_size(root / rel)))
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        kept = []
        for name in dirnames:
            path = Path(dirpath) / name
            if path.is_symlink():
                rel = path.relative_to(root).as_posix()
                if rel not in seen:
                    seen.add(rel)
                    found.append((rel, _link_size(path)))
                continue
            if name not in IGNORED_DIRS:
                kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            if name in IGNORED_FILES:
                continue
            path = Path(dirpath) / name
            if not path.is_symlink():
                continue
            rel = path.relative_to(root).as_posix()
            if rel == "lab_manifest.json" or rel in seen:
                continue
            seen.add(rel)
            found.append((rel, _link_size(path)))
    return found


def _tree_files(root: Path) -> set[str]:
    found = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in IGNORED_DIRS]
        for name in filenames:
            if name in IGNORED_FILES:
                continue
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if rel == "lab_manifest.json":
                continue
            found.add(rel)
    return found


def _display(text: str) -> str:
    """Sections 3 and 4 are display only. The changes body is not passed here."""
    text = (
        text.replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2026", "...")
    )
    return text.encode("ascii", "replace").decode("ascii")


def _is_ascii(text: str) -> bool:
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def _quote_git(prefix: str, rel: str) -> str:
    token = prefix + rel
    if any(ch in token for ch in ' \t"\\'):
        escaped = token.replace("\\", "\\\\").replace('"', '\\"')
        return '"' + escaped + '"'
    return token


def _new_file_diff(rel: str, text: str) -> str:
    lines = text.splitlines()
    count = len(lines)
    hunk = [f"@@ -0,0 +1,{count} @@" if count else "@@ -0,0 +1,0 @@"]
    hunk.extend("+" + line for line in lines)
    header = [
        f"diff --git {_quote_git('a/', rel)} {_quote_git('b/', rel)}",
        "new file mode 100644",
        "--- /dev/null",
        f"+++ {_quote_git('b/', rel)}",
    ]
    return "\n".join(header + hunk) + "\n"


def _binary(data: bytes) -> bool:
    if b"\0" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _version(root: Path) -> str:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("version") and "=" in line:
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "unknown"


def _file_sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(data).hexdigest()


def _read_manifest(root: Path) -> dict | None:
    path = root / "lab_manifest.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _git_available() -> bool:
    return shutil.which("git") is not None


def _is_repo(root: Path) -> bool:
    return (root / ".git").exists()


def _git_line(root: Path, args: list[str]) -> str:
    if not _git_available() or not _is_repo(root):
        return "unavailable"
    completed = _git(root, args)
    if completed.returncode != 0:
        return "unavailable"
    return completed.stdout.strip() or "unavailable"


def _git(root: Path, args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-c", "core.autocrlf=false", "-c", "core.quotepath=false", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args, 1, "", f"timed out after {GIT_TIMEOUT_SECONDS} s"
        )


def _limited(args: list[str], cwd: Path, timeout: float):
    proc = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc.pid)
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
        return 1, b"", b"", True
    return proc.returncode or 0, stdout or b"", stderr or b"", False


def _kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
        return
    try:
        os.kill(pid, 15)
    except OSError:
        return
