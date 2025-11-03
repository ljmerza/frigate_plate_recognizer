"""Image handling helpers for Frigate plate recognizer."""

from __future__ import annotations

import io
import os
import time
from datetime import datetime
from typing import Any, Dict, Optional, Sequence

from PIL import Image, ImageDraw, ImageFont
from requests import Session
from requests.exceptions import RequestException


def fetch_snapshot(
    session: Session,
    *,
    frigate_url: str,
    frigate_event_id: str,
    cropped: bool,
    logger,
    histogram,
) -> Optional[bytes]:
    logger.debug("Getting snapshot for event: %s, Crop: %s", frigate_event_id, cropped)
    snapshot_url = f"{frigate_url}/api/events/{frigate_event_id}/snapshot.jpg"
    logger.debug("event URL: %s", snapshot_url)

    params = {"crop": 1 if cropped else 0, "quality": 95}
    start_time = time.perf_counter()
    try:
        response = session.get(snapshot_url, params=params)
    except RequestException as exc:
        logger.error("Error getting snapshot: %s", exc)
        return None
    finally:
        histogram.labels('frigate', 'snapshot').observe(time.perf_counter() - start_time)

    if response.status_code != 200:
        logger.error("Error getting snapshot: %s", response.status_code)
        return None

    return response.content


def fetch_final_attributes(
    session: Session,
    *,
    event_url: str,
    use_frigate_plus: bool,
    logger,
    histogram,
) -> Optional[Sequence[Dict[str, Any]]]:
    if not use_frigate_plus:
        return None

    start_time = time.perf_counter()
    try:
        response = session.get(event_url)
    except RequestException as exc:
        logger.error("Error getting final data: %s", exc)
        return None
    finally:
        histogram.labels('frigate', 'event_data').observe(time.perf_counter() - start_time)

    if response.status_code != 200:
        logger.error("Error getting final data: %s", response.status_code)
        return None

    try:
        event_json = response.json()
    except ValueError:
        logger.error("Error parsing event JSON from Frigate")
        return None

    event_data = event_json.get('data', {})
    if not event_data:
        return None

    attributes = event_data.get('attributes', [])
    return [attribute for attribute in attributes if attribute.get('label') == 'license_plate']


def save_image(
    *,
    config: Dict[str, Any],
    after_data: Dict[str, Any],
    frigate_url: str,
    frigate_event_id: str,
    plate_number: Optional[str],
    snapshot_path: str,
    datetime_format: str,
    session: Session,
    logger,
    histogram,
) -> None:
    if not config['frigate'].get('save_snapshots', False):
        logger.debug("Skipping saving snapshot because save_snapshots is set to false")
        return

    event_url = f"{frigate_url}/api/events/{frigate_event_id}"
    final_attribute = fetch_final_attributes(
        session,
        event_url=event_url,
        use_frigate_plus=config['frigate'].get('frigate_plus', False),
        logger=logger,
        histogram=histogram,
    )

    snapshot = fetch_snapshot(
        session,
        frigate_url=frigate_url,
        frigate_event_id=frigate_event_id,
        cropped=False,
        logger=logger,
        histogram=histogram,
    )
    if not snapshot:
        return

    image = Image.open(io.BytesIO(bytearray(snapshot)))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("./Arial.ttf", size=14)

    if final_attribute:
        image_width, image_height = image.size
        dimension_1 = final_attribute[0]['box'][0]
        dimension_2 = final_attribute[0]['box'][1]
        dimension_3 = final_attribute[0]['box'][2]
        dimension_4 = final_attribute[0]['box'][3]

        plate = (
            dimension_1 * image_width,
            dimension_2 * image_height,
            (dimension_1 + dimension_3) * image_width,
            (dimension_2 + dimension_4) * image_height,
        )
        draw.rectangle(plate, outline="red", width=2)
        logger.debug("Drawing Plate Box: %s", plate)

        if plate_number:
            draw.text(
                (
                    (dimension_1 * image_width) + 5,
                    ((dimension_2 + dimension_4) * image_height) + 5,
                ),
                str(plate_number).upper(),
                font=font,
            )

    timestamp = datetime.now().strftime(datetime_format)
    image_name = f"{after_data['camera']}_{timestamp}.png"
    if plate_number:
        image_name = f"{str(plate_number).upper()}_{image_name}"

    os.makedirs(snapshot_path, exist_ok=True)
    image_path = os.path.join(snapshot_path, image_name)
    logger.info("Saving image with path: %s", image_path)
    image.save(image_path)


__all__ = ['fetch_snapshot', 'fetch_final_attributes', 'save_image']
