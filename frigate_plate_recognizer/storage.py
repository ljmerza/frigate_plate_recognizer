"""SQLite storage utilities for plate history."""

from __future__ import annotations

import sqlite3

from .metrics import db_errors_counter, db_writes_counter


def _configure_connection(conn: sqlite3.Connection, busy_timeout_ms: int) -> None:
    conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")


def initialise_database(db_path: str, *, timeout_seconds: int, busy_timeout_ms: int, logger) -> None:
    with sqlite3.connect(db_path, timeout=timeout_seconds) as conn:
        _configure_connection(conn, busy_timeout_ms)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                detection_time TIMESTAMP NOT NULL,
                score TEXT NOT NULL,
                plate_number TEXT NOT NULL,
                frigate_event TEXT NOT NULL UNIQUE,
                camera_name TEXT NOT NULL
            )
            """
        )
    logger.debug("Database initialised at %s", db_path)


def insert_plate(
    db_path: str,
    *,
    timeout_seconds: int,
    busy_timeout_ms: int,
    logger,
    detection_time: str,
    score: float,
    plate_number: str,
    frigate_event_id: str,
    camera_name: str,
) -> bool:
    try:
        with sqlite3.connect(db_path, timeout=timeout_seconds) as conn:
            _configure_connection(conn, busy_timeout_ms)
            conn.execute(
                """INSERT INTO plates (detection_time, score, plate_number, frigate_event, camera_name)
                VALUES (?, ?, ?, ?, ?)""",
                (detection_time, score, plate_number, frigate_event_id, camera_name),
            )
        db_writes_counter.labels(status='success').inc()
        return True
    except sqlite3.IntegrityError as exc:
        db_errors_counter.labels(operation='insert').inc()
        logger.debug("Plate for event %s already stored: %s", frigate_event_id, exc)
        return False
    except sqlite3.Error as exc:
        db_errors_counter.labels(operation='insert').inc()
        logger.error("SQLite error storing plate %s: %s", frigate_event_id, exc)
        raise


def has_processed_event(
    db_path: str,
    frigate_event_id: str,
    *,
    timeout_seconds: int,
    busy_timeout_ms: int,
    logger,
) -> bool:
    with sqlite3.connect(db_path, timeout=timeout_seconds) as conn:
        conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM plates WHERE frigate_event = ?", (frigate_event_id,))
        row = cursor.fetchone()

    if row is not None:
        logger.debug("Skipping event: %s because it has already been processed", frigate_event_id)
        return True
    return False

__all__ = [
    'initialise_database',
    'insert_plate',
    'has_processed_event',
]
