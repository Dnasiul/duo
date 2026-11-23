from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from .models import MAX_CONTEXT_BYTES, MAX_TEXT_BYTES, RunError, new_secret, normalize


MAX_SOURCE_RECORD_BYTES = 16 * 1024 * 1024
MAX_SOURCE_SNAPSHOTS = 256


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_input(path: Path, label: str | None = None, *, follow_symlinks: bool = True,
              max_bytes: int = MAX_TEXT_BYTES) -> bytes:
    """Read a bounded regular input file, without blocking on a named pipe."""
    label = label or path.name
    flags = os.O_RDONLY | os.O_NONBLOCK
    if not follow_symlinks:
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if not follow_symlinks and exc.errno == errno.ELOOP:
            raise RunError(f"{label} must be a regular file, not a symbolic link") from exc
        raise
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise RunError(f"{label} must be a regular file")
        message = f"{label} exceeds the {max_bytes // 1024} KiB limit"
        if info.st_size > max_bytes:
            raise RunError(message)
        # Bound the read even if the file grows after fstat.
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise RunError(message)
        return data
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class UserClarification:
    source_run: str
    source_run_id: str
    question: str
    blocked_document_sha256: str
    current_document_sha256: str
    answer: str


def load_blocked_source(path: Path, *, repo: Path, document: Path, mode: str,
                        task: str, criteria: str, current_plan: str,
                        answer: str) -> UserClarification:
    """Validate a completed blocked record; read no paths supplied by its contents."""
    source = path.expanduser().resolve()
    if not source.is_dir():
        raise RunError("Source run must be an existing directory")

    def read(name: str, *, max_bytes: int = MAX_TEXT_BYTES) -> str:
        try:
            return read_input(source / name, f"Source run {name}",
                              follow_symlinks=False, max_bytes=max_bytes).decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise RunError(f"Could not read source run {name}: {exc}") from exc

    def record(name: str) -> dict:
        try:
            value = json.loads(read(name, max_bytes=MAX_SOURCE_RECORD_BYTES))
        except (ValueError, RecursionError) as exc:
            raise RunError(f"Invalid source run {name}: {exc}") from exc
        if not isinstance(value, dict):
            raise RunError(f"Invalid source run {name}: must be a JSON object")
        return value

    manifest, result = record("manifest.json"), record("result.json")
    if result.get("status") != "blocked":
        raise RunError("Source run result.json status must be blocked")
    history = result.get("history")
    if not isinstance(history, list) or not history or not isinstance(history[-1], dict):
        raise RunError("Source run history must have a final blocked turn")
    turn = history[-1]
    if turn.get("decision") != "blocked":
        raise RunError("Source run last history turn must be blocked")
    if any(not isinstance(item, dict) or item.get("decision") == "blocked"
           for item in history[:-1]):
        raise RunError("Source run history must end at its first blocked turn")
    question, blocked_hash = turn.get("reason"), turn.get("plan_hash")
    if not isinstance(question, str) or not question.strip():
        raise RunError("Source run blocked question must be a nonempty string")
    if not isinstance(blocked_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", blocked_hash):
        raise RunError("Source run blocked document hash must be a SHA-256 string")
    if not isinstance(result.get("plan"), str):
        raise RunError("Source run result plan must be a string matching the blocked document hash")
    try:
        question.encode("utf-8")
        result_hash = content_hash(result["plan"])
    except UnicodeError as exc:
        raise RunError("Source run result.json question and plan must be valid UTF-8") from exc
    if result_hash != blocked_hash:
        raise RunError("Source run result plan does not match the blocked document hash")
    try:
        snapshots = [p.name for p in source.iterdir()
                     if re.fullmatch(r"plan-[0-9]{3,}\.md", p.name)]
    except OSError as exc:
        raise RunError(f"Could not list source run snapshots: {exc}") from exc
    if len(snapshots) > MAX_SOURCE_SNAPSHOTS:
        raise RunError(f"Source run exceeds the {MAX_SOURCE_SNAPSHOTS} snapshot limit")
    snapshots.sort(key=lambda name: int(name[5:-3]), reverse=True)
    if not any(content_hash(read(name)) == blocked_hash for name in snapshots):
        raise RunError("Source run blocked document hash matches no recorded snapshot")
    config = manifest.get("config")
    if not isinstance(config, dict):
        raise RunError("Source run manifest.json config must be an object")
    for label, recorded, current in (
        ("repository", config.get("repo"), str(repo)),
        ("document path", manifest.get("document"), str(document)),
        ("mode", config.get("mode"), mode),
        ("task", manifest.get("task"), task),
        ("criteria", manifest.get("criteria"), criteria),
    ):
        if recorded != current:
            raise RunError(f"Source run {label} does not match the current run")
    return UserClarification(str(source), source.name, question, blocked_hash,
                             content_hash(current_plan), answer)


@dataclass(frozen=True)
class ContextFile:
    label: str
    source: str
    data: bytes

    @property
    def text(self) -> str:
        return self.data.decode("utf-8")


def capture_context(paths: list[Path]) -> tuple[ContextFile, ...]:
    """Capture and scan all sources before any contents can be saved or sent."""
    captured = []
    total = 0
    for path in paths:
        source = path.expanduser().absolute()
        data = read_input(source, f"Context file {path}")
        total += len(data)
        if total > MAX_CONTEXT_BYTES:
            raise RunError("Aggregate context exceeds the 64 KiB limit (--context-file)")
        try:
            text = data.decode("utf-8")
        except UnicodeError as exc:
            raise RunError(f"Context file {path} must be valid UTF-8") from exc
        # No known-document exception, even for credentials in the current plan.
        kind = new_secret(normalize(text), "")
        if kind:
            raise RunError(f"Context file {path}: recognized credential ({kind})")
        captured.append(ContextFile(path.name, str(source), data))
    return tuple(captured)


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".duo-", delete=False) as f:
            temporary = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def new_file_mode() -> int:
    """The mode a new file would get from the user's umask (0644 for umask 022)."""
    mask = os.umask(0)
    os.umask(mask)
    return 0o666 & ~mask


