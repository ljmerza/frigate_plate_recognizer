
import json
import logging
from pathlib import Path
import os
import yaml
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
        index.SNAPSHOT_PATH = 'dummy_snapshot_path'
        index.load_config()
        mock_open.assert_called_once_with('dummy_path', 'r')
        mock_safe_load.assert_called_once()
        mock_isdir.assert_called_once_with('dummy_snapshot_path')
        mock_makedirs.assert_called_once_with('dummy_snapshot_path')
        
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
        result = index.get_license_plate(index.config, after_data)
        self.assertEqual(result, [{'label': 'license_plate', 'score': 0.9}])

    def test_get_license_plate_with_frigate_plus_disabled(self):
        index.config = {'frigate': {'frigate_plus': False}}
        after_data = {'current_attributes': [{'label': 'license_plate', 'score': 0.9}]}
        result = index.get_license_plate(index.config, after_data)
        self.assertIsNone(result)

    def test_get_license_plate_with_no_license_plate_attribute(self):
        index.config = {'frigate': {'frigate_plus': True}}
        after_data = {'current_attributes': [{'label': 'other_attribute', 'score': 0.8}]}
        result = index.get_license_plate(index.config, after_data)
        self.assertEqual(result, [])

    def test_get_license_plate_with_empty_attributes(self):
        index.config = {'frigate': {'frigate_plus': True}}
        after_data = {'current_attributes': []}
        result = index.get_license_plate(index.config, after_data)
        self.assertEqual(result, [])

   
if __name__ == '__main__':
    unittest.main()
