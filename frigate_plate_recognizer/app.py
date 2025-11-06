from __future__ import annotations

import concurrent.futures
import json
import logging
import signal
import sqlite3
import sys
import time
from datetime import datetime
from typing import Any, Dict, Optional

import prometheus_client
from requests import Session
from requests.exceptions import RequestException

from frigate_plate_recognizer import __version__ as PACKAGE_VERSION
from frigate_plate_recognizer.config import (
    DEFAULT_DB_PATH,
    DEFAULT_LOG_FILE,
    DEFAULT_METRICS_PORT,
    DEFAULT_SNAPSHOT_DIR,
    AppConfig,
    load_app_config,
)
from frigate_plate_recognizer.event_filters import (
    check_first_message as filter_check_first_message,
)
from frigate_plate_recognizer.event_filters import (
    check_invalid_event as filter_check_invalid_event,
)
from frigate_plate_recognizer.event_filters import (
    get_license_plate_attribute as filter_get_license_plate_attribute,
)
from frigate_plate_recognizer.events import (
    clear_event,
    get_event_attempts,
    increment_event_attempt,
    is_event_tracked,
    track_event_start,
)
from frigate_plate_recognizer.http_client import build_session
from frigate_plate_recognizer.images import (
    fetch_final_attributes,
    fetch_snapshot,
)
from frigate_plate_recognizer.images import (
    save_image as save_snapshot_image,
)
from frigate_plate_recognizer.messaging import create_mqtt_client, publish_plate_message
from frigate_plate_recognizer.metrics import (
    http_request_latency_histogram,
    processed_events_counter,
)
from frigate_plate_recognizer.pipeline import get_plate as pipeline_get_plate
from frigate_plate_recognizer.storage import (
    has_processed_event,
    initialise_database,
    insert_plate,
)

mqtt_client: Optional[Any] = None
config: Optional[Dict[str, Any]] = None
first_message = True
_LOGGER: logging.Logger = logging.getLogger(__name__)

executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
_shutdown_requested = False

APP_CONFIG: AppConfig | None = None


def require_config() -> Dict[str, Any]:
    if config is None:
        raise RuntimeError("Configuration has not been loaded")
    return config


def require_logger() -> logging.Logger:
    return _LOGGER


def require_app_config() -> AppConfig:
    if APP_CONFIG is None:
        raise RuntimeError("App configuration has not been loaded")
    return APP_CONFIG

FRIGATE_SESSION: Optional[Session] = None
PLATE_RECOGNIZER_SESSION: Optional[Session] = None
CODE_PROJECT_SESSION: Optional[Session] = None

VERSION = PACKAGE_VERSION

DB_PATH = str(DEFAULT_DB_PATH)
LOG_FILE = str(DEFAULT_LOG_FILE)
SNAPSHOT_PATH = str(DEFAULT_SNAPSHOT_DIR)

DATETIME_FORMAT = "%Y-%m-%d_%H-%M"
PORT = DEFAULT_METRICS_PORT
DB_TIMEOUT_SECONDS = 30
DB_BUSY_TIMEOUT_MS = 5000


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
    track_event_start(event_id)


def _is_event_tracked(event_id: str) -> bool:
    return is_event_tracked(event_id)


def _increment_event_attempt(event_id: str) -> int:
    return increment_event_attempt(event_id)


def _get_event_attempts(event_id: str) -> int:
    return get_event_attempts(event_id)


def _clear_event(event_id: str) -> None:
    clear_event(event_id)


def get_snapshot(frigate_event_id, frigate_url, cropped):
    session = get_frigate_session()
    return fetch_snapshot(
        session,
        frigate_url=frigate_url,
        frigate_event_id=frigate_event_id,
        cropped=cropped,
        logger=_LOGGER,
        histogram=http_request_latency_histogram,
    )


def get_final_data(event_url):
    cfg = require_config()
    session = get_frigate_session()
    return fetch_final_attributes(
        session,
        event_url=event_url,
        use_frigate_plus=cfg['frigate'].get('frigate_plus', False),
        logger=require_logger(),
        histogram=http_request_latency_histogram,
    )

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

def has_common_value(array1, array2):
    return any(value in array2 for value in array1)


