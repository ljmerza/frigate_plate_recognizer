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
from pathlib import Path
from PIL import Image, ImageDraw, UnidentifiedImageError, ImageFont

mqtt_client = None
config = None
first_message = True
_LOGGER = None

VERSION = '1.7.4'

CONFIG_PATH = './config/config.yml'
DB_PATH = './config/frigate_plate_recogizer.db'
LOG_FILE = './config/frigate_plate_recogizer.log'
SNAPSHOT_PATH = './plates/'

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


def set_sublabel(frigate_url, frigate_event, sublabel, score):
    post_url = f"{frigate_url}/api/events/{frigate_event}/sub_label"
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

def send_mqtt_message(message):
    _LOGGER.debug(f"Sending MQTT message: {message}")

    main_topic = config['frigate']['main_topic']
    return_topic = config['frigate']['return_topic']
    topic = f'{main_topic}/{return_topic}'

    mqtt_client.publish(topic, json.dumps(message))

def has_common_value(array1, array2):
    return any(value in array2 for value in array1)

def save_image(after_data, snapshot_url, plate_number):
    _LOGGER.info(f"Getting image file: {snapshot_url}")
    _LOGGER.debug(f"Saving image data: {after_data}")
    response = requests.get(snapshot_url, params={ "crop": 0, "quality": 95})

    # Check if the request was successful (HTTP status code 200)
    if response.status_code != 200:
        _LOGGER.error(f"Error getting snapshot: {response.status_code}")
        return
    
    image = Image.open(io.BytesIO(bytearray(response.content)))
    last_detection = datetime.now().strftime(DATETIME_FORMAT)
    
    if(config['frigate'].get('frigate_plus', False)):
        attributes = after_data.get('current_attributes', [])
        license_plate_attribute = [attribute for attribute in attributes if attribute['label'] == 'license_plate']
        if not any(license_plate_attribute):
            _LOGGER.debug(f"no license_plate attribute found in event attributes")
            return

    draw = ImageDraw.Draw(image)
    vehicle = (
        license_plate_attribute[0]['box'][0],
        license_plate_attribute[0]['box'][1],
        license_plate_attribute[0]['box'][2],
        license_plate_attribute[0]['box'][3]
    )
    text = plate_number.upper()
    font= ImageFont.truetype("./Arial.ttf", size=14)
    _LOGGER.debug(f"Drawing box: {vehicle}")
    draw.rectangle(vehicle, outline="red", width=2)
    draw.text((license_plate_attribute[0]['box'][0]+5,license_plate_attribute[0]['box'][3]+5),text, font=font)
    latest_snapshot_path = f"{snapshot_path}/{after_data['camera']}_latest.png"
    _LOGGER.debug(f"Saving image snapshot: {latest_snapshot_path}")
    image.save(latest_snapshot_path)
    
    if config['frigate']['save_timestamped_file']:
        if plate_number is not None:
            timestamp_save_path = f"{snapshot_path}/{after_data['camera']}_{text}_{last_detection}.png"
        else:
            timestamp_save_path = f"{snapshot_path}/{after_data['camera']}_{last_detection}.png"
        image.save(timestamp_save_path)
        _LOGGER.info("Platerecognizer saved timestamped file %s", timestamp_save_path)
        
    

