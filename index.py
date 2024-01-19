#!/bin/python3

from datetime import datetime

import os
import sqlite3
import time
import logging

import paho.mqtt.client as mqtt
import yaml
import sys
import json
import requests

import io
from PIL import Image, ImageDraw, UnidentifiedImageError, ImageFont

mqtt_client = None
config = None
first_message = True
_LOGGER = None

VERSION = '1.8.9'

CONFIG_PATH = './config/config.yml'
DB_PATH = './config/frigate_plate_recogizer.db'
LOG_FILE = './config/frigate_plate_recogizer.log'
SNAPSHOT_PATH = '/plates'

DATETIME_FORMAT = "%Y-%m-%d_%H-%M-%S"

PLATE_RECOGIZER_BASE_URL = 'https://api.platerecognizer.com/v1/plate-reader'
DEFAULT_OBJECTS = ['car', 'motorcycle', 'bus']


def on_connect(mqtt_client, userdata, flags, rc):
    _LOGGER.info("MQTT Connected")
    mqtt_client.subscribe(config['frigate']['main_topic'] + "/events")

def on_disconnect(mqtt_client, userdata, rc):
    if rc != 0:
        _LOGGER.warning("Unexpected disconnection, trying to reconnect")
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

    # Submit the POST request with the JSON payload
    payload = { "subLabel": sublabel }
    headers = { "Content-Type": "application/json" }
    response = requests.post(post_url, data=json.dumps(payload), headers=headers)


    percentscore = "{:.1%}".format(score)

    # Check for a successful response
    if response.status_code == 200:
        _LOGGER.info(f"Sublabel set successfully to: {sublabel} with {percentscore} confidence")
    else:
        _LOGGER.error(f"Failed to set sublabel. Status code: {response.status_code}")

def code_project(image):
    api_url = config['code_project'].get('api_url')

    response = requests.post(
        api_url,
        files=dict(upload=image),
    )
    response = response.json()
    _LOGGER.debug(f"response: {response}")

    if response.get('predictions') is None:
        _LOGGER.error(f"Failed to get plate number. Response: {response}")
        return None, None

    if len(response['predictions']) == 0:
        _LOGGER.debug(f"No plates found")
        return None, None

    plate_number = response['predictions'][0].get('plate')
    score = response['predictions'][0].get('confidence')

    return plate_number, score

def plate_recognizer(image):
    api_url = config['plate_recognizer'].get('api_url') or PLATE_RECOGIZER_BASE_URL
    token = config['plate_recognizer']['token']

    response = requests.post(
        api_url,
        data=dict(regions=config['plate_recognizer']['regions']),
        files=dict(upload=image),
        headers={'Authorization': f'Token {token}'}
    )

    response = response.json()
    _LOGGER.debug(f"response: {response}")

    if response.get('results') is None:
        _LOGGER.error(f"Failed to get plate number. Response: {response}")
        return None, None

    if len(response['results']) == 0:
        _LOGGER.debug(f"No plates found")
        return None, None

    plate_number = response['results'][0].get('plate')
    score = response['results'][0].get('score')

    return plate_number, score

def send_mqtt_message(plate_number, plate_score, frigate_event_id, after_data, formatted_start_time):
    if not config['frigate'].get('return_topic'):
        return

    message = {
        'plate_number': plate_number,
        'score': plate_score,
        'frigate_event_id': frigate_event_id,
        'camera_name': after_data['camera'],
        'start_time': formatted_start_time
    }

    _LOGGER.debug(f"Sending MQTT message: {message}")

    main_topic = config['frigate']['main_topic']
    return_topic = config['frigate']['return_topic']
    topic = f'{main_topic}/{return_topic}'

    mqtt_client.publish(topic, json.dumps(message))

def has_common_value(array1, array2):
    return any(value in array2 for value in array1)

