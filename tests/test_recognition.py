from __future__ import annotations

from unittest.mock import MagicMock

from frigate_plate_recognizer.recognition import check_watched_plates


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
