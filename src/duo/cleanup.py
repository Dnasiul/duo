"""duo-clean: delete old Duo audit runs. Dry run unless --delete is given.

Only directories directly under <state-dir>/runs are considered. Active runs
(locked by a running Duo) are skipped, symbolic links are never followed, and
nothing outside the runs directory, such as a project's plan.md, is touched.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import shutil
import stat
import sys
import time

from .storage import run_usage

MIB = 1024 * 1024


@dataclass
class Run:
    path: Path
    modified: float  # newest of the directory and its top-level files
    size: int        # bytes, including any leftover Copilot runtime
    runtime: int     # bytes in leftover copilot-cache/pkg directories


def default_state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "duo"


def tree_size(path: Path) -> int:
    total = 0
    for directory, _, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(directory, name)).st_size
            except FileNotFoundError:
                pass
    return total


def runtimes(run: Path) -> list[Path]:
    """Leftover Copilot runtime copies (from Duo versions that kept them)."""
    found = []
    for child in run.iterdir():
        cache = child / "copilot-cache"
        candidate = cache / "pkg"
        # Checking only pkg follows an intermediate copilot-cache symlink.
        if all(not path.is_symlink() and path.is_dir() for path in (child, cache, candidate)):
            found.append(candidate)
    return found


def scan(runs_dir: Path) -> tuple[list[Run], list[str]]:
    runs, skipped = [], []
    for path in sorted(runs_dir.iterdir()):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            skipped.append(f"{path.name}: symbolic link, not followed")
            continue
        if not stat.S_ISDIR(info.st_mode) or not path.name.startswith("run-"):
            if not path.name.startswith("."):  # e.g. .DS_Store
                skipped.append(f"{path.name}: not a run directory")
            continue
        modified = max([info.st_mtime] + [child.lstat().st_mtime for child in path.iterdir()])
        size = run_usage(path)
        leftover = sum(tree_size(item) for item in runtimes(path))
        runs.append(Run(path, modified, size + leftover, leftover))
    return runs, skipped


def try_lock(path: Path) -> int | None:
    """A descriptor holding the run's lock, or None while Duo is using the run."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return None
    return descriptor


def select(runs: list[Run], older_than_days: float | None, max_total_mb: float | None,
           now: float) -> list[Run]:
    chosen = []
    if older_than_days is not None:
        chosen = [run for run in runs if now - run.modified > older_than_days * 86400]
    if max_total_mb is not None:
        kept = sorted((run for run in runs if run not in chosen), key=lambda run: run.modified)
        total = sum(run.size for run in kept)
        while kept and total > max_total_mb * MIB:
            oldest = kept.pop(0)  # oldest first
            chosen.append(oldest)
            total -= oldest.size
    return chosen


def describe(run: Run, now: float) -> str:
    age = (now - run.modified) / 86400
    return f"{run.path.name}  {run.size / MIB:8.1f} MiB  {age:6.1f} days old"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="duo-clean", description=__doc__.splitlines()[0])
    parser.add_argument("--state-dir", type=Path, default=None,
                        help="Duo state directory (default: $XDG_STATE_HOME/duo or ~/.local/state/duo)")
    parser.add_argument("--older-than", type=float, metavar="DAYS",
                        help="Select runs not modified for more than DAYS days")
    parser.add_argument("--max-total-mb", type=float, metavar="MIB",
                        help="Select the oldest runs until the rest fit in MIB mebibytes")
    parser.add_argument("--copilot-runtime", action="store_true",
                        help="Remove leftover Copilot runtime copies (copilot-cache/pkg) from all "
                             "inactive runs, keeping their audit files")
    parser.add_argument("--delete", action="store_true", help="Delete; without it, only report")
    args = parser.parse_args(argv)
    if args.older_than is None and args.max_total_mb is None and not args.copilot_runtime:
        parser.error("choose --older-than, --max-total-mb, and/or --copilot-runtime")
    for name in ("older_than", "max_total_mb"):
        value = getattr(args, name)
        if value is not None and value < 0:
            parser.error(f"--{name.replace('_', '-')} must not be negative")

    runs_dir = (args.state_dir or default_state_dir()).expanduser() / "runs"
    if runs_dir.is_symlink() or not runs_dir.is_dir():
        print(f"No runs directory at {runs_dir}")
        return 0
    runs, skipped = scan(runs_dir)
    now = time.time()
    chosen = select(runs, args.older_than, args.max_total_mb, now)
    verb = "Deleted" if args.delete else "Would delete"
    freed = 0
    for line in skipped:
        print(f"Skipped {line}")
    for run in runs:
        delete_run = run in chosen
        if not delete_run and not (args.copilot_runtime and run.runtime):
            continue
        lock = try_lock(run.path)
        if lock is None:
            print(f"Skipped {run.path.name}: in use by a running Duo")
            continue
        try:
            if delete_run:
                print(f"{verb} {describe(run, now)}")
                if args.delete:
                    # rmtree never follows symbolic links inside the tree.
                    shutil.rmtree(run.path)
                freed += run.size
            else:
                print(f"{verb} Copilot runtime from {run.path.name} ({run.runtime / MIB:.1f} MiB)")
                if args.delete:
                    for item in runtimes(run.path):
                        shutil.rmtree(item)
                freed += run.runtime
        finally:
            os.close(lock)
    total = sum(run.size for run in runs)
    print(f"{len(runs)} runs, {total / MIB:.1f} MiB; {verb.lower()} {freed / MIB:.1f} MiB"
          + ("" if args.delete else " (dry run; add --delete)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
