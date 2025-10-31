#!/bin/python3

from __future__ import annotations

from datetime import datetime
import concurrent.futures
import os
import sqlite3
import time
import logging
from typing import Any, Dict, Optional
import threading

import paho.mqtt.client as mqtt
import sys
import json
import requests
from requests import Session
from requests.exceptions import RequestException

import io
from PIL import Image, ImageDraw, ImageFont
import difflib
import prometheus_client

from frigate_plate_recognizer import __version__ as PACKAGE_VERSION
from frigate_plate_recognizer.config import (
    AppConfig,
    DEFAULT_DB_PATH,
    DEFAULT_LOG_FILE,
    DEFAULT_METRICS_PORT,
    DEFAULT_SNAPSHOT_DIR,
    load_app_config,
)
from frigate_plate_recognizer.http_client import build_session

mqtt_client = None
config = None
first_message = True
_LOGGER = None

executor = None

APP_CONFIG: AppConfig | None = None

FRIGATE_SESSION: Optional[Session] = None
PLATE_RECOGNIZER_SESSION: Optional[Session] = None
CODE_PROJECT_SESSION: Optional[Session] = None

VERSION = PACKAGE_VERSION

DB_PATH = str(DEFAULT_DB_PATH)
LOG_FILE = str(DEFAULT_LOG_FILE)
SNAPSHOT_PATH = str(DEFAULT_SNAPSHOT_DIR)

DATETIME_FORMAT = "%Y-%m-%d_%H-%M"

PLATE_RECOGIZER_BASE_URL = 'https://api.platerecognizer.com/v1/plate-reader'
DEFAULT_OBJECTS = ['car', 'motorcycle', 'bus']
CURRENT_EVENTS = {}
CURRENT_EVENTS_LOCK = threading.Lock()
PORT = DEFAULT_METRICS_PORT
DB_TIMEOUT_SECONDS = 30
DB_BUSY_TIMEOUT_MS = 5000

on_connect_counter = prometheus_client.Counter('on_connect', 'count of connects')
on_disconnect_counter = prometheus_client.Counter('on_disconnect', 'count of connects')
mqtt_sends_counter = prometheus_client.Counter('mqtt_sends', 'count of sends', ['watched'])

code_project_counter = prometheus_client.Counter('code_project_calls', 'count of sends')
plate_recognizer_counter = prometheus_client.Counter('plate_recognizer_calls', 'count of sends')
plate_recognizer_err = prometheus_client.Counter('plate_recognizer_errors', 'count of sends')
http_request_latency_histogram = prometheus_client.Histogram(
    'http_request_duration_seconds',
    'HTTP request duration',
    ['service', 'operation'],
)
current_events_gauge = prometheus_client.Gauge(
    'current_events_tracked',
    'Number of events currently tracked',
)
processed_events_counter = prometheus_client.Counter(
    'processed_events_total',
    'Number of events processed categorised by result',
    ['result'],
)
db_writes_counter = prometheus_client.Counter(
    'db_writes_total',
    'Database write operations',
    ['status'],
)
db_errors_counter = prometheus_client.Counter(
    'db_errors_total',
    'Database errors',
    ['operation'],
)


def _require_session(session: Optional[Session], label: str) -> Session:
    if session is None:
        raise RuntimeError(f"{label} HTTP session is not initialised")
    return session


def get_frigate_session() -> Session:
    return _require_session(FRIGATE_SESSION, "Frigate")


def get_plate_recognizer_session() -> Session:
    return _require_session(PLATE_RECOGNIZER_SESSION, "Plate Recognizer")


def get_code_project_session() -> Session:
    return _require_session(CODE_PROJECT_SESSION, "CodeProject.AI")


