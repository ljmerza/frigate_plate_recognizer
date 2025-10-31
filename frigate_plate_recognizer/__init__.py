"""Frigate Plate Recognizer package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("frigate-plate-recognizer")
except PackageNotFoundError:  # pragma: no cover - local development fallback
    __version__ = "2.2.1"

__all__ = ["__version__"]
