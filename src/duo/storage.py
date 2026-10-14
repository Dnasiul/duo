from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from .models import MAX_TEXT_BYTES, RunError, normalize


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_input(path: Path, label: str | None = None, *, follow_symlinks: bool = True) -> bytes:
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
        message = f"{label} exceeds the {MAX_TEXT_BYTES // 1024} KiB limit"
        if info.st_size > MAX_TEXT_BYTES:
            raise RunError(message)
        # Bound the read even if the file grows after fstat.
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(MAX_TEXT_BYTES + 1)
        if len(data) > MAX_TEXT_BYTES:
            raise RunError(message)
        return data
    finally:
        os.close(descriptor)


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

    def event(self, value: dict) -> None:
        with (self.path / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": timestamp(), **value}) + "\n")

    def attempt_dir(self) -> Path:
        return Path(tempfile.mkdtemp(prefix="attempt-", dir=self.path))