def initialize_http_clients() -> None:
    global FRIGATE_SESSION, PLATE_RECOGNIZER_SESSION, CODE_PROJECT_SESSION

    if APP_CONFIG is None:
        raise RuntimeError("Configuration must be loaded before initialising HTTP clients")

    FRIGATE_SESSION = build_session(
        timeout=APP_CONFIG.frigate.request_timeout,
        retries=APP_CONFIG.frigate.api_retries,
        verify=APP_CONFIG.frigate.verify_ssl,
    )

    if APP_CONFIG.plate_recognizer:
        PLATE_RECOGNIZER_SESSION = build_session(
            timeout=APP_CONFIG.plate_recognizer.request_timeout,
            retries=APP_CONFIG.plate_recognizer.max_retries,
            verify=APP_CONFIG.plate_recognizer.verify_ssl,
        )
    else:
        PLATE_RECOGNIZER_SESSION = None

    if APP_CONFIG.code_project:
        CODE_PROJECT_SESSION = build_session(
            timeout=APP_CONFIG.code_project.request_timeout,
            retries=APP_CONFIG.code_project.max_retries,
            verify=APP_CONFIG.code_project.verify_ssl,
        )
    else:
        CODE_PROJECT_SESSION = None


def _track_event_start(event_id: str) -> None:
    with CURRENT_EVENTS_LOCK:
        CURRENT_EVENTS.setdefault(event_id, 0)
        current_events_gauge.set(len(CURRENT_EVENTS))


def _is_event_tracked(event_id: str) -> bool:
    with CURRENT_EVENTS_LOCK:
        return event_id in CURRENT_EVENTS


def _increment_event_attempt(event_id: str) -> int:
    with CURRENT_EVENTS_LOCK:
        CURRENT_EVENTS[event_id] = CURRENT_EVENTS.get(event_id, 0) + 1
        current_events_gauge.set(len(CURRENT_EVENTS))
        return CURRENT_EVENTS[event_id]


def _get_event_attempts(event_id: str) -> int:
    with CURRENT_EVENTS_LOCK:
        return CURRENT_EVENTS.get(event_id, 0)


def _clear_event(event_id: str) -> None:
    with CURRENT_EVENTS_LOCK:
        if event_id in CURRENT_EVENTS:
            del CURRENT_EVENTS[event_id]
            current_events_gauge.set(len(CURRENT_EVENTS))

def on_connect(mqtt_client, userdata, flags, reason_code, properties):
    on_connect_counter.inc()
    _LOGGER.info("MQTT Connected")
    mqtt_client.subscribe(config['frigate']['main_topic'] + "/events")

def on_disconnect(mqtt_client, userdata, flags, reason_code, properties):
    on_disconnect_counter.inc()
    if reason_code != 0:
        _LOGGER.warning(f"Unexpected disconnection, trying to reconnect userdata:{userdata}, flags:{flags}, properties:{properties}")
        while True:
            try:
                mqtt_client.reconnect()
                break
            except Exception as e:
                _LOGGER.warning(f"Reconnection failed due to {e}, retrying in 60 seconds")
                time.sleep(60)
    else:
        _LOGGER.error("Expected disconnection")

def set_sublabel(frigate_url, frigate_event_id, sublabel, score):
    post_url = f"{frigate_url}/api/events/{frigate_event_id}/sub_label"
    _LOGGER.debug(f'sublabel: {sublabel}')
    _LOGGER.debug(f'sublabel url: {post_url}')

    # frigate limits sublabels to 20 characters currently
    if len(sublabel) > 20:
        sublabel = sublabel[:20]

    sublabel = str(sublabel).upper() # plates are always upper cased

    # Submit the POST request with the JSON payload
    payload = { "subLabel": sublabel }
    headers = { "Content-Type": "application/json" }
    session = get_frigate_session()
    start_time = time.perf_counter()
    try:
        response = session.post(post_url, data=json.dumps(payload), headers=headers)
    except RequestException as exc:
        _LOGGER.error(f"Failed to set sublabel due to HTTP error: {exc}")
        return
    finally:
        http_request_latency_histogram.labels('frigate', 'set_sublabel').observe(
            time.perf_counter() - start_time
        )

    percent_score = "{:.1%}".format(score)

    # Check for a successful response
    if response.status_code == 200:
        _LOGGER.info(f"Sublabel set successfully to: {sublabel} with {percent_score} confidence")
    else:
        _LOGGER.error(
            "Failed to set sublabel. Status code: %s, response: %s",
            response.status_code,
            response.text,
        )