def run_usage(path: Path) -> int:
    """Bytes stored under a run directory, without following symbolic links.

    Copilot's unpacked runtime (copilot-cache/pkg, about 165 MiB) is excluded: it
    is the CLI's own program, deleted after each attempt, not run output.
    """
    total = 0
    for directory, subdirectories, files in os.walk(path, followlinks=False):
        if os.path.basename(directory) == "copilot-cache" and "pkg" in subdirectories:
            subdirectories.remove("pkg")
        for name in files:
            try:
                total += os.lstat(os.path.join(directory, name)).st_size
            except FileNotFoundError:
                pass  # removed while counting
    return total


class PlanFile:
    """Keep the bytes last observed, including the distinction absent vs empty.

    The document under review: plan.md by default, or --output inside the repository.
    """

    def __init__(self, repo: Path, name: str = "plan.md"):
        self.path = repo / name
        self.expected = self._read()
        self.initial = normalize(self.expected.decode("utf-8")) if self.expected else ""

    def _read(self) -> bytes | None:
        try:
            return read_input(self.path, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def check(self) -> None:
        if self._read() != self.expected:
            raise RunError(f"{self.path.name} changed outside Duo; restart review with the current file")

    def write(self, text: str) -> None:
        self.check()
        # The document belongs to the repository; audit files stay private (0600).
        mode = (self.path.stat().st_mode & 0o777) if self.expected is not None else new_file_mode()
        data = text.encode("utf-8")
        atomic_write(self.path, data, mode)
        self.expected = data


@contextmanager
def run_lock(repo: Path):
    """Exclude other Duo runs on this repository, whatever their state directory.

    The lock is taken on the repository directory itself, so no file is created
    there. macOS/Linux release flock when a process exits, including crashes.
    """
    import fcntl

    descriptor = os.open(repo, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RunError("Another Duo run holds the lock for this repository") from exc
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


class RunStore:
    """Audit artifacts; these are deliberately not resumable graph checkpoints."""

    def __init__(self, state_dir: Path):
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        runs = state_dir / "runs"
        runs.mkdir(exist_ok=True, mode=0o700)
        self.path = Path(tempfile.mkdtemp(prefix="run-", dir=runs))
        # Held for the run's lifetime so duo-clean never deletes an active run.
        import fcntl
        self._lock = os.open(self.path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def close(self) -> None:
        if self._lock is not None:
            os.close(self._lock)  # also releases the flock
            self._lock = None

    def json(self, name: str, value: object) -> None:
        atomic_write(self.path / name, (json.dumps(value, indent=2) + "\n").encode("utf-8"))

    def snapshot(self, turn: int, text: str) -> None:
        atomic_write(self.path / f"plan-{turn:03d}.md", text.encode("utf-8"))

    def context(self, captured: tuple[ContextFile, ...]) -> None:
        entries = []
        for order, item in enumerate(captured, 1):
            artifact = f"context-{order:03d}.bin"
            atomic_write(self.path / artifact, item.data)
            entries.append({"order": order, "label": item.label, "source": item.source,
                            "artifact": artifact, "bytes": len(item.data),
                            "sha256": hashlib.sha256(item.data).hexdigest()})
        self.json("context.json", entries)

    def event(self, value: dict) -> None:
        with (self.path / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": timestamp(), **value}) + "\n")

    def attempt_dir(self) -> Path:
        return Path(tempfile.mkdtemp(prefix="attempt-", dir=self.path))