def on_message(client, userdata, message):
    global first_message
    if first_message:
        first_message = False
        _LOGGER.debug("skipping first message")
        return

    # get frigate event payload
    payload_dict = json.loads(message.payload)
    # _LOGGER.debug(f'mqtt message: {payload_dict}')

    before_data = payload_dict.get('before', {})
    after_data = payload_dict.get('after', {})

    # check if it is from the correct camera or zone
    config_zones = config['frigate'].get('zones', [])
    config_cameras = config['frigate'].get('camera', [])

    matching_zone = any(value in after_data['current_zones'] for value in config_zones) if config_zones else True
    matching_camera = after_data['camera'] in config_cameras if config_cameras else True

    # Check if either both match (when both are defined) or at least one matches (when only one is defined)
    if not (matching_zone and matching_camera):
        # _LOGGER.debug(f"Skipping event: {after_data['id']} because it does not match the configured zones/cameras")
        return

    # check if it is a valid object
    valid_objects = config['frigate'].get('objects', DEFAULT_OBJECTS)
    if(after_data['label'] not in valid_objects):
        _LOGGER.debug(f"is not a correct label: {after_data['label']}")
        return

    # if user has frigate plus then check license plate attribute else
    # limit api calls by only checking the best score for an event
    if(config['frigate'].get('frigate_plus', False)):
        attributes = after_data.get('current_attributes', [])
        license_plate_attribute = [attribute for attribute in attributes if attribute['label'] == 'license_plate']
        if not any(license_plate_attribute):
            _LOGGER.debug(f"no license_plate attribute found in event attributes")
            return

        # check min score of license plate attribute
        license_plate_min_score = config['frigate'].get('license_plate_min_score', 0)
        if license_plate_attribute[0]['score'] < license_plate_min_score:
            _LOGGER.debug(f"license_plate attribute score is below minimum: {license_plate_attribute[0]['score']}")
            return

    elif(before_data['top_score'] == after_data['top_score']):
        _LOGGER.debug(f"duplicated snapshot from Frigate as top_score from before and after are the same: {after_data['top_score']}")
        return

    # get frigate event
    frigate_event = after_data['id']
    frigate_url = config['frigate']['frigate_url']

    # see if we have already processed this event
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM plates WHERE frigate_event = ?
    """, (frigate_event,))
    row = cursor.fetchone()
    conn.close()

    if row is not None:
        _LOGGER.debug(f"Skipping event: {frigate_event} because it has already been processed")
        return

    snapshot_url = f"{frigate_url}/api/events/{frigate_event}/snapshot.jpg"
    _LOGGER.debug(f"Getting image for event: {frigate_event}" )
    _LOGGER.debug(f"event URL: {snapshot_url}")

    response = requests.get(snapshot_url, params={ "crop": 1, "quality": 95 })

    # Check if the request was successful (HTTP status code 200)
    if response.status_code != 200:
        _LOGGER.error(f"Error getting snapshot: {response.status_code}")
        return

    # try to get plate number
    plate_number = None
    score = None
    is_valid_plate = True

    if config.get('plate_recognizer'):
        plate_number, score = plate_recognizer(response.content)
    elif config.get('code_project'):
        plate_number, score = code_project(response.content)
    else:
        _LOGGER.error("Plate Recognizer is not configured")
        return    

    if plate_number is None:
        _LOGGER.info(f'No plate number found for event {frigate_event}')
        is_valid_plate = False

    # check score
    min_score = config['frigate'].get('min_score')
    if min_score and score:
        if score < min_score:
            _LOGGER.info(f"Score is below minimum: {score}")
            is_valid_plate = False

    
    if is_valid_plate:
        # get db connection
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Insert a new record of plate number
        _LOGGER.info(f"Storing plate number in database: {plate_number} with score: {score}")

        start_time = datetime.fromtimestamp(after_data['start_time'])
        formatted_start_time = start_time.strftime("%Y-%m-%d %H:%M:%S")
        
        cursor.execute("""
            INSERT INTO plates (detection_time, score, plate_number, frigate_event, camera_name) VALUES (?, ?, ?, ?, ?)
        """, (formatted_start_time, score, plate_number, frigate_event, after_data['camera']))
        conn.commit()
        conn.close()

        # set the sublabel
        set_sublabel(frigate_url, frigate_event, plate_number, score)

        # send mqtt message
        if config['frigate'].get('return_topic'):
            send_mqtt_message({
                'plate_number': plate_number,
                'score': score,
                'frigate_event': frigate_event,
                'camera_name': after_data['camera'],
                'start_time': formatted_start_time
            })
            
    # save image
    if config['frigate']['save_snapshots']:
        if (plate_number is not None and min_score and score > min_score) or config['frigate']['always_save_latest_file']:
            save_image(after_data, snapshot_url, plate_number)

        

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
    global snapshot_path
    with open(CONFIG_PATH, 'r') as config_file:
        config = yaml.safe_load(config_file)
    
    if SNAPSHOT_PATH:
        snapshot_path = Path(SNAPSHOT_PATH)      
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
    _LOGGER.setLevel(config['logger_level'])

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