def code_project(image):
    code_project_counter.inc()
    api_url = config['code_project'].get('api_url')
    session = get_code_project_session()

    start_time = time.perf_counter()
    try:
        http_response = session.post(api_url, files=dict(upload=image))
        http_response.raise_for_status()
    except RequestException as exc:
        _LOGGER.error(f"CodeProject.AI request failed: {exc}")
        return None, None, None, None
    finally:
        http_request_latency_histogram.labels('code_project', 'recognize').observe(
            time.perf_counter() - start_time
        )

    try:
        response = http_response.json()
    except ValueError:
        _LOGGER.error("CodeProject.AI returned invalid JSON response")
        return None, None, None, None

    _LOGGER.debug(f"response: {response}")

    predictions = response.get('predictions')
    if not predictions:
        _LOGGER.debug("No plates found")
        return None, None, None, None

    plate_number = predictions[0].get('plate')
    score = predictions[0].get('confidence')

    watched_plate, watched_score, fuzzy_score = check_watched_plates(plate_number, predictions)
    if fuzzy_score:
        return plate_number, score, watched_plate, fuzzy_score
    elif watched_plate:
        return plate_number, watched_score, watched_plate, None
    else:
        return plate_number, score, None, None

def plate_recognizer(image):
    plate_recognizer_counter.inc()

    if APP_CONFIG is None or not APP_CONFIG.plate_recognizer:
        _LOGGER.error("Plate Recognizer configuration missing")
        return None, None, None, None

    recognizer_config = APP_CONFIG.plate_recognizer
    api_url = recognizer_config.api_url or PLATE_RECOGIZER_BASE_URL
    headers = {'Authorization': f"Token {recognizer_config.token}"}
    data = dict(regions=recognizer_config.regions)

    session = get_plate_recognizer_session()

    attempts = max(1, recognizer_config.max_retries + 1)
    delay_seconds = 1

    for attempt in range(1, attempts + 1):
        start_time = time.perf_counter()
        try:
            response = session.post(
                api_url,
                data=data,
                files=dict(upload=image),
                headers=headers,
            )
        except RequestException as exc:
            _LOGGER.error(
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
            _LOGGER.warning(
                "Plate Recognizer rate limit hit (attempt %s/%s). Retrying in %s seconds",
                attempt,
                attempts,
                delay_seconds,
            )
            time.sleep(delay_seconds)
            delay_seconds = min(delay_seconds * 2, 60)
            continue

        if response.status_code not in (200, 201):
            _LOGGER.error(
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
            response_json = response.json()
        except ValueError:
            _LOGGER.error("Plate Recognizer returned invalid JSON response")
            plate_recognizer_err.inc()
            return None, None, None, None

        _LOGGER.debug(f"response: {response_json}")

        results = response_json.get('results')
        if not results:
            _LOGGER.debug(f"No plates found or invalid response: {response_json}")
            return None, None, None, None

        plate_result = results[0]
        plate_number = plate_result.get('plate')
        score = plate_result.get('score')
        watched_plate, watched_score, fuzzy_score = check_watched_plates(
            plate_number, plate_result.get('candidates')
        )
        if fuzzy_score:
            return plate_number, score, watched_plate, fuzzy_score
        if watched_plate:
            return plate_number, watched_score, watched_plate, None
        return plate_number, score, None, None

    _LOGGER.error("Failed to get plate number after exhausting retries")
    return None, None, None, None


def check_watched_plates(plate_number, response):
    config_watched_plates = config['frigate'].get('watched_plates', [])
    if not config_watched_plates:
        _LOGGER.debug("Skipping checking Watched Plates because watched_plates is not set")
        return None, None, None

    config_watched_plates = [str(x).lower() for x in config_watched_plates] #make sure watched_plates are all lower case

    #Step 1 - test if top plate is a watched plate
    matching_plate = str(plate_number).lower() in config_watched_plates
    if matching_plate:
        plate_recognizer_errors.inc()
        _LOGGER.info(f"Recognised plate is a Watched Plate: {plate_number}")
        return None, None, None

    #Step 2 - test against AI candidates:
    for i, plate in enumerate(response):
        matching_plate = plate.get('plate') in config_watched_plates
        if matching_plate:
            if config.get('plate_recognizer'):
                score = plate.get('score')
            else:
                if i == 0: continue  #skip first response for CodeProjet.AI as index 0 = original plate.
                score = plate.get('confidence')
            _LOGGER.info(f"Watched plate found from AI candidates: {plate.get('plate')} with score {score}")
            return plate.get('plate'), score, None

    _LOGGER.debug("No Watched Plates found from AI candidates")

    #Step 3 - test against fuzzy match:
    fuzzy_match = config['frigate'].get('fuzzy_match', 0)

    if fuzzy_match == 0:
        _LOGGER.debug(f"Skipping fuzzy matching because fuzzy_match value not set in config")
        return None, None, None

    max_score = 0
    best_match = None
    for candidate in config_watched_plates:
        seq = difflib.SequenceMatcher(a=str(plate_number).lower(), b=str(candidate).lower())
        if seq.ratio() > max_score:
            max_score = seq.ratio()
            best_match = candidate

    _LOGGER.debug(f"Best fuzzy_match: {best_match} ({max_score})")

    if max_score >= fuzzy_match:
        _LOGGER.info(f"Watched plate found from fuzzy matching: {best_match} with score {max_score}")
        return best_match, None, max_score


    _LOGGER.debug("No matching Watched Plates found.")
    #No watched_plate matches found
    return None, None, None

def send_mqtt_message(plate_number, plate_score, frigate_event_id, after_data, formatted_start_time, watched_plate, fuzzy_score):
    mqtt_sends_counter.labels(watched=bool(watched_plate)).inc()
    if not config['frigate'].get('return_topic'):
        return

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
            'plate_number': str(plate_number).upper(),
            'score': plate_score,
            'frigate_event_id': frigate_event_id,
            'camera_name': after_data['camera'],
            'start_time': formatted_start_time,
            'is_watched_plate': False,
        }

    _LOGGER.debug(f"Sending MQTT message: {message}")

    main_topic = config['frigate']['main_topic']
    return_topic = config['frigate']['return_topic']
    topic = f'{main_topic}/{return_topic}'

    mqtt_client.publish(topic, json.dumps(message))

def has_common_value(array1, array2):
    return any(value in array2 for value in array1)

def save_image(config, after_data, frigate_url, frigate_event_id, plate_number):
    if not config['frigate'].get('save_snapshots', False):
        _LOGGER.debug(f"Skipping saving snapshot because save_snapshots is set to false")
        return

    # get latest Event Data from Frigate API
    event_url = f"{frigate_url}/api/events/{frigate_event_id}"

    final_attribute = get_final_data(event_url)

    # get latest snapshot
    snapshot = get_snapshot(frigate_event_id, frigate_url, False)
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
            (dimension_2 + dimension_4) * image_height
        )
        draw.rectangle(plate, outline="red", width=2)
        _LOGGER.debug(f"Drawing Plate Box: {plate}")

        if plate_number:
            draw.text(
                (
                    (dimension_1 * image_width)+  5,
                    ((dimension_2 + dimension_4) * image_height) + 5
                ),
                str(plate_number).upper(),
                font=font
            )

    # save image
    timestamp = datetime.now().strftime(DATETIME_FORMAT)
    image_name = f"{after_data['camera']}_{timestamp}.png"
    if plate_number:
        image_name = f"{str(plate_number).upper()}_{image_name}"

    image_path = f"{SNAPSHOT_PATH}/{image_name}"
    _LOGGER.info(f"Saving image with path: {image_path}")
    image.save(image_path)

