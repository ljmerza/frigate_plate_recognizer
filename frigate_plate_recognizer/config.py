"""Configuration loading and validation for Frigate Plate Recognizer."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Optional, Sequence

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

DEFAULT_CONFIG_PATH = Path("/config/config.yml")
DEFAULT_DB_PATH = Path("/config/frigate_plate_recognizer.db")
DEFAULT_LOG_FILE = Path("/config/frigate_plate_recognizer.log")
DEFAULT_SNAPSHOT_DIR = Path("/plates")
DEFAULT_METRICS_PORT = 8080
DEFAULT_MAX_WORKERS = 10

ENV_BOOL_VALUES = {"1", "true", "t", "yes", "y", "on"}

ENV_FIELD_MAP: Dict[str, Sequence[str]] = {
    "FRP_FRIGATE_URL": ("frigate", "frigate_url"),
    "FRP_MQTT_SERVER": ("frigate", "mqtt_server"),
    "FRP_MQTT_PORT": ("frigate", "mqtt_port"),
    "FRP_MQTT_USERNAME": ("frigate", "mqtt_username"),
    "FRP_MQTT_PASSWORD": ("frigate", "mqtt_password"),
    "FRP_MAIN_TOPIC": ("frigate", "main_topic"),
    "FRP_RETURN_TOPIC": ("frigate", "return_topic"),
    "FRP_FRIGATE_PLUS": ("frigate", "frigate_plus"),
    "FRP_MIN_SCORE": ("frigate", "min_score"),
    "FRP_LICENSE_PLATE_MIN_SCORE": ("frigate", "license_plate_min_score"),
    "FRP_FUZZY_MATCH": ("frigate", "fuzzy_match"),
    "FRP_MAX_ATTEMPTS": ("frigate", "max_attempts"),
    "FRP_LOG_LEVEL": ("logger_level",),
    "FRP_METRICS_PORT": ("metrics_port",),
    "FRP_MAX_WORKERS": ("max_workers",),
    "FRP_PLATE_RECOGNIZER_TOKEN": ("plate_recognizer", "token"),
    "FRP_PLATE_RECOGNIZER_API_URL": ("plate_recognizer", "api_url"),
    "FRP_CODE_PROJECT_API_URL": ("code_project", "api_url"),
}

LIST_FIELDS = {
    ("frigate", "camera"),
    ("frigate", "zones"),
    ("frigate", "objects"),
    ("frigate", "watched_plates"),
    ("plate_recognizer", "regions"),
}

PATH_ENV_VARS = {
    "FRP_CONFIG_PATH": "config_path",
    "FRP_DB_PATH": "db_path",
    "FRP_LOG_FILE": "log_file",
    "FRP_SNAPSHOT_DIR": "snapshot_dir",
}


class FrigateConfig(BaseModel):
    """Configuration block for Frigate integration."""

    frigate_url: str
    mqtt_server: str
    mqtt_port: int = Field(default=1883, ge=1, le=65535)
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None
    mqtt_auth: bool = False
    main_topic: str = "frigate"
    return_topic: Optional[str] = "plate_recognizer"
    frigate_plus: bool = False
    license_plate_min_score: float = Field(default=0.0, ge=0.0, le=1.0)
    camera: list[str] = Field(default_factory=list)
    zones: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    min_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    save_snapshots: bool = False
    draw_box: bool = False
    always_save_snapshot: bool = False
    watched_plates: list[str] = Field(default_factory=list)
    fuzzy_match: float = Field(default=0.0, ge=0.0, le=1.0)
    max_attempts: int = Field(default=0, ge=0)

    @field_validator("camera", "zones", "objects", "watched_plates", mode="before")
    @classmethod
    def _ensure_list(cls, value: Any) -> list[str]:
        if value in (None, "", []):
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return value
        raise TypeError(f"Expected list or comma separated string, got {type(value)!r}")


class PlateRecognizerConfig(BaseModel):
    """Configuration for Plate Recognizer API."""

    token: str
    regions: list[str]
    api_url: Optional[str] = None

    @field_validator("regions", mode="before")
    @classmethod
    def _ensure_regions(cls, value: Any) -> list[str]:
        if value in (None, "", []):
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return value
        raise TypeError("regions must be a list or comma separated string")


class CodeProjectConfig(BaseModel):
    """Configuration for CodeProject.AI API."""

    api_url: str


class PathsConfig(BaseModel):
    """Paths used by the application."""

    config_path: Path = DEFAULT_CONFIG_PATH
    db_path: Path = DEFAULT_DB_PATH
    log_file: Path = DEFAULT_LOG_FILE
    snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR


class AppConfig(BaseModel):
    """Top-level configuration model."""

    paths: PathsConfig
    frigate: FrigateConfig
    plate_recognizer: Optional[PlateRecognizerConfig] = None
    code_project: Optional[CodeProjectConfig] = None
    logger_level: str = "INFO"
    metrics_port: int = Field(default=DEFAULT_METRICS_PORT, ge=1, le=65535)
    max_workers: int = Field(default=DEFAULT_MAX_WORKERS, ge=1, le=64)

    @model_validator(mode="after")
    def validate_recognizer(cls, values: "AppConfig") -> "AppConfig":
        if not (values.plate_recognizer or values.code_project):
            raise ValueError("Configure either plate_recognizer or code_project")
        return values

    @property
    def uses_plate_recognizer(self) -> bool:
        return self.plate_recognizer is not None

    @property
    def uses_code_project(self) -> bool:
        return self.code_project is not None

    def runtime_dict(self) -> Dict[str, Any]:
        """Return a dict that mirrors the legacy runtime config structure."""
        data = {
            "frigate": self.frigate.model_dump(),
            "logger_level": self.logger_level,
            "metrics_port": self.metrics_port,
            "max_workers": self.max_workers,
        }
        if self.plate_recognizer:
            data["plate_recognizer"] = self.plate_recognizer.model_dump()
        if self.code_project:
            data["code_project"] = self.code_project.model_dump()
        return data


def _deep_set(mutable: MutableMapping[str, Any], keys: Sequence[str], value: Any) -> None:
    current: MutableMapping[str, Any] = mutable
    for key in keys[:-1]:
        if key not in current or not isinstance(current[key], MutableMapping):
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def _apply_env_overrides(config_data: Dict[str, Any], env: Mapping[str, str]) -> None:
    for env_key, path in ENV_FIELD_MAP.items():
        if env_key not in env:
            continue
        raw_value = env[env_key]
        if raw_value == "":
            continue
        target_value: Any = raw_value
        if tuple(path) in LIST_FIELDS:
            target_value = [item.strip() for item in raw_value.split(",") if item.strip()]
        elif raw_value.lower() in ENV_BOOL_VALUES:
            target_value = True
        elif raw_value.lower() in {"0", "false", "f", "no", "n", "off"}:
            target_value = False
        _deep_set(config_data, path, target_value)


def _resolve_paths(env: Mapping[str, str], config_path: Optional[Path]) -> PathsConfig:
    resolved: Dict[str, Path] = {
        "config_path": config_path or DEFAULT_CONFIG_PATH,
        "db_path": DEFAULT_DB_PATH,
        "log_file": DEFAULT_LOG_FILE,
        "snapshot_dir": DEFAULT_SNAPSHOT_DIR,
    }

    for env_key, attribute in PATH_ENV_VARS.items():
        raw_value = env.get(env_key)
        if not raw_value:
            continue
        resolved[attribute] = Path(raw_value)

    return PathsConfig(**resolved)


def load_app_config(
    config_path: Optional[Path] = None,
    *,
    env: Mapping[str, str] | None = None,
) -> AppConfig:
    """Load, validate, and return the application configuration."""

    env_mapping: Mapping[str, str] = env or os.environ
    resolved_config_path = Path(env_mapping.get("FRP_CONFIG_PATH", config_path or DEFAULT_CONFIG_PATH))

    if not resolved_config_path.exists():
        raise FileNotFoundError(
            f"Configuration file not found at {resolved_config_path}. "
            "Set FRP_CONFIG_PATH or create the file."
        )

    with resolved_config_path.open("r", encoding="utf-8") as config_file:
        raw_data = yaml.safe_load(config_file) or {}

    _apply_env_overrides(raw_data, env_mapping)

    paths = _resolve_paths(env_mapping, resolved_config_path)
    raw_data.setdefault("paths", paths.model_dump())

    try:
        return AppConfig.model_validate(raw_data)
    except ValidationError as err:
        raise ValueError(f"Invalid configuration: {err}") from err


__all__ = [
    "AppConfig",
    "CodeProjectConfig",
    "FrigateConfig",
    "PlateRecognizerConfig",
    "PathsConfig",
    "load_app_config",
]
