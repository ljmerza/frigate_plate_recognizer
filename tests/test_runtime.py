
import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import frigate_plate_recognizer.app as index
from frigate_plate_recognizer.config import (
    AppConfig,
    FrigateConfig,
    PathsConfig,
    PlateRecognizerConfig,
)


class BaseTestCase(unittest.TestCase):
    def setUp(self):
        mock_logger = MagicMock()
        index._LOGGER = mock_logger
        self.mock_logger = mock_logger
        self._orig_snapshot_path = index.SNAPSHOT_PATH
        self._orig_db_path = index.DB_PATH
        self._orig_log_file = index.LOG_FILE
        self._orig_app_config = index.APP_CONFIG
        self._orig_config = index.config

        index.config = {'frigate': {}}
        index.APP_CONFIG = MagicMock()

    def tearDown(self):
        index.SNAPSHOT_PATH = self._orig_snapshot_path
        index.DB_PATH = self._orig_db_path
        index.LOG_FILE = self._orig_log_file
        index.APP_CONFIG = self._orig_app_config
        index.config = self._orig_config

class TestLoadConfig(BaseTestCase):
    @patch('frigate_plate_recognizer.app.load_app_config')
    def test_load_config(self, mock_load_app_config):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_path = Path(tmpdir)
            paths = PathsConfig(
                config_path=base_path / 'config.yml',
                db_path=base_path / 'data' / 'frigate.db',
                log_file=base_path / 'logs' / 'app.log',
                snapshot_dir=base_path / 'plates',
            )

            frigate_cfg = FrigateConfig(
                frigate_url='http://localhost:5000',
                mqtt_server='mqtt.local',
            )
            plate_cfg = PlateRecognizerConfig(token='token', regions=['us-ca'])

            app_cfg = AppConfig(
                paths=paths,
                frigate=frigate_cfg,
                plate_recognizer=plate_cfg,
                logger_level='DEBUG',
            )

            mock_load_app_config.return_value = app_cfg

            index.load_config()

            self.assertEqual(index.config, app_cfg.runtime_dict())
            self.assertEqual(index.DB_PATH, str(paths.db_path))
            self.assertEqual(index.LOG_FILE, str(paths.log_file))
            self.assertEqual(index.SNAPSHOT_PATH, str(paths.snapshot_dir))
            self.assertTrue(paths.snapshot_dir.exists())
            self.assertTrue(paths.db_path.parent.exists())
            self.assertTrue(paths.log_file.parent.exists())

class TestSaveImage(BaseTestCase):
    def setUp(self):
        super().setUp()
        index._LOGGER = logging.getLogger(__name__)

    @patch('frigate_plate_recognizer.app.save_snapshot_image')
    def test_save_image_with_box(self, mock_save_snapshot_image):
        index.config = {'frigate': {'save_snapshots': True, 'draw_box': True}}
        after_data = {'camera': 'test_camera'}
        frigate_url = 'http://example.com'
        frigate_event_id = 'test_event_id'
        plate_number = 'ABC123'

        index.save_image(index.config, after_data, frigate_url, frigate_event_id, plate_number)

        mock_save_snapshot_image.assert_called_once()
        kwargs = mock_save_snapshot_image.call_args.kwargs
        self.assertEqual(kwargs['config'], index.config)
        self.assertEqual(kwargs['after_data'], after_data)
        self.assertEqual(kwargs['frigate_url'], frigate_url)
        self.assertEqual(kwargs['frigate_event_id'], frigate_event_id)
        self.assertEqual(kwargs['plate_number'], plate_number)
        self.assertEqual(kwargs['snapshot_path'], index.SNAPSHOT_PATH)

    @patch('frigate_plate_recognizer.app.save_snapshot_image')
    def test_save_image_with_save_snapshots_false(self, mock_save_snapshot_image):
        index.config = {'frigate': {'save_snapshots': False}}
        after_data = {}

        with patch.object(index._LOGGER, 'debug') as mock_debug:
            index.save_image(index.config, after_data, 'url', 'event', 'plate')
            mock_debug.assert_called_with("Skipping saving snapshot because save_snapshots is set to false")

        mock_save_snapshot_image.assert_not_called()