def save_image(config, after_data, image_content, license_plate_attribute, plate_number):
    if not config['frigate'].get('save_snapshots', False):
        _LOGGER.debug(f"Skipping saving snapshot because save_snapshots is set to false")
        return

    image = Image.open(io.BytesIO(bytearray(image_content)))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("./Arial.ttf", size=14)

    # if given a plate number then draw it on the image along with the box around it
    if license_plate_attribute and config['frigate'].get('draw_box', False):
        vehicle = (
            license_plate_attribute[0]['box'][0],
            license_plate_attribute[0]['box'][1],
            license_plate_attribute[0]['box'][2],
            license_plate_attribute[0]['box'][3]
        )
        _LOGGER.debug(f"Drawing box: {vehicle}")
        draw.rectangle(vehicle, outline="red", width=2)

        if plate_number:
            draw.text((license_plate_attribute[0]['box'][0]+5,license_plate_attribute[0]['box'][3]+5), plate_number, font=font)

    # save image
    timestamp = datetime.now().strftime(DATETIME_FORMAT)
    image_name = f"{after_data['camera']}_{timestamp}.png"
    if plate_number:
        image_name = f"{plate_number}_{image_name}"

    image_path = f"{SNAPSHOT_PATH}/{image_name}"
    _LOGGER.debug(f"Saving image with path: {image_path}")
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
    if(before_data['top_score'] == after_data['top_score']):
        _LOGGER.debug(f"duplicated snapshot from Frigate as top_score from before and after are the same: {after_data['top_score']}")
        return True
    return False

def get_snapshot(frigate_event_id, frigate_url):
    _LOGGER.debug(f"Getting snapshot for event: {frigate_event_id}")
    snapshot_url = f"{frigate_url}/api/events/{frigate_event_id}/snapshot.jpg"
    _LOGGER.debug(f"event URL: {snapshot_url}")

    # get snapshot
    if config['frigate']['crop_image']:
        response = requests.get(snapshot_url, params={ "crop": 1, "quality": 95 })
    else:
        response = requests.get(snapshot_url, params={ "crop": 0, "quality": 95 })

    # Check if the request was successful (HTTP status code 200)
    if response.status_code != 200:
        _LOGGER.error(f"Error getting snapshot: {response.status_code}")
        return

    return response.content

def get_license_plate(after_data):
    if config['frigate'].get('frigate_plus', False):
        attributes = after_data.get('current_attributes', [])
        license_plate_attribute = [attribute for attribute in attributes if attribute['label'] == 'license_plate']
        return license_plate_attribute
    else:
        return None

def is_valid_license_plate(after_data):
    # if user has frigate plus then check license plate attribute
    license_plate_attribute = get_license_plate(after_data)
    if not any(license_plate_attribute):
        _LOGGER.debug(f"no license_plate attribute found in event attributes")
        return False

    # check min score of license plate attribute
    license_plate_min_score = config['frigate'].get('license_plate_min_score', 0)
    if license_plate_attribute[0]['score'] < license_plate_min_score:
        _LOGGER.debug(f"license_plate attribute score is below minimum: {license_plate_attribute[0]['score']}")
        return False

    return True

