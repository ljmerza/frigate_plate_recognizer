"""Utilities for tracking in-flight Frigate events."""

from __future__ import annotations

import threading
from typing import Dict

from .metrics import current_events_gauge

CURRENT_EVENTS: Dict[str, int] = {}
_CURRENT_EVENTS_LOCK = threading.Lock()


def track_event_start(event_id: str) -> None:
    with _CURRENT_EVENTS_LOCK:
        CURRENT_EVENTS.setdefault(event_id, 0)
        current_events_gauge.set(len(CURRENT_EVENTS))


def is_event_tracked(event_id: str) -> bool:
    with _CURRENT_EVENTS_LOCK:
        return event_id in CURRENT_EVENTS


def increment_event_attempt(event_id: str) -> int:
    with _CURRENT_EVENTS_LOCK:
        CURRENT_EVENTS[event_id] = CURRENT_EVENTS.get(event_id, 0) + 1
        current_events_gauge.set(len(CURRENT_EVENTS))
        return CURRENT_EVENTS[event_id]


def get_event_attempts(event_id: str) -> int:
    with _CURRENT_EVENTS_LOCK:
        return CURRENT_EVENTS.get(event_id, 0)


def clear_event(event_id: str) -> None:
    with _CURRENT_EVENTS_LOCK:
        if event_id in CURRENT_EVENTS:
            del CURRENT_EVENTS[event_id]
            current_events_gauge.set(len(CURRENT_EVENTS))


def reset() -> None:
    with _CURRENT_EVENTS_LOCK:
        CURRENT_EVENTS.clear()
        current_events_gauge.set(0)

__all__ = [
    'CURRENT_EVENTS',
    'track_event_start',
    'is_event_tracked',
    'increment_event_attempt',
    'get_event_attempts',
    'clear_event',
    'reset',
]
