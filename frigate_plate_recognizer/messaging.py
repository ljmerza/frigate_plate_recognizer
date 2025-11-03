"""MQTT messaging helpers."""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Optional

import paho.mqtt.client as mqtt

from .metrics import (
    mqtt_sends_counter,
    on_connect_counter,
    on_disconnect_counter,
)


def make_on_connect(logger, config: Dict[str, Any]) -> Callable:
    def _on_connect(client, userdata, flags, reason_code, properties):
        on_connect_counter.inc()
        logger.info("MQTT Connected")
        client.subscribe(config['frigate']['main_topic'] + "/events")

    return _on_connect


def make_on_disconnect(logger) -> Callable:
    def _on_disconnect(client, userdata, flags, reason_code, properties):
        on_disconnect_counter.inc()
        if reason_code != 0:
            logger.warning(
                "Unexpected disconnection, trying to reconnect userdata:%s, flags:%s, properties:%s",
                userdata,
                flags,
                properties,
            )
            while True:
                try:
                    client.reconnect()
                    break
                except Exception as exc:  # pragma: no cover - backoff loop is hard to test
                    logger.warning("Reconnection failed due to %s, retrying in 60 seconds", exc)
                    time.sleep(60)
        else:
            logger.error("Expected disconnection")

    return _on_disconnect


def publish_plate_message(
    *,
    mqtt_client,
    config: Dict[str, Any],
    plate_number: Optional[str],
    plate_score: Optional[float],
    frigate_event_id: str,
    after_data: Dict[str, Any],
    formatted_start_time: str,
    watched_plate: Optional[str],
    fuzzy_score: Optional[float],
    logger,
) -> None:
    if not config['frigate'].get('return_topic'):
        return

    mqtt_sends_counter.labels(watched=bool(watched_plate)).inc()

    if watched_plate:
        message = {
            'plate_number': str(watched_plate).upper(),
            'score': plate_score,
            'frigate_event_id': frigate_event_id,
            'camera_name': after_data['camera'],
            'start_time': formatted_start_time,
            'fuzzy_score': fuzzy_score,
            'original_plate': str(plate_number).upper(),
            'is_watched_plate': True,
        }
    else:
        message = {
            'plate_number': str(plate_number).upper() if plate_number else None,
            'score': plate_score,
            'frigate_event_id': frigate_event_id,
            'camera_name': after_data['camera'],
            'start_time': formatted_start_time,
            'is_watched_plate': False,
        }

    logger.debug("Sending MQTT message: %s", message)

    main_topic = config['frigate']['main_topic']
    return_topic = config['frigate']['return_topic']
    topic = f'{main_topic}/{return_topic}'

    mqtt_client.publish(topic, json.dumps(message))


def create_mqtt_client(
    *,
    config: Dict[str, Any],
    logger,
    message_callback,
) -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.enable_logger()
    client.on_connect = make_on_connect(logger, config)
    client.on_disconnect = make_on_disconnect(logger)
    client.on_message = message_callback

    if config['frigate'].get('mqtt_username'):
        username = config['frigate']['mqtt_username']
        password = config['frigate'].get('mqtt_password', '')
        client.username_pw_set(username, password)

    return client


__all__ = ['publish_plate_message', 'create_mqtt_client', 'make_on_connect', 'make_on_disconnect']