def is_duplicate_event(frigate_event_id):
     # see if we have already processed this event
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""SELECT * FROM plates WHERE frigate_event = ?""", (frigate_event_id,))
    row = cursor.fetchone()
    conn.close()

    if row is not None:
        _LOGGER.debug(f"Skipping event: {frigate_event_id} because it has already been processed")
        return True

    return False

def get_plate(snapshot, after_data, license_plate_attribute):
    # try to get plate number
    plate_number = None
    plate_score = None

    if config.get('plate_recognizer'):
        plate_number, plate_score = plate_recognizer(snapshot)
    elif config.get('code_project'):
        plate_number, plate_score = code_project(snapshot)
    else:
        _LOGGER.error("Plate Recognizer is not configured")
        return None, None

    # check Plate Recognizer score
    min_score = config['frigate'].get('min_score')
    score_too_low = min_score and plate_score and plate_score < min_score

    if not score_too_low or config['frigate'].get('always_save_snapshot', False):
        save_image(
            config=config,
            after_data=after_data,
            image_content=snapshot,
            license_plate_attribute=license_plate_attribute,
            plate_number=plate_number
        )

    if score_too_low:
        _LOGGER.info(f"Score is below minimum: {plate_score}")
        return None, None

    return plate_number, plate_score

def store_plate_in_db(plate_number, plate_score, frigate_event_id, after_data, formatted_start_time):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    _LOGGER.info(f"Storing plate number in database: {plate_number} with score: {plate_score}")

    cursor.execute("""INSERT INTO plates (detection_time, score, plate_number, frigate_event, camera_name) VALUES (?, ?, ?, ?, ?)""",
        (formatted_start_time, plate_score, plate_number, frigate_event_id, after_data['camera'])
    )

    conn.commit()
    conn.close()

def on_message(client, userdata, message):
    if check_first_message():
        return

    # get frigate event payload
    payload_dict = json.loads(message.payload)
    _LOGGER.debug(f'mqtt message: {payload_dict}')

    before_data = payload_dict.get('before', {})
    after_data = payload_dict.get('after', {})

    if check_invalid_event(before_data, after_data):
        return

    frigate_url = config['frigate']['frigate_url']
    frigate_event_id = after_data['id']

    if is_duplicate_event(frigate_event_id):
        return

    snapshot = get_snapshot(frigate_event_id, frigate_url)
    if not snapshot:
        return

    frigate_plus = config['frigate'].get('frigate_plus', False)
    if frigate_plus and not is_valid_license_plate(after_data):
        return

    license_plate_attribute = get_license_plate(after_data)

    plate_number, plate_score = get_plate(snapshot, after_data, license_plate_attribute)
    if not plate_number:
        return

    start_time = datetime.fromtimestamp(after_data['start_time'])
    formatted_start_time = start_time.strftime("%Y-%m-%d %H:%M:%S")

    store_plate_in_db(plate_number, plate_score, frigate_event_id, after_data, formatted_start_time)
    set_sublabel(frigate_url, frigate_event_id, plate_number, plate_score)

    send_mqtt_message(plate_number, plate_score, frigate_event_id, after_data, formatted_start_time)


def setup_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS plates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detection_time TIMESTAMP NOT NULL,
            score TEXT NOT NULL,
            plate_number TEXT NOT NULL,
            frigate_event TEXT NOT NULL UNIQUE,
            camera_name TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def load_config():
    global config
    with open(CONFIG_PATH, 'r') as config_file:
        config = yaml.safe_load(config_file)

    if SNAPSHOT_PATH:
        if not os.path.isdir(SNAPSHOT_PATH):
            os.makedirs(SNAPSHOT_PATH)

def run_mqtt_client():
    global mqtt_client
    _LOGGER.info(f"Starting MQTT client. Connecting to: {config['frigate']['mqtt_server']}")
    now = datetime.now()
    current_time = now.strftime("%Y%m%d%H%M%S")

    # setup mqtt client
    mqtt_client = mqtt.Client("FrigatePlateRecognizer" + current_time)
    mqtt_client.on_message = on_message
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_connect = on_connect

    # check if we are using authentication and set username/password if so
    if config['frigate']['mqtt_auth']:
        username = config['frigate']['mqtt_username']
        password = config['frigate']['mqtt_password']
        mqtt_client.username_pw_set(username, password)

    mqtt_client.connect(config['frigate']['mqtt_server'])
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
    load_config()
    setup_db()
    load_logger()

    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    _LOGGER.info(f"Time: {current_time}")
    _LOGGER.info(f"Python Version: {sys.version}")
    _LOGGER.info(f"Frigate Plate Recognizer Version: {VERSION}")
    _LOGGER.debug(f"config: {config}")

    if config.get('plate_recognizer'):
        _LOGGER.info(f"Using Plate Recognizer API")
    else:
        _LOGGER.info(f"Using CodeProject.AI API")


    run_mqtt_client()


if __name__ == '__main__':
    main()
