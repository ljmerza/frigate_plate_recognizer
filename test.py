
import json
import logging
from pathlib import Path
import os
import unittest
from unittest.mock import patch, MagicMock, mock_open

from PIL import Image, ImageDraw
import yaml

import index

class BaseTestCase(unittest.TestCase):
    def setUp(self):
        mock_logger = MagicMock()
        index._LOGGER = mock_logger
        self.mock_logger = mock_logger

class TestLoadConfig(BaseTestCase):
    @patch('os.path.isdir', return_value=False)
    @patch('os.makedirs')
    @patch('yaml.safe_load')
    @patch('builtins.open', new_callable=mock_open, read_data="config: {}")
    def test_load_config(self, mock_open, mock_safe_load, mock_makedirs, mock_isdir):
        index.CONFIG_PATH = 'dummy_path'
        index.load_config()
        mock_open.assert_called_once_with('dummy_path', 'r')
        mock_safe_load.assert_called_once()
        mock_isdir.assert_called_once_with(index.SNAPSHOT_PATH)
        mock_makedirs.assert_called_once_with(index.SNAPSHOT_PATH)

class TestSaveImage(BaseTestCase):
    def setUp(self):
      index._LOGGER = logging.getLogger(__name__)

    @patch('index.get_snapshot')
    @patch('index.get_final_data')
    @patch('index.Image.open')
    @patch('index.ImageDraw.Draw')
    @patch('index.ImageFont.truetype')
    @patch('index.datetime')
    @patch('index.open', new_callable=mock_open)
    def test_save_image_with_box(self, mock_file, mock_datetime, mock_truetype, mock_draw, mock_open, mock_get_final_data, mock_get_snapshot):
        # Mock current time
        mock_now = mock_datetime.now.return_value
        mock_now.strftime.return_value = '20210101_120000'

        # Setup configuration and input data
        index.config = {'frigate': {'save_snapshots': True, 'draw_box': True}}
        after_data = {'camera': 'test_camera'}
        frigate_url = 'http://example.com'
        frigate_event_id = 'test_event_id'
        plate_number = 'ABC123'

        # Mock PIL dependencies
        mock_image = MagicMock(spec=Image.Image)
        mock_image.size = (640, 480)  # Example size
        mock_image_draw = mock_draw.return_value
        mock_open.return_value = mock_image
        mock_truetype.return_value = MagicMock()

        mock_get_final_data.return_value = [{'box': [0, 0, 100, 100]}]
        mock_get_snapshot.return_value = b'ImageBytes'

        # Call the function
        index.save_image(index.config, after_data, frigate_url, frigate_event_id, plate_number)

        # Assert image operations
        # Assert image operations
        expected_path = '/plates/ABC123_test_camera_20210101_120000.png'
        mock_image.save.assert_called_once_with(expected_path)
        mock_image_draw.rectangle.assert_called_once_with(
            (0, 0, 640 * 100, 480 * 100), outline="red", width=2  # Ensure this matches what's being called
        )
        mock_image_draw.text.assert_called_once_with((5, 48005), 'ABC123', font=mock_truetype.return_value)

    @patch('index.Image.open')
    @patch('index.ImageDraw.Draw')
    @patch('index.ImageFont.truetype')
    @patch('index.datetime')
    @patch('index.open', new_callable=mock_open)
    def test_save_image_with_save_snapshots_false(self, mock_file, mock_datetime, mock_truetype, mock_draw, mock_open):
        mock_now = mock_datetime.now.return_value
        mock_now.strftime.return_value = '20210101_120000'

        index.config = {'frigate': {'save_snapshots': False }}
        after_data = {}
        image_content = b'test_image_content'
        license_plate_attribute = []
        plate_number = ''

        # Mock PIL dependencies
        mock_image = MagicMock()
        mock_open.return_value = mock_image
        mock_draw.return_value = MagicMock()
        mock_truetype.return_value = MagicMock()

        index.snapshot_path = 'dummy/snapshot/path'

        # Call the function
        with patch.object(index._LOGGER, 'debug') as mock_debug:
            index.save_image(index.config, after_data, image_content, license_plate_attribute, plate_number)
            mock_debug.assert_called_with(f"Skipping saving snapshot because save_snapshots is set to false")

        # Assert that the image is not saved when save_snapshots is False
        mock_image.save.assert_not_called()

