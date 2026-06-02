from __future__ import annotations

from unittest.mock import MagicMock

from frigate_plate_recognizer.recognition import (
    check_watched_plates,
    recognize_with_code_project,
)


def _code_project_session(predictions):
    session = MagicMock()
    response = MagicMock()
    response.json.return_value = {"predictions": predictions}
    session.post.return_value = response
    return session


def test_code_project_sends_min_confidence_when_configured():
    runtime_config = {
        "frigate": {},
        "code_project": {
            "api_url": "http://cpai/v1/image/alpr",
            "min_confidence": 0.4,
        },
    }
    session = _code_project_session([{"plate": "ABC123", "confidence": 0.9}])

    plate, score, watched, fuzzy = recognize_with_code_project(
        b"image-bytes", runtime_config, session, MagicMock()
    )

    assert plate == "ABC123"
    assert score == 0.9
    session.post.assert_called_once_with(
        "http://cpai/v1/image/alpr",
        files={"upload": b"image-bytes"},
        data={"min_confidence": 0.4},
    )


def test_code_project_omits_data_when_min_confidence_unset():
    runtime_config = {
        "frigate": {},
        "code_project": {"api_url": "http://cpai/v1/image/alpr"},
    }
    session = _code_project_session([{"plate": "XYZ789", "confidence": 0.7}])

    recognize_with_code_project(b"image-bytes", runtime_config, session, MagicMock())

    session.post.assert_called_once_with(
        "http://cpai/v1/image/alpr",
        files={"upload": b"image-bytes"},
        data=None,
    )


def test_check_watched_plate_exact_match_uses_top_score():
    runtime_config = {"frigate": {"watched_plates": ["ABC123"]}}

    watched_plate, watched_score, fuzzy_score = check_watched_plates(
        "ABC123",
        candidates=None,
        runtime_config=runtime_config,
        logger=MagicMock(),
        top_score=0.9,
    )

    assert watched_plate == "ABC123"
    assert watched_score == 0.9
    assert fuzzy_score is None


def test_check_watched_plate_skips_when_not_configured():
    runtime_config = {"frigate": {}}

    watched_plate, watched_score, fuzzy_score = check_watched_plates(
        "XYZ999",
        candidates=None,
        runtime_config=runtime_config,
        logger=MagicMock(),
        top_score=0.5,
    )

    assert watched_plate is None
    assert watched_score is None
    assert fuzzy_score is None
