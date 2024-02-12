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
import difflib

mqtt_client = None
config = None
first_message = True
_LOGGER = None

VERSION = '1.8.12'

CONFIG_PATH = '/config/config.yml'
DB_PATH = '/config/frigate_plate_recogizer.db'
LOG_FILE = '/config/frigate_plate_recogizer.log'
SNAPSHOT_PATH = '/plates'

DATETIME_FORMAT = "%Y-%m-%d_%H-%M"

PLATE_RECOGIZER_BASE_URL = 'https://api.platerecognizer.com/v1/plate-reader'
DEFAULT_OBJECTS = ['car', 'motorcycle', 'bus']
CURRENT_EVENTS = {}


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

    sublabel = str(sublabel).upper() # plates are always upper cased

    # Submit the POST request with the JSON payload
    payload = { "subLabel": sublabel }
    headers = { "Content-Type": "application/json" }
    response = requests.post(post_url, data=json.dumps(payload), headers=headers)

    percent_score = "{:.1%}".format(score)

    # Check for a successful response
    if response.status_code == 200:
        _LOGGER.info(f"Sublabel set successfully to: {sublabel} with {percent_score} confidence")
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
        return None, None, None, None

    if len(response['predictions']) == 0:
        _LOGGER.debug(f"No plates found")
        return None, None, None, None

    plate_number = response['predictions'][0].get('plate')
    score = response['predictions'][0].get('confidence')
    
    watched_plate, watched_score, fuzzy_score = check_watched_plates(plate_number, response['predictions'])   
    if fuzzy_score:
        return plate_number, score, watched_plate, fuzzy_score
    elif watched_plate: 
        return plate_number, watched_score, watched_plate, None
    else:
        return plate_number, score, None, None

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
        return None, None, None, None

    if len(response['results']) == 0:
        _LOGGER.debug(f"No plates found")
        return None, None, None, None

    plate_number = response['results'][0].get('plate')
    score = response['results'][0].get('score')
    
    watched_plate, watched_score, fuzzy_score = check_watched_plates(plate_number, response['results'][0].get('candidates'))
    if fuzzy_score:
        return plate_number, score, watched_plate, fuzzy_score
    elif watched_plate: 
        return plate_number, watched_score, watched_plate, None
    else:
        return plate_number, score, None, None

def check_watched_plates(plate_number, response):
    config_watched_plates = config['frigate'].get('watched_plates', [])
    if not config_watched_plates:
        _LOGGER.debug("Skipping checking Watched Plates because watched_plates is not set")
        return None, None, None
    
    config_watched_plates = [str(x).lower() for x in config_watched_plates] #make sure watched_plates are all lower case
    
    #Step 1 - test if top plate is a watched plate
    matching_plate = str(plate_number).lower() in config_watched_plates 
    if matching_plate:
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
            'original_plate': str(plate_number).upper()
        }
    else:
        message = {
            'plate_number': str(plate_number).upper(),
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
        dimension_1 = int(final_attribute[0]['box'][0])
        dimension_2 = int(final_attribute[0]['box'][1])
        dimension_3 = int(final_attribute[0]['box'][2])
        dimension_4 = int(final_attribute[0]['box'][3])

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
    if(before_data['top_score'] == after_data['top_score'] and after_data['id'] in CURRENT_EVENTS) and not config['frigate'].get('frigate_plus', False):
        _LOGGER.debug(f"duplicated snapshot from Frigate as top_score from before and after are the same: {after_data['top_score']} {after_data['id']}")
        return True
    return False

def get_snapshot(frigate_event_id, frigate_url, cropped):
    _LOGGER.debug(f"Getting snapshot for event: {frigate_event_id}, Crop: {cropped}")
    snapshot_url = f"{frigate_url}/api/events/{frigate_event_id}/snapshot.jpg"
    _LOGGER.debug(f"event URL: {snapshot_url}")

    # get snapshot
    response = requests.get(snapshot_url, params={ "crop": cropped, "quality": 95 })

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
        response = requests.get(event_url)
        if response.status_code != 200:
            _LOGGER.error(f"Error getting final data: {response.status_code}")
            return
        event_json = response.json()
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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""SELECT * FROM plates WHERE frigate_event = ?""", (frigate_event_id,))
    row = cursor.fetchone()
    conn.close()

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
    type = payload_dict.get('type','')
    
    frigate_url = config['frigate']['frigate_url']
    frigate_event_id = after_data['id']
    
    if type == 'end' and after_data['id'] in CURRENT_EVENTS:
        _LOGGER.debug(f"CLEARING EVENT: {frigate_event_id} after {CURRENT_EVENTS[frigate_event_id]} calls to AI engine")
        del CURRENT_EVENTS[frigate_event_id]
    
    if check_invalid_event(before_data, after_data):
        return

    if is_duplicate_event(frigate_event_id):
        return

    frigate_plus = config['frigate'].get('frigate_plus', False)
    if frigate_plus and not is_valid_license_plate(after_data):
        return
    
    if not type == 'end' and not after_data['id'] in CURRENT_EVENTS:
        CURRENT_EVENTS[frigate_event_id] =  0
        
    
    snapshot = get_snapshot(frigate_event_id, frigate_url, True)
    if not snapshot:
        del CURRENT_EVENTS[frigate_event_id] # remove existing id from current events due to snapshot failure - will try again next frame
        return

    _LOGGER.debug(f"Getting plate for event: {frigate_event_id}")
    if frigate_event_id in CURRENT_EVENTS:
        if config['frigate'].get('max_attempts', 0) > 0 and CURRENT_EVENTS[frigate_event_id] > config['frigate'].get('max_attempts', 0):
            _LOGGER.debug(f"Maximum number of AI attempts reached for event {frigate_event_id}: {CURRENT_EVENTS[frigate_event_id]}")
            return
        CURRENT_EVENTS[frigate_event_id] += 1

    plate_number, plate_score, watched_plate, fuzzy_score = get_plate(snapshot)
    if plate_number:
        start_time = datetime.fromtimestamp(after_data['start_time'])
        formatted_start_time = start_time.strftime("%Y-%m-%d %H:%M:%S")
        
        if watched_plate:
            store_plate_in_db(watched_plate, plate_score, frigate_event_id, after_data, formatted_start_time)
        else:
            store_plate_in_db(plate_number, plate_score, frigate_event_id, after_data, formatted_start_time)
        set_sublabel(frigate_url, frigate_event_id, watched_plate if watched_plate else plate_number, plate_score)

        send_mqtt_message(plate_number, plate_score, frigate_event_id, after_data, formatted_start_time, watched_plate, fuzzy_score)
         
    if plate_number or config['frigate'].get('always_save_snapshot', False):
        save_image(
            config=config,
            after_data=after_data,
            frigate_url=frigate_url,
            frigate_event_id=frigate_event_id,
            plate_number=watched_plate if watched_plate else plate_number
        )

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
    if config['frigate'].get('mqtt_username', False):
        username = config['frigate']['mqtt_username']
        password = config['frigate'].get('mqtt_password', '')
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