class TestSetSubLabel(BaseTestCase):
    def setUp(self):
      index._LOGGER = MagicMock()

    @patch('index.requests.post') 
    def test_set_sublabel(self, mock_post):
        mock_response = mock_post.return_value
        mock_response.status_code = 200

        index.set_sublabel("http://example.com", "123", "test_label", 0.95)

        mock_post.assert_called_with(
            "http://example.com/api/events/123/sub_label",
            data='{"subLabel": "TEST_LABEL"}',
            headers={"Content-Type": "application/json"}
        )

    @patch('index.requests.post') 
    def test_set_sublabel_shorten(self, mock_post):
        mock_response = mock_post.return_value
        mock_response.status_code = 200

        index.set_sublabel("http://example.com", "123", "test_label_too_long_for_api", 0.95)

        mock_post.assert_called_with(
            "http://example.com/api/events/123/sub_label",
            data='{"subLabel": "TEST_LABEL_TOO_LONG_"}',
            headers={"Content-Type": "application/json"}
        )

class TestRunMqttClient(BaseTestCase):
    @patch('index.mqtt.Client')
    def test_run_mqtt_client(self, mock_mqtt_client):
        # Setup configuration
        index.config = {
            'frigate': {
                'mqtt_server': 'mqtt.example.com',
                'mqtt_auth': True,
                'mqtt_username': 'username',
                'mqtt_password': 'password'
            }
        }

        # Mock _LOGGER to prevent actual logging
        index._LOGGER = MagicMock()

        # Call the function
        index.run_mqtt_client()

        # Assert that the MQTT client is created with the correct client_id format
        mock_mqtt_client.assert_called()
        args, kwargs = mock_mqtt_client.call_args
        self.assertTrue(args[0].startswith('FrigatePlateRecognizer'))

        # Get the mock client instance
        mock_client_instance = mock_mqtt_client.return_value

        # Assert that the on_message, on_disconnect, and on_connect handlers are set
        self.assertEqual(mock_client_instance.on_message, index.on_message)
        self.assertEqual(mock_client_instance.on_disconnect, index.on_disconnect)
        self.assertEqual(mock_client_instance.on_connect, index.on_connect)

        # Assert that username and password are set for the client
        mock_client_instance.username_pw_set.assert_called_with('username', 'password')

        # Assert that the client attempts to connect and enters the loop
        mock_client_instance.connect.assert_called_with('mqtt.example.com')
        mock_client_instance.loop_forever.assert_called()

class TestHasCommonValue(BaseTestCase):
    def test_has_common_value_with_common_elements(self):
        self.assertTrue(index.has_common_value([1, 2, 3], [3, 4, 5]))

    def test_has_common_value_without_common_elements(self):
        self.assertFalse(index.has_common_value([1, 2, 3], [4, 5, 6]))

    def test_has_common_value_with_empty_arrays(self):
        self.assertFalse(index.has_common_value([], []))

    def test_has_common_value_with_one_empty_array(self):
        self.assertFalse(index.has_common_value([1, 2, 3], []))

    def test_has_common_value_with_identical_arrays(self):
        self.assertTrue(index.has_common_value([1, 2, 3], [1, 2, 3]))

