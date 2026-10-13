from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .models import RunError, normalize


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


class PlanFile:
    """Keep the bytes last observed, including the distinction absent vs empty."""

    def __init__(self, repo: Path):
        self.path = repo / "plan.md"
        self.expected = self._read()
        self.initial = normalize(self.expected.decode("utf-8")) if self.expected else ""

    def _read(self) -> bytes | None:
        if self.path.is_symlink():
            raise RunError("plan.md must be a regular file, not a symbolic link")
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return None

    def check(self) -> None:
        if self._read() != self.expected:
            raise RunError("plan.md changed outside Duo; restart review with the current file")

    def write(self, text: str) -> None:
        self.check()
        # plan.md belongs to the repository; audit files stay private (0600).
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

    def json(self, name: str, value: object) -> None:
        atomic_write(self.path / name, (json.dumps(value, indent=2) + "\n").encode("utf-8"))

    def snapshot(self, turn: int, text: str) -> None:
        atomic_write(self.path / f"plan-{turn:03d}.md", text.encode("utf-8"))

    def event(self, value: dict) -> None:
        with (self.path / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": timestamp(), **value}) + "\n")

    def attempt_dir(self) -> Path:
        return Path(tempfile.mkdtemp(prefix="attempt-", dir=self.path))