def check_first_message():
    global first_message
    if first_message:
        first_message = False
        _LOGGER.debug("Skipping first message")
        return True
    return False

def check_invalid_event(before_data, after_data):
    # check if it is from the correct camera or zone
    config_zones = config['frigate'].get('zones', [])
    config_cameras = config['frigate'].get('camera', [])

    matching_zone = any(value in after_data['current_zones'] for value in config_zones) if config_zones else True
    matching_camera = after_data['camera'] in config_cameras if config_cameras else True

    # Check if either both match (when both are defined) or at least one matches (when only one is defined)
    if not (matching_zone and matching_camera):
        _LOGGER.debug(f"Skipping event: {after_data['id']} because it does not match the configured zones/cameras")
        return True

    # check if it is a valid object
    valid_objects = config['frigate'].get('objects', DEFAULT_OBJECTS)
    if(after_data['label'] not in valid_objects):
        _LOGGER.debug(f"is not a correct label: {after_data['label']}")
        return True

    # limit api calls to plate checker api by only checking the best score for an event
    if(before_data['top_score'] == after_data['top_score'] and _is_event_tracked(after_data['id'])) and not config['frigate'].get('frigate_plus', False):
        _LOGGER.debug(f"duplicated snapshot from Frigate as top_score from before and after are the same: {after_data['top_score']} {after_data['id']}")
        return True
    return False