def send_mqtt_message(plate_number, plate_score, frigate_event_id, after_data, formatted_start_time, watched_plate, fuzzy_score):
    publish_plate_message(
        mqtt_client=mqtt_client,
        config=config,
        plate_number=plate_number,
        plate_score=plate_score,
        frigate_event_id=frigate_event_id,
        after_data=after_data,
        formatted_start_time=formatted_start_time,
        watched_plate=watched_plate,
        fuzzy_score=fuzzy_score,
        logger=_LOGGER,
    )


def save_image(config, after_data, frigate_url, frigate_event_id, plate_number):
    if not config['frigate'].get('save_snapshots', False):
        require_logger().debug("Skipping saving snapshot because save_snapshots is set to false")
        return

    session = get_frigate_session()
    save_snapshot_image(
        config=config,
        after_data=after_data,
        frigate_url=frigate_url,
        frigate_event_id=frigate_event_id,
        plate_number=plate_number,
        snapshot_path=SNAPSHOT_PATH,
        datetime_format=DATETIME_FORMAT,
        session=session,
        logger=require_logger(),
        histogram=http_request_latency_histogram,
    )


def check_first_message():
    global first_message
    should_skip = filter_check_first_message(first_message, _LOGGER)
    first_message = False
    return should_skip


def check_invalid_event(before_data, after_data):
    cfg = require_config()
    return filter_check_invalid_event(
        config=cfg,
        before_data=before_data,
        after_data=after_data,
        is_tracked=_is_event_tracked(after_data.get('id')),
        logger=require_logger(),
    )


def get_license_plate_attribute(after_data):
    cfg = require_config()
    return filter_get_license_plate_attribute(cfg, after_data)


def is_valid_license_plate(after_data):
    logger = require_logger()
    cfg = require_config()
    attributes = get_license_plate_attribute(after_data) or []
    if not attributes:
        logger.debug("no license_plate attribute found in event attributes")
        return False

    license_plate_min_score = cfg['frigate'].get('license_plate_min_score', 0)
    score = attributes[0].get('score', 0) if isinstance(attributes[0], dict) else 0
    if score < license_plate_min_score:
        logger.debug("license_plate attribute score is below minimum: %s", score)
        return False

    return True

def is_duplicate_event(frigate_event_id):
    return has_processed_event(
        DB_PATH,
        frigate_event_id,
        timeout_seconds=DB_TIMEOUT_SECONDS,
        busy_timeout_ms=DB_BUSY_TIMEOUT_MS,
        logger=_LOGGER,
    )

def get_plate(snapshot):
    cfg = require_config()
    app_cfg = require_app_config()
    logger = require_logger()
    return pipeline_get_plate(
        snapshot,
        config=cfg,
        app_config=app_cfg,
        logger=logger,
        plate_session=get_plate_recognizer_session() if cfg.get('plate_recognizer') else None,
        code_project_session=get_code_project_session() if cfg.get('code_project') else None,
    )

