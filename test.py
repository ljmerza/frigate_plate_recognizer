
import json
import logging
from pathlib import Path
import os
import yaml
import logging
import unittest
from unittest.mock import patch, MagicMock, mock_open

import index

class TestLoadConfig(unittest.TestCase):
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

class TestSaveImage(unittest.TestCase):
    def setUp(self):
      index._LOGGER = logging.getLogger(__name__)

    @patch('index.Image.open')
    @patch('index.ImageDraw.Draw')
    @patch('index.ImageFont.truetype')
    @patch('index.datetime')
    @patch('index.open', new_callable=mock_open)
    def test_save_image(self, mock_file, mock_datetime, mock_truetype, mock_draw, mock_open):
        # Mock current time
        mock_now = mock_datetime.now.return_value
        mock_now.strftime.return_value = '20210101_120000'

        # Setup configuration and input data
        index.config = {'frigate': {'save_snapshots': True, 'draw_box': True}}
        after_data = {'camera': 'test_camera'}
        image_content = b'test_image_content'
        license_plate_attribute = [{'box': [0, 0, 100, 100]}]
        plate_number = 'ABC123'

        # Mock PIL dependencies
        mock_image = MagicMock()
        mock_open.return_value = mock_image
        mock_draw.return_value = MagicMock()
        mock_truetype.return_value = MagicMock()

        # Call the function
        index.save_image(index.config, after_data, image_content, license_plate_attribute, plate_number)

        # Assert image operations
        mock_image.save.assert_called_with(f'./plates/{plate_number}_test_camera_20210101_120000.png')
        mock_draw.return_value.rectangle.assert_called_with((0, 0, 100, 100), outline='red', width=2)
        mock_draw.return_value.text.assert_called_with((5, 105), 'ABC123', font=mock_truetype.return_value)

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

class TestSetSubLabel(unittest.TestCase):
    def setUp(self):
      index._LOGGER = MagicMock()

    @patch('index.requests.post') 
    def test_set_sublabel(self, mock_post):
        mock_response = mock_post.return_value
        mock_response.status_code = 200

        index.set_sublabel("http://example.com", "123", "test_label", 0.95)

        mock_post.assert_called_with(
            "http://example.com/api/events/123/sub_label",
            data='{"subLabel": "test_label"}',
            headers={"Content-Type": "application/json"}
        )

    @patch('index.requests.post') 
    def test_set_sublabel_shorten(self, mock_post):
        mock_response = mock_post.return_value
        mock_response.status_code = 200

        index.set_sublabel("http://example.com", "123", "test_label_too_long_for_api", 0.95)

        mock_post.assert_called_with(
            "http://example.com/api/events/123/sub_label",
            data='{"subLabel": "test_label_too_long_"}',
            headers={"Content-Type": "application/json"}
        )

class TestRunMqttClient(unittest.TestCase):
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

class TestSendMqttMessage(unittest.TestCase):
    def setUp(self):
      index.mqtt_client = MagicMock()

      index.config = {
        'logger_level': 'DEBUG',
        'frigate': {
          'main_topic': 'frigate',
          'return_topic': 'return_topic'
        }
      }

      index._LOGGER = logging.getLogger(__name__)
      
    def test_send_mqtt_message(self):
      test_message = {"test": "message"}
      with patch.object(index._LOGGER, 'debug') as mock_debug:
        index.send_mqtt_message(test_message)

        main_topic = index.config['frigate']['main_topic']
        return_topic = index.config['frigate']['return_topic']
        expected_topic = f'{main_topic}/{return_topic}'

        index.mqtt_client.publish.assert_called_with(
            expected_topic, json.dumps(test_message)
        )

        # Check if the logger was called correctly
        mock_debug.assert_called_with(f"Sending MQTT message: {test_message}")

class TestHasCommonValue(unittest.TestCase):
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