def get_snapshot(frigate_event_id, frigate_url, cropped):
    _LOGGER.debug(f"Getting snapshot for event: {frigate_event_id}, Crop: {cropped}")
    snapshot_url = f"{frigate_url}/api/events/{frigate_event_id}/snapshot.jpg"
    _LOGGER.debug(f"event URL: {snapshot_url}")

    # get snapshot
    parameters = {"crop": 1 if cropped else 0, "quality": 95}
    session = get_frigate_session()
    start_time = time.perf_counter()
    try:
        response = session.get(snapshot_url, params=parameters)
    except RequestException as exc:
        _LOGGER.error(f"Error getting snapshot: {exc}")
        return
    finally:
        http_request_latency_histogram.labels('frigate', 'snapshot').observe(
            time.perf_counter() - start_time
        )

    # Check if the request was successful (HTTP status code 200)
    if response.status_code != 200:
        _LOGGER.error(f"Error getting snapshot: {response.status_code}")
        return

    return response.content

def get_license_plate_attribute(after_data):
    if config['frigate'].get('frigate_plus', False):
        attributes = after_data.get('current_attributes', [])
        license_plate_attribute = [attribute for attribute in attributes if attribute['label'] == 'license_plate']
        return license_plate_attribute
    else:
        return None

def get_final_data(event_url):
    if config['frigate'].get('frigate_plus', False):
        session = get_frigate_session()
        start_time = time.perf_counter()
        try:
            response = session.get(event_url)
        except RequestException as exc:
            _LOGGER.error(f"Error getting final data: {exc}")
            return
        finally:
            http_request_latency_histogram.labels('frigate', 'event_data').observe(
                time.perf_counter() - start_time
            )
        if response.status_code != 200:
            _LOGGER.error(f"Error getting final data: {response.status_code}")
            return
        try:
            event_json = response.json()
        except ValueError:
            _LOGGER.error("Error parsing event JSON from Frigate")
            return
        event_data = event_json.get('data', {})

        if event_data:
            attributes = event_data.get('attributes', [])
            final_attribute = [attribute for attribute in attributes if attribute['label'] == 'license_plate']
            return final_attribute
        else:
            return None
    else:
        return None


def is_valid_license_plate(after_data):
    # if user has frigate plus then check license plate attribute
    after_license_plate_attribute = get_license_plate_attribute(after_data)
    if not any(after_license_plate_attribute):
        _LOGGER.debug(f"no license_plate attribute found in event attributes")
        return False

    # check min score of license plate attribute
    license_plate_min_score = config['frigate'].get('license_plate_min_score', 0)
    if after_license_plate_attribute[0]['score'] < license_plate_min_score:
        _LOGGER.debug(f"license_plate attribute score is below minimum: {after_license_plate_attribute[0]['score']}")
        return False

    return True

def is_duplicate_event(frigate_event_id):
     # see if we have already processed this event
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        conn.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}")
        cursor = conn.cursor()
        cursor.execute("""SELECT 1 FROM plates WHERE frigate_event = ?""", (frigate_event_id,))
        row = cursor.fetchone()

    if row is not None:
        _LOGGER.debug(f"Skipping event: {frigate_event_id} because it has already been processed")
        return True

    return False