class TestGetLicensePlate(BaseTestCase):
    def test_get_license_plate_with_frigate_plus_enabled(self):
        index.config = {'frigate': {'frigate_plus': True}}
        after_data = {
            'current_attributes': [
                {'label': 'license_plate', 'score': 0.9},
                {'label': 'other_attribute', 'score': 0.8}
            ]
        }
        result = index.get_license_plate_attribute(after_data)
        self.assertEqual(result, [{'label': 'license_plate', 'score': 0.9}])

    def test_get_license_plate_with_frigate_plus_disabled(self):
        index.config = {'frigate': {'frigate_plus': False}}
        after_data = {'current_attributes': [{'label': 'license_plate', 'score': 0.9}]}
        result = index.get_license_plate_attribute(after_data)
        self.assertIsNone(result)

    def test_get_license_plate_with_no_license_plate_attribute(self):
        index.config = {'frigate': {'frigate_plus': True}}
        after_data = {'current_attributes': [{'label': 'other_attribute', 'score': 0.8}]}
        result = index.get_license_plate_attribute(after_data)
        self.assertEqual(result, [])

    def test_get_license_plate_with_empty_attributes(self):
        index.config = {'frigate': {'frigate_plus': True}}
        after_data = {'current_attributes': []}
        result = index.get_license_plate_attribute(after_data)
        self.assertEqual(result, [])

class TestCheckFirstMessage(BaseTestCase):

    def setUp(self):
        super().setUp()
        index.first_message = True

    def test_first_message_true(self):
        result = index.check_first_message()
        self.assertTrue(result)
        self.mock_logger.debug.assert_called_with("Skipping first message")

    def test_first_message_false(self):
        index.first_message = False
        result = index.check_first_message()
        self.assertFalse(result)
        self.mock_logger.debug.assert_not_called()

class TestIsDuplicateEvent(BaseTestCase):
    def setUp(self):
        super().setUp()

    @patch('index.sqlite3.connect')
    def test_event_is_duplicate(self, mock_connect):
        # Mocking the database connection and cursor
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        # Setting up the cursor to return a non-empty row, indicating a duplicate event
        mock_cursor.fetchone.return_value = ('some_row_data',)

        frigate_event_id = 'test_event_id'
        result = index.is_duplicate_event(frigate_event_id)

        # Assert the function returns True for a duplicate event
        self.assertTrue(result)
        mock_cursor.execute.assert_called_with(
            "SELECT * FROM plates WHERE frigate_event = ?",
            (frigate_event_id,)
        )
        self.mock_logger.debug.assert_called_with(f"Skipping event: {frigate_event_id} because it has already been processed")

    @patch('index.sqlite3.connect')
    def test_event_is_not_duplicate(self, mock_connect):
        # Mocking the database connection and cursor
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        # Setting up the cursor to return None, indicating the event is not a duplicate
        mock_cursor.fetchone.return_value = None

        frigate_event_id = 'test_event_id'
        result = index.is_duplicate_event(frigate_event_id)

        # Assert the function returns False for a non-duplicate event
        self.assertFalse(result)
        mock_cursor.execute.assert_called_with(
            "SELECT * FROM plates WHERE frigate_event = ?",
            (frigate_event_id,)
        )
        self.mock_logger.debug.assert_not_called()

class TestIsValidLicensePlate(BaseTestCase):
    def setUp(self):
        super().setUp()

    @patch('index.get_license_plate_attribute')
    def test_no_license_plate_attribute(self, mock_get_license_plate):
        # Setup: No license plate attribute found
        mock_get_license_plate.return_value = []
        after_data = {'current_attributes': []}

        # Call the function
        result = index.is_valid_license_plate(after_data)

        # Assertions
        self.assertFalse(result)
        self.mock_logger.debug.assert_called_with("no license_plate attribute found in event attributes")

    @patch('index.get_license_plate_attribute')
    def test_license_plate_below_min_score(self, mock_get_license_plate):
        # Setup: License plate attribute found but below minimum score
        index.config = {'frigate': {'frigate_plus': True, 'license_plate_min_score': 0.5}}
        mock_get_license_plate.return_value = [{'score': 0.4}]
        after_data = {'current_attributes': [{'label': 'license_plate', 'score': 0.4}]}

        # Call the function
        result = index.is_valid_license_plate(after_data)
        self.assertFalse(result)
        self.mock_logger.debug.assert_called_with("license_plate attribute score is below minimum: 0.4")


    @patch('index.get_license_plate_attribute')
    def test_valid_license_plate(self, mock_get_license_plate):
        # Setup: Valid license plate attribute
        index.config = {'frigate': {'license_plate_min_score': 0.5}}
        mock_get_license_plate.return_value = [{'score': 0.6}]
        after_data = {'current_attributes': [{'label': 'license_plate', 'score': 0.6}]}

        result = index.is_valid_license_plate(after_data)
        self.assertTrue(result)

