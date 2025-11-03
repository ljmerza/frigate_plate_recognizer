"""High-level plate detection pipeline helpers."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .recognition import recognize_with_code_project, recognize_with_plate_recognizer

RecognitionOutcome = Tuple[Optional[str], Optional[float], Optional[str], Optional[float]]


def get_plate(
    snapshot: bytes,
    *,
    config: Dict[str, Any],
    app_config,
    logger,
    plate_session,
    code_project_session,
) -> RecognitionOutcome:
    plate_number: Optional[str] = None
    plate_score: Optional[float] = None
    watched_plate: Optional[str] = None
    fuzzy_score: Optional[float] = None

    if config.get('plate_recognizer'):
        if plate_session is None:
            logger.error("Plate Recognizer session is not initialised")
            return None, None, None, None
        plate_number, plate_score, watched_plate, fuzzy_score = recognize_with_plate_recognizer(
            snapshot,
            config,
            app_config,
            plate_session,
            logger,
        )
    elif config.get('code_project'):
        if code_project_session is None:
            logger.error("CodeProject.AI session is not initialised")
            return None, None, None, None
        plate_number, plate_score, watched_plate, fuzzy_score = recognize_with_code_project(
            snapshot,
            config,
            code_project_session,
            logger,
        )
    else:
        logger.error("Plate Recognizer is not configured")
        return None, None, None, None

    min_score = config['frigate'].get('min_score')
    score_too_low = bool(min_score and plate_score and plate_score < min_score)

    if not fuzzy_score and score_too_low:
        logger.info("Score is below minimum: %s (%s)", plate_score, plate_number)
        return None, None, None, None

    return plate_number, plate_score, watched_plate, fuzzy_score


__all__ = ['get_plate']
