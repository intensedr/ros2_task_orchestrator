import json

import pytest

from task_orchestrator_media.config import (
    MediaConfigError,
    normalize_json_object,
    parse_audio_devices_json,
    parse_devices_json,
)


def test_parse_devices_json_builds_backend_neutral_device():
    devices = parse_devices_json(
        json.dumps(
            [
                {
                    "device_id": "front_rgb",
                    "source_topic": "/camera/front/image/compressed",
                    "media_kind": "thermal",
                    "mime_type": "image/png",
                    "extension": ".png",
                    "capabilities": ["snapshot"],
                    "metadata": {"vendor": "example"},
                }
            ]
        )
    )

    assert len(devices) == 1
    assert devices[0].device_id == "front_rgb"
    assert devices[0].media_kind == "thermal"
    assert devices[0].capabilities == ("snapshot",)
    assert json.loads(devices[0].metadata_json) == {"vendor": "example"}


@pytest.mark.parametrize("device_id", ["../camera", "/camera", "camera/../../etc", ""])
def test_parse_devices_json_rejects_unsafe_device_id(device_id):
    with pytest.raises(MediaConfigError):
        parse_devices_json(json.dumps([{"device_id": device_id, "source_topic": "/image"}]))


def test_parse_devices_json_rejects_duplicates():
    payload = [
        {"device_id": "camera", "source_topic": "/one"},
        {"device_id": "camera", "source_topic": "/two"},
    ]
    with pytest.raises(MediaConfigError, match="duplicate"):
        parse_devices_json(json.dumps(payload))


def test_parse_audio_devices_json_builds_pcm_source():
    devices = parse_audio_devices_json(
        json.dumps(
            [
                {
                    "device_id": "body_microphone",
                    "source_topic": "/audio/pcm",
                    "sample_format": "s16le",
                    "sample_rate_hz": 48000,
                    "channels": 2,
                }
            ]
        )
    )

    assert devices[0].device_id == "body_microphone"
    assert devices[0].sample_rate_hz == 48000
    assert devices[0].channels == 2


def test_parse_audio_devices_json_rejects_unknown_pcm_format():
    with pytest.raises(MediaConfigError, match="sample_format"):
        parse_audio_devices_json(
            json.dumps(
                [{"device_id": "microphone", "source_topic": "/audio", "sample_format": "mp3"}]
            )
        )


def test_normalize_json_object_is_canonical():
    assert normalize_json_object('{"z": 1, "a": true}') == '{"a":true,"z":1}'


def test_normalize_json_object_rejects_array():
    with pytest.raises(MediaConfigError, match="object"):
        normalize_json_object("[]")
