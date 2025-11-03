"""Plate recognition helpers for external services."""

from __future__ import annotations

import difflib
import time
from typing import Any, Dict, Iterable, Optional, Tuple

from requests import Session
from requests.exceptions import RequestException

from .metrics import (
    code_project_counter,
    http_request_latency_histogram,
    plate_recognizer_counter,
    plate_recognizer_err,
)

PLATE_RECOGNIZER_BASE_URL = 'https://api.platerecognizer.com/v1/plate-reader'

RecognitionResult = Tuple[Optional[str], Optional[float], Optional[str], Optional[float]]


def _normalise_watched(plates: Iterable[str]) -> set[str]:
    return {str(item).lower() for item in plates}


def check_watched_plates(
    plate_number: Optional[str],
    candidates: Optional[Iterable[Dict[str, Any]]],
    runtime_config: Dict[str, Any],
    logger,
) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    if not plate_number:
        return None, None, None

    config_watched_plates = runtime_config['frigate'].get('watched_plates', [])
    if not config_watched_plates:
        logger.debug("Skipping checking Watched Plates because watched_plates is not set")
        return None, None, None

    watched_set = _normalise_watched(config_watched_plates)

    # Step 1 - exact match on top plate
    if str(plate_number).lower() in watched_set:
        plate_recognizer_err.inc()
        logger.info("Recognised plate is a Watched Plate: %s", plate_number)
        return None, None, None

    # Step 2 - examine AI candidates
    if candidates:
        for idx, candidate in enumerate(candidates):
            candidate_plate = candidate.get('plate') or candidate.get('candidate')
            if candidate_plate and candidate_plate.lower() in watched_set:
                if runtime_config.get('plate_recognizer'):
                    score = candidate.get('score')
                else:
                    if idx == 0:
                        continue  # Skip original plate for CodeProject.AI responses
                    score = candidate.get('confidence')
                logger.info(
                    "Watched plate found from AI candidates: %s with score %s",
                    candidate_plate,
                    score,
                )
                return candidate_plate, score, None

    logger.debug("No Watched Plates found from AI candidates")

    # Step 3 - fuzzy match fallback
    fuzzy_match = runtime_config['frigate'].get('fuzzy_match', 0)
    if not fuzzy_match:
        logger.debug("Skipping fuzzy matching because fuzzy_match value not set in config")
        return None, None, None

    best_match = None
    best_score = 0.0
    for watched_candidate in watched_set:
        score = difflib.SequenceMatcher(a=str(plate_number).lower(), b=watched_candidate).ratio()
        if score > best_score:
            best_score = score
            best_match = watched_candidate

    logger.debug("Best fuzzy_match: %s (%s)", best_match, best_score)
    if best_match and best_score >= fuzzy_match:
        logger.info("Watched plate found from fuzzy matching: %s with score %s", best_match, best_score)
        return best_match, None, best_score

    logger.debug("No matching Watched Plates found.")
    return None, None, None


def recognize_with_code_project(
    image: bytes,
    runtime_config: Dict[str, Any],
    session: Session,
    logger,
) -> RecognitionResult:
    code_project_counter.inc()
    api_config = runtime_config.get('code_project') or {}
    api_url = api_config.get('api_url')
    if not api_url:
        logger.error("CodeProject.AI API URL is not configured")
        return None, None, None, None

    start_time = time.perf_counter()
    try:
        response = session.post(api_url, files={'upload': image})
        response.raise_for_status()
    except RequestException as exc:
        logger.error("CodeProject.AI request failed: %s", exc)
        return None, None, None, None
    finally:
        http_request_latency_histogram.labels('code_project', 'recognize').observe(
            time.perf_counter() - start_time
        )

    try:
        payload = response.json()
    except ValueError:
        logger.error("CodeProject.AI returned invalid JSON response")
        return None, None, None, None

    logger.debug("CodeProject.AI response: %s", payload)

    predictions = payload.get('predictions') or []
    if not predictions:
        logger.debug("No plates found")
        return None, None, None, None

    top_prediction = predictions[0]
    plate_number = top_prediction.get('plate')
    score = top_prediction.get('confidence')
    watched_plate, watched_score, fuzzy_score = check_watched_plates(
        plate_number, predictions, runtime_config, logger
    )
    if fuzzy_score:
        return plate_number, score, watched_plate, fuzzy_score
    if watched_plate:
        return plate_number, watched_score, watched_plate, None
    return plate_number, score, None, None


def recognize_with_plate_recognizer(
    image: bytes,
    runtime_config: Dict[str, Any],
    app_config,
    session: Session,
    logger,
) -> RecognitionResult:
    plate_recognizer_counter.inc()

    recognizer_config = getattr(app_config, 'plate_recognizer', None)
    if not recognizer_config:
        logger.error("Plate Recognizer configuration missing")
        return None, None, None, None

    api_url = recognizer_config.api_url or PLATE_RECOGNIZER_BASE_URL
    headers = {'Authorization': f"Token {recognizer_config.token}"}
    data = dict(regions=recognizer_config.regions)

    attempts = max(1, recognizer_config.max_retries + 1)
    delay_seconds = 1

    for attempt in range(1, attempts + 1):
        start_time = time.perf_counter()
        try:
            response = session.post(api_url, data=data, files={'upload': image}, headers=headers)
        except RequestException as exc:
            logger.error(
                "Plate Recognizer request failed (attempt %s/%s): %s",
                attempt,
                attempts,
                exc,
            )
            if attempt == attempts:
                plate_recognizer_err.inc()
                return None, None, None, None
            time.sleep(delay_seconds)
            delay_seconds = min(delay_seconds * 2, 60)
            continue
        finally:
            http_request_latency_histogram.labels('plate_recognizer', 'recognize').observe(
                time.perf_counter() - start_time
            )

        if response.status_code == 429:
            logger.warning(
                "Plate Recognizer rate limit hit (attempt %s/%s). Retrying in %s seconds",
                attempt,
                attempts,
                delay_seconds,
            )
            time.sleep(delay_seconds)
            delay_seconds = min(delay_seconds * 2, 60)
            continue

        if response.status_code not in (200, 201):
            logger.error(
                "Plate Recognizer API error (attempt %s/%s): %s %s",
                attempt,
                attempts,
                response.status_code,
                response.text,
            )
            if attempt == attempts:
                plate_recognizer_err.inc()
                return None, None, None, None
            time.sleep(delay_seconds)
            delay_seconds = min(delay_seconds * 2, 60)
            continue

        try:
            payload = response.json()
        except ValueError:
            logger.error("Plate Recognizer returned invalid JSON response")
            plate_recognizer_err.inc()
            return None, None, None, None

        logger.debug("Plate Recognizer response: %s", payload)

        results = payload.get('results') or []
        if not results:
            logger.debug("No plates found or invalid response: %s", payload)
            return None, None, None, None

        top_result = results[0]
        plate_number = top_result.get('plate')
        score = top_result.get('score')
        watched_plate, watched_score, fuzzy_score = check_watched_plates(
            plate_number,
            top_result.get('candidates') or [],
            runtime_config,
            logger,
        )
        if fuzzy_score:
            return plate_number, score, watched_plate, fuzzy_score
        if watched_plate:
            return plate_number, watched_score, watched_plate, None
        return plate_number, score, None, None

    logger.error("Failed to get plate number after exhausting retries")
    return None, None, None, None


__all__ = [
    'PLATE_RECOGNIZER_BASE_URL',
    'check_watched_plates',
    'recognize_with_code_project',
    'recognize_with_plate_recognizer',
]
