"""Event filtering utilities for Frigate plate recognizer."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

DEFAULT_OBJECTS = ('car', 'motorcycle', 'bus')


def check_first_message(first_message: bool, logger) -> bool:
    if first_message:
        logger.debug("Skipping first message")
        return True
    return False


def check_invalid_event(
    *,
    config: Dict[str, Any],
    before_data: Dict[str, Any],
    after_data: Dict[str, Any],
    is_tracked: bool,
    logger,
) -> bool:
    config_zones: Iterable[str] = config['frigate'].get('zones', [])
    config_cameras: Iterable[str] = config['frigate'].get('camera', [])

    matching_zone = (
        any(value in after_data.get('current_zones', []) for value in config_zones)
        if config_zones
        else True
    )
    camera = after_data.get('camera')
    matching_camera = (camera in list(config_cameras)) if config_cameras else True

    if not (matching_zone and matching_camera):
        logger.debug(
            "Skipping event: %s because it does not match the configured zones/cameras",
            after_data.get('id'),
        )
        return True

    valid_objects = config['frigate'].get('objects', DEFAULT_OBJECTS)
    if after_data.get('label') not in valid_objects:
        logger.debug("is not a correct label: %s", after_data.get('label'))
        return True

    if (
        before_data.get('top_score') == after_data.get('top_score')
        and is_tracked
        and not config['frigate'].get('frigate_plus', False)
    ):
        logger.debug(
            "duplicated snapshot from Frigate as top_score from before and after are the same: %s %s",
            after_data.get('top_score'),
            after_data.get('id'),
        )
        return True

    return False


def get_license_plate_attribute(config: Dict[str, Any], after_data: Dict[str, Any]) -> Optional[list]:
    if config['frigate'].get('frigate_plus', False):
        attributes = after_data.get('current_attributes', [])
        return [attribute for attribute in attributes if attribute.get('label') == 'license_plate']
    return None


def is_valid_license_plate(
    config: Dict[str, Any],
    after_data: Dict[str, Any],
    logger,
) -> bool:
    after_license_plate_attribute = get_license_plate_attribute(config, after_data)
    attributes = after_license_plate_attribute or []
    if not attributes:
        logger.debug("no license_plate attribute found in event attributes")
        return False

    license_plate_min_score = config['frigate'].get('license_plate_min_score', 0)
    score = attributes[0].get('score', 0) if isinstance(attributes[0], dict) else 0
    if score < license_plate_min_score:
        logger.debug(
            "license_plate attribute score is below minimum: %s",
            score,
        )
        return False

    return True


__all__ = [
    'DEFAULT_OBJECTS',
    'check_first_message',
    'check_invalid_event',
    'get_license_plate_attribute',
    'is_valid_license_plate',
]