class TestGetSnapshot(BaseTestCase):
    def setUp(self):
        super().setUp()

    @patch('index.requests.get')
    def test_get_snapshot_successful(self, mock_requests_get):
        # Setup mock response for successful request
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'image_data'
        mock_requests_get.return_value = mock_response

        frigate_event_id = 'event123'
        frigate_url = 'http://example.com'

        result = index.get_snapshot(frigate_event_id, frigate_url, True)

        self.assertEqual(result, b'image_data')
        mock_requests_get.assert_called_with(f"{frigate_url}/api/events/{frigate_event_id}/snapshot.jpg",
                                             params={"crop": 1, "quality": 95})
        self.mock_logger.debug.assert_any_call(f"Getting snapshot for event: {frigate_event_id}, Crop: True")
        self.mock_logger.debug.assert_any_call(f"event URL: {frigate_url}/api/events/{frigate_event_id}/snapshot.jpg")

    @patch('index.requests.get')
    def test_get_snapshot_failure(self, mock_requests_get):
        # Setup mock response for unsuccessful request
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_requests_get.return_value = mock_response

        frigate_event_id = 'event123'
        frigate_url = 'http://example.com'

        result = index.get_snapshot(frigate_event_id, frigate_url, True)

        self.assertIsNone(result)
        mock_requests_get.assert_called_with(f"{frigate_url}/api/events/{frigate_event_id}/snapshot.jpg",
                                             params={"crop": 1, "quality": 95})
        self.mock_logger.error.assert_called_with(f"Error getting snapshot: 404")

class TestCheckInvalidEvent(BaseTestCase):
    def setUp(self):
        super().setUp()
        index.config = {
            'frigate': {
                'zones': ['zone1', 'zone2'],
                'camera': ['camera1', 'camera2'],
                'objects': ['car', 'bus']
            }
        }
    

    def test_event_invalid_zone_and_camera(self):
        before_data = {}
        after_data = {
            'current_zones': ['zone3'],
            'camera': 'camera3',
            'label': 'car',
            'id': 'event123',
            'top_score': 0.8
        }
        result = index.check_invalid_event(before_data, after_data)
        self.assertTrue(result)
        self.mock_logger.debug.assert_called_with("Skipping event: event123 because it does not match the configured zones/cameras")

    def test_event_invalid_object(self):
        before_data = {}
        after_data = {
            'current_zones': ['zone1'],
            'camera': 'camera1',
            'label': 'tree',
            'id': 'event123',
            'top_score': 0.8
        }
        result = index.check_invalid_event(before_data, after_data)
        self.assertTrue(result)
        self.mock_logger.debug.assert_called_with("is not a correct label: tree")

    def test_event_valid(self):
        before_data = {'top_score': 0.7}
        after_data = {
            'current_zones': ['zone1'],
            'camera': 'camera1',
            'label': 'car',
            'id': 'event123',
            'top_score': 0.8
        }
        result = index.check_invalid_event(before_data, after_data)
        self.assertFalse(result)
        self.mock_logger.debug.assert_not_called()