class TestSetSubLabel(BaseTestCase):
    @patch('frigate_plate_recognizer.app.get_frigate_session')
    def test_set_sublabel(self, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        mock_response = mock_session.post.return_value
        mock_response.status_code = 200

        index.set_sublabel("http://example.com", "123", "test_label", 0.95)

        mock_session.post.assert_called_with(
            "http://example.com/api/events/123/sub_label",
            data='{"subLabel": "TEST_LABEL"}',
            headers={"Content-Type": "application/json"}
        )

    @patch('frigate_plate_recognizer.app.get_frigate_session')
    def test_set_sublabel_shorten(self, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        mock_response = mock_session.post.return_value
        mock_response.status_code = 200

        index.set_sublabel("http://example.com", "123", "test_label_too_long_for_api", 0.95)

        mock_session.post.assert_called_with(
            "http://example.com/api/events/123/sub_label",
            data='{"subLabel": "TEST_LABEL_TOO_LONG_"}',
            headers={"Content-Type": "application/json"}
        )

class TestRunMqttClient(BaseTestCase):
    @patch('frigate_plate_recognizer.app.create_mqtt_client')
    def test_run_mqtt_client(self, mock_create_client):
        # Setup configuration
        index.config = {
            'frigate': {
                'mqtt_server': 'mqtt.example.com',
                'mqtt_auth': True,
                'mqtt_username': 'username',
                'mqtt_password': 'password'
            }
        }

        mock_client = MagicMock()
        mock_create_client.return_value = mock_client

        index._LOGGER = MagicMock()

        index.run_mqtt_client()

        mock_create_client.assert_called_once_with(
            config=index.config,
            logger=index._LOGGER,
            message_callback=index.on_message,
        )
        mock_client.connect.assert_called_with('mqtt.example.com', 1883)
        mock_client.loop_forever.assert_called()

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

    @patch('frigate_plate_recognizer.app.has_processed_event')
    def test_event_is_duplicate(self, mock_has_processed):
        mock_has_processed.return_value = True

        frigate_event_id = 'test_event_id'
        result = index.is_duplicate_event(frigate_event_id)

        self.assertTrue(result)
        mock_has_processed.assert_called_once_with(
            index.DB_PATH,
            frigate_event_id,
            timeout_seconds=index.DB_TIMEOUT_SECONDS,
            busy_timeout_ms=index.DB_BUSY_TIMEOUT_MS,
            logger=index._LOGGER,
        )

    @patch('frigate_plate_recognizer.app.has_processed_event')
    def test_event_is_not_duplicate(self, mock_has_processed):
        mock_has_processed.return_value = False

        frigate_event_id = 'test_event_id'
        result = index.is_duplicate_event(frigate_event_id)

        self.assertFalse(result)
        mock_has_processed.assert_called_once()

class TestIsValidLicensePlate(BaseTestCase):
    def setUp(self):
        super().setUp()

    @patch('frigate_plate_recognizer.app.get_license_plate_attribute')
    def test_no_license_plate_attribute(self, mock_get_license_plate):
        # Setup: No license plate attribute found
        mock_get_license_plate.return_value = []
        after_data = {'current_attributes': []}

        # Call the function
        result = index.is_valid_license_plate(after_data)

        # Assertions
        self.assertFalse(result)
        self.mock_logger.debug.assert_called_with("no license_plate attribute found in event attributes")

    @patch('frigate_plate_recognizer.app.get_license_plate_attribute')
    def test_license_plate_below_min_score(self, mock_get_license_plate):
        # Setup: License plate attribute found but below minimum score
        index.config = {'frigate': {'frigate_plus': True, 'license_plate_min_score': 0.5}}
        mock_get_license_plate.return_value = [{'score': 0.4}]
        after_data = {'current_attributes': [{'label': 'license_plate', 'score': 0.4}]}

        # Call the function
        result = index.is_valid_license_plate(after_data)
        self.assertFalse(result)
        self.mock_logger.debug.assert_called_with("license_plate attribute score is below minimum: %s", 0.4)


    @patch('frigate_plate_recognizer.app.get_license_plate_attribute')
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

    @patch('frigate_plate_recognizer.app.get_frigate_session')
    def test_get_snapshot_successful(self, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'image_data'
        mock_session.get.return_value = mock_response

        frigate_event_id = 'event123'
        frigate_url = 'http://example.com'

        result = index.get_snapshot(frigate_event_id, frigate_url, True)

        self.assertEqual(result, b'image_data')
        mock_session.get.assert_called_with(
            f"{frigate_url}/api/events/{frigate_event_id}/snapshot.jpg",
            params={"crop": 1, "quality": 95}
        )
        self.mock_logger.debug.assert_any_call(
            'Getting snapshot for event: %s, Crop: %s',
            frigate_event_id,
            True,
        )
        self.mock_logger.debug.assert_any_call(
            'event URL: %s',
            f"{frigate_url}/api/events/{frigate_event_id}/snapshot.jpg",
        )

    @patch('frigate_plate_recognizer.app.get_frigate_session')
    def test_get_snapshot_failure(self, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_session.get.return_value = mock_response

        frigate_event_id = 'event123'
        frigate_url = 'http://example.com'

        result = index.get_snapshot(frigate_event_id, frigate_url, True)

        self.assertIsNone(result)
        mock_session.get.assert_called_with(
            f"{frigate_url}/api/events/{frigate_event_id}/snapshot.jpg",
            params={"crop": 1, "quality": 95}
        )
        self.mock_logger.error.assert_called_with("Error getting snapshot: %s", 404)

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
        self.mock_logger.debug.assert_called_with(
            "Skipping event: %s because it does not match the configured zones/cameras",
            'event123',
        )

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
        self.mock_logger.debug.assert_called_with("is not a correct label: %s", 'tree')

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
        
    @patch('frigate_plate_recognizer.app.pipeline_get_plate')
    def test_plate_score_okay(self, mock_pipeline):
        index.config = {'plate_recognizer': True, 'frigate': {'min_score': 0.5, 'always_save_snapshot': False}}
        snapshot = b'image_data'

        mock_pipeline.return_value = ('ABC123', 0.6, None, None)
        plate_number, plate_score, watched_plate, fuzzy_score = index.get_plate(snapshot)

        self.assertEqual(plate_number, 'ABC123')
        self.assertEqual(plate_score, 0.6)
        mock_pipeline.assert_called_once()

    @patch('frigate_plate_recognizer.app.pipeline_get_plate')
    def test_plate_score_too_low(self, mock_pipeline):
        index.config = {'plate_recognizer': True, 'frigate': {'min_score': 0.7, 'always_save_snapshot': False}}
        snapshot = b'image_data'

        mock_pipeline.return_value = (None, None, None, None)
        plate_number, plate_score, watched_plate, fuzzy_score = index.get_plate(snapshot)

        self.assertIsNone(plate_number)
        self.assertIsNone(plate_score)
        mock_pipeline.assert_called_once()

    @patch('frigate_plate_recognizer.app.pipeline_get_plate')
    def test_fuzzy_response(self, mock_pipeline):
        index.config = {'plate_recognizer': True, 'frigate': {'min_score': 0.7, 'always_save_snapshot': False}}
        snapshot = b'image_data'

        mock_pipeline.return_value = ('DEF456', 0.8, None, 0.9)
        plate_number, plate_score, watched_plate, fuzzy_score = index.get_plate(snapshot)

        self.assertEqual(plate_number, 'DEF456')
        self.assertEqual(plate_score, 0.8)
        self.assertIsNone(watched_plate)
        self.assertEqual(fuzzy_score, 0.9)

class TestSendMqttMessage(BaseTestCase):
    def setUp(self):
        super().setUp()

    @patch('frigate_plate_recognizer.app.mqtt_client')
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
            'original_plate': watched_plate,
            'is_watched_plate': True,
        }

        # Assert that the MQTT client publish method is called correctly
        mock_mqtt_client.publish.assert_called_with(
            'frigate/return_topic', json.dumps(expected_message)
        )

class TestStorePlateInDb(BaseTestCase):
    def setUp(self):
        super().setUp()

    @patch('frigate_plate_recognizer.app.insert_plate')
    def test_store_plate_in_db(self, mock_insert):
        mock_insert.return_value = True

        plate_number = 'ABC123'
        plate_score = 0.95
        frigate_event_id = 'event123'
        after_data = {'camera': 'camera1'}
        formatted_start_time = '2021-01-01 12:00:00'

        result = index.store_plate_in_db(plate_number, plate_score, frigate_event_id, after_data, formatted_start_time)

        self.assertTrue(result)
        mock_insert.assert_called_once()

if __name__ == '__main__':
    unittest.main()
