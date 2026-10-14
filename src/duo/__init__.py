"""Duo's plan review orchestrator."""

from importlib.metadata import PackageNotFoundError, version

try:
    # pyproject.toml is the only place the version is set.
    __version__ = version("duo-plan")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"
