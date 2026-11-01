"""Media-device configuration parsing and validation."""

from __future__ import annotations

import json
import re
from typing import Any

from task_orchestrator_media.models import AudioDeviceConfig, DeviceConfig

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_EXTENSION_PATTERN = re.compile(r"^\.[A-Za-z0-9]{1,10}$")
_AUDIO_SAMPLE_FORMATS = {"u8", "s16le", "s24le", "s32le", "f32le", "f64le"}


class MediaConfigError(ValueError):
    """Raised when media-manager configuration is malformed."""


def validate_identifier(value: str, field_name: str) -> str:
    """Validate an identifier that will also be used as a path component."""
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise MediaConfigError(
            f"{field_name} must start with an alphanumeric character and contain only "
            "letters, digits, '.', '_' or '-'"
        )
    return value


def validate_extension(value: str, field_name: str = "extension") -> str:
    """Validate a short file extension used below the spool root."""
    if not isinstance(value, str) or not _EXTENSION_PATTERN.fullmatch(value):
        raise MediaConfigError(f"{field_name} must look like '.jpg'")
    return value.lower()


def normalize_json_object(value: str, field_name: str = "metadata_json") -> str:
    """Validate and canonically serialize a JSON object."""
    payload_text = value or "{}"
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise MediaConfigError(f"{field_name} is not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise MediaConfigError(f"{field_name} must decode to an object")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def parse_devices_json(value: str) -> tuple[DeviceConfig, ...]:
    """Parse the node's JSON array of compressed-image devices."""
    try:
        payload = json.loads(value or "[]")
    except json.JSONDecodeError as exc:
        raise MediaConfigError(f"devices_json is not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, list):
        raise MediaConfigError("devices_json must decode to an array")

    devices: list[DeviceConfig] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise MediaConfigError(f"devices_json[{index}] must be an object")
        device = _parse_device(item, index=index)
        if device.device_id in seen_ids:
            raise MediaConfigError(f"duplicate device_id: {device.device_id}")
        seen_ids.add(device.device_id)
        devices.append(device)
    return tuple(devices)


def parse_audio_devices_json(value: str) -> tuple[AudioDeviceConfig, ...]:
    """Parse raw PCM microphone sources."""
    try:
        payload = json.loads(value or "[]")
    except json.JSONDecodeError as exc:
        raise MediaConfigError(f"audio_devices_json is not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, list):
        raise MediaConfigError("audio_devices_json must decode to an array")

    devices: list[AudioDeviceConfig] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise MediaConfigError(f"audio_devices_json[{index}] must be an object")
        device = _parse_audio_device(item, index)
        if device.device_id in seen_ids:
            raise MediaConfigError(f"duplicate audio device_id: {device.device_id}")
        seen_ids.add(device.device_id)
        devices.append(device)
    return tuple(devices)


def _parse_device(item: dict[str, Any], index: int) -> DeviceConfig:
    prefix = f"devices_json[{index}]"
    device_id = validate_identifier(item.get("device_id", ""), f"{prefix}.device_id")
    source_topic = item.get("source_topic", "")
    if not isinstance(source_topic, str) or not source_topic:
        raise MediaConfigError(f"{prefix}.source_topic must be a non-empty string")

    extension = validate_extension(item.get("extension", ".jpg"), f"{prefix}.extension")

    capabilities = item.get("capabilities", ["snapshot", "recording"])
    if not isinstance(capabilities, list) or not all(isinstance(value, str) for value in capabilities):
        raise MediaConfigError(f"{prefix}.capabilities must be an array of strings")

    metadata = item.get("metadata", {})
    if not isinstance(metadata, dict):
        raise MediaConfigError(f"{prefix}.metadata must be an object")

    display_name = item.get("display_name", device_id)
    media_kind = item.get("media_kind", "image")
    mime_type = item.get("mime_type", "image/jpeg")
    frame_id = item.get("frame_id", "")
    for field_name, field_value in (
        ("display_name", display_name),
        ("media_kind", media_kind),
        ("mime_type", mime_type),
        ("frame_id", frame_id),
    ):
        if not isinstance(field_value, str):
            raise MediaConfigError(f"{prefix}.{field_name} must be a string")
    if not media_kind or not mime_type:
        raise MediaConfigError(f"{prefix}.media_kind and {prefix}.mime_type must not be empty")

    return DeviceConfig(
        device_id=device_id,
        source_topic=source_topic,
        display_name=display_name,
        media_kind=media_kind,
        mime_type=mime_type,
        extension=extension,
        frame_id=frame_id,
        capabilities=tuple(capabilities),
        metadata_json=json.dumps(metadata, sort_keys=True, separators=(",", ":")),
    )


def _parse_audio_device(item: dict[str, Any], index: int) -> AudioDeviceConfig:
    prefix = f"audio_devices_json[{index}]"
    device_id = validate_identifier(item.get("device_id", ""), f"{prefix}.device_id")
    source_topic = item.get("source_topic", "")
    if not isinstance(source_topic, str) or not source_topic:
        raise MediaConfigError(f"{prefix}.source_topic must be a non-empty string")
    sample_format = item.get("sample_format", "s16le")
    if sample_format not in _AUDIO_SAMPLE_FORMATS:
        raise MediaConfigError(
            f"{prefix}.sample_format must be one of {sorted(_AUDIO_SAMPLE_FORMATS)}"
        )
    sample_rate_hz = item.get("sample_rate_hz", 48_000)
    channels = item.get("channels", 1)
    if not isinstance(sample_rate_hz, int) or not 8_000 <= sample_rate_hz <= 384_000:
        raise MediaConfigError(f"{prefix}.sample_rate_hz must be between 8000 and 384000")
    if not isinstance(channels, int) or not 1 <= channels <= 8:
        raise MediaConfigError(f"{prefix}.channels must be between 1 and 8")
    message_type = item.get(
        "message_type",
        "task_orchestrator_media_msgs/msg/AudioChunkV1",
    )
    display_name = item.get("display_name", device_id)
    frame_id = item.get("frame_id", "")
    capabilities = item.get("capabilities", ["recording"])
    metadata = item.get("metadata", {})
    if not all(isinstance(value, str) for value in (message_type, display_name, frame_id)):
        raise MediaConfigError(f"{prefix} string fields must contain strings")
    if not message_type or "/msg/" not in message_type:
        raise MediaConfigError(f"{prefix}.message_type must be a ROS message type")
    if not isinstance(capabilities, list) or not all(isinstance(value, str) for value in capabilities):
        raise MediaConfigError(f"{prefix}.capabilities must be an array of strings")
    if not isinstance(metadata, dict):
        raise MediaConfigError(f"{prefix}.metadata must be an object")
    return AudioDeviceConfig(
        device_id=device_id,
        source_topic=source_topic,
        display_name=display_name,
        message_type=message_type,
        sample_format=sample_format,
        sample_rate_hz=sample_rate_hz,
        channels=channels,
        frame_id=frame_id,
        capabilities=tuple(capabilities),
        metadata_json=json.dumps(metadata, sort_keys=True, separators=(",", ":")),
    )