def get_plate(snapshot):
    # try to get plate number
    plate_number = None
    plate_score = None

    if config.get('plate_recognizer'):
        plate_number, plate_score , watched_plate, fuzzy_score = plate_recognizer(snapshot)
    elif config.get('code_project'):
        plate_number, plate_score, watched_plate, fuzzy_score = code_project(snapshot)
    else:
        _LOGGER.error("Plate Recognizer is not configured")
        return None, None, None, None

    # check Plate Recognizer score
    min_score = config['frigate'].get('min_score')
    score_too_low = min_score and plate_score and plate_score < min_score

    if not fuzzy_score and score_too_low:
        _LOGGER.info(f"Score is below minimum: {plate_score} ({plate_number})")
        return None, None, None, None

    return plate_number, plate_score, watched_plate, fuzzy_score

def store_plate_in_db(plate_number, plate_score, frigate_event_id, after_data, formatted_start_time) -> bool:
    _LOGGER.info(f"Storing plate number in database: {plate_number} with score: {plate_score}")

    try:
        with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
            conn.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """INSERT INTO plates (detection_time, score, plate_number, frigate_event, camera_name)
                VALUES (?, ?, ?, ?, ?)""",
                (formatted_start_time, plate_score, plate_number, frigate_event_id, after_data['camera'])
            )
        db_writes_counter.labels(status='success').inc()
        return True
    except sqlite3.IntegrityError as exc:
        db_errors_counter.labels(operation='insert').inc()
        _LOGGER.debug(f"Plate for event {frigate_event_id} already stored: {exc}")
        return False
    except sqlite3.Error as exc:
        db_errors_counter.labels(operation='insert').inc()
        _LOGGER.error(f"SQLite error storing plate {frigate_event_id}: {exc}")
        raise

def on_message(client, userdata, message):
    global executor
    executor.submit(process_message, message)

def process_message(message):
    result = 'error'
    try:
        result = _process_message_inner(message)
    except Exception:
        _LOGGER.exception("Unhandled error while processing MQTT message")
    finally:
        processed_events_counter.labels(result=result).inc()


def _process_message_inner(message) -> str:
    if check_first_message():
        return 'first_message'

    payload_dict = json.loads(message.payload)
    _LOGGER.debug(f'mqtt message: {payload_dict}')

    before_data = payload_dict.get('before', {})
    after_data = payload_dict.get('after', {})
    message_type = payload_dict.get('type', '')

    frigate_url = config['frigate']['frigate_url']
    frigate_event_id = after_data['id']

    if message_type == 'end' and _is_event_tracked(frigate_event_id):
        _LOGGER.debug(
            f"CLEARING EVENT: {frigate_event_id} after {_get_event_attempts(frigate_event_id)} calls to AI engine"
        )
        _clear_event(frigate_event_id)

    if check_invalid_event(before_data, after_data):
        return 'invalid_event'

    if is_duplicate_event(frigate_event_id):
        return 'duplicate_event'

    frigate_plus = config['frigate'].get('frigate_plus', False)
    if frigate_plus and not is_valid_license_plate(after_data):
        return 'invalid_license_plate'

    if message_type != 'end' and not _is_event_tracked(frigate_event_id):
        _track_event_start(frigate_event_id)

    snapshot = None
    if after_data.get('has_snapshot'):
        snapshot = get_snapshot(frigate_event_id, frigate_url, True)
    if not snapshot:
        _LOGGER.debug(f"Event {frigate_event_id} has no snapshot")
        _clear_event(frigate_event_id)
        return 'no_snapshot'

    _LOGGER.debug(f"Getting plate for event: {frigate_event_id}")

    max_attempts = config['frigate'].get('max_attempts', 0)
    if max_attempts > 0 and _get_event_attempts(frigate_event_id) >= max_attempts:
        _LOGGER.debug(
            f"Maximum number of AI attempts reached for event {frigate_event_id}: {_get_event_attempts(frigate_event_id)}"
        )
        return 'max_attempts'

    attempt_count = _increment_event_attempt(frigate_event_id)
    _LOGGER.debug(f"Attempt {attempt_count} for event {frigate_event_id}")

    plate_number, plate_score, watched_plate, fuzzy_score = get_plate(snapshot)
    result = 'no_plate'
    saved_plate_number = watched_plate if watched_plate else plate_number

    if plate_number:
        start_time = datetime.fromtimestamp(after_data['start_time'])
        formatted_start_time = start_time.strftime("%Y-%m-%d %H:%M:%S")

        try:
            stored = store_plate_in_db(
                saved_plate_number,
                plate_score,
                frigate_event_id,
                after_data,
                formatted_start_time,
            )
            result = 'success' if stored else 'duplicate_event'
        except sqlite3.Error:
            result = 'db_error'

        set_sublabel(frigate_url, frigate_event_id, saved_plate_number, plate_score)

        send_mqtt_message(
            plate_number,
            plate_score,
            frigate_event_id,
            after_data,
            formatted_start_time,
            watched_plate,
            fuzzy_score,
        )

    if saved_plate_number or config['frigate'].get('always_save_snapshot', False):
        save_image(
            config=config,
            after_data=after_data,
            frigate_url=frigate_url,
            frigate_event_id=frigate_event_id,
            plate_number=saved_plate_number
        )

    return result