def store_plate_in_db(plate_number, plate_score, frigate_event_id, after_data, formatted_start_time) -> bool:
    require_logger().info("Storing plate number in database: %s with score: %s", plate_number, plate_score)

    return insert_plate(
        DB_PATH,
        timeout_seconds=DB_TIMEOUT_SECONDS,
        busy_timeout_ms=DB_BUSY_TIMEOUT_MS,
        logger=require_logger(),
        detection_time=formatted_start_time,
        score=plate_score,
        plate_number=plate_number,
        frigate_event_id=frigate_event_id,
        camera_name=after_data['camera'],
    )

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
    logger = require_logger()
    cfg = require_config()

    if check_first_message():
        return 'first_message'

    payload_dict = json.loads(message.payload)
    logger.debug('mqtt message: %s', payload_dict)

    before_data = payload_dict.get('before', {})
    after_data = payload_dict.get('after', {})
    message_type = payload_dict.get('type', '')

    frigate_url = cfg['frigate']['frigate_url']
    frigate_event_id = after_data['id']

    if message_type == 'end' and _is_event_tracked(frigate_event_id):
        logger.debug(
            f"CLEARING EVENT: {frigate_event_id} after {_get_event_attempts(frigate_event_id)} calls to AI engine"
        )
        _clear_event(frigate_event_id)

    if check_invalid_event(before_data, after_data):
        return 'invalid_event'

    if is_duplicate_event(frigate_event_id):
        return 'duplicate_event'

    frigate_plus = cfg['frigate'].get('frigate_plus', False)
    if frigate_plus and not is_valid_license_plate(after_data):
        return 'invalid_license_plate'

    if message_type != 'end' and not _is_event_tracked(frigate_event_id):
        _track_event_start(frigate_event_id)

    snapshot = None
    if after_data.get('has_snapshot'):
        snapshot = get_snapshot(frigate_event_id, frigate_url, True)
    if not snapshot:
        logger.debug(f"Event {frigate_event_id} has no snapshot")
        _clear_event(frigate_event_id)
        return 'no_snapshot'

    logger.debug(f"Getting plate for event: {frigate_event_id}")

    max_attempts = cfg['frigate'].get('max_attempts', 0)
    if max_attempts > 0 and _get_event_attempts(frigate_event_id) >= max_attempts:
        logger.debug(
            f"Maximum number of AI attempts reached for event {frigate_event_id}: {_get_event_attempts(frigate_event_id)}"
        )
        return 'max_attempts'

    attempt_count = _increment_event_attempt(frigate_event_id)
    logger.debug(f"Attempt {attempt_count} for event {frigate_event_id}")

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

        publish_plate_message(
            mqtt_client=mqtt_client,
            config=cfg,
            plate_number=plate_number,
            plate_score=plate_score,
            frigate_event_id=frigate_event_id,
            after_data=after_data,
            formatted_start_time=formatted_start_time,
            watched_plate=watched_plate,
            fuzzy_score=fuzzy_score,
            logger=logger,
        )

    if saved_plate_number or cfg['frigate'].get('always_save_snapshot', False):
        save_image(
            config=cfg,
            after_data=after_data,
            frigate_url=frigate_url,
            frigate_event_id=frigate_event_id,
            plate_number=saved_plate_number
        )

    return result

def setup_db():
    logger = _LOGGER or logging.getLogger(__name__)
    initialise_database(
        DB_PATH,
        timeout_seconds=DB_TIMEOUT_SECONDS,
        busy_timeout_ms=DB_BUSY_TIMEOUT_MS,
        logger=logger,
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
    cfg = require_config()
    logger = require_logger()
    logger.info(f"Starting MQTT client. Connecting to: {cfg['frigate']['mqtt_server']}")

    mqtt_client = create_mqtt_client(
        config=cfg,
        logger=logger,
        message_callback=on_message,
    )
    mqtt_client.connect(cfg['frigate']['mqtt_server'], cfg['frigate'].get('mqtt_port', 1883))
    
    # Loop with periodic checks for shutdown signal
    while not _shutdown_requested:
        mqtt_client.loop(timeout=1.0)

def load_logger():
    cfg = require_config()
    logger = require_logger()
    logger.setLevel(cfg.get('logger_level', 'INFO'))

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
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

def _signal_handler(signum: int, frame: Any) -> None:
    """Handle shutdown signals gracefully."""
    global _shutdown_requested, mqtt_client
    signal_name = signal.Signals(signum).name
    _LOGGER.info(f"Received {signal_name} signal, initiating graceful shutdown...")
    _shutdown_requested = True
    
    # Disconnect MQTT client
    if mqtt_client:
        try:
            mqtt_client.disconnect()
            _LOGGER.info("MQTT client disconnected")
        except Exception as exc:
            _LOGGER.warning(f"Error disconnecting MQTT client: {exc}")

def main():
    global executor

    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    load_config()
    setup_db()
    load_logger()

    cfg = require_config()

    _LOGGER.info("starting prom http server")
    prometheus_client.start_http_server(PORT)
    _LOGGER.info(f"Prometheus metrics listening on port {PORT}")

    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    _LOGGER.info(f"Time: {current_time}")
    _LOGGER.info(f"Python Version: {sys.version}")
    _LOGGER.info(f"Frigate Plate Recognizer Version: {VERSION}")
    _LOGGER.debug(f"config: {cfg}")

    if cfg.get('plate_recognizer'):
        _LOGGER.info("Using Plate Recognizer API")
    else:
        _LOGGER.info("Using CodeProject.AI API")


    max_workers = cfg.get('max_workers', 10)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    try:
        run_mqtt_client()
    except KeyboardInterrupt:
        _LOGGER.info("Received keyboard interrupt, shutting down...")
    finally:
        _LOGGER.info("Shutting down thread pool executor...")
        executor.shutdown(wait=True)
        _LOGGER.info("Shutdown complete")


if __name__ == '__main__':
    main()