class TestGetPlate(BaseTestCase):
    def setUp(self):
        super().setUp()
        
    @patch('index.plate_recognizer')
    @patch('index.save_image')
    def test_plate_score_okay(self, mock_save_image, mock_plate_recognizer):
        # Set up configuration to use plate_recognizer
        index.config = {'plate_recognizer': True, 'frigate': {'min_score': 0.5, 'always_save_snapshot': False}}
        snapshot = b'image_data'

        # Mock the plate_recognizer to return a specific plate number and score
        mock_plate_recognizer.return_value = ('ABC123', 0.6, None, None)
        plate_number, plate_score, watched_plate, fuzzy_score = index.get_plate(snapshot)

        # Assert that the correct plate number is returned
        self.assertEqual(plate_number, 'ABC123')
        self.assertEqual(plate_score, 0.6)
        mock_plate_recognizer.assert_called_once_with(snapshot)
        mock_save_image.assert_not_called()  # Assert that save_image is not called when plate_recognizer is used

    @patch('index.plate_recognizer')
    @patch('index.save_image')
    def test_plate_score_too_low(self, mock_save_image, mock_plate_recognizer):
        index.config = {'plate_recognizer': True, 'frigate': {'min_score': 0.7, 'always_save_snapshot': False}}
        snapshot = b'image_data'

        # Mock the plate_recognizer to return a plate number with a low score
        mock_plate_recognizer.return_value = ('ABC123', 0.6, None, None)
        plate_number, plate_score, watched_plate, fuzzy_score = index.get_plate(snapshot)

        # Assert that no plate number is returned due to low score
        self.assertIsNone(plate_number)
        self.assertIsNone(plate_score)
        self.mock_logger.info.assert_called_with("Score is below minimum: 0.6 (ABC123)")
        mock_save_image.assert_not_called()

    @patch('index.plate_recognizer')
    @patch('index.save_image')
    def test_fuzzy_response(self, mock_save_image, mock_plate_recognizer):
        index.config = {'plate_recognizer': True, 'frigate': {'min_score': 0.7, 'always_save_snapshot': False}}
        snapshot = b'image_data'

        # Mock the plate_recognizer to return a plate number with a fuzzy score
        mock_plate_recognizer.return_value = ('DEF456', 0.8, None, 0.9)
        plate_number, plate_score, watched_plate, fuzzy_score = index.get_plate(snapshot)

        # Assert that plate number and score are returned despite the fuzzy score
        self.assertEqual(plate_number, 'DEF456')
        self.assertEqual(plate_score, 0.8)
        self.assertIsNone(watched_plate)
        self.assertEqual(fuzzy_score, 0.9)
        self.mock_logger.error.assert_not_called()
        mock_save_image.assert_not_called()

class TestSendMqttMessage(BaseTestCase):
    def setUp(self):
        super().setUp()

    @patch('index.mqtt_client')
    def test_send_mqtt_message(self, mock_mqtt_client):
        index.config = {
            'frigate': {
                'main_topic': 'frigate',
                'return_topic': 'return_topic'
            }
        }

        plate_number = 'ABC123'
        plate_score = 0.95
        frigate_event_id = 'event123'
        after_data = {'camera': 'camera1'}
        formatted_start_time = '2021-01-01 12:00:00'
        watched_plate = 'ABC123'
        fuzzy_score = 0.8

        # Call the function
        index.send_mqtt_message(plate_number, plate_score, frigate_event_id, after_data, formatted_start_time, watched_plate, fuzzy_score)

        # Construct expected message
        expected_message = {
            'plate_number': plate_number,
            'score': plate_score,
            'frigate_event_id': frigate_event_id,
            'camera_name': after_data['camera'],
            'start_time': formatted_start_time,
            'fuzzy_score': fuzzy_score,
            'original_plate': watched_plate
        }

        # Assert that the MQTT client publish method is called correctly
        mock_mqtt_client.publish.assert_called_with(
            'frigate/return_topic', json.dumps(expected_message)
        )

class TestStorePlateInDb(BaseTestCase):
    def setUp(self):
        super().setUp()

    @patch('index.sqlite3.connect')
    def test_store_plate_in_db(self, mock_connect):
        # Mocking the database connection and cursor
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        plate_number = 'ABC123'
        plate_score = 0.95
        frigate_event_id = 'event123'
        after_data = {'camera': 'camera1'}
        formatted_start_time = '2021-01-01 12:00:00'

        index.store_plate_in_db(plate_number, plate_score, frigate_event_id, after_data, formatted_start_time)

        # Assert that the correct SQL command is executed
        mock_cursor.execute.assert_called_with(
            """INSERT INTO plates (detection_time, score, plate_number, frigate_event, camera_name) VALUES (?, ?, ?, ?, ?)""",
            (formatted_start_time, plate_score, plate_number, frigate_event_id, after_data['camera'])
        )

        # Assert database commit and connection close
        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()

if __name__ == '__main__':
    unittest.main()