def setup_db():
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        conn.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
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

def load_config():
    global config, APP_CONFIG, DB_PATH, LOG_FILE, SNAPSHOT_PATH, PORT

    APP_CONFIG = load_app_config()
    config = APP_CONFIG.runtime_dict()

    DB_PATH = str(APP_CONFIG.paths.db_path)
    LOG_FILE = str(APP_CONFIG.paths.log_file)
    SNAPSHOT_PATH = str(APP_CONFIG.paths.snapshot_dir)
    PORT = APP_CONFIG.metrics_port

    snapshot_dir = APP_CONFIG.paths.snapshot_dir
    if not snapshot_dir.exists():
        snapshot_dir.mkdir(parents=True, exist_ok=True)

    log_path = APP_CONFIG.paths.log_file
    if not log_path.parent.exists():
        log_path.parent.mkdir(parents=True, exist_ok=True)

    db_path = APP_CONFIG.paths.db_path
    if not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)

    initialize_http_clients()

def run_mqtt_client():
    global mqtt_client
    _LOGGER.info(f"Starting MQTT client. Connecting to: {config['frigate']['mqtt_server']}")

    # setup mqtt client
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    mqtt_client.enable_logger()
    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message

    # check if we are using authentication and set username/password if so
    if config['frigate'].get('mqtt_username', False):
        username = config['frigate']['mqtt_username']
        password = config['frigate'].get('mqtt_password', '')
        mqtt_client.username_pw_set(username, password)

    mqtt_client.connect(config['frigate']['mqtt_server'], config['frigate'].get('mqtt_port', 1883))
    mqtt_client.loop_forever()

def load_logger():
    global _LOGGER
    _LOGGER = logging.getLogger(__name__)
    _LOGGER.setLevel(config.get('logger_level', 'INFO'))

    # Create a formatter to customize the log message format
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Create a console handler and set the level to display all messages
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)

    # Create a file handler to log messages to a file
    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # Add the handlers to the logger
    _LOGGER.addHandler(console_handler)
    _LOGGER.addHandler(file_handler)

def main():
    global executor

    load_config()
    setup_db()
    load_logger()

    _LOGGER.info("starting prom http server")
    prometheus_client.start_http_server(PORT)
    _LOGGER.info(f"Prometheus metrics listening on port {PORT}")

    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    _LOGGER.info(f"Time: {current_time}")
    _LOGGER.info(f"Python Version: {sys.version}")
    _LOGGER.info(f"Frigate Plate Recognizer Version: {VERSION}")
    _LOGGER.debug(f"config: {config}")

    if config.get('plate_recognizer'):
        _LOGGER.info(f"Using Plate Recognizer API")
    else:
        _LOGGER.info(f"Using CodeProject.AI API")


    max_workers = config.get('max_workers', 10)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    try:
        run_mqtt_client()
    finally:
        executor.shutdown(wait=True)


if __name__ == '__main__':
    main()