class TestGetLicensePlate(unittest.TestCase):
    def test_get_license_plate_with_frigate_plus_enabled(self):
        index.config = {'frigate': {'frigate_plus': True}}
        after_data = {
            'current_attributes': [
                {'label': 'license_plate', 'score': 0.9},
                {'label': 'other_attribute', 'score': 0.8}
            ]
        }
        result = index.get_license_plate(after_data)
        self.assertEqual(result, [{'label': 'license_plate', 'score': 0.9}])

    def test_get_license_plate_with_frigate_plus_disabled(self):
        index.config = {'frigate': {'frigate_plus': False}}
        after_data = {'current_attributes': [{'label': 'license_plate', 'score': 0.9}]}
        result = index.get_license_plate(after_data)
        self.assertIsNone(result)

    def test_get_license_plate_with_no_license_plate_attribute(self):
        index.config = {'frigate': {'frigate_plus': True}}
        after_data = {'current_attributes': [{'label': 'other_attribute', 'score': 0.8}]}
        result = index.get_license_plate(after_data)
        self.assertEqual(result, [])

    def test_get_license_plate_with_empty_attributes(self):
        index.config = {'frigate': {'frigate_plus': True}}
        after_data = {'current_attributes': []}
        result = index.get_license_plate(after_data)
        self.assertEqual(result, [])

class TestCheckFirstMessage(unittest.TestCase):

    def setUp(self):
        index.first_message = True

    @patch('index._LOGGER')
    def test_first_message_true(self, mock_logger):
        result = index.check_first_message()
        self.assertTrue(result)
        mock_logger.debug.assert_called_with("Skipping first message")

    @patch('index._LOGGER')
    def test_first_message_false(self, mock_logger):
        index.first_message = False
        result = index.check_first_message()
        self.assertFalse(result)
        mock_logger.debug.assert_not_called()

class TestIsDuplicateEvent(unittest.TestCase):

    @patch('index.sqlite3.connect')
    @patch('index._LOGGER')
    def test_event_is_duplicate(self, mock_logger, mock_connect):
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
        mock_logger.debug.assert_called_with(f"Skipping event: {frigate_event_id} because it has already been processed")

    @patch('index.sqlite3.connect')
    @patch('index._LOGGER')
    def test_event_is_not_duplicate(self, mock_logger, mock_connect):
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
        mock_logger.debug.assert_not_called()

class TestIsValidLicensePlate(unittest.TestCase):

    @patch('index.get_license_plate')
    @patch('index._LOGGER')
    def test_no_license_plate_attribute(self, mock_logger, mock_get_license_plate):
        # Setup: No license plate attribute found
        mock_get_license_plate.return_value = []
        after_data = {'current_attributes': []}

        # Call the function
        result = index.is_valid_license_plate(after_data)

        # Assertions
        self.assertFalse(result)
        mock_logger.debug.assert_called_with("no license_plate attribute found in event attributes")

    @patch('index.get_license_plate')
    @patch('index._LOGGER')
    def test_license_plate_below_min_score(self, mock_logger, mock_get_license_plate):
        # Setup: License plate attribute found but below minimum score
        index.config = {'frigate': {'frigate_plus': True, 'license_plate_min_score': 0.5}}
        mock_get_license_plate.return_value = [{'score': 0.4}]
        after_data = {'current_attributes': [{'label': 'license_plate', 'score': 0.4}]}

        result = index.is_valid_license_plate(after_data)
        self.assertFalse(result)

    @patch('index.get_license_plate')
    @patch('index._LOGGER')
    def test_valid_license_plate(self, mock_logger, mock_get_license_plate):
        # Setup: Valid license plate attribute
        index.config = {'frigate': {'license_plate_min_score': 0.5}}
        mock_get_license_plate.return_value = [{'score': 0.6}]
        after_data = {'current_attributes': [{'label': 'license_plate', 'score': 0.6}]}

        result = index.is_valid_license_plate(after_data)
        self.assertTrue(result)

if __name__ == '__main__':
    unittest.main()
