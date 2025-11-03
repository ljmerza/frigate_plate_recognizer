from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

from frigate_plate_recognizer.config import load_app_config


def _write_config(base_dir: Path, data: Dict[str, Any]) -> Path:
    config_path = base_dir / "config.yml"
    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle)
    return config_path


def _base_config() -> Dict[str, Any]:
    return {
        "frigate": {
            "frigate_url": "http://127.0.0.1:5000",
            "mqtt_server": "mqtt.local",
        },
        "plate_recognizer": {
            "token": "token",
            "regions": ["us-ca"],
        },
    }


def test_env_overrides(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _base_config())

    env = {
        "FRP_CONFIG_PATH": str(config_path),
        "FRP_MQTT_SERVER": "override.local",
        "FRP_PLATE_RECOGNIZER_RETRIES": "5",
    }

    app_config = load_app_config(config_path=config_path, env=env)

    assert app_config.frigate.mqtt_server == "override.local"
    assert app_config.plate_recognizer is not None
    assert app_config.plate_recognizer.max_retries == 5


def test_missing_recognizer_raises(tmp_path: Path) -> None:
    data = {
        "frigate": {
            "frigate_url": "http://127.0.0.1:5000",
            "mqtt_server": "mqtt.local",
        }
    }
    config_path = _write_config(tmp_path, data)

    with pytest.raises(ValueError):
        load_app_config(config_path=config_path, env={})


def test_missing_file(tmp_path: Path) -> None:
    missing_path = tmp_path / "no-config.yml"
    with pytest.raises(FileNotFoundError):
        load_app_config(config_path=missing_path, env={})
