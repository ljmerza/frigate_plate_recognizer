# Project Improvement Proposals

This document outlines concrete, high‑impact improvements for the Frigate Plate Recognizer project. Items are grouped by area with rationale and suggested actions. Use this as a living roadmap — check off items as they’re completed.

## High‑Impact First

- Add robust config validation and defaults to prevent runtime KeyErrors and misconfiguration.
- Add timeouts, retries, and exception handling for all HTTP requests (Frigate, Plate Recognizer, CodeProject.AI).
- Unify Python version to 3.11 across Dockerfile, CI, and Sonar to avoid environment drift.
- Refactor `index.py` into small modules to improve maintainability and testability.
- Harden concurrency: protect shared state (e.g., `CURRENT_EVENTS`) and make message handling failure‑safe.
- Strengthen secrets handling: never log tokens or credentials and keep real config out of VCS.
- Pin all dependencies and add linting/formatting/type checks to CI.

## Architecture & Structure

- Split `index.py` into cohesive modules:
  - `config.py` (load/validate config, env overrides)
  - `mqtt_client.py` (connect, subscribe, publish, reconnect logic)
  - `recognizers.py` (Plate Recognizer and CodeProject.AI integrations)
  - `processing.py` (message pipeline, dedupe, filtering, snapshots)
  - `storage.py` (SQLite access, schema, migrations)
  - `metrics.py` (Prometheus counters/histograms)
- Introduce data classes (or Pydantic models) for Frigate events and recognition results to reduce ad‑hoc dict access.
- Provide a CLI entry point (e.g., `python -m frigate_plate_recognizer`) and package metadata (pyproject.toml).

## Configuration

- Define a schema (Pydantic or Voluptuous) and validate on startup. Provide clear error messages and defaults.
- Support env var overrides for secrets and paths (e.g., `CONFIG_PATH`, `DB_PATH`, `LOG_FILE`, `PLATES_PATH`).
- Remove the `LOCAL` path switch; use explicit, documented paths and sane defaults that work both in Docker and locally.
- Provide `config/config.example.yml` and keep real configs out of VCS. Add docs for all fields with defaults.
- Normalize naming and fix typos in constants and files (e.g., “recognizer” vs “recogizer”).

## Reliability & Error Handling

- Wrap network calls in try/except and add timeouts:
  - Frigate snapshot and event fetches
  - Plate Recognizer/CodeProject.AI POSTs
  - Frigate sublabel POSTs
- Use exponential backoff with jitter for reconnects and 5xx/connection errors (not just 429).
- Ensure `process_message` is exception‑safe so a failure does not kill the thread.
- Guard shared state (`CURRENT_EVENTS`) with a lock, and periodically clean stale entries (TTL) to avoid leaks.
- Add graceful shutdown (signal handlers) to stop MQTT loop and `ThreadPoolExecutor` cleanly.
- Handle SQLite “database is locked” with WAL mode, `busy_timeout`, and per‑operation connections.

## Observability & Metrics

- Expand Prometheus metrics:
  - Request latency histograms for external calls (Frigate, PR, CP.AI)
  - Error counters by type/status
  - Queue depth/gauges (e.g., in‑flight events) and processed event counts
  - Build info gauge with version
- Add structured logging (JSON) option and log correlation (event id / camera) in every message.
- Mask secrets when logging config. Avoid logging entire config at DEBUG.

## Performance & Rate Limiting

- Tune concurrency to respect external API limits; allow `max_workers` and per‑event `max_attempts` in config.
- Debounce/reduce redundant snapshots more aggressively (e.g., only process on `type: end` or top_score deltas > threshold).
- Resize/crop images before sending to external services when beneficial to reduce payload size and latency.
- Avoid loading fonts on every draw; cache the font object and ship an open‑licensed font (e.g., DejaVuSans) with a fallback.

## Security & Secrets

- Never commit real tokens/passwords; ensure `config/*` is ignored (it is), and purge any historical commits containing secrets.
- Support TLS for MQTT (CA, cert, key) and document configuration.
- Add request timeouts and verify SSL by default for external APIs (allow opt‑out only when necessary).
- Run container as non‑root; restrict file permissions for config, DB, and logs.
- Avoid writing logs to `/config` by default in containers; prefer stdout or an optional rotating file handler.

## Storage (SQLite) and Data Model

- Add WAL mode + `busy_timeout` and use context managers to reduce lock errors.
- Consider a simple migrations mechanism for schema changes (e.g., `alembic` optional or a tiny in‑house version table).
- Store additional fields (e.g., source engine, fuzzy score, original plate) for richer history/analytics.
- Add a maintenance job to purge old rows and/or archive snapshots.

## Testing & QA

- Migrate to pytest with fixtures and coverage; keep `unittest` compatibility for now if desired.
- Add tests for:
  - Config validation and defaults
  - Network error/retry paths
  - CodeProject.AI response parsing (candidates and top prediction handling)
  - MQTT publish payload correctness (watched vs non‑watched)
  - Concurrency (executor) and dedupe logic
- Add static checks: ruff/flake8, black/ruff format, mypy type checks.
- Increase coverage and make CI fail on low coverage for critical modules.

## CI/CD & Release

- Pin all dependencies (including `prometheus-client`) and use `--no-cache-dir` installs.
- Align Python to 3.11 everywhere (Dockerfile, CI, Sonar). Consider `python:3.11-slim` base.
- Build multi‑arch images via Buildx (already in place) with explicit tags and labels (source, version, VCS ref).
- Publish SBOM and image scan (e.g., Trivy) as part of release.
- Add a changelog and conventional commits for automated release notes.

## Container & Runtime

- Multi‑stage Dockerfile to slim image size. Create a non‑root user, set `WORKDIR`, and `EXPOSE` metrics port.
- Configurable metrics port via config or env var; avoid hardcoding `8080`.
- Healthcheck endpoint (e.g., `/metrics` OK + MQTT connected flag) for orchestrators.

## Feature Enhancements

- Improve watched plates matching:
  - Use RapidFuzz for faster/better fuzzy matching
  - Keep audit fields (original vs matched plate, method, scores)
- Optionally draw boxes using recognition engine response even without Frigate+.
- Add a replay/backfill tool to reprocess events from DB or from a directory of snapshots.
- Optional MQTT retained messages and configurable QoS.

## Documentation

- Expand README with a full configuration reference, security/TLS examples, metrics docs, and troubleshooting.
- Add `docs/` with architecture overview, dataflow diagram, and local dev guide.
- Provide a sample docker‑compose with volumes and an example config mounted.

## Known Issues & Quick Wins

- Fix typos in constants and filenames (e.g., “recogizer” → “recognizer”).
- Add request timeouts to all `requests` calls.
- Mask secrets in logs and stop logging the full config at DEBUG.
- Align Python to 3.11 and pin `prometheus-client`.
- Replace Arial with an OSS font or make the font configurable with a fallback.

---

If you want, I can start by implementing config validation, adding request timeouts with retries, and refactoring the Dockerfile to 3.11‑slim in small, reviewable PRs.

